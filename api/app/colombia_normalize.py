"""
Pure normalization logic for SECOP II (Colombia Compra Eficiente).
Zero I/O, same separation pattern as every other normalizer here.

WHY COLOMBIA NEEDS A RELEVANCE TEST NO OTHER SOURCE NEEDS: CanadaBuys
and CPPP decide relevance from the buying entity alone, and that works
because their feeds are procurement of things. Colombia's is not — of
549 live rows from military buyers sampled on 2026-09-16, 339 were
"Prestación de servicios", the individual-contractor hiring that makes
up the bulk of Colombian public procurement, and whose procedure name
is frequently just a person's name. Buyer filtering alone would fill
this platform with employment records.

So relevance here is a conjunction:
  military buyer  AND  goods procurement  AND  not a welfare/health unit
each of which is a separate observable fact in the row, and each of
which is documented below against the live data that motivated it.

WHAT MAKES THIS SOURCE WORTH THE EXTRA FILTER: it publishes UNSPSC
(in codigo_principal_de_categoria, prefixed "V1."), so unlike India
and South Africa it needs no organisation sentinel — the codes feed
the same longest-prefix matching CanadaBuys already uses. It also
publishes the winning supplier inline, making it the third source
able to populate contract_awards.
"""

import re
import unicodedata
from datetime import datetime
from typing import Optional, TypedDict

from app.address_format import format_address
from app.works_filter import is_facilities_works, is_uninformative_title

# Socrata column names, from the live dataset rather than docs.
COL_ENTITY = "entidad"
COL_NAME = "nombre_del_procedimiento"
COL_DESCRIPTION = "descripci_n_del_procedimiento"
COL_PROCESS_ID = "id_del_proceso"
COL_REFERENCE = "referencia_del_proceso"
COL_PUBLISHED = "fecha_de_publicacion_del"
COL_CATEGORY = "codigo_principal_de_categoria"
COL_CONTRACT_TYPE = "tipo_de_contrato"
COL_PHASE = "fase"
COL_STATUS = "estado_del_procedimiento"
COL_URL = "urlproceso"
COL_AWARDED = "adjudicado"
COL_SUPPLIER = "nombre_del_proveedor"
COL_SUPPLIER_NIT = "nit_del_proveedor_adjudicado"
COL_AWARD_VALUE = "valor_total_adjudicacion"
COL_BASE_PRICE = "precio_base"
COL_CITY = "ciudad_entidad"
COL_DEPARTMENT = "departamento_entidad"

# Colombia writes the same entity several ways ("EJERCITO" /
# "EJÉRCITO"), so every comparison in this module runs on an
# accent-stripped, upper-cased copy of the name.
def _fold(value: Optional[str]) -> str:
    if not value:
        return ""
    stripped = unicodedata.normalize("NFKD", value)
    return "".join(c for c in stripped if not unicodedata.combining(c)).upper()


DEFENCE_BUYER_PHRASES = (
    "MINISTERIO DE DEFENSA NACIONAL",
    "EJERCITO",                      # Ejército Nacional and its commands
    "ARMADA NACIONAL",
    "FUERZA AEREA",
    "COMANDO GENERAL DE LAS FUERZAS MILITARES",
    "AGENCIA LOGISTICA DE LAS FUERZAS MILITARES",
    "INDUMIL",                       # Industria Militar, the state arms manufacturer
    "INDUSTRIA MILITAR",
    "COTECMAR",                      # state naval shipbuilder
    "CORPORACION DE CIENCIA Y TECNOLOGIA PARA EL DESARROLLO DE LA INDUSTRIA NAVAL",
    "CIAC",                          # Corporación de la Industria Aeronáutica Colombiana
    # Colombia's national police sits UNDER the Ministry of Defence
    # and buys from the same supplier base (weapons, armoured
    # vehicles, comms). Included on the same reasoning that puts the
    # Canadian Coast Guard in canada_buys_normalize — a civilian-
    # facing force whose materiel procurement is the defence
    # industrial base's.
    "POLICIA NACIONAL",
)

# "DEFENSA" in an entity name is NOT evidence of defence. Both of
# these are among the largest matches for that word in the live feed
# and neither is a military body — one is an environmental authority,
# the other the state's legal-defence agency. Checked BEFORE the
# phrase list, so a name cannot be rescued by an incidental match.
NON_DEFENCE_DESPITE_THE_NAME = (
    "MESETA DE BUCARAMANGA",
    "DEFENSA JURIDICA",
    "DEFENSA CIVIL",
)

