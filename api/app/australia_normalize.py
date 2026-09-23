"""
Pure normalization logic for AusTender (Australia). Zero I/O, same
separation pattern as every other normalizer here.

THIS SOURCE IS CONTRACT NOTICES ONLY, not a pre-award tender
pipeline — every release already carries a signed contract and a
named winner (see db/migrations/038 for what was verified live before
building this). That is genuinely different from every source except
CanadaBuys's separate award-file phase: there is no "stage" to infer
from a status field, because every row here IS an award. Every
programme from this module is therefore always stage =
'contract_awarded'.

RELEVANCE: unlike Colombia (which needs a three-part test because most
of its feed is unrelated public procurement), a live 100-row sample
here found "Department of Defence" alone already had a 55% hit rate,
with genuinely materiel-relevant descriptions (pump/battery/
transformer repairs). Buyer name is therefore the only relevance test
this source needs, so far — see the module docstring in
db/migrations/038 for exactly what was and was not confirmed live
before being added to DEFENCE_BUYER_NAMES.
"""

from typing import Optional, TypedDict


class NormalizedAward(TypedDict):
    external_ref: str
    name: str
    country: str
    organization_name: Optional[str]
    classification_code: Optional[str]
    classification_scheme: Optional[str]
    posted_date: Optional[str]
    ui_link: Optional[str]
    winner_name: Optional[str]
    winner_identifier: Optional[str]
    contact_address: Optional[str]
    contact_name: Optional[str]
    contact_email: Optional[str]
    contact_phone: Optional[str]
    value_amount: Optional[float]
    value_currency: Optional[str]


# Only what was actually seen in a live sample — see db/migrations/038
# for the exact count and what deliberately was NOT added (other real
# Australian defence bodies that may exist under their own party
# names but weren't observed).
DEFENCE_BUYER_NAMES = (
    "Department of Defence",
)


def is_defence_buyer(procuring_entity_name: Optional[str]) -> bool:
    return (procuring_entity_name or "").strip() in DEFENCE_BUYER_NAMES


def _party_with_role(parties: list[dict], role: str) -> Optional[dict]:
    for party in parties:
        if role in (party.get("roles") or []):
            return party
    return None


def _abn(party: Optional[dict]) -> Optional[str]:
    if not party:
        return None
    for identifier in party.get("additionalIdentifiers") or []:
        if identifier.get("scheme") == "AU-ABN":
            value = str(identifier.get("id") or "").strip()
            return value or None
    return None


def normalize_release(release: dict) -> Optional[NormalizedAward]:
    """
    Returns None for a release with no usable contract — a release
    without a `contracts` entry (e.g. an amendment-only or planning
    release, both real shapes this API can return) has nothing to
    ingest, not a parse failure.
    """
    contracts = release.get("contracts") or []
    if not contracts:
        return None
    contract = contracts[0]

    external_ref = str(contract.get("id") or "").strip()
    if not external_ref:
        # Falls back to the release id — contracts[].id (the real
        # "CN" contract notice number) is missing on a small minority
        # of releases in live data, and the release id is still
        # genuinely unique.
        external_ref = str(release.get("id") or "").strip()
    if not external_ref:
        return None

    parties = release.get("parties") or []
    buyer = _party_with_role(parties, "procuringEntity")
    supplier = _party_with_role(parties, "supplier")
    buyer_name = (buyer or {}).get("name") or None

    if not is_defence_buyer(buyer_name):
        return None

    # AusTender's own `description` is the real human text ("Pump
    # Repair", "Battery Charger Repairs"); `title` on a live sample
    # was frequently just the numeric solicitation ID, not a title —
    # so description is preferred and title is only the fallback.
    name = str(contract.get("description") or "").strip()
    if not name:
        name = str(contract.get("title") or "").strip()
    if not name:
        name = f"AusTender Contract {external_ref}"

    items = contract.get("items") or []
    # Only the first item's code is kept — the same single-category-
    # per-row simplification Colombia already uses, documented in
    # db/migrations/038.
    classification = (items[0].get("classification") or {}) if items else {}
    code = str(classification.get("id") or "").strip() or None
    scheme = classification.get("scheme")

    winner_name = None
    winner_identifier = None
    awards = release.get("awards") or []
    if awards:
        suppliers = awards[0].get("suppliers") or []
        if suppliers:
            winner_name = str(suppliers[0].get("name") or "").strip() or None
    if winner_name is None and supplier:
        winner_name = str(supplier.get("name") or "").strip() or None
    if winner_name:
        winner_identifier = _abn(supplier)

    value = contract.get("value") or {}
    value_amount = None
    if value.get("amount") is not None:
        try:
            value_amount = float(value["amount"])
        except (TypeError, ValueError):
            value_amount = None
    value_currency = str(value.get("currency") or "").strip() or None

    return {
        "external_ref": external_ref,
        "name": name,
        "country": "Australia",
        "organization_name": buyer_name,
        "classification_code": code,
        "classification_scheme": "UNSPSC" if (code and scheme == "UNSPSC") else None,
        "posted_date": contract.get("dateSigned") or release.get("date") or None,
        "ui_link": None,  # AusTender's OCDS API publishes no public notice URL field
        "winner_name": winner_name,
        "winner_identifier": winner_identifier,
        # Deliberately NOT populated from parties[procuringEntity], even
        # though that party object does carry an `address` and a
        # `contactPoint` field: checked live across every Department of
        # Defence row sampled and found `address` is always an empty
        # object and `contactPoint` is always the SAME literal
        # "tenders@finance.gov.au" — a shared, department-wide inbox,
        # not anything specific to the buying unit. Showing the
        # byte-identical "contact" on every single AusTender programme
        # would look like fabricated data, not a real lead — the exact
        # thing this platform's evidence model exists to avoid. Left
        # None, the same honest choice already made for CanadaBuys and
        # South Africa's genuinely-missing address fields.
        "contact_address": None,
        "contact_name": None,
        "contact_email": None,
        "contact_phone": None,
        "value_amount": value_amount,
        "value_currency": value_currency,
    }


def normalize_batch(releases: list[dict]) -> tuple[list[NormalizedAward], list[dict]]:
    """
    Normalizes every release, keeping only defence-buyer contract
    notices. A release with no `contracts` or failing to parse is not
    a failure worth surfacing — it's a real, expected shape (planning/
    amendment releases) this API mixes into the same feed.
    """
    normalized = []
    failures = []
    for release in releases:
        try:
            record = normalize_release(release)
        except (AttributeError, TypeError, KeyError) as e:
            failures.append({"error": str(e), "release_id": release.get("id")})
            continue
        if record is not None:
            normalized.append(record)
    return normalized, failures
