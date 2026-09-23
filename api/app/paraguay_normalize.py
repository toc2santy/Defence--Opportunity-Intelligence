"""
Pure normalization logic for DNCP Paraguay. Zero I/O, same separation
pattern as every other normalizer here.

WHY BUYER FILTERING ALONE IS NOT ENOUGH — the same class of problem
Colombia has, different specifics: a live sample of "Ministerio de
Defensa Nacional" procurement mixed real materiel (vehicle spare
parts, military vehicles, aircraft parts, arms-registry supplies,
real US MIL-SPEC part numbers) with generic institutional procurement
(kitchen utensils, office folders, firewall software licences,
building maintenance, food supply contracts, photocopier rental,
insurance) — see db/migrations/039's header for the exact live sample
this was built from. Relevance here is therefore buyer AND category,
using DNCP's own printed category text
(tender.mainProcurementCategoryDetails), not a code.

CLASSIFICATION: DNCP publishes UNSPSC under its own scheme name
"catalogoNivel5DNCP", with a local suffix appended to the real 8-digit
UNSPSC code (e.g. "23151607-001") — parse_unspsc strips that suffix,
the same shape of transformation Colombia needs for its "V1." prefix,
just on the other end of the string.
"""

import re
import unicodedata
from typing import Optional, TypedDict

# DNCP's own printed category text. "DNCP-CODE-N4" appears with the
# leading 8-digit UNSPSC code, then a "-" and a local suffix.
_UNSPSC_RE = re.compile(r"^(\d{8})(?:-\d+)?$")

# Tender Briefing "Open" link (2026-09) — real gap found live: this
# source published NO ui_link at all (the field wasn't even in
# NormalizedProgramme), unlike every other source, so every DNCP
# tender's briefing had no working "open on source" button.
#
# DNCP's own real ocid format is "ocds-03ad3f-{id_llamado}-{n}" — the
# middle numeric segment is the portal's own "id_llamado" (call ID),
# confirmed by checking a real, publicly-documented DNCP URL pattern
# (contrataciones.gov.py's own visualisation page,
# ?id_llamado=<id>) and live-loading it with our own real ocid's
# extracted number: the page returned HTTP 200 with real
# licitación-cycle content, not an error/not-found page. Left as a
# regex-extraction helper (not hardcoded per-record) so a malformed
# or unexpected ocid shape degrades to no link rather than a broken
# one — same "graceful absence over a guessed-wrong link" rule
# AusTender's own ui_link:None decision already established.
_OCID_ID_LLAMADO_RE = re.compile(r"^ocds-[0-9a-f]+-(\d+)-\d+$")


def _ui_link_from_ocid(ocid: str) -> Optional[str]:
    match = _OCID_ID_LLAMADO_RE.match(ocid or "")
    if not match:
        return None
    return f"https://www.contrataciones.gov.py/datos/visualizaciones/ciclo_licitacion/index.html?id_llamado={match.group(1)}"


def _fold(value: Optional[str]) -> str:
    if not value:
        return ""
    stripped = unicodedata.normalize("NFKD", value)
    return "".join(c for c in stripped if not unicodedata.combining(c)).upper()


# Only DNCP's own printed unit names, verified against a live sample
# (see db/migrations/039) — not guessed. "Ministerio de Defensa
# Nacional" itself, plus its named commands, both appear as the
# procuringEntity on real defence-materiel rows.
DEFENCE_BUYER_PHRASES = (
    "MINISTERIO DE DEFENSA NACIONAL",
    "COMANDO DEL EJERCITO",
    "COMANDO DE LA ARMADA",
    "ARMADA PARAGUAYA",
    "COMANDO DE LA FUERZA AEREA",
    "FUERZA AEREA PARAGUAYA",
)

# Category text DNCP itself prints (tender.mainProcurementCategoryDetails)
# that showed up on genuinely non-materiel rows in the live sample —
# food, furniture/appliances (which is where the sample's kitchen
# utensils and a butcher saw landed), office supplies, financial/
# insurance services, building maintenance, professional/admin
# services, and equipment rental. Each phrase is DNCP's own printed
# text, not invented.
NON_MATERIEL_CATEGORY_PHRASES = (
    "ALIMENTOS BEBIDAS Y TABACO",
    "MUEBLES ACCESORIOS, ELECTRODOMESTICOS",
    "EQUIPOS ACCESORIOS Y SUMINISTROS DE OFICINA",
    "SERVICIOS FINANCIEROS Y DE SEGUROS",
    "SERVICIOS DE CONSTRUCCION Y MANTENIMIENTO",
    "SERVICIOS DE GESTION PROFESIONALES",
    "PRODUCTOS PUBLICADOS",
    # Found live on a second sample (2026-09-18): "Servicios basados
    # en ingenieria investigacion y tecnologia" is DNCP's catch-all
    # for IT/software services (firewall licences, software support
    # renewals) — genuinely broad enough to be a real gap left
    # in by the first pass, not a materiel category despite the
    # "ingenieria" in its name.
    "SERVICIOS BASADOS EN INGENIERIA INVESTIGACION Y TECNOLOGIA",
    # DNCP's category for educational/ceremonial supplies (found on
    # a literal "Servicio de Ceremonial" row) — instruments, toys,
    # crafts, teaching materials.
    "MATERIALES DIDACTICOS PROFESIONALES",
)