# Military health, welfare and education units. Genuinely military,
# genuinely not capability procurement — they buy medicines, lab
# reagents, surgical equipment and teaching services. Dropping them is
# the same judgement already applied to MES estate works in India and
# CFB Halifax construction in Canada, and it matters more here: these
# units are the single largest block of military rows by volume.
SUPPORT_UNIT_PHRASES = (
    "SANIDAD",
    "DISPENSARIO",
    "HOSPITAL",
    "JEFATURA SALUD",
    "SALUD ",
    "LICEO",
    "CLUB MILITAR",
    "CAJA DE RETIRO",
    "CAJA DE SUELDOS",
    "FONDO ROTATORIO",
)

# Contract types that denote procurement of goods. Taken from the
# live distribution, not from a code list: of 549 military rows,
# Suministros (67) and Compraventa (45) were the goods types, against
# 339 Prestación de servicios, plus Obra (works), Consultoría,
# Interventoría, Seguros and Arrendamiento de inmuebles (real
# estate) — none of which is materiel.
GOODS_CONTRACT_TYPES = (
    "COMPRAVENTA",
    "SUMINISTRO",       # matches "Suministros" and "Suministro"
    "VENTA MUEBLES",
    "ARRENDAMIENTO DE MUEBLES",
)

# The literal string Colombia uses for "no value here" — it appears
# in supplier names, NITs and several other fields, and treating it
# as data would invent a supplier called "No Definido".
NOT_DEFINED = "NO DEFINIDO"

_UNSPSC_RE = re.compile(r"^(?:V\d+\.)?(\d{8})$", re.IGNORECASE)


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
    winner_name: Optional[str]
    winner_identifier: Optional[str]
    contact_address: Optional[str]
    value_amount: Optional[float]
    value_currency: Optional[str]


def is_defence_buyer(entity_name: Optional[str]) -> bool:
    """Pure relevance check on the buying entity alone."""
    folded = _fold(entity_name)
    if not folded:
        return False
    if any(phrase in folded for phrase in NON_DEFENCE_DESPITE_THE_NAME):
        return False
    return any(phrase in folded for phrase in DEFENCE_BUYER_PHRASES)


def is_support_unit(entity_name: Optional[str]) -> bool:
    """Military health/welfare/education unit — not capability procurement."""
    folded = _fold(entity_name)
    return any(phrase in folded for phrase in SUPPORT_UNIT_PHRASES)


def is_goods_procurement(contract_type: Optional[str]) -> bool:
    folded = _fold(contract_type)
    return any(t in folded for t in GOODS_CONTRACT_TYPES)


def parse_unspsc(value: Optional[str]) -> Optional[str]:
    """
    Strips the "V1." version prefix Colombia puts in front of the
    UNSPSC code. Returns None for the feed's non-codes ("UNSPECIFIED",
    "PECI...") rather than storing a value that isn't a classification
    — a wrong code would match the wrong capability at full +3 bonus.
    """
    match = _UNSPSC_RE.match((value or "").strip())
    return match.group(1) if match else None


