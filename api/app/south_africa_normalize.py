"""
Pure normalization logic for eTenders South Africa (National
Treasury). Zero I/O — same separation pattern as every other
normalizer in this project.

Same architectural shape as CPPP (India), for the same reason: the
portal's OCDS feed carries no CPV/NAICS/UNSPSC-equivalent code —
tender.category is free text ("Services: Building", "Goods"), not a
structured scheme. Defence-relevance is decided by the buying/
procuring entity instead, the same approach app/cppp_india_normalize.py
uses. Matched records carry the sentinel classification code
SOUTH_AFRICA_DEFENCE_ORG_CODE ('ZA-DEF'); see
app/programme_matching.py for how matching treats every sentinel
(India's and South Africa's both) the same way — it grants the same
category bonus a real code would, but a sentinel match with zero
keyword corroboration is dropped outright rather than surfaced as a
weak match, because a sentinel alone distinguishes nothing between
capabilities.

Source of the field layout: OCDS release JSON from
ocds-api.etenders.gov.za/api/OCDSReleases, confirmed live —
tender.title, tender.description, tender.category, tender.tenderPeriod
.endDate, tender.procuringEntity.name (falls back to buyer.name),
tender.contactPerson.{name,email,telephoneNumber}, tender.documents[0]
.url (the closest this feed has to a per-notice detail link — there
is no separate public tender-detail webpage URL in the release).
"""

import re
from typing import Optional, TypedDict

# Estate/facilities-works exclusion is shared with CPPP and CanadaBuys
# — see app/works_filter.py's own docstring for why the same list
# applies to a third armed forces' estate-maintenance procurement.
from app.works_filter import (  # noqa: F401  (re-exported for callers/tests)
    DEFENCE_MATERIEL_TERMS,
    FACILITIES_WORKS_TERMS,
    is_facilities_works,
    is_uninformative_title,
)

# Sentinel stored in programmes.naics_code for this source — same
# compromise the README already documents for programmes.naics_code
# holding CPV codes for non-US sources, and IN-DEF for India.
SOUTH_AFRICA_DEFENCE_ORG_CODE = "ZA-DEF"

# South Africa's defence-relevant buying entities. ARMSCOR (the
# Armaments Corporation of South Africa, the state's defence
# acquisition agency) is the one confirmed live in the feed before
# this module was written; the Department of Defence and SANDF branch
# names are included because they are the other bodies a South
# African defence-procurement source would plausibly name, on the
# same "verify before promising" footing as every other source here —
# they have not each been individually confirmed live the way ARMSCOR
# was, and a run that never sees them is not a bug.
DEFENCE_ORG_PHRASES = (
    "ARMSCOR",
    "ARMAMENTS CORPORATION",
    "DEPARTMENT OF DEFENCE",
    "DEPARTMENT OF DEFENSE",
    "SOUTH AFRICAN NATIONAL DEFENCE FORCE",
    "SOUTH AFRICAN ARMY",
    "SOUTH AFRICAN NAVY",
    "SOUTH AFRICAN AIR FORCE",
    "MILITARY HEALTH",
    # DENEL is South Africa's state-owned defence manufacturer —
    # the country's main prime for artillery, missiles, armoured
    # vehicles and aerostructures, and the closest equivalent to the
    # Indian DPSUs already covered in cppp_india_normalize. It was
    # missing from this list entirely, which is how a live 90-day
    # window containing "DENEL (Pty) Ltd" as a publishing buyer
    # returned zero defence-relevant releases. Found by checking the
    # raw feed's distinct buyer names against this filter rather than
    # trusting the filter's own output.
    "DENEL",
)

# Acronyms matched on word boundaries only — substring matching
# "SANDF" is safe on its own, but "SAAF" or "SAN" risk colliding with
# unrelated names, so every acronym here goes through the same
# boundary-matched regex CPPP uses for MES/BSF/etc.
DEFENCE_ORG_ACRONYMS = ("SANDF", "SAAF", "SAMHS")

_ACRONYM_RE = re.compile(
    r"(?<![A-Za-z0-9])(" + "|".join(re.escape(a) for a in DEFENCE_ORG_ACRONYMS) + r")(?![A-Za-z0-9])",
    re.IGNORECASE,
)


def is_defence_organisation(org_name: Optional[str]) -> bool:
    """Pure relevance check — the South Africa analogue of is_defence_organisation in cppp_india_normalize."""
    if not org_name:
        return False
    upper = org_name.upper()
    if any(phrase in upper for phrase in DEFENCE_ORG_PHRASES):
        return True
    return bool(_ACRONYM_RE.search(org_name))


class NormalizedProgramme(TypedDict):
    external_ref: str
    name: str
    country: str
    organization_name: Optional[str]
    stage: Optional[str]
    classification_code: Optional[str]
    posted_date: Optional[str]
    response_deadline: Optional[str]
    ui_link: Optional[str]
    contact_name: Optional[str]
    contact_email: Optional[str]
    contact_phone: Optional[str]


