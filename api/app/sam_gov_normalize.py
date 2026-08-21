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
"""

from typing import Optional, TypedDict

DEFENSE_RELEVANT_NAICS = {
    "336411": "Aircraft Manufacturing",
    "336414": "Guided Missile and Space Vehicle Manufacturing",
    "334511": "Search, Detection, Navigation, Guidance, Aeronautical Systems Manufacturing",
    "541330": "Engineering Services",
    "541712": "R&D in the Physical, Engineering, and Life Sciences (except Biotechnology)",
    "928110": "National Security",
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


def normalize_opportunity(raw: dict) -> NormalizedProgramme:
    """
    Maps one SAM.gov opportunitiesData[] record into our schema's
    shape. Uses .get() defensively throughout because SAM.gov's own
    documentation has inconsistent field naming between its request
    parameter table and its actual JSON examples (e.g.
    'responseDeadLine' vs 'reponseDeadLine') — real vendor API
    quirk, not a mistake in this code.
    """
    notice_id = raw.get("noticeId") or raw.get("solicitationNumber")
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
        "ui_link": raw.get("uiLink"),
        "raw_type": raw_type,
        "set_aside_code": set_aside_code,
        "set_aside_description": set_aside_description,
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