def parse_date(value: Optional[str]) -> Optional[str]:
    """Socrata floating timestamps: '2026-09-15T00:00:00.000'."""
    raw = (value or "").strip()
    if not raw:
        return None
    for fmt in ("%Y-%m-%dT%H:%M:%S.%f", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d"):
        try:
            return datetime.strptime(raw, fmt).isoformat()
        except ValueError:
            continue
    return raw


def parse_url(value) -> Optional[str]:
    """
    urlproceso is a nested object — {"url": "https://community.secop
    .gov.co/..."} — not a string, which is the kind of thing that
    silently produces "[object Object]" links if assumed.
    """
    if isinstance(value, dict):
        url = (value.get("url") or "").strip()
        return url or None
    url = str(value or "").strip()
    return url or None


def _real_value(value: Optional[str]) -> Optional[str]:
    text = (value or "").strip()
    if not text or _fold(text) == NOT_DEFINED:
        return None
    return text


# SECOP II's own procedure states, mapped onto programmes.stage.
# Colombia publishes two overlapping fields — `fase` (where the
# procedure is) and `estado_del_procedimiento` (its status) — and
# the phase is the more specific of the two, so it is read first.
_PHASE_MAP = {
    "PRESENTACION DE OFERTA": "rfp_issued",
    "FASE DE OFERTAS": "rfp_issued",
    "PROCESO DE OFERTAS": "rfp_issued",
    "FASE DE SELECCION": "rfp_issued",
    "PRESENTACION DE OBSERVACIONES": "requirement_defined",
    "MANIFESTACION DE INTERES": "requirement_defined",
    "ESTIMATE PHASE": "early_concept",
    "CLARIFICATION SUBMISSION": "requirement_defined",
}

_STATUS_MAP = {
    "SELECCIONADO": "contract_awarded",
    "ADJUDICADO": "contract_awarded",
    "CELEBRADO": "contract_awarded",
    "EVALUACION": "rfp_issued",
    "ABIERTO": "rfp_issued",
    "PUBLICADO": "rfp_issued",
    "APROBADO": "contract_awarded",
    "BORRADOR": "early_concept",
}

# Procedures nobody can bid on any more. Same treatment, and the same
# reasoning, as South Africa's cancelled/withdrawn OCDS statuses:
# programmes.stage has no state meaning "called off", and mapping
# these onto rfp_issued would advertise a dead procurement.
NON_INGESTABLE_STATUSES = ("CANCELADO", "DESIERTO", "TERMINADO ANORMALMENTE")


def _stage_for(phase: Optional[str], status: Optional[str]) -> Optional[str]:
    folded_status = _fold(status)
    if any(s in folded_status for s in NON_INGESTABLE_STATUSES):
        return None

    folded_phase = _fold(phase)
    for key, stage in _PHASE_MAP.items():
        if key in folded_phase:
            return stage
    for key, stage in _STATUS_MAP.items():
        if key in folded_status:
            return stage
    # Unknown but not known-dead: the honest floor, same fallback
    # CanadaBuys uses for an unrecognised notice type.
    return "requirement_defined"


def normalize_row(raw: dict) -> NormalizedProgramme:
    """Maps one Socrata row into our schema's shape."""
    external_ref = str(raw.get(COL_PROCESS_ID) or "").strip()
    if not external_ref:
        external_ref = str(raw.get(COL_REFERENCE) or "").strip()
    if not external_ref:
        raise ValueError("Row has neither id_del_proceso nor referencia_del_proceso — cannot dedupe safely")

    # The procedure name is the subject; the description repeats it on
    # most rows but is occasionally the fuller text.
    name = str(raw.get(COL_NAME) or "").strip()
    description = str(raw.get(COL_DESCRIPTION) or "").strip()
    if len(description) > len(name):
        name = description
    if not name:
        name = f"SECOP II {external_ref}"

    code = parse_unspsc(raw.get(COL_CATEGORY))
    awarded = _fold(raw.get(COL_AWARDED)) == "SI"
    winner = _real_value(raw.get(COL_SUPPLIER)) if awarded else None

    value_amount = None
    if winner and raw.get(COL_AWARD_VALUE) is not None:
        try:
            value_amount = float(raw[COL_AWARD_VALUE])
        except (TypeError, ValueError):
            value_amount = None

    return {
        "external_ref": external_ref,
        "name": name,
        "country": "Colombia",
        "organization_name": str(raw.get(COL_ENTITY) or "").strip() or None,
        "stage": _stage_for(raw.get(COL_PHASE), raw.get(COL_STATUS)),
        "classification_code": code,
        "classification_scheme": "UNSPSC" if code else None,
        "posted_date": parse_date(raw.get(COL_PUBLISHED)),
        # SECOP II's open dataset publishes no closing date for the
        # procedure — checked across the live columns. Left None
        # rather than reusing the publication date, which would put a
        # deadline on screen that the source never stated.
        "response_deadline": None,
        "ui_link": parse_url(raw.get(COL_URL)),
        "winner_name": winner,
        "winner_identifier": _real_value(raw.get(COL_SUPPLIER_NIT)) if winner else None,
        # SECOP II publishes no street-level address for the buying
        # entity, only its city and department — verified against the
        # dataset's own column list (see db/migrations/027's header).
        # Real government office locations even at this coarser
        # grain, not invented, so kept rather than left blank.
        "contact_address": format_address(
            locality=raw.get(COL_CITY), region=raw.get(COL_DEPARTMENT), country="Colombia",
        ),
        # SECOP II publishes no explicit currency column — Colombian
        # public procurement is denominated in COP by law, so this is
        # a stated convention of the source, not a guessed value.
        # Only set when a real award value was actually parsed, so a
        # row with no value never gets a currency with nothing to
        # attach it to.
        "value_amount": value_amount,
        "value_currency": "COP" if value_amount is not None else None,
    }


def normalize_batch(raw_rows: list[dict]) -> tuple[list[NormalizedProgramme], list[dict]]:
    """
    Normalizes, then applies the three-part relevance test. Rows
    dropped by any part are not failures — they parsed fine, they are
    simply not capability procurement.
    """
    normalized = []
    failures = []
    for raw in raw_rows:
        try:
            record = normalize_row(raw)
        except (ValueError, AttributeError, TypeError) as e:
            failures.append({"error": str(e), "reference": raw.get(COL_PROCESS_ID)})
            continue
        if not is_defence_buyer(record["organization_name"]):
            continue
        if is_support_unit(record["organization_name"]):
            continue
        if not is_goods_procurement(raw.get(COL_CONTRACT_TYPE)):
            continue
        if record["stage"] is None:          # cancelled / desierto
            continue
        if is_facilities_works(record["name"]) or is_uninformative_title(record["name"]):
            continue
        normalized.append(record)
    return normalized, failures
