"""
Resolves a programme's raw classification code (NAICS, CPV, or
UNSPSC — whichever scheme its source uses) back to a human capability
label from `capability_taxonomy`. This is the inverse of
app/programme_matching.py, which goes capability -> codes to find
matching programmes; this goes code -> capability to describe what a
buyer or OEM actually deals in, for Customer/OEM Intelligence.

Deliberately split into an I/O step (build_code_index, one query per
mapping table) and a pure step (resolve_capability) — the same
separation every normalizer in this project uses, and what makes the
resolution logic itself unit-testable without a database.

ONE CODE CAN MEAN SEVERAL CAPABILITIES, and that is normal, not a
data error — CPV 35400000 ("military vehicles and associated parts")
is deliberately mapped to BOTH LAND.SYSTEMS and
MANUFACTURING.DEFENCE in db/migrations/010. So resolution returns a
LIST. An earlier version of this module kept a dict keyed by code and
returned a single capability, which silently discarded every mapping
but one — a buyer purchasing 35400000 was attributed to whichever
capability happened to be inserted last. That produced wrong (not
merely incomplete) capability breakdowns in every module that uses
this: Customer, OEM, Competitor and Partner intelligence.

MATCH ORDER MATTERS: NAICS and CPV are checked by exact equality
first (the mapping tables store real codes, one row per code).
UNSPSC is checked by prefix, and only at the LONGEST matching prefix
length — see programme_matching.py's own comment on why UNSPSC needs
prefix matching (it's hierarchical: Segment/Family/Class/Commodity).
Taking only the longest matching length is what makes a specific
mapping like SONAR.PASSIVE's '411155' win over a broader one like
SENSORS.GENERAL's '4111' for the same code, while still returning
every capability that shares that winning length. Resolution does not
depend on the prefix list arriving in any particular order.
"""

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Optional

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession


@dataclass
class CodeIndex:
    # code -> LIST of (capability_code, label); one code legitimately
    # maps to several capabilities (see module docstring).
    exact: dict[str, list[tuple[str, str]]] = field(default_factory=dict)
    prefixes: list[tuple[str, str, str]] = field(default_factory=list)  # (prefix, capability_code, label), longest first


async def build_code_index(session: "AsyncSession") -> CodeIndex:
    # Imported here, not at module level: this file's pure
    # resolve_capability() function is meant to be importable and
    # unit-testable without sqlalchemy installed at all, matching
    # every pure normalizer module in this project. The local dev
    # venv deliberately excludes sqlalchemy (see requirements-dev.txt
    # vs requirements.txt) for exactly this reason.
    from sqlalchemy import text

    index = CodeIndex()

    naics_rows = await session.execute(text("""
        select m.naics_code as code, ct.code as capability_code, ct.label
        from taxonomy_naics_mapping m
        join capability_taxonomy ct on ct.id = m.capability_id
    """))
    cpv_rows = await session.execute(text("""
        select m.cpv_code as code, ct.code as capability_code, ct.label
        from taxonomy_cpv_mapping m
        join capability_taxonomy ct on ct.id = m.capability_id
    """))
    # Both schemes populate the same dict, APPENDING rather than
    # overwriting — several capabilities can map to one code, and
    # dropping all but the last is exactly the bug this structure
    # exists to prevent (see module docstring).
    for row in list(naics_rows) + list(cpv_rows):
        index.exact.setdefault(row.code, []).append((row.capability_code, row.label))

    unspsc_rows = await session.execute(text("""
        select m.unspsc_prefix as prefix, ct.code as capability_code, ct.label
        from taxonomy_unspsc_mapping m
        join capability_taxonomy ct on ct.id = m.capability_id
    """))
    index.prefixes = sorted(
        ((row.prefix, row.capability_code, row.label) for row in unspsc_rows),
        key=lambda t: len(t[0]),
        reverse=True,
    )
    return index


def resolve_capabilities(code: Optional[str], index: CodeIndex) -> list[tuple[str, str]]:
    """
    Returns every (capability_code, capability_label) the code maps
    to, or [] if it resolves to none — an unmapped or unrecognised
    code is a real, expected outcome (see the deliberately-unmapped
    capabilities documented in db/migrations/010 and 015), not an
    error.
    """
    if not code:
        return []
    if code in index.exact:
        return list(index.exact[code])

    # Prefix matching, in two passes so the result does NOT depend on
    # index.prefixes being pre-sorted. build_code_index does sort it
    # longest-first, but a function that silently returns the wrong
    # answer for an unsorted input is a trap — one this project's own
    # unit test fixture fell into immediately.
    candidates = [
        (len(prefix), capability_code, label)
        for prefix, capability_code, label in index.prefixes
        if code.startswith(prefix)
    ]
    if not candidates:
        return []
    best_len = max(length for length, _, _ in candidates)
    return [
        (capability_code, label)
        for length, capability_code, label in candidates
        if length == best_len
    ]


def resolve_capability(code: Optional[str], index: CodeIndex) -> Optional[tuple[str, str]]:
    """
    Convenience wrapper returning only the FIRST resolved capability.
    Use resolve_capabilities() unless you genuinely want just one —
    this discards co-mapped capabilities by design.
    """
    resolved = resolve_capabilities(code, index)
    return resolved[0] if resolved else None
