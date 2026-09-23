"""
One tiny pure function, in its own module for the same reason
app/scoring.py and app/works_filter.py are: several normalizers need
it and none of them may import sqlalchemy at module level (see
app/capability_resolver.py's docstring for why that constraint
exists — the local dev venv has no sqlalchemy installed on purpose,
so every pure normalizer stays unit-testable without it).
"""

from typing import Optional


def format_address(
    street: Optional[str] = None,
    locality: Optional[str] = None,
    region: Optional[str] = None,
    postal_code: Optional[str] = None,
    country: Optional[str] = None,
) -> Optional[str]:
    """
    Joins whatever address parts a source actually published into one
    display line — "123 Main St, Springfield, IL 62701, United
    States" — skipping any part that's missing rather than leaving a
    blank in the middle ("Springfield, , 62701"). Returns None if
    nothing at all was published, so a normalizer can tell "no address
    on this record" apart from "address parsed to an empty string".

    Deliberately not locale-aware (no country-specific ordering) — the
    sources here span enough countries that a single honest
    comma-joined line beats guessing at each country's postal
    convention.
    """
    parts = [p.strip() for p in (street, locality, region, postal_code, country) if p and p.strip()]
    return ", ".join(parts) if parts else None
