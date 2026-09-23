"""
One tiny pure function, in its own module for the same reason
app/address_format.py and app/scoring.py are: app/ingestion_common.py
is genuinely I/O-adjacent (it imports sqlalchemy at module level
throughout), so a pure helper that unit tests need to import without
sqlalchemy installed cannot live there directly — the local dev venv
deliberately excludes sqlalchemy (see app/capability_resolver.py's
docstring for the full reasoning every pure module here follows).
"""

import re

_WHITESPACE_RE = re.compile(r"\s+")


def normalize_org_name(name: str) -> str:
    """
    Collapses repeated whitespace and trims — nothing more. Found live
    (db/migrations/035): SAM.gov's fullParentPathName inserted the
    same DLA Aviation Ogden office twice, once with a double space.
    Deliberately whitespace-only: anything more aggressive (accent-
    folding, fuzzy matching) risks merging two genuinely DIFFERENT
    buyers into one, which is worse than the fragmentation it would
    fix — see migration 035's header for the full reasoning.
    """
    return _WHITESPACE_RE.sub(" ", name.strip())
