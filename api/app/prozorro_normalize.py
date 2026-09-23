"""
Pure normalization logic for ProZorro (Ukraine's public procurement
system, api.openprocurement.org). Zero I/O, same separation pattern
as every other normalizer here.

REAL ARCHITECTURAL DIFFERENCE FROM EVERY OTHER SOURCE: ProZorro's
public sync API is a raw CHANGES FEED, not a queryable dataset. It
supports no server-side filter of any kind — verified live by sending
`classification_id=35000000` and `opt_fields=classification` and
finding both silently ignored (HTTP 200, but the returned rows were
identical with or without them). The only way to narrow it is to walk
the feed and filter client-side, which is exactly what
app/prozorro_ingestion.py does in two phases: a cheap LIST scan
(id/procuringEntity/status/tenderID only — the only fields opt_fields
actually honours on this endpoint) to find candidates, then one
DETAIL fetch per surviving candidate to get its title, item
classification, value and award data — none of which the list
endpoint will return no matter what opt_fields is asked for.

WHY BUYER FILTERING ALONE IS NOT ENOUGH HERE, verified against a live
3,000-row sample (2026-09-16): 681 of 3,000 recent releases (23%) came
from a defence-institution buyer — a far higher hit rate than any
other source in this platform — but almost all of it was Ukrainian
military units buying underwear, potatoes, printer paper and office
laptops for their own internal use, not materiel. "Military buyer"
here answers "does the army run this office", not "is this a defence
capability requirement" — the same gap Colombia's SECOP II has, for
the same underlying reason: front-line units place their own routine
purchasing through the same public system used for actual armament
and equipment procurement.

So relevance is a conjunction of THREE checks, exactly Colombia's
shape:
  defence buyer  AND  not a military health/welfare unit  AND
  the item's own CPV code is genuinely materiel-relevant
The CPV test reuses `uk_ft_normalize.is_defense_relevant_cpv` rather
than a second, drifting copy of the same list — Ukraine's ДК021
classification scheme IS the EU's CPV, verified live: code
"18310000-5" printed by ProZorro as "Спідня білизна" is CPV
18310000's real EU label ("Underwear"), and "34130000-7" as
"Мототранспортні вантажні засоби" matches CPV 34130000 ("Motor
lorries") — same numbers, same hierarchy, only the description is
translated.

WHY `is_uninformative_title`/`is_facilities_works` FROM
app/works_filter.py ARE DELIBERATELY NOT USED HERE: both are
ASCII-only, English-keyword regexes.
`is_uninformative_title` in particular tests for
`[A-Za-z]{4,}\\s+[A-Za-z]{3,}` — a title written entirely in Cyrillic
matches ZERO Latin letters, so that regex finds nothing and the
function returns True ("uninformative — drop it") for every single
genuine Ukrainian title. Calling it here would have silently deleted
this entire source's data on day one; caught before it shipped, not
after. `is_facilities_works` is merely a silent no-op on Cyrillic text
(its keyword list is also English-only) rather than actively harmful,
but calling a filter that can provably never fire is misleading dead
code, so it is skipped too, honestly, rather than left in for
appearances.
"""

import unicodedata
from typing import Optional, TypedDict

from app.address_format import format_address
from app.uk_ft_normalize import is_defense_relevant_cpv

# Institutions verified in a live sample to place real defence-relevant
# procurement. "національної гвардії" (National Guard) is included on
# the same dual-use reasoning CanadaBuys already applies to the
# Canadian Coast Guard: formally a separate service, but it operates
# armoured vehicles, artillery and combat units and buys the same
# materiel base a defence supplier would sell into — confirmed live,
# it is in fact the single largest defence-buyer group in this feed.
# "військова частина" (military unit) is the dominant pattern by
# volume: individual army units place their own procurement directly,
# and the phrase appears literally in every such buyer's name
# regardless of that unit's numeric/alphanumeric code, so a single
# substring match covers all of them without enumerating unit IDs.
DEFENCE_BUYER_PHRASES = (
    "оборони україни",           # "... of Defence of Ukraine" (Ministry of Defence and its bodies)
    "оборонних закупівель",      # Defence Procurement Agency
    "збройних сил україни",      # Armed Forces of Ukraine
    "військова частина",         # military unit — the dominant real pattern
    "національної гвардії",      # National Guard
    "державної прикордонної служби",  # State Border Guard Service
    "сил територіальної оборони",     # Territorial Defence Forces
    "головне управління розвідки",    # Main Intelligence Directorate
    "ради національної безпеки і оборони",  # National Security and Defence Council
)

