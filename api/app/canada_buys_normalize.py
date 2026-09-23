"""
Pure normalization logic for CanadaBuys (Government of Canada).
Zero I/O, same separation pattern as every other normalizer here.

WHY THIS SOURCE IS DIFFERENT FROM THE INDIA ONE, and better: CPPP
publishes no classification code at all, so Indian programmes carry a
sentinel and can only ever be matched on keywords. CanadaBuys
publishes **UNSPSC** codes on ~84% of open tenders — and UNSPSC is an
international standard, not a national scheme, so those codes feed
real capability matching through `taxonomy_unspsc_mapping` exactly as
NAICS does for SAM.gov and CPV does for the UK/EU sources.

RELEVANCE IS DECIDED BY THE BUYER, NOT THE CODE — verified against
live data rather than assumed. Real defence tenders in the open feed
carry UNSPSC codes spread across many segments (25 vehicles/aircraft,
31 components, 39 electrical, 40 distribution, 41 measuring, 92
defence services), so filtering on UNSPSC segment alone would miss
most of them. The reliable signals are the end-user/contracting
entity naming a defence body, and the Public Works `W`-prefixed
solicitation number that denotes a Department of National Defence
requirement. The UNSPSC code is then kept for MATCHING, which is the
division of labour that source has and India's does not.

Field layout comes from the live open-tenders CSV
(canadabuys.canada.ca/opendata/pub/openTenderNotice-ouvertAvisAppelOffres.csv)
and its published data dictionary, not from guesswork.
"""

import re
from datetime import datetime
from typing import Optional, TypedDict

from app.address_format import format_address
from app.works_filter import is_facilities_works, is_uninformative_title

# Column names are bilingual and verbose in this feed
# ('title-titre-eng'), so they are named once here rather than being
# repeated as string literals throughout.
COL_TITLE = "title-titre-eng"
COL_DESCRIPTION = "tenderDescription-descriptionAppelOffres-eng"
COL_REFERENCE = "referenceNumber-numeroReference"
COL_SOLICITATION = "solicitationNumber-numeroSollicitation"
COL_PUBLISHED = "publicationDate-datePublication"
COL_CLOSING = "tenderClosingDate-appelOffresDateCloture"
COL_STATUS = "tenderStatus-appelOffresStatut-eng"
COL_NOTICE_TYPE = "noticeType-avisType-eng"
COL_UNSPSC = "unspsc"
COL_UNSPSC_DESC = "unspscDescription-eng"
COL_GSIN = "gsin-nibs"
COL_CONTRACTING_ENTITY = "contractingEntityName-nomEntitContractante-eng"
COL_END_USER = "endUserEntitiesName-nomEntitesUtilisateurFinal-eng"
COL_URL = "noticeURL-URLavis-eng"
COL_CONTACT_NAME = "contactInfoName-informationsContactNom"
COL_CONTACT_EMAIL = "contactInfoEmail-informationsContactCourriel"
COL_CONTACT_PHONE = "contactInfoPhone-contactInfoTelephone"
# Eligibility/Backup Phase 1 (2026-09) — the real, documented Canadian
# legal term for a non-open-competition tender. Live-verified against
# a real 905-row pull: 307 rows (34%) carry a value, with exactly 3
# distinct real values seen — "None" (open competition, not
# restricted) and two genuinely-restricted cases: "Exclusive Rights"
# (only one specific supplier is eligible) and "No response to bid
# solicitation" (open competition already failed to attract bids, so
# the buyer is now contacting specific known suppliers directly —
# also genuinely not open to a general bidder). Any value other than
# "None" is therefore surfaced as a stated restriction, uniformly.
COL_LIMITED_TENDERING_REASON = "limitedTenderingReason-raisonAppelOffresLimite-eng"

# --- Award notices CSV only (different file, see
# app/canada_buys_ingestion.run_canada_buys_ingestion's award phase).
# Confirmed live (2026-09-16) to share the EXACT same column names as
# the open-tenders CSV for title/solicitation/end-user/contracting-
# entity/UNSPSC/contact-name-email-phone — is_defence_buyer and
# parse_unspsc below are reused unchanged against award rows. Only
# genuinely new columns get their own constants.
COL_AWARD_STATUS = "awardStatus-attributionStatut-eng"
COL_AWARD_DATE = "contractAwardDate-dateAttributionContrat"
COL_SUPPLIER_NAME = "supplierLegalName-nomLegalFournisseur-eng"
COL_SUPPLIER_COUNTRY = "supplierAddressCountry-fournisseurAdressePays-eng"
COL_CONTACT_ADDRESS_LINE = "contactInfoAddressLine-contactInfoAdresseLigne-eng"
COL_CONTACT_CITY = "contactInfoCity-contacterInfoVille-eng"
COL_CONTACT_PROVINCE = "contactInfoProvince-contacterInfoProvince-eng"
COL_CONTACT_POSTAL = "contactInfoPostalcode"
COL_CONTACT_COUNTRY = "contactInfoCountry-contactInfoPays-eng"
COL_AWARD_DESCRIPTION = "awardDescription-descriptionAttribution-eng"