# Procedure states seen to mean "nobody can bid on this any more" —
# same treatment as Colombia's Cancelado/Desierto and South Africa's
# cancelled/withdrawn OCDS statuses. Kept deliberately short: only
# DESIERTO/CANCELADO have been confirmed in live data so far; an
# unrecognised statusDetails value falls through to the honest floor
# below rather than being guessed into this list.
_NON_INGESTABLE_STATUS_PHRASES = ("DESIERTO", "CANCELADO", "FRACASADO")


class NormalizedProgramme(TypedDict):
    external_ref: str
    name: str
    country: str
    organization_name: Optional[str]
    stage: Optional[str]
    classification_code: Optional[str]
    classification_scheme: Optional[str]
    winner_name: Optional[str]
    set_aside_code: Optional[str]
    set_aside_description: Optional[str]
    ui_link: Optional[str]


def is_defence_buyer(entity_name: Optional[str]) -> bool:
    folded = _fold(entity_name)
    if not folded:
        return False
    return any(phrase in folded for phrase in DEFENCE_BUYER_PHRASES)


def is_non_materiel_category(category_details: Optional[str]) -> bool:
    folded = _fold(category_details)
    return any(phrase in folded for phrase in NON_MATERIEL_CATEGORY_PHRASES)


def parse_unspsc(classification_id: Optional[str]) -> Optional[str]:
    """
    Strips DNCP's local suffix from its catalogoNivel5DNCP id, e.g.
    "23151607-001" -> "23151607". Returns None for anything that
    doesn't start with a genuine 8-digit UNSPSC code rather than
    storing a value that isn't a real classification.
    """
    match = _UNSPSC_RE.match((classification_id or "").strip())
    return match.group(1) if match else None


def _stage_for(status_details: Optional[str]) -> Optional[str]:
    folded = _fold(status_details)
    if any(s in folded for s in _NON_INGESTABLE_STATUS_PHRASES):
        return None
    if "ADJUDICADO" in folded or "FIRMADO" in folded:
        return "contract_awarded"
    if "CONVOCATORIA" in folded or "ABIERTA" in folded:
        return "rfp_issued"
    # Honest floor for a recognised-but-uncategorised status, same
    # fallback Colombia and CanadaBuys both use for the same reason.
    return "requirement_defined"


def extract_classification_from_detail(detail_payload: dict) -> Optional[str]:
    """
    /search/processes's own compiledRelease is a SUMMARY that does
    NOT include tender.items at all — confirmed live (see
    db/migrations/039) — so the classification code can only be read
    from a full record fetch (GET /ocds/record/{ocid}), whose real
    shape is a RECORD PACKAGE ({"records": [{"ocid", "releases",
    "compiledRelease"}]}), not a bare release package — a genuine
    mistake in this function's first version, caught by testing it
    against a live fetch rather than trusting the shape assumed from
    a partial manual read earlier. `records[].releases[]` entries
    themselves carry only date/tag/url, no items at all;
    `records[].compiledRelease` is where the real item data lives.

    Each item carries classification TWICE: `classification`
    (DNCP's own "catalogoNivel5DNCP" scheme, code+local suffix, e.g.
    "73161607-001") AND `additionalClassifications` (a genuine bare
    UNSPSC entry, e.g. {"scheme": "UNSPSC", "id": "73161607"}) — the
    latter is preferred when present since it needs no suffix
    stripping at all; parse_unspsc on the former is the fallback.
    """
    records = detail_payload.get("records") or []
    if not records:
        return None
    items = (records[0].get("compiledRelease") or {}).get("tender", {}).get("items") or []
    if not items:
        return None
    item = items[0]
    for extra in item.get("additionalClassifications") or []:
        if extra.get("scheme") == "UNSPSC":
            code = str(extra.get("id") or "").strip()
            if code:
                return code
    classification = item.get("classification") or {}
    return parse_unspsc(classification.get("id"))