# Military health, welfare and dental units. Genuinely under a defence
# institution, genuinely not capability procurement — same judgement
# already applied to Colombia's SANIDAD/HOSPITAL/DISPENSARIO units and
# India's MES estate works. Found live: "ВІЙСЬКОВИЙ ГОСПІТАЛЬ
# НАЦІОНАЛЬНОЇ ГВАРДІЇ" (Military Hospital of the National Guard) was
# the single largest defence buyer by volume in the sample.
SUPPORT_UNIT_PHRASES = (
    "госпіталь",       # hospital
    "поліклініка",     # polyclinic
    "медичн",          # medical (stem — matches медичний/медична/медичного etc.)
    "стоматолог",      # dental
    "санаторій",       # sanatorium
    "аптек",           # pharmacy
)


def _fold(value: Optional[str]) -> str:
    """Lower-cases for comparison. No accent-stripping needed — Cyrillic case-folding is what matters here, not Latin diacritics."""
    return (value or "").lower()


def is_defence_buyer(entity_name: Optional[str]) -> bool:
    folded = _fold(entity_name)
    if not folded:
        return False
    return any(phrase in folded for phrase in DEFENCE_BUYER_PHRASES)


def is_support_unit(entity_name: Optional[str]) -> bool:
    folded = _fold(entity_name)
    return any(phrase in folded for phrase in SUPPORT_UNIT_PHRASES)


# ProZorro's own tender status codelist, mapped onto our
# programmes.stage constraint. "active.awarded" and "complete" both
# mean a winner has been decided — complete additionally means the
# contract itself exists — but this platform's stage enum has no
# state between "an award decision was made" and "the contract is
# live", so both map to the same bucket every other source uses for
# an award notice.
_STAGE_MAP = {
    "draft": "early_concept",
    "active.enquiries": "rfp_issued",
    "active.tendering": "rfp_issued",
    "active.auction": "rfp_issued",
    "active.qualification": "rfp_issued",
    "active.awarded": "contract_awarded",
    "complete": "contract_awarded",
    # The bare, un-dotted "active" status — genuinely missing here
    # until a live trigger run surfaced it as an "unmapped status"
    # failure. Confirmed live (2026-09-16): it appears ONLY on
    # procurementMethodType "reporting" and "negotiation"/
    # "negotiation.quick" — direct/sole-source procurement and
    # after-the-fact disclosure of a purchase already made, never on
    # a competitive method (tendering/auction/qualification). Mapped
    # to requirement_defined rather than rfp_issued: unlike the
    # dotted "active.*" states, there is no open competitive step a
    # new supplier could respond to here, so calling it "RFP issued"
    # would overstate it. Also not mapped to contract_awarded — a
    # "reporting" record documents that a purchase happened, not that
    # this platform has verified award/winner data for it the way
    # active.awarded/complete genuinely do.
    "active": "requirement_defined",
}

# Statuses meaning nobody can bid on or win this any more. Same
# treatment as South Africa's cancelled/withdrawn OCDS statuses and
# Colombia's Cancelado/Desierto: skipped as "not an opportunity",
# never counted as a parse failure.
NON_INGESTABLE_STATUSES = ("unsuccessful", "cancelled")


class NotAnOpportunity(ValueError):
    """Understood perfectly, just not something to ingest — see NON_INGESTABLE_STATUSES."""


class NormalizedWinner(TypedDict):
    name: str
    country: Optional[str]


class NormalizedProgramme(TypedDict):
    external_ref: str
    name: str
    country: str
    organization_name: Optional[str]
    stage: str
    classification_code: Optional[str]
    classification_scheme: Optional[str]
    posted_date: Optional[str]
    response_deadline: Optional[str]
    ui_link: str
    contact_name: Optional[str]
    contact_email: Optional[str]
    contact_phone: Optional[str]
    contact_address: Optional[str]
    winners: list[NormalizedWinner]


