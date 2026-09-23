"""
Pure normalization logic for CPPP (Central Public Procurement Portal,
Government of India). Zero I/O — same separation pattern as the
SAM.gov, UK Find a Tender and TED normalizers.

THE ARCHITECTURAL DIFFERENCE THAT MATTERS, stated plainly: CPPP
publishes NO usable classification code. Its entire vocabulary is
Goods / Services / Works (three buckets), and the public tender
listing does not even expose that much. There is no CPV, no NAICS,
no equivalent of any kind.

So defence-relevance here is decided by the PUBLISHING ORGANISATION
rather than by a category code — an Indian defence organisation
(Military Engineer Services, BSF, DRDO, Ordnance, the service
branches, defence PSUs) publishing a tender is itself the evidence
that it is defence procurement. That is a verifiable fact taken
straight from the source row, not an inference, which is why records
from this source are stored with evidence_status 'verified'.

Because there is no real code to store, matched records carry the
sentinel classification code INDIA_DEFENCE_ORG_CODE ('IN-DEF'). See
app/programme_matching.py for how matching treats it: it grants the
same category bonus a real NAICS/CPV match would, but — unlike a
real code — a sentinel match with zero keyword corroboration is
dropped entirely rather than surfaced as a weak match. Without that
rule every Indian programme would match every capability.

Source of the field layout: the live listing at
eprocure.gov.in/cppp/latestactivetendersnew, whose table is
  Sl.No | e-Published Date | Bid Submission Closing Date |
  Tender Opening Date | Title/Ref.No./Tender Id | Organisation Name |
  Corrigendum
"""

import html as _html
import re
from datetime import datetime
from typing import Optional, TypedDict

# Estate/facilities-works exclusion lives in app/works_filter.py —
# CanadaBuys needed exactly the same logic (its Department of National
# Defence publishes construction source lists just as MES publishes
# sewage repairs), so the term lists are shared rather than duplicated.
# Re-exported here so this module stays the single import point for
# everything CPPP normalization needs.
from app.works_filter import (  # noqa: F401  (re-exported for callers/tests)
    DEFENCE_MATERIEL_TERMS,
    FACILITIES_WORKS_TERMS,
    is_facilities_works,
    is_uninformative_title,
)

# Sentinel stored in programmes.naics_code for this source. That
# column already holds CPV codes for the UK/EU sources despite its
# name (documented naming debt in the README) — this continues that
# existing compromise rather than inventing a parallel column.
INDIA_DEFENCE_ORG_CODE = "IN-DEF"

# Long, distinctive organisation phrases — safe to match as plain
# substrings because they cannot plausibly collide with a non-defence
# body's name.
DEFENCE_ORG_PHRASES = (
    "MILITARY ENGINEER SERVICES",
    "BORDER SECURITY FORCE",
    "COAST GUARD",
    "INDIAN ARMY",
    "INDIAN NAVY",
    "INDIAN AIR FORCE",
    "AIR FORCE",
    "ORDNANCE",
    "DEFENCE",
    "DEFENSE",
    "QUALITY ASSURANCE",  # DGQA — Directorate General of Quality Assurance
    "NAVAL DOCKYARD",
    "ARMY BASE WORKSHOP",
    # The seven Ordnance Factory Board successor DPSUs — OFB itself
    # was DISSOLVED on 1 October 2021 and its 41 production units
    # reorganised into these seven separate companies (verified
    # live: press releases from the corporatisation, plus each
    # entity's own Wikipedia page). The old "OFB" acronym match
    # below is kept for pre-2021 historical records, but every
    # tender these entities publish going forward used one of these
    # names instead — a real, previously-missed gap, not a guess.
    "YANTRA INDIA",
    "MUNITIONS INDIA",
    "ARMOURED VEHICLES NIGAM",
    "ADVANCED WEAPONS AND EQUIPMENT INDIA",
    "TROOP COMFORTS",
    "INDIA OPTEL",
    "GLIDERS INDIA",
)

# Acronyms matched on word boundaries only. Substring matching these
# would produce real false positives — "BEL" inside "BELGAUM", "MES"
# inside "MESSAGE", "SSB" inside longer codes — so they are matched
# as whole tokens.
DEFENCE_ORG_ACRONYMS = (
    "MES", "E-IN-C", "BSF", "DRDO", "OFB", "DGQA",
    "CRPF", "CISF", "ITBP", "SSB", "NSG", "NCC",
    "HAL", "BEL", "BEML", "BDL", "GRSE", "MDL", "GSL", "HSL", "MIDHANI",
)

_ACRONYM_RE = re.compile(
    r"(?<![A-Za-z0-9])(" + "|".join(re.escape(a) for a in DEFENCE_ORG_ACRONYMS) + r")(?![A-Za-z0-9])",
    re.IGNORECASE,
)