# South Africa's own OCDS tender.status values, mapped to this
# platform's programmes.stage enum. Anything not listed here (e.g.
# 'cancelled', 'unsuccessful' — statuses this feed also uses) is left
# unmapped and the record is dropped rather than guessed at, same
# discipline as every other normalizer's stage mapping.
_STAGE_MAP = {
    "planning": "requirement_defined",
    "planned": "requirement_defined",
    "active": "rfp_issued",
    "complete": "contract_awarded",
}

# The rest of the OCDS tender-status codelist. These are perfectly
# valid statuses, but none of them is a procurement anyone can still
# bid on, and programmes.stage has no state that means "called off" —
# mapping them onto rfp_issued would show a customer a live
# opportunity that no longer exists.
#
# This distinction is not hypothetical: in the first working run after
# the fetch was repaired, South Africa returned 94 releases in 30
# days, of which exactly two were defence-relevant, and BOTH were
# cancelled. Before this set existed they were recorded as parse
# failures, which read as "the normalizer is broken" rather than "the
# tenders were called off".
NON_INGESTABLE_STATUSES = {"cancelled", "withdrawn", "unsuccessful"}


class NotAnOpportunity(ValueError):
    """
    Understood perfectly, just not something to ingest — the same
    category as a non-defence buyer, not a parse failure. Subclasses
    ValueError so any caller that only knows the old contract still
    behaves safely.
    """


def normalize_release(raw: dict) -> NormalizedProgramme:
    """
    Maps one OCDS release into our schema's shape. Raises ValueError
    for a release with no usable tender id — the same "can't dedupe
    safely" discipline every other normalizer's normalize_row/
    normalize_release applies.
    """
    tender = raw.get("tender") or {}
    external_ref = str(tender.get("id") or raw.get("ocid") or "").strip()
    if not external_ref:
        raise ValueError("Release has no tender id or ocid — cannot dedupe safely")

    title = (tender.get("title") or "").strip()
    description = (tender.get("description") or "").strip()
    # The title field is often just an internal reference code (see
    # is_uninformative_title) — the description is where the actual
    # subject lives, so it is what's kept for keyword matching and
    # display, falling back to the title only when no description
    # was published.
    name = description or title or f"eTenders SA Notice {external_ref}"

    status = (tender.get("status") or "").strip().lower()
    stage = _STAGE_MAP.get(status)
    if stage is None:
        if status in NON_INGESTABLE_STATUSES:
            raise NotAnOpportunity(f"Tender status {status!r} — not open to bid on")
        # A status outside the whole OCDS codelist is a genuine
        # surprise and must stay loud.
        raise ValueError(f"Unmapped OCDS tender status: {status!r}")

    procuring_entity = tender.get("procuringEntity") or {}
    buyer = raw.get("buyer") or {}
    organisation = (procuring_entity.get("name") or buyer.get("name") or "").strip() or None

    tender_period = tender.get("tenderPeriod") or {}
    documents = tender.get("documents") or []
    ui_link = documents[0].get("url") if documents else None

    contact = tender.get("contactPerson") or {}

    return {
        "external_ref": external_ref,
        "name": name,
        "country": "South Africa",
        "organization_name": organisation,
        "stage": stage,
        "classification_code": SOUTH_AFRICA_DEFENCE_ORG_CODE,
        "posted_date": raw.get("date"),
        "response_deadline": tender_period.get("endDate"),
        "ui_link": ui_link,
        "contact_name": contact.get("name") or None,
        "contact_email": contact.get("email") or None,
        "contact_phone": contact.get("telephoneNumber") or None,
    }


def normalize_batch(raw_releases: list[dict]) -> tuple[list[NormalizedProgramme], list[dict]]:
    """
    Normalizes and filters for defence-relevance in one pass, same
    shape as cppp_india_normalize.normalize_batch. A release from a
    non-defence buyer, or one whose tender is cancelled/withdrawn/
    unsuccessful, is not a failure — it parsed fine, it's just
    outside what this source is used for / what this platform's stage
    enum represents.
    """
    normalized = []
    failures = []
    for raw in raw_releases:
        try:
            record = normalize_release(raw)
        except NotAnOpportunity:
            # Skipped, exactly like a non-defence buyer below — never
            # reported as a failure, which is what the docstring above
            # always promised and the code did not do.
            continue
        except (ValueError, AttributeError, TypeError) as e:
            failures.append({"error": str(e), "ocid": raw.get("ocid")})
            continue
        if not is_defence_organisation(record["organization_name"]):
            continue
        if is_facilities_works(record["name"]) or is_uninformative_title(record["name"]):
            continue
        normalized.append(record)
    return normalized, failures