def extract_winners(raw: dict) -> list[NormalizedWinner]:
    """
    `awards[]` with `status == 'active'` names the real winner(s) —
    confirmed live: a tender can carry a cancelled/unsuccessful award
    alongside the active one (a re-run after the first award fell
    through), so status is checked rather than assuming the last
    entry is the live one.
    """
    winners = []
    for award in raw.get("awards") or []:
        if award.get("status") != "active":
            continue
        for supplier in award.get("suppliers") or []:
            name = (supplier.get("name") or "").strip()
            if not name:
                continue
            country = ((supplier.get("address") or {}).get("countryName") or "").strip() or None
            winners.append({"name": name, "country": country})
    return winners


def extract_contact(raw: dict) -> dict:
    """
    `procuringEntity.contactPoint` — confirmed live as a REQUIRED,
    consistently-populated field on this feed (name/email/telephone),
    unlike UK Find a Tender's buyer contactPoint, which is present on
    only some notices. `procuringEntity.address` uses the same OCDS
    Address shape (streetAddress/locality/region/postalCode/
    countryName) already relied on elsewhere in this project.
    """
    entity = raw.get("procuringEntity") or {}
    cp = entity.get("contactPoint") or {}
    address = entity.get("address") or {}
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


def normalize_tender_detail(raw: dict) -> NormalizedProgramme:
    """
    Maps one FULL tender detail record (from GET /tenders/{id}) into
    our schema's shape — NOT the lightweight list-endpoint row, which
    carries none of title/items/value. See module docstring for why
    this source needs the two-phase fetch at all.
    """
    tender_id = str(raw.get("tenderID") or raw.get("id") or "").strip()
    if not tender_id:
        raise ValueError("Record has neither tenderID nor id — cannot dedupe safely")

    status = (raw.get("status") or "").strip()
    stage = _STAGE_MAP.get(status)
    if stage is None:
        if status in NON_INGESTABLE_STATUSES:
            raise NotAnOpportunity(f"Tender status {status!r} — not open to bid on")
        raise ValueError(f"Unmapped ProZorro tender status: {status!r}")

    title = (raw.get("title") or "").strip()
    items = raw.get("items") or []
    classification = (items[0].get("classification") or {}) if items else {}
    code = (classification.get("id") or "").strip() or None
    # ДК021 IS CPV (see module docstring) — normalized code is the
    # bare digits, dropping the check-digit suffix ProZorro prints
    # ("35613000-4" -> "35613000") to match the 8-digit form every
    # other CPV-coded source and taxonomy_cpv_mapping already use.
    if code and "-" in code:
        code = code.split("-", 1)[0]
    if not title:
        title = classification.get("description") or f"ProZorro Notice {tender_id}"

    entity = raw.get("procuringEntity") or {}
    tender_period = raw.get("tenderPeriod") or {}

    return {
        "external_ref": tender_id,
        "name": title,
        "country": "Ukraine",
        "organization_name": (entity.get("name") or "").strip() or None,
        "stage": stage,
        "classification_code": code,
        "classification_scheme": "CPV" if code else None,
        "posted_date": raw.get("dateCreated") or tender_period.get("startDate"),
        "response_deadline": tender_period.get("endDate"),
        # Real, live-verified URL pattern (both prozorro.gov.ua/tender/
        # and its /en/ variant return HTTP 200 for a real tenderID).
        "ui_link": f"https://prozorro.gov.ua/tender/{tender_id}",
        **extract_contact(raw),
        "winners": extract_winners(raw),
    }


def is_relevant(entity_name: Optional[str], classification_code: Optional[str]) -> bool:
    """
    The three-part test as one callable, matching Colombia's shape:
    a defence buyer, not a support unit, AND a materiel-relevant CPV
    code. All three run on data already available before any detail
    fetch happens EXCEPT the classification code, which only the
    detail record carries — so the ingestion layer calls this again,
    fully, once the detail is in hand, rather than trusting the
    buyer-only pre-filter as the final word.
    """
    return (
        is_defence_buyer(entity_name)
        and not is_support_unit(entity_name)
        and is_defense_relevant_cpv(classification_code)
    )
