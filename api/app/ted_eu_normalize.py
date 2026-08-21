"""
Pure normalization logic for EU TED (Tenders Electronic Daily).
Zero I/O, same separation pattern as the SAM.gov and UK normalizers.

KEY DIFFERENCE FROM UK FIND A TENDER: TED's Search API v3 DOES
support server-side CPV filtering (classification-cpv=35000000),
unlike UK Find a Tender which has no category filter at all. This
means we filter defense-relevant notices at the API level — much
more efficient, no need for client-side filtering like the UK source.

TED response format is distinctive: field values are multilingual
objects like {"eng": ["Some title"], "fra": ["Un titre"]} — this
normalizer extracts English preferentially, falling back to any
available language.

Covers all 27 EU member states plus EEA countries in one single
API — the highest-value single source addition in this project.
"""

from typing import Optional, TypedDict


# Defense-relevant CPV prefixes — same verified set used by UK
# Find a Tender, reused here for consistency. TED's server-side
# filter handles the broad "35" division; these are for the
# specific 50-series repair/maintenance codes queried separately.
DEFENSE_CPV_QUERIES = [
    "35",       # Entire division: security, fire-fighting, police and defence equipment
    "5063",     # Repair/maintenance of military vehicles
    "5064",     # Repair/maintenance of warships
    "5065",     # Repair/maintenance of military aircraft
    "5066",     # Repair/maintenance of military electronic systems
]


# TED notice-type values mapped to our programmes.stage constraint.
# These are eForms notice subtypes, not the same as SAM.gov's or
# UK Find a Tender's stage vocabulary.
_STAGE_MAP = {
    "pin-only": "early_concept",              # Prior information notice
    "pin-cfc-standard": "early_concept",      # PIN used as call for competition
    "pin-cfc-social": "early_concept",
    "cn-standard": "rfp_issued",              # Contract notice (standard)
    "cn-social": "rfp_issued",                # Contract notice (social/special)
    "cn-desg": "rfp_issued",                  # Design contest
    "can-standard": "contract_awarded",       # Contract award notice
    "can-social": "contract_awarded",
    "can-desg": "contract_awarded",
    "veat": "contract_awarded",               # Voluntary ex-ante transparency
    "subco": "rfp_issued",                    # Subcontracting notice
}


class NormalizedProgramme(TypedDict):
    external_ref: str
    name: str
    country: str
    organization_name: Optional[str]
    stage: Optional[str]
    classification_code: Optional[str]
    classification_scheme: Optional[str]
    posted_date: Optional[str]
    response_deadline: Optional[str]
    ui_link: Optional[str]
    notice_type: Optional[str]


def _extract_multilingual(obj, field_name: str) -> Optional[str]:
    """
    TED returns multilingual objects like {"eng": ["value"], "fra":
    ["valeur"]}. Extract English preferentially, fall back to any
    available language, return None if empty/missing.
    """
    if obj is None:
        return None
    val = obj if isinstance(obj, dict) else {}
    # Try English first
    for lang_key in ("eng", "ENG", "en", "EN"):
        if lang_key in val:
            items = val[lang_key]
            if isinstance(items, list) and items:
                return str(items[0]).strip()
            elif isinstance(items, str):
                return items.strip()
    # Fall back to any language
    for lang_key, items in val.items():
        if isinstance(items, list) and items:
            return str(items[0]).strip()
        elif isinstance(items, str) and items.strip():
            return items.strip()
    # Direct string value (some fields aren't multilingual)
    if isinstance(obj, str):
        return obj.strip() if obj.strip() else None
    if isinstance(obj, list) and obj:
        return str(obj[0]).strip()
    return None


def normalize_ted_notice(raw: dict) -> NormalizedProgramme:
    """
    Maps one TED Search API v3 result into our schema's shape.
    Field names use eForms kebab-case (e.g. publication-number,
    notice-title, buyer-name) as documented in the API.
    """
    pub_number = raw.get("publication-number")
    if not pub_number:
        raise ValueError("Notice has no publication-number — cannot dedupe safely")

    title = _extract_multilingual(raw.get("notice-title"), "notice-title")
    if not title:
        title = f"TED Notice {pub_number}"

    notice_type = raw.get("notice-type")
    if isinstance(notice_type, list):
        notice_type = notice_type[0] if notice_type else None
    stage = _STAGE_MAP.get(notice_type, "requirement_defined")

    buyer_name = _extract_multilingual(raw.get("buyer-name"), "buyer-name")

    buyer_country = raw.get("buyer-country")
    if isinstance(buyer_country, list):
        buyer_country = buyer_country[0] if buyer_country else None
    # TED uses 3-letter ISO codes (DEU, FRA, etc.) — store as-is,
    # consistent with how we store "United States" and "United
    # Kingdom" for the other sources (country display name resolution
    # can be done at the frontend level if needed later).
    country = buyer_country or "EU"

    cpv = raw.get("classification-cpv")
    if isinstance(cpv, list):
        cpv = cpv[0] if cpv else None

    pub_date = raw.get("publication-date")
    if isinstance(pub_date, list):
        pub_date = pub_date[0] if pub_date else None

    deadline = _extract_multilingual(raw.get("deadline-receipt-tenders"), "deadline")
    if not deadline:
        deadline = _extract_multilingual(raw.get("deadline-receipt-request"), "deadline")

    return {
        "external_ref": pub_number,
        "name": title,
        "country": country,
        "organization_name": buyer_name.strip() if buyer_name else None,
        "stage": stage,
        "classification_code": cpv,
        "classification_scheme": "CPV",
        "posted_date": pub_date,
        "response_deadline": str(deadline) if deadline else None,
        "ui_link": f"https://ted.europa.eu/en/notice/-/detail/{pub_number}",
        "notice_type": notice_type,
    }


def normalize_batch(raw_notices: list[dict]) -> tuple[list[NormalizedProgramme], list[dict]]:
    """
    Unlike the UK normalizer, no client-side CPV filtering is needed
    here — TED's API already filtered server-side. Every record that
    parses successfully IS defense-relevant by construction.
    """
    normalized = []
    failures = []
    for raw in raw_notices:
        try:
            record = normalize_ted_notice(raw)
            normalized.append(record)
        except (ValueError, AttributeError, TypeError) as e:
            failures.append({
                "error": str(e),
                "publication_number": raw.get("publication-number"),
            })
    return normalized, failures
