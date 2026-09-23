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

from app.address_format import format_address


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
    response_deadline: Optional[str]
    ui_link: Optional[str]
    notice_type: Optional[str]
    winners: list[NormalizedWinner]
    contact_email: Optional[str]
    contact_address: Optional[str]
    set_aside_code: Optional[str]
    set_aside_description: Optional[str]


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


def extract_winners(winner_name_field, winner_country_field) -> list[NormalizedWinner]:
    """
    A single award notice can name SEVERAL winners at once (one per
    lot) — confirmed against live data, so this returns a list, not
    a single value like _extract_multilingual does.

    Real TED data has two irregularities this deliberately guards
    against rather than assumes away:
      1. winner-name's list routinely contains the SAME company
         repeated once per lot it won (a live notice with 14 raw
         names had only 11 distinct companies) — deduplicated here,
         preserving first-seen order.
      2. winner-name and winner-country are positionally parallel
         MOST of the time (8 of 9 live notices sampled had exactly
         one country per distinct winner) but not always — one
         sample had 2 distinct names against 3 countries. Zipping
         them regardless would silently mis-attribute a country to
         the wrong company, so countries are only attached when the
         deduplicated name count matches the country count exactly;
         otherwise every winner in that notice gets country=None
         rather than a guess.
    """
    if not winner_name_field:
        return []

    if isinstance(winner_name_field, dict):
        raw_names = None
        for lang_key in ("eng", "ENG", "en", "EN"):
            if lang_key in winner_name_field:
                raw_names = winner_name_field[lang_key]
                break
        if raw_names is None and winner_name_field:
            raw_names = next(iter(winner_name_field.values()))
    else:
        raw_names = winner_name_field

    if raw_names is None:
        return []
    if not isinstance(raw_names, list):
        raw_names = [raw_names]

    seen = []
    for name in raw_names:
        name = str(name).strip()
        if name and name not in seen:
            seen.append(name)
    if not seen:
        return []

    countries = winner_country_field if isinstance(winner_country_field, list) else (
        [winner_country_field] if winner_country_field else []
    )
    countries_align = len(countries) == len(seen)

    return [
        {"name": name, "country": (countries[i] if countries_align else None)}
        for i, name in enumerate(seen)
    ]


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

    deadline = _extract_multilingual(raw.get("deadline-date-lot"), "deadline")
    if not deadline:
        deadline = _extract_multilingual(raw.get("deadline-receipt-request"), "deadline")

    # Only populated for award-stage notices — TED returns
    # winner-name/winner-country keys as absent (not empty) on
    # non-award notices, and extract_winners already returns []
    # for a missing field, so no stage check is needed here to
    # avoid attaching winners to the wrong notice type.
    winners = extract_winners(raw.get("winner-name"), raw.get("winner-country"))

    # Live-verified 2026-09 (see db/migrations' own CLAUDE.md entry):
    # buyer-email/organisation-email-buyer are identical duplicates on
    # every sampled notice, so only the shorter field name is
    # requested. buyer-post-code and organisation-street-buyer come
    # back as PLAIN LISTS on real notices, not the multilingual dict
    # shape most other TED fields use — _extract_multilingual already
    # handles both shapes, so no separate parsing path is needed.
    contact_email = _extract_multilingual(raw.get("buyer-email"), "buyer-email")
    street = _extract_multilingual(raw.get("organisation-street-buyer"), "street")
    locality = _extract_multilingual(raw.get("buyer-city"), "city")
    postal_code = _extract_multilingual(raw.get("buyer-post-code"), "post-code")
    # A bare country name on its own ("DEU") isn't an address anyone
    # could actually write to — only build one when at least one real
    # locality-level part is present; country alone is left out
    # rather than shown as a sparse, low-value single-word "address".
    contact_address = (
        format_address(street=street, locality=locality, postal_code=postal_code, country=country)
        if (street or locality or postal_code) else None
    )

    # Eligibility/Backup Phase 1 (2026-09) — the first non-SAM.gov
    # source confirmed to publish a real, structured bidder-
    # restriction signal. Live-verified against 10 real defence-
    # relevant notices before wiring this in: `sme-lot` is a plain
    # boolean per lot (true on 2 of 10 sampled), `reserved-procurement
    # -lot` is "none" on every sampled notice when unrestricted —
    # never seen a real non-"none" value live, so that case is
    # handled generically (the raw value shown as-is) rather than
    # translating a category this project hasn't actually observed
    # and can't verify the wording of. sme-lot checked first since a
    # real notice can carry both, and "reserved for SMEs" is the
    # clearer, more specific fact to surface.
    sme_lot = raw.get("sme-lot")
    if isinstance(sme_lot, list) and any(v is True for v in sme_lot):
        set_aside_code, set_aside_description = "SME", "Reserved for small/medium enterprises (SME)"
    else:
        reserved = raw.get("reserved-procurement-lot")
        reserved_values = reserved if isinstance(reserved, list) else ([reserved] if reserved else [])
        non_none = next((v for v in reserved_values if v and v != "none"), None)
        set_aside_code, set_aside_description = (non_none, f"Reserved procurement: {non_none}") if non_none else (None, None)

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
        "winners": winners,
        "contact_email": contact_email,
        "contact_address": contact_address,
        "set_aside_code": set_aside_code,
        "set_aside_description": set_aside_description,
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