# An award record with this status was called off — real history,
# never a live winner. Confirmed live: 8 of 3,771 rows in the current
# fiscal year's file. Same treatment as every other source's
# cancelled/unsuccessful statuses: skipped, not counted as a failure.
AWARD_STATUS_CANCELLED = "Cancelled"

# Defence buyers as they actually appear in the feed. "Coast Guard" is
# included deliberately: the CCG is a civilian fleet, but its vessel
# systems, sonar and marine electronics procurement is the same
# supplier base as naval work, and it publishes 27 open tenders here.
DEFENCE_BUYER_TERMS = (
    "NATIONAL DEFENCE",
    "DEPARTMENT OF NATIONAL DEFENCE",
    "CANADIAN ARMED FORCES",
    "ROYAL CANADIAN NAVY",
    "ROYAL CANADIAN AIR FORCE",
    "CANADIAN COAST GUARD",
    "DEFENCE RESEARCH",
    "COMMUNICATIONS SECURITY ESTABLISHMENT",
)

# Public Works solicitation numbers beginning 'W' followed by a digit
# denote a Department of National Defence requirement — e.g.
# W6399-27-TR21, W0113-27CS25. Verified against live rows whose
# end-user entity is explicitly DND. Anchored and digit-qualified so
# it cannot match an ordinary word starting with W.
_DND_SOLICITATION_RE = re.compile(r"^W\d", re.IGNORECASE)


class NormalizedProgramme(TypedDict):
    external_ref: str
    name: str
    country: str
    organization_name: Optional[str]
    stage: Optional[str]
    classification_code: Optional[str]
    classification_scheme: Optional[str]
    all_classification_codes: list[str]
    posted_date: Optional[str]
    response_deadline: Optional[str]
    ui_link: Optional[str]
    contact_name: Optional[str]
    contact_email: Optional[str]
    contact_phone: Optional[str]
    set_aside_code: Optional[str]
    set_aside_description: Optional[str]


def parse_unspsc(value: Optional[str]) -> list[str]:
    """
    The feed encodes UNSPSC as asterisk-prefixed, newline-separated
    values — a single code renders as '*10100000' and multiple as
    '*10191500\\n*77121608'. 275 of 824 coded rows in a live pull were
    multi-valued, so treating this field as a plain scalar would
    silently discard most of a tender's classification.
    """
    if not value:
        return []
    codes = []
    for part in re.split(r"[\r\n]+", value):
        code = part.strip().lstrip("*").strip()
        if code and code.isdigit():
            codes.append(code)
    return codes


