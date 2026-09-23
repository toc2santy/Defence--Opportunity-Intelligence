"""
Pure normalization logic for UK Find a Tender Service (OCDS format).
Zero I/O, same separation pattern as app/sam_gov_normalize.py — this
is what the unit tests import, tested against GOV.UK's own
documented example response
(find-tender.service.gov.uk/apidocumentation/1.0/GET-ocdsReleasePackages),
not invented data.

REAL ARCHITECTURAL DIFFERENCE FROM SAM.GOV, worth stating plainly:
Find a Tender's API has NO server-side category filter — unlike
SAM.gov's `ncode` parameter, there's no way to ask the API for "only
CPV 35xxxxxx". Every call returns whatever's in the date/stage
window regardless of category, and defense-relevance is filtered
HERE, client-side, after fetching. This also means no NAICS-style
rotation is needed for this source (see app/uk_ft_ingestion.py) —
rotation existed for SAM.gov specifically because of its per-code
server-side filtering; a date-range pull naturally covers every
category in one call, real classification-diversity notwithstanding.

CPV codes verified against multiple independent procurement-data
sources, not guessed:
  35000000 division = "Security, fire-fighting, police and defence
  equipment" (this whole division is defense/security-relevant).
  Specific 50xxxxxx codes for military repair/maintenance services
  sit outside that division and are matched individually.
"""

from typing import Optional, TypedDict

from app.address_format import format_address

# The entire "35" CPV division is security/defence equipment — see
# module docstring. These specific 50-series codes are defense-
# relevant repair/maintenance services that sit OUTSIDE that
# division, so they're matched individually rather than by prefix.
DEFENSE_RELEVANT_CPV_PREFIXES = {
    "35": "Security, fire-fighting, police and defence equipment (entire division)",
}
DEFENSE_RELEVANT_CPV_EXACT_PREFIXES = {
    "5063": "Repair and maintenance services of military vehicles",
    "5064": "Repair and maintenance services of warships",
    "5065": "Repair and maintenance services of military aircraft, missiles and spacecraft",
    "5066": "Repair and maintenance services of military electronic systems",
}


def is_defense_relevant_cpv(cpv_code: Optional[str]) -> bool:
    """Pure classification check — no I/O, unit-testable in isolation."""
    if not cpv_code:
        return False
    if cpv_code.startswith("35"):
        return True
    return any(cpv_code.startswith(prefix) for prefix in DEFENSE_RELEVANT_CPV_EXACT_PREFIXES)


# Find a Tender's own `tag` values (planning/tender/award, matching
# the `stages` request parameter) mapped to our programmes.stage
# check constraint — same defensive-fallback philosophy as the
# SAM.gov stage map: an unrecognized tag should never crash
# ingestion over one release, it should fall back honestly.
_STAGE_MAP = {
    "planning": "early_concept",
    "tender": "rfp_issued",
    "award": "contract_awarded",
}


class NormalizedWinner(TypedDict):
    name: str
    country: Optional[str]


class NormalizedProgramme(TypedDict):
    external_ref: str
    name: str
    country: str
    organization_name: Optional[str]
    stage: Optional[str]
    classification_code: Optional[str]
    classification_scheme: Optional[str]
    posted_date: Optional[str]
    ui_link: Optional[str]
    winners: list[NormalizedWinner]
    contact_name: Optional[str]
    contact_email: Optional[str]
    contact_phone: Optional[str]
    contact_address: Optional[str]
    set_aside_code: Optional[str]
    set_aside_description: Optional[str]


# Eligibility/Backup Phase 1 follow-up (2026-09) — the field this
# project's own eligibility audit found live but left unwired. Real
# path confirmed by a live scan of ~1000 real Find a Tender releases:
# `tender.otherRequirements.reservedParticipation` — an ARRAY of OCDS
# codelist values (not a top-level `reservedParticipationLocation`
# field, which was never actually observed live despite the name
# this project originally noted; `reservedParticipation` inside
# `otherRequirements` is the real field, confirmed by walking a real
# release's full JSON for every key containing "reserved"). One real
# example found live: release 041633-2026 carries
# `["shelteredWorkshop"]`. Only that one value has ever been directly
# observed, so only it gets a translated description — any other
# codelist value this project hasn't seen yet is shown as its raw
# code rather than a guessed-at label, same "don't invent, surface
# the raw source text" rule DNCP Paraguay's own eligibility fallback
# already established.
_RESERVED_PARTICIPATION_LABELS = {
    "shelteredWorkshop": "Reserved for sheltered workshops / supported businesses",
}


def extract_set_aside(tender: dict) -> tuple[Optional[str], Optional[str]]:
    values = (tender.get("otherRequirements") or {}).get("reservedParticipation") or []
    if not values:
        return None, None
    code = values[0]
    description = _RESERVED_PARTICIPATION_LABELS.get(code, f"Reserved participation: {code}")
    return code, description