def is_defence_organisation(org_name: Optional[str]) -> bool:
    """
    Pure relevance check — the India analogue of
    uk_ft_normalize.is_defense_relevant_cpv, keyed on the publishing
    body instead of a category code (see module docstring).
    """
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
    classification_scheme: Optional[str]
    posted_date: Optional[str]
    response_deadline: Optional[str]
    ui_link: Optional[str]


def _clean(fragment: str) -> str:
    """Strip tags and collapse whitespace from one table cell."""
    return _html.unescape(re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", fragment))).strip()


def parse_date(value: Optional[str]) -> Optional[str]:
    """
    CPPP renders dates as '29-Aug-2026 03:00 PM'. Converted to ISO
    8601 so deadlines from this source sort alongside the other
    sources' dates; the raw string is returned unchanged if it
    doesn't parse, rather than dropping a real deadline over a
    format surprise.
    """
    if not value:
        return None
    value = value.strip()
    try:
        return datetime.strptime(value, "%d-%b-%Y %I:%M %p").isoformat()
    except ValueError:
        return value or None


def parse_rows(page_html: str) -> list[dict]:
    """
    Extracts the tender rows from one listing page. Pure string
    processing — no network, no DB — so the row shape is unit-testable
    against a captured page without hitting the live portal.

    Header rows and any layout rows are skipped by requiring the full
    complement of data cells.
    """
    rows = []
    for row_html in re.findall(r"<tr[^>]*>(.*?)</tr>", page_html, re.S | re.I):
        cells = re.findall(r"<td[^>]*>(.*?)</td>", row_html, re.S | re.I)
        if len(cells) < 6:
            continue  # header row, or a layout row with no tender in it
        detail_links = re.findall(r"href=[\"']([^\"']+)", cells[4])
        rows.append({
            "published": _clean(cells[1]),
            "closing": _clean(cells[2]),
            "opening": _clean(cells[3]),
            "title_cell": _clean(cells[4]),
            "title_link_text": _clean(re.sub(r"</a>.*$", "", cells[4], flags=re.S)),
            "detail_url": detail_links[0] if detail_links else None,
            "organisation": _clean(cells[5]),
        })
    return rows


def normalize_row(raw: dict) -> NormalizedProgramme:
    """
    Maps one parsed listing row into our schema's shape.

    The Title/Ref.No./Tender Id cell renders as
    '<title> /<ref no>/<tender id>' — and titles themselves routinely
    contain slashes ('AGE(I)(U)B/R-TOKEN-25/2026-27'), so the tender
    id is taken as the final slash-separated segment rather than by
    splitting the cell into fixed parts.
    """
    title_cell = raw.get("title_cell") or ""
    external_ref = title_cell.rsplit("/", 1)[-1].strip() if "/" in title_cell else ""
    if not external_ref:
        raise ValueError("Row has no tender id in the Title/Ref.No./Tender Id cell — cannot dedupe safely")

    name = (raw.get("title_link_text") or "").strip() or title_cell.strip() or f"CPPP Tender {external_ref}"

    organisation = (raw.get("organisation") or "").strip() or None

    return {
        "external_ref": external_ref,
        "name": name,
        "country": "India",
        "organization_name": organisation,
        # Everything on the active-tenders listing is open for bidding
        # by definition — CPPP publishes no stage/tag field to derive
        # anything finer from, so claiming more would be invented.
        "stage": "rfp_issued",
        "classification_code": INDIA_DEFENCE_ORG_CODE,
        "classification_scheme": "IN-DEFENCE-ORG",
        "posted_date": parse_date(raw.get("published")),
        "response_deadline": parse_date(raw.get("closing")),
        "ui_link": raw.get("detail_url"),
    }


def normalize_batch(raw_rows: list[dict]) -> tuple[list[NormalizedProgramme], list[dict]]:
    """
    Normalizes and filters for defence-relevance in one pass, same
    shape as the UK normalizer's batch function. A row published by a
    non-defence organisation is not a failure — it parsed fine, it's
    just procurement this platform doesn't cover.
    """
    normalized = []
    failures = []
    for raw in raw_rows:
        try:
            record = normalize_row(raw)
        except (ValueError, AttributeError, TypeError) as e:
            failures.append({"error": str(e), "organisation": raw.get("organisation")})
            continue
        if not is_defence_organisation(record["organization_name"]):
            continue
        # Published by a defence body, but estate/facilities work
        # rather than capability procurement — dropped for the same
        # reason non-defence organisations are: correctly parsed,
        # simply not what this platform covers.
        if is_facilities_works(record["name"]) or is_uninformative_title(record["name"]):
            continue
        normalized.append(record)
    return normalized, failures
