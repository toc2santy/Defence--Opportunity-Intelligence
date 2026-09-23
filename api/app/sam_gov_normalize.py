"""
Pure normalization logic for SAM.gov Contract Opportunities records.

Deliberately separated from the network-fetching code (in
sam_gov_ingestion.py) the same way app/scoring.py is separated
from app/classification.py — this module has zero I/O, so it can
be unit-tested against a fixture matching SAM.gov's real,
documented response schema without needing a live API key or
network access.

Schema reference (verified against GSA's own documentation,
open.gsa.gov/api/get-opportunities-public-api, not guessed):
  https://open.gsa.gov/api/get-opportunities-public-api/

Known real defence-relevant NAICS codes, for reference by callers
building an ingestion request. Not exhaustive — a starting set.

EXPANDED 2026-09 after a Report Intel review found the real reason
Programme Intelligence's live SAM.gov data was so thin for Naval,
Land Systems, Comms, Aerospace, Space, Cyber and MRO: it was never a
`taxonomy_naics_mapping` gap for most of them — several already had
correct codes mapped (336611, 334220, 336413, 517410 among them) that
could simply never appear, because SAM.gov is queried BY these six
codes (see run_sam_gov_ingestion's `ncode` rotation) and none of the
missing capabilities' codes were ever in the list being queried for.
A perfect mapping table produces nothing if the source is never asked
for that code. Verified live: of the 3,514 programmes in this
database, only 5 distinct NAICS codes appear anywhere in SAM.gov data
— exactly the 6 below, minus one never returned live results.

Every new code added below is verified against the official 2022
NAICS structure (naics.com / Census.gov / IBISWorld — cross-checked
across independent directories, not a single source) before being
added, the same standard used for the CPV expansion in migration 025.
"""

from typing import Optional, TypedDict

from app.address_format import format_address

DEFENSE_RELEVANT_NAICS = {
    "336411": "Aircraft Manufacturing",
    "336414": "Guided Missile and Space Vehicle Manufacturing",
    "334511": "Search, Detection, Navigation, Guidance, Aeronautical Systems Manufacturing",
    "541330": "Engineering Services",
    "541712": "R&D in the Physical, Engineering, and Life Sciences (except Biotechnology)",
    "928110": "National Security",
    # Added 2026-09 — each already has a real capability behind it in
    # taxonomy_naics_mapping that had NEVER once been queried for:
    "336611": "Ship Building and Repairing",                                    # -> NAVAL.SYSTEMS
    "336992": "Military Armored Vehicle, Tank, and Tank Component Manufacturing",  # -> LAND.SYSTEMS / MANUFACTURING.DEFENCE
    "334220": "Radio and Television Broadcasting and Wireless Communications Equipment Manufacturing",  # -> COMMS.SECURE.TACTICAL
    "336413": "Other Aircraft Parts and Auxiliary Equipment Manufacturing",      # -> AEROSPACE.COMPONENTS
    "336412": "Aircraft Engine and Engine Parts Manufacturing",                 # -> AEROSPACE.COMPONENTS / AVIATION.MILITARY
    "517410": "Satellite Telecommunications",                                   # -> SPACE.DEFENCE
    "541512": "Computer Systems Design Services",                               # -> CYBER.DEFENCE / C4ISR.INTEGRATION — more specific than the generic 541712 R&D code both were relying on alone
    "811210": "Electronic and Precision Equipment Repair and Maintenance",      # -> MRO.GENERAL — complements 488190, which is air-transport-specific only
}

NAICS_GROUP_SIZE = 3