def extract_eligibility_from_detail(detail_payload: dict) -> tuple[Optional[str], Optional[str]]:
    """
    Eligibility/Backup Phase 1 (2026-09) — a real, live-verified DNCP
    field at the SAME path extract_classification_from_detail already
    reads (`records[0].compiledRelease.tender.eligibilityCriteria`),
    so this needs no separate fetch — sibling extraction on the exact
    same detail payload the two-phase architecture already pulls for
    classification.

    Real format confirmed on the first genuine live re-ingestion run
    (2026-09-22, not just the small sample this function was first
    written against): this field carries several different real
    "actually not restricted" phrasings, not only "ninguna" —
    "Restricciones: NO APLICA" (N/A) was found being surfaced as a
    stated restriction, a real false positive caught by checking the
    actual ingested data rather than trusting the first sample. Also
    found: standard Paraguayan legal boilerplate ("Podran participar
    todos los oferentes que cumplan..." — "All bidders who meet the
    [normal legal] requirements may participate...", citing Ley
    7021/2022 Art. 21) that states the DEFAULT baseline eligibility
    rule everyone must meet, not a restriction to a specific bidder
    category — treating that as a stated restriction would overclaim
    exactly the kind of thing this whole feature exists to avoid.
    Both real phrasings are now checked (via `_fold`, this module's
    own accent-folding helper — confirmed necessary here too: the same
    boilerplate appeared both with and without Spanish accents across
    different real rows). Any other real text is surfaced as-is,
    since a genuinely narrower restriction (e.g. "reserved for
    MIPYMES") was never actually observed live.
    """
    records = detail_payload.get("records") or []
    if not records:
        return None, None
    tender = (records[0].get("compiledRelease") or {}).get("tender") or {}
    raw = str(tender.get("eligibilityCriteria") or "").strip()
    if not raw:
        return None, None
    folded = _fold(raw)
    # _fold uppercases (see its own definition above) — match against
    # UPPERCASE phrases, the same convention every other _fold user in
    # this module (e.g. DEFENCE_BUYER_PHRASES) already follows.
    if "NINGUNA" in folded or "NO APLICA" in folded or "TODOS LOS OFERENTES" in folded:
        return None, None
    return raw, raw


def normalize_record(compiled_release: dict) -> Optional[NormalizedProgramme]:
    """
    Returns None for a release with no usable tender.id — a real,
    expected shape for a planning-only release this feed also mixes
    in, not a parse failure.
    """
    tender = compiled_release.get("tender") or {}
    ocid = str(compiled_release.get("ocid") or "").strip()
    if not ocid:
        return None

    buyer = tender.get("procuringEntity") or {}
    buyer_name = str(buyer.get("name") or "").strip() or None

    if not is_defence_buyer(buyer_name):
        return None

    category_details = tender.get("mainProcurementCategoryDetails")
    if is_non_materiel_category(category_details):
        return None

    stage = _stage_for(tender.get("statusDetails"))
    if stage is None:
        return None

    name = str(tender.get("title") or "").strip() or f"DNCP Paraguay {ocid}"

    # Always None here — the search endpoint's compiledRelease carries
    # no items at all (see extract_classification_from_detail's own
    # docstring). The caller (paraguay_ingestion.py) fills this in
    # from a separate per-candidate detail fetch, AFTER this filter
    # has already narrowed the field, the same two-phase shape
    # ProZorro uses for exactly the same reason (its list endpoint is
    # equally thin).
    code = None
    # Same reasoning as classification `code` above — the search
    # endpoint's compiledRelease has no eligibilityCriteria either;
    # the caller fills this in from the same per-candidate detail
    # fetch it already does for classification (see
    # extract_eligibility_from_detail).
    set_aside_code, set_aside_description = None, None

    winner_name = None
    awards = compiled_release.get("awards") or []
    if awards:
        suppliers = awards[0].get("suppliers") or []
        if suppliers:
            winner_name = str(suppliers[0].get("name") or "").strip() or None

    return {
        "external_ref": ocid,
        "name": name,
        "country": "Paraguay",
        "organization_name": buyer_name,
        "stage": stage,
        "classification_code": code,
        "classification_scheme": "UNSPSC" if code else None,
        "winner_name": winner_name,
        "set_aside_code": set_aside_code,
        "set_aside_description": set_aside_description,
        "ui_link": _ui_link_from_ocid(ocid),
    }


def normalize_batch(compiled_releases: list[dict]) -> tuple[list[NormalizedProgramme], list[dict]]:
    normalized = []
    failures = []
    for cr in compiled_releases:
        try:
            record = normalize_record(cr)
        except (AttributeError, TypeError, KeyError) as e:
            failures.append({"error": str(e), "ocid": cr.get("ocid")})
            continue
        if record is not None:
            normalized.append(record)
    return normalized, failures