def extract_winners(raw: dict) -> list[NormalizedWinner]:
    """
    OCDS represents a winner as a `parties` entry with role
    'supplier' — confirmed live, and unlike TED, each supplier party
    already carries its own name and country directly (no separate
    array to zip and no risk of misalignment), because OCDS parties
    are individually-addressed objects, not the parallel arrays TED
    uses. Only present on award-stage releases in practice, since
    only those name a supplier at all.
    """
    winners = []
    for party in raw.get("parties") or []:
        if "supplier" not in (party.get("roles") or []):
            continue
        name = (party.get("name") or "").strip()
        if not name:
            continue
        country = ((party.get("address") or {}).get("countryName") or "").strip() or None
        winners.append({"name": name, "country": country})
    return winners


def extract_contact(raw: dict) -> dict:
    """
    OCDS puts the procurement contact on the buyer party's
    `contactPoint`. Live data is a mix of named individuals
    ("MIRELA SIMIONOV") and team inboxes ("Corporate Procurement
    Team"), which cannot be reliably told apart — so both are treated
    as personal data and handled under the same restriction (see
    db/migrations/017).

    The buyer party's `address` is read from the same object, using
    the identical OCDS Address shape (streetAddress/locality/region/
    postalCode/countryName) already confirmed live on SUPPLIER parties
    in this exact payload type (see extract_winners below) — the
    schema is the same regardless of which role the party carries.
    """
    for party in raw.get("parties") or []:
        if "buyer" not in (party.get("roles") or []):
            continue
        cp = party.get("contactPoint") or {}
        address = party.get("address") or {}
        return {
            "contact_name": (cp.get("name") or "").strip() or None,
            "contact_email": (cp.get("email") or "").strip() or None,
            "contact_phone": (cp.get("telephone") or "").strip() or None,
            "contact_address": format_address(
                street=address.get("streetAddress"), locality=address.get("locality"),
                region=address.get("region"), postal_code=address.get("postalCode"),
                country=address.get("countryName"),
            ),
        }
    return {"contact_name": None, "contact_email": None, "contact_phone": None, "contact_address": None}


def normalize_release(raw: dict) -> NormalizedProgramme:
    """
    Maps one OCDS release object into our schema's shape. Uses
    .get() defensively throughout — OCDS is a large, extensible
    standard, and not every field is guaranteed present on every
    release (the official example itself shows many optional
    nested objects).
    """
    release_id = raw.get("id") or raw.get("ocid")
    if not release_id:
        raise ValueError("Release has neither id nor ocid — cannot dedupe safely")

    tender = raw.get("tender") or {}
    title = (tender.get("title") or "Untitled Find a Tender Notice").strip()

    tags = raw.get("tag") or []
    stage_tag = tags[0] if tags else None
    stage = _STAGE_MAP.get(stage_tag, "requirement_defined")

    classification = tender.get("classification") or {}
    classification_code = classification.get("id")
    classification_scheme = classification.get("scheme")

    buyer = raw.get("buyer") or {}
    organization_name = buyer.get("name")
    if not organization_name:
        # fall back to the parties array if buyer.name wasn't set directly
        for party in raw.get("parties") or []:
            if "buyer" in (party.get("roles") or []):
                organization_name = party.get("name")
                break

    set_aside_code, set_aside_description = extract_set_aside(tender)

    return {
        "external_ref": release_id,
        "name": title,
        "country": "United Kingdom",
        "organization_name": organization_name.strip() if organization_name else None,
        "stage": stage,
        "classification_code": classification_code,
        "classification_scheme": classification_scheme,
        "posted_date": raw.get("date"),
        "ui_link": f"https://www.find-tender.service.gov.uk/Notice/{release_id}" if release_id else None,
        "winners": extract_winners(raw),
        # Procurement contact for THIS tender, from the buyer party's
        # OCDS contactPoint — see db/migrations/017 for the deliberate
        # scope limit on how this personal data may be used.
        **extract_contact(raw),
        "set_aside_code": set_aside_code,
        "set_aside_description": set_aside_description,
    }


def normalize_batch(raw_releases: list[dict]) -> tuple[list[NormalizedProgramme], list[dict]]:
    """
    Normalizes a batch AND filters for defense-relevance in one
    pass (see module docstring for why filtering has to happen
    here rather than server-side for this source). Malformed
    records are collected as failures, same pattern as
    sam_gov_normalize.normalize_batch; non-defense-relevant records
    are simply not included — they aren't failures, they're
    correctly-parsed records this platform doesn't care about.
    """
    normalized = []
    failures = []
    for raw in raw_releases:
        try:
            record = normalize_release(raw)
        except (ValueError, AttributeError) as e:
            failures.append({"error": str(e), "id": raw.get("id"), "ocid": raw.get("ocid")})
            continue
        if is_defense_relevant_cpv(record["classification_code"]):
            normalized.append(record)
    return normalized, failures