def parse_date(value: Optional[str]) -> Optional[str]:
    """
    Dates arrive as ISO-ish strings ('2026-08-28T23:59:00' or
    '2026-08-21'). Normalized to ISO 8601 where parseable, returned
    unchanged otherwise rather than dropping a real deadline over an
    unexpected format.
    """
    if not value:
        return None
    value = value.strip()
    if not value:
        return None
    for fmt in ("%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
        try:
            return datetime.strptime(value, fmt).isoformat()
        except ValueError:
            continue
    return value


def is_defence_buyer(row: dict) -> bool:
    """
    Pure relevance check — the Canadian analogue of
    uk_ft_normalize.is_defense_relevant_cpv, keyed on the buyer rather
    than the category code (see module docstring for why the code is
    the wrong signal here).
    """
    buyer_text = " ".join(
        str(row.get(col) or "") for col in (COL_END_USER, COL_CONTRACTING_ENTITY)
    ).upper()
    if any(term in buyer_text for term in DEFENCE_BUYER_TERMS):
        return True
    return bool(_DND_SOLICITATION_RE.match(str(row.get(COL_SOLICITATION) or "").strip()))


# CanadaBuys notice types mapped onto our programmes.stage constraint.
# Same defensive-fallback philosophy as the other sources: an
# unrecognised type falls back honestly rather than crashing a run.
_STAGE_MAP = {
    "REQUEST FOR INFORMATION": "requirement_defined",
    "LETTER OF INTEREST": "requirement_defined",
    "REQUEST FOR PROPOSAL": "rfp_issued",
    "INVITATION TO TENDER": "rfp_issued",
    "REQUEST FOR STANDING OFFERS": "rfp_issued",
    "REQUEST FOR SUPPLY ARRANGEMENTS": "rfp_issued",
    "NOTICE OF PROPOSED PROCUREMENT": "rfp_issued",
    "ADVANCE CONTRACT AWARD NOTICE": "contract_awarded",
}


def _stage_for(notice_type: Optional[str]) -> str:
    text = (notice_type or "").strip().upper()
    for key, stage in _STAGE_MAP.items():
        if key in text:
            return stage
    return "requirement_defined"


# CanadaBuys' own CSV leaves noticeURL empty on a majority of rows —
# 481 of 901 in a live pull carried neither the English nor the French
# URL, and the ones that DO carry it point at MERX or Ariba (where the
# tender is hosted), never at CanadaBuys itself. But the CanadaBuys
# portal does have a page per notice, and its URL slug is simply the
# reference number lowercased — confirmed by pulling the portal's own
# rendered listing links and matching them against stored references:
# slugs like `ws5801048823-doc5822430726` and `cb-957-33733663` are
# character-for-character our `external_ref` values, including for
# records whose CSV row had no URL at all.
#
# ONLY the two reference shapes that check confirmed are reconstructed.
# A third shape exists in the feed (`PW-_KIN-519-8707` and similar) and
# is deliberately left without a link: a direct fetch of its
# constructed slug returned a genuine 404, so its slug is evidently
# built some other way, and a plausible-looking dead link is worse
# than an honest blank.
_CB_PORTAL_REF_SHAPES = (
    re.compile(r"^cb-\d+-\d+$", re.IGNORECASE),
    re.compile(r"^ws\d+-doc\d+$", re.IGNORECASE),
)
CANADA_BUYS_PORTAL_BASE = "https://canadabuys.canada.ca/en/tender-opportunities/tender-notice"


def canadabuys_portal_url(external_ref: str) -> Optional[str]:
    """Portal URL for a reference whose slug shape is verified — else None."""
    ref = (external_ref or "").strip()
    if not ref or not any(shape.match(ref) for shape in _CB_PORTAL_REF_SHAPES):
        return None
    return f"{CANADA_BUYS_PORTAL_BASE}/{ref.lower()}"


class NormalizedAward(TypedDict):
    external_ref: str
    name: str
    country: str
    organization_name: Optional[str]
    stage: str
    classification_code: Optional[str]
    classification_scheme: Optional[str]
    posted_date: Optional[str]
    ui_link: Optional[str]
    contact_name: Optional[str]
    contact_email: Optional[str]
    contact_phone: Optional[str]
    contact_address: Optional[str]
    winner_name: Optional[str]
    winner_country: Optional[str]


def normalize_award_row(raw: dict) -> NormalizedAward:
    """
    Maps one row of the AWARD NOTICES CSV — a different file from the
    open-tenders one `normalize_row` handles, published per fiscal
    year (see app/canada_buys_ingestion.py for the URL and why the
    current-FY file, not the multi-year "Complete" one, is fetched).
    Every award in this file is, by definition, already decided, so
    `stage` is always 'contract_awarded' — there is no mapping table
    the way normalize_row has one for open notices.
    """
    external_ref = str(raw.get(COL_REFERENCE) or "").strip()
    if not external_ref:
        external_ref = str(raw.get(COL_SOLICITATION) or "").strip()
    if not external_ref:
        raise ValueError("Award row has neither referenceNumber nor solicitationNumber — cannot dedupe safely")

    title = str(raw.get(COL_TITLE) or "").strip()
    if not title:
        title = str(raw.get(COL_AWARD_DESCRIPTION) or "").strip() or f"CanadaBuys Award {external_ref}"

    codes = parse_unspsc(raw.get(COL_UNSPSC))
    organisation = (
        str(raw.get(COL_END_USER) or "").strip()
        or str(raw.get(COL_CONTRACTING_ENTITY) or "").strip()
        or None
    )
    winner = str(raw.get(COL_SUPPLIER_NAME) or "").strip() or None

    return {
        "external_ref": external_ref,
        "name": title,
        "country": "Canada",
        "organization_name": organisation,
        "stage": "contract_awarded",
        "classification_code": codes[0] if codes else None,
        "classification_scheme": "UNSPSC" if codes else None,
        "posted_date": raw.get(COL_AWARD_DATE) or None,
        # This file publishes no notice URL of its own (confirmed live
        # — no such column exists here) but its reference numbers
        # follow the same verified "cb-###-########" shape the
        # open-tenders portal-URL fallback already checks, so the
        # exact same reconstruction is reused rather than duplicated.
        "ui_link": canadabuys_portal_url(external_ref),
        "contact_name": str(raw.get(COL_CONTACT_NAME) or "").strip() or None,
        "contact_email": str(raw.get(COL_CONTACT_EMAIL) or "").strip() or None,
        "contact_phone": str(raw.get(COL_CONTACT_PHONE) or "").strip() or None,
        "contact_address": format_address(
            street=raw.get(COL_CONTACT_ADDRESS_LINE), locality=raw.get(COL_CONTACT_CITY),
            region=raw.get(COL_CONTACT_PROVINCE), postal_code=raw.get(COL_CONTACT_POSTAL),
            country=raw.get(COL_CONTACT_COUNTRY),
        ),
        "winner_name": winner,
        "winner_country": str(raw.get(COL_SUPPLIER_COUNTRY) or "").strip() or None,
    }


def normalize_award_batch(raw_rows: list[dict]) -> tuple[list[NormalizedAward], list[dict]]:
    """
    Same relevance test as normalize_batch (buyer + facilities-works
    filter) plus one that has no open-tenders equivalent: a cancelled
    award is real history but never a real winner, so it is skipped
    here rather than surfaced as either an opportunity or a competitor
    data point.
    """
    normalized = []
    failures = []
    for raw in raw_rows:
        if str(raw.get(COL_AWARD_STATUS) or "").strip() == AWARD_STATUS_CANCELLED:
            continue
        try:
            record = normalize_award_row(raw)
        except (ValueError, AttributeError, TypeError) as e:
            failures.append({"error": str(e), "reference": raw.get(COL_REFERENCE)})
            continue
        if not is_defence_buyer(raw):
            continue
        if is_facilities_works(record["name"]) or is_uninformative_title(record["name"]):
            continue
        normalized.append(record)
    return normalized, failures


def normalize_row(raw: dict) -> NormalizedProgramme:
    """Maps one CSV row into our schema's shape."""
    external_ref = str(raw.get(COL_REFERENCE) or "").strip()
    if not external_ref:
        # Fall back to the solicitation number before giving up — the
        # reference number is normally present, but dedupe safety
        # matters more than assuming it always is.
        external_ref = str(raw.get(COL_SOLICITATION) or "").strip()
    if not external_ref:
        raise ValueError("Row has neither referenceNumber nor solicitationNumber — cannot dedupe safely")

    title = str(raw.get(COL_TITLE) or "").strip() or f"CanadaBuys Notice {external_ref}"

    codes = parse_unspsc(raw.get(COL_UNSPSC))
    organisation = (
        str(raw.get(COL_END_USER) or "").strip()
        or str(raw.get(COL_CONTRACTING_ENTITY) or "").strip()
        or None
    )

    tendering_reason = str(raw.get(COL_LIMITED_TENDERING_REASON) or "").strip()
    if tendering_reason and tendering_reason != "None":
        set_aside_code, set_aside_description = tendering_reason, f"Limited tendering — {tendering_reason} (not open to all bidders)"
    else:
        set_aside_code, set_aside_description = None, None

    return {
        "external_ref": external_ref,
        "name": title,
        "country": "Canada",
        "organization_name": organisation,
        "stage": _stage_for(raw.get(COL_NOTICE_TYPE)),
        # programmes.naics_code holds one code, but a tender may carry
        # several — the first is stored for matching and the full set
        # is preserved on the record so the ingestion layer can record
        # all of them in the evidence claim rather than losing them.
        "classification_code": codes[0] if codes else None,
        "classification_scheme": "UNSPSC" if codes else None,
        "all_classification_codes": codes,
        "posted_date": parse_date(raw.get(COL_PUBLISHED)),
        "response_deadline": parse_date(raw.get(COL_CLOSING)),
        "ui_link": str(raw.get(COL_URL) or "").strip() or canadabuys_portal_url(external_ref),
        # Procurement contact for THIS tender — see
        # db/migrations/017 for the deliberate scope limit on how
        # this personal data may and may not be used.
        "contact_name": str(raw.get(COL_CONTACT_NAME) or "").strip() or None,
        "contact_email": str(raw.get(COL_CONTACT_EMAIL) or "").strip() or None,
        "contact_phone": str(raw.get(COL_CONTACT_PHONE) or "").strip() or None,
        "set_aside_code": set_aside_code,
        "set_aside_description": set_aside_description,
    }


def normalize_batch(raw_rows: list[dict]) -> tuple[list[NormalizedProgramme], list[dict]]:
    """
    Normalizes, then keeps only defence-buyer tenders that are actual
    capability procurement. Rows dropped by either filter are not
    failures — they parsed fine, they're simply not what this platform
    covers.

    The estate-works filter matters here as much as it does for India:
    DND's open feed is full of "Open Construction Source List for CFB
    Halifax" entries, which are base construction, not materiel.
    """
    normalized = []
    failures = []
    for raw in raw_rows:
        try:
            record = normalize_row(raw)
        except (ValueError, AttributeError, TypeError) as e:
            failures.append({"error": str(e), "reference": raw.get(COL_REFERENCE)})
            continue
        if not is_defence_buyer(raw):
            continue
        if is_facilities_works(record["name"]) or is_uninformative_title(record["name"]):
            continue
        normalized.append(record)
    return normalized, failures
