"""
Its own tiny module for the same reason app/address_format.py and
app/org_name_format.py are separate files: a pure helper with no
sqlalchemy import at module level, usable from anywhere without risking
a circular import — main.py imports this, and so does
procurement_intelligence.py (which main.py itself imports), so
not_a_test_fixture could not live in main.py once a second caller
outside it needed it too.
"""


def not_a_test_fixture(alias: str = "") -> str:
    """
    SQL predicate identifying a genuinely ingested government tender as
    opposed to a fixture row the test suite seeded into this same
    shared table. Applied everywhere a count could be read as a real,
    customer-facing claim — the public homepage stats and their
    drill-downs, and (2026-09, after a user spotted Home and
    Presentation showing different "Live Tenders Tracked" numbers)
    Procurement Intelligence's funnel, which the Presentation page
    also reads live during sales demos.

    Takes the table alias rather than being a bare string callers
    string-replace into: matching on both `name` and `external_ref`
    means a naive .replace() would silently corrupt the predicate the
    moment another column containing either word is added to it.

    The returned SQL contains only these hardcoded literals — no
    caller-supplied value is ever interpolated, so there is nothing
    here for a request to inject into.
    """
    p = f"{alias}." if alias else ""
    return (
        f"({p}name not ilike 'Test Fixture:%' "
        f"and ({p}external_ref is null or {p}external_ref !~ '^(test|ptc-test|wontest|cltest|wcl)'))"
    )