def get_naics_group(all_codes: list[str], rotation_index: int, group_size: int = NAICS_GROUP_SIZE) -> list[str]:
    """
    Pure rotation math — no I/O, unit-testable without a database.
    Splits `all_codes` into fixed-size groups and returns the group
    at `rotation_index`, wrapping around once every group has been
    covered. This is what actually widens real ingested coverage
    over successive runs, instead of a static "first N codes" slice
    that never changes.
    """
    if not all_codes:
        return []
    total_groups = max(1, (len(all_codes) + group_size - 1) // group_size)
    idx = rotation_index % total_groups
    start = idx * group_size
    return all_codes[start:start + group_size]


class NormalizedProgramme(TypedDict):
    external_ref: str          # SAM.gov noticeId — the dedup key
    name: str
    country: str
    organization_name: Optional[str]
    stage: Optional[str]
    naics_code: Optional[str]
    posted_date: Optional[str]
    response_deadline: Optional[str]
    ui_link: Optional[str]
    raw_type: Optional[str]
    set_aside_code: Optional[str]
    set_aside_description: Optional[str]
    contact_name: Optional[str]
    contact_email: Optional[str]
    contact_email_secondary: Optional[str]
    contact_phone: Optional[str]
    contact_address: Optional[str]
    winner_name: Optional[str]


# SAM.gov's own "type" field values, mapped to our programmes.stage
# check constraint. Anything not in this map falls back to
# 'requirement_defined' rather than raising — a genuinely unknown
# or new SAM.gov type should not crash an ingestion run over one
# unmapped record; it should be visible in the data instead.
_STAGE_MAP = {
    "Presolicitation": "early_concept",
    "Sources Sought": "early_concept",
    "Special Notice": "requirement_defined",
    "Combined Synopsis/Solicitation": "rfp_issued",
    "Solicitation": "rfp_issued",
    "Award Notice": "contract_awarded",
    "Justification": "requirement_defined",
    "Intent to Bundle Requirements (DoD-Funded)": "requirement_defined",
}


def extract_contact(raw: dict) -> dict:
    """
    SAM.gov publishes `pointOfContact` as an array — real live shape
    confirmed against GSA's own documented example: a list of
    {fullName, email, phone, title, type} objects, `type` being
    "primary" or "secondary". This was NOT being read at all before —
    SAM.gov is the single largest source in this platform (724
    ingested programmes) and every one of its Tender Briefings was
    showing no procurement contact whatsoever, despite the data
    being right there in every raw record this project already
    fetches. Picks "primary" when marked; otherwise the first entry,
    since a notice with exactly one contact rarely bothers marking
    its type.

    The SECOND entry, when one genuinely exists, is kept too
    (`contact_email_secondary`) — real notices publish a backup
    procurement contact, and showing both is what makes a lead read
    as a real, reachable government office rather than a single
    fragile point of contact. Deliberately only the email, not a full
    second name/phone: `programmes` has one contact_name/contact_phone
    pair by design (see migration 017), and a second person's full
    detail would be a bigger, separate schema question than "give the
    user a second way to reach the same office".
    """
    contacts = raw.get("pointOfContact") or []
    if not contacts:
        return {"contact_name": None, "contact_email": None, "contact_phone": None, "contact_email_secondary": None}
    primary = next((c for c in contacts if (c.get("type") or "").lower() == "primary"), contacts[0])
    secondary = next((c for c in contacts if (c.get("type") or "").lower() == "secondary"), None)
    return {
        "contact_name": (primary.get("fullName") or "").strip() or None,
        "contact_email": (primary.get("email") or "").strip() or None,
        "contact_phone": (primary.get("phone") or "").strip() or None,
        "contact_email_secondary": (secondary.get("email") or "").strip() or None if secondary else None,
    }


def extract_contact_address(raw: dict) -> Optional[str]:
    """
    `officeAddress` — the CONTRACTING OFFICE's address, e.g.
    {"zipcode": "60604", "city": "CHICAGO", "countryCode": "USA",
    "state": "IL"} — real shape confirmed against GSA's documented
    example. Deliberately NOT `placeOfPerformance`, which is a
    different, separate field meaning where the awarded work happens
    (often a different city, sometimes a different country) — the
    office address is where a supplier would actually write to reach
    this contracting office, which is the "valid lead" the address is
    for.
    """
    office = raw.get("officeAddress") or {}
    return format_address(
        locality=office.get("city"),
        region=office.get("state"),
        postal_code=office.get("zipcode"),
        country=office.get("countryCode"),
    )


def extract_award_winner(raw: dict) -> Optional[str]:
    """
    SAM.gov's `award` object, confirmed against BOTH of GSA's own
    documented examples — a real gap this closes: this field has been
    fetched by this project since day one (every raw record passes
    through this function) but never once read, despite SAM.gov being
    the single largest source in this database (769 programmes),
    which meant Competitor/OEM Intelligence had zero data from it.

    Genuinely optional, not just empty-string-vs-null: GSA's own
    "Example 2" shows an `award` object present with date/number/
    amount but its `awardee` key ABSENT ENTIRELY (not null, not
    missing-with-a-default — simply not a key in that dict), which is
    why this uses nested `.get()` throughout rather than assuming the
    sub-object's shape once `award` itself is confirmed present.
    """
    award = raw.get("award")
    if not isinstance(award, dict):
        return None
    awardee = award.get("awardee")
    if not isinstance(awardee, dict):
        return None
    return (awardee.get("name") or "").strip() or None


def normalize_opportunity(raw: dict) -> NormalizedProgramme:
    """
    Maps one SAM.gov opportunitiesData[] record into our schema's
    shape. Uses .get() defensively throughout because SAM.gov's own
    documentation has inconsistent field naming between its request
    parameter table and its actual JSON examples (e.g.
    'responseDeadLine' vs 'reponseDeadLine') — real vendor API
    quirk, not a mistake in this code.
    """
    notice_id_field = raw.get("noticeId")
    notice_id = notice_id_field or raw.get("solicitationNumber")
    if not notice_id:
        raise ValueError("Record has neither noticeId nor solicitationNumber — cannot dedupe safely")

    raw_type = raw.get("type") or raw.get("baseType")
    stage = _STAGE_MAP.get(raw_type, "requirement_defined")

    org_name = raw.get("fullParentPathName") or raw.get("department")

    response_deadline = raw.get("responseDeadLine") or raw.get("reponseDeadLine")

    # Real eligibility signal, straight from the official source —
    # this is exactly the "surface what the government data itself
    # already states" approach: a fact with a source, not an
    # inference. e.g. code="SBA", description="Total Small Business
    # Set-Aside". None/None here honestly means the notice carries
    # no set-aside restriction, not that we failed to check.
    set_aside_code = raw.get("typeOfSetAside")
    set_aside_description = raw.get("typeOfSetAsideDescription")

    return {
        "external_ref": notice_id,
        "name": (raw.get("title") or "Untitled SAM.gov Opportunity").strip(),
        "country": "United States",
        "organization_name": org_name.strip() if org_name else None,
        "stage": stage,
        "naics_code": raw.get("naicsCode"),
        "posted_date": raw.get("postedDate"),
        "response_deadline": response_deadline,
        # SAM.gov's own `uiLink` field is missing on most records —
        # confirmed against live data: only ~10% of ingested notices
        # carried it, with no pattern by stage/type, so this isn't a
        # narrow "RFIs don't get one" case. But every one of the
        # records that DID carry uiLink followed the exact same
        # pattern (verified: 44/44 in a live check), keyed on
        # noticeId — so that pattern is reconstructed here as a
        # fallback rather than left blank, restoring the same "Open
        # Tender / Apply" link Path to Contract already shows for
        # every other source. Deliberately keyed on notice_id_field
        # specifically, not the resolved notice_id which can fall back
        # to solicitationNumber — that fallback ID was never verified
        # to follow this URL pattern, so it's left blank rather than
        # guessed at.
        "ui_link": raw.get("uiLink") or (
            f"https://sam.gov/workspace/contract/opp/{notice_id_field}/view" if notice_id_field else None
        ),
        "raw_type": raw_type,
        "set_aside_code": set_aside_code,
        "set_aside_description": set_aside_description,
        **extract_contact(raw),
        "contact_address": extract_contact_address(raw),
        "winner_name": extract_award_winner(raw),
    }


def normalize_batch(raw_records: list[dict]) -> tuple[list[NormalizedProgramme], list[dict]]:
    """
    Normalizes a batch, collecting failures instead of letting one
    malformed record abort the whole ingestion run. Returns
    (successfully_normalized, failures) where failures carry enough
    context to debug without re-fetching from SAM.gov.
    """
    normalized = []
    failures = []
    for raw in raw_records:
        try:
            normalized.append(normalize_opportunity(raw))
        except (ValueError, AttributeError) as e:
            failures.append({"error": str(e), "notice_id": raw.get("noticeId"), "title": raw.get("title")})
    return normalized, failures
