"""
Unit tests for app.colombia_normalize — no network, no database.

Every fixture below is copied from a real row of the live SECOP II
dataset (datos.gov.co resource p6dx-8zbt): the Spanish column names,
the "V1."-prefixed UNSPSC, the nested urlproceso object, the literal
"No Definido" placeholder, and real Colombian Navy and Army procedure
titles including the M113-A2 spares and the CIAC Firehawk material.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.colombia_normalize import (
    COL_AWARD_VALUE,
    COL_AWARDED,
    COL_CATEGORY,
    COL_CONTRACT_TYPE,
    COL_ENTITY,
    COL_NAME,
    COL_PHASE,
    COL_PROCESS_ID,
    COL_STATUS,
    COL_SUPPLIER,
    COL_SUPPLIER_NIT,
    COL_URL,
    is_defence_buyer,
    is_goods_procurement,
    is_support_unit,
    normalize_batch,
    normalize_row,
    parse_date,
    parse_unspsc,
    parse_url,
)


def _row(**overrides):
    base = {
        COL_ENTITY: "ARMADA NACIONAL BASE NAVAL No. 1 ARC BOLÍVAR",
        COL_NAME: "ADQUISICION DE LLANTAS NUEVAS A TODO COSTO NO REMANUFACTURADAS",
        "descripci_n_del_procedimiento": "",
        COL_PROCESS_ID: "CO1.REQ.9911223",
        "referencia_del_proceso": "SA-MC-015-2026",
        "fecha_de_publicacion_del": "2026-09-10T00:00:00.000",
        COL_CATEGORY: "V1.25172504",
        COL_CONTRACT_TYPE: "Suministros",
        COL_PHASE: "Presentación de oferta",
        COL_STATUS: "Evaluación",
        COL_URL: {"url": "https://community.secop.gov.co/Public/Tendering/OpportunityDetail/Index?noticeUID=CO1.NTC.9911223"},
        COL_AWARDED: "No",
        COL_SUPPLIER: "No Definido",
        COL_SUPPLIER_NIT: "No Definido",
    }
    base.update(overrides)
    return base


# --- buyer relevance -------------------------------------------------

def test_real_military_buyers_are_recognised():
    for name in (
        "MINISTERIO DE DEFENSA NACIONAL",
        "ARMADA NACIONAL BASE NAVAL No. 6 ARC BOGOTA",
        "EJERCITO DIRECCION DE ADQUISICIONES",
        "JEFATURA DE FUERZA AEREA",
        "INDUMIL",
        "CIAC S.A.",
        "COTECMAR",
    ):
        assert is_defence_buyer(name), name


def test_accented_and_unaccented_spellings_both_match():
    # The feed writes the same force both ways.
    assert is_defence_buyer("EJÉRCITO NACIONAL DE COLOMBIA")
    assert is_defence_buyer("EJERCITO NACIONAL DE COLOMBIA")
    assert is_defence_buyer("FUERZA AÉREA COLOMBIANA")


def test_defensa_in_the_name_is_not_evidence_of_defence():
    """
    Both of these are among the largest matches for the word DEFENSA
    in the live feed, and neither is a military body — one is an
    environmental authority, the other the state's legal-defence
    agency. A naive substring filter ingests both.
    """
    assert not is_defence_buyer(
        "CORPORACIÓN AUTÓNOMA REGIONAL PARA LA DEFENSA DE LA MESETA DE BUCARAMANGA"
    )
    assert not is_defence_buyer("AGENCIA NACIONAL DE DEFENSA JURÍDICA DEL ESTADO")
    assert not is_defence_buyer("DEFENSA CIVIL COLOMBIANA")


def test_ordinary_civilian_buyers_are_rejected():
    assert not is_defence_buyer("DEPARTAMENTO ADMINISTRATIVO NACIONAL DE ESTADISTICA (DANE)")
    assert not is_defence_buyer("")
    assert not is_defence_buyer(None)


def test_military_health_and_welfare_units_are_identified():
    """
    Genuinely military, genuinely not capability procurement — and the
    single largest block of military rows by volume in the live feed.
    """
    for name in (
        "DIRECCION DE SANIDAD EJERCITO DISPENSARIO MÉDICO SUROCCIDENTE",
        "ARMADA NACIONAL HOSPITAL NAVAL DE CARTAGENA",
        "JEFATURA SALUD FUERZA AEREA",
        "LICEOS DEL EJERCITO",
    ):
        assert is_defence_buyer(name), f"{name} is still a military body"
        assert is_support_unit(name), f"{name} should be filtered as a support unit"

    assert not is_support_unit("ARMADA NACIONAL BASE NAVAL No. 1 ARC BOLÍVAR")


# --- goods vs services ----------------------------------------------

def test_goods_contract_types_pass_and_services_do_not():
    assert is_goods_procurement("Suministros")
    assert is_goods_procurement("Compraventa")
    # 339 of 549 live military rows were this type — individual
    # contractor hiring, not materiel.
    assert not is_goods_procurement("Prestación de servicios")
    assert not is_goods_procurement("Obra")
    assert not is_goods_procurement("Consultoría")
    assert not is_goods_procurement("Arrendamiento de inmuebles")
    assert not is_goods_procurement("")


# --- UNSPSC ----------------------------------------------------------

def test_unspsc_version_prefix_is_stripped():
    assert parse_unspsc("V1.25172504") == "25172504"
    assert parse_unspsc("V2.46181500") == "46181500"
    assert parse_unspsc("25172504") == "25172504"


def test_feed_non_codes_return_nothing_rather_than_a_wrong_code():
    """
    A wrong classification code matches the wrong capability at the
    full +3 category bonus, so these must be dropped, not coerced.
    """
    assert parse_unspsc("UNSPECIFIED") is None
    assert parse_unspsc("PECI123") is None
    assert parse_unspsc("") is None
    assert parse_unspsc(None) is None


# --- other field shapes ----------------------------------------------

def test_url_is_read_out_of_the_nested_object():
    assert parse_url({"url": "https://community.secop.gov.co/x"}) == "https://community.secop.gov.co/x"
    assert parse_url("https://plain.example") == "https://plain.example"
    assert parse_url({}) is None
    assert parse_url(None) is None


def test_dates_parse_socrata_floating_timestamps():
    assert parse_date("2026-09-10T00:00:00.000") == "2026-09-10T00:00:00"
    assert parse_date("2026-09-10") == "2026-09-10T00:00:00"
    assert parse_date(None) is None


# --- normalization ---------------------------------------------------

def test_normalize_row_core_fields():
    r = normalize_row(_row())
    assert r["external_ref"] == "CO1.REQ.9911223"
    assert r["country"] == "Colombia"
    assert r["classification_code"] == "25172504"
    assert r["classification_scheme"] == "UNSPSC"
    assert r["stage"] == "rfp_issued"
    assert r["ui_link"].startswith("https://community.secop.gov.co/")
    # The open dataset publishes no closing date for the procedure.
    # Reusing the publication date would put a deadline on screen the
    # source never stated.
    assert r["response_deadline"] is None


def test_normalize_row_falls_back_to_the_reference_number():
    r = normalize_row(_row(**{COL_PROCESS_ID: ""}))
    assert r["external_ref"] == "SA-MC-015-2026"


def test_normalize_row_raises_without_any_identifier():
    try:
        normalize_row(_row(**{COL_PROCESS_ID: "", "referencia_del_proceso": ""}))
        assert False, "expected ValueError"
    except ValueError:
        pass


def test_cancelled_procedures_have_no_stage_and_are_dropped():
    """
    programmes.stage has no state meaning "called off" — same
    reasoning as South Africa's cancelled OCDS statuses. Mapping this
    onto rfp_issued would advertise a dead procurement.
    """
    assert normalize_row(_row(**{COL_STATUS: "Cancelado"}))["stage"] is None
    normalized, failures = normalize_batch([_row(**{COL_STATUS: "Cancelado"})])
    assert normalized == []
    assert failures == [], "a cancelled procedure is a skip, not a failure"


def test_awarded_status_maps_to_contract_awarded():
    assert normalize_row(_row(**{COL_PHASE: "", COL_STATUS: "Seleccionado"}))["stage"] == "contract_awarded"


# --- award winners ---------------------------------------------------

def test_winner_is_captured_when_one_is_actually_named():
    r = normalize_row(_row(**{
        COL_AWARDED: "Si",
        COL_SUPPLIER: "MORARCI GROUP S.A.S",
        COL_SUPPLIER_NIT: "900110012",
    }))
    assert r["winner_name"] == "MORARCI GROUP S.A.S"
    assert r["winner_identifier"] == "900110012"


def test_awarded_flag_alone_never_invents_a_supplier():
    """
    Observed live on 14 of 36 awarded rows: adjudicado = 'Si' with the
    supplier field set to the literal string 'No Definido'. Treating
    that as data would create an OEM organisation called "No Definido"
    and credit it with contract awards.
    """
    r = normalize_row(_row(**{COL_AWARDED: "Si", COL_SUPPLIER: "No Definido"}))
    assert r["winner_name"] is None
    assert r["winner_identifier"] is None


def test_no_winner_is_read_from_an_unawarded_row():
    r = normalize_row(_row(**{COL_AWARDED: "No", COL_SUPPLIER: "MORARCI GROUP S.A.S"}))
    assert r["winner_name"] is None


def test_award_value_is_captured_when_a_real_winner_is_named():
    r = normalize_row(_row(**{
        COL_AWARDED: "Si",
        COL_SUPPLIER: "MORARCI GROUP S.A.S",
        COL_AWARD_VALUE: "125000000",
    }))
    assert r["value_amount"] == 125000000.0
    assert r["value_currency"] == "COP"


def test_award_value_absent_when_no_real_winner():
    """
    Same guard as winner_name itself: a 'No Definido' supplier must
    never leave a stray value+currency pair attached to nothing.
    """
    r = normalize_row(_row(**{COL_AWARDED: "Si", COL_SUPPLIER: "No Definido", COL_AWARD_VALUE: "125000000"}))
    assert r["value_amount"] is None
    assert r["value_currency"] is None


def test_award_value_missing_from_source_is_none_not_zero():
    r = normalize_row(_row(**{COL_AWARDED: "Si", COL_SUPPLIER: "MORARCI GROUP S.A.S"}))
    assert r["value_amount"] is None
    assert r["value_currency"] is None


# --- batch filtering -------------------------------------------------

def test_batch_keeps_real_military_materiel():
    normalized, failures = normalize_batch([_row()])
    assert failures == []
    assert len(normalized) == 1
    assert normalized[0]["classification_code"] == "25172504"


def test_batch_drops_individual_service_contracts():
    rows = [_row(**{
        COL_NAME: "PRESTACION DE SERVICIOS COMO AUXILIAR DE ENFERMERIA",
        COL_CONTRACT_TYPE: "Prestación de servicios",
    })]
    normalized, failures = normalize_batch(rows)
    assert normalized == []
    assert failures == []


def test_batch_drops_military_hospital_purchases():
    rows = [_row(**{
        COL_ENTITY: "JEFATURA SALUD FUERZA AEREA",
        COL_NAME: "ADQUISICION EQUIPO MEDICO PARA SALAS DE CIRUGIA",
        COL_CATEGORY: "V1.42181602",
    })]
    normalized, _ = normalize_batch(rows)
    assert normalized == []


def test_batch_drops_the_environmental_authority_with_defensa_in_its_name():
    rows = [_row(**{
        COL_ENTITY: "CORPORACIÓN AUTÓNOMA REGIONAL PARA LA DEFENSA DE LA MESETA DE BUCARAMANGA",
    })]
    normalized, _ = normalize_batch(rows)
    assert normalized == []


def test_batch_isolates_malformed_rows_as_failures():
    good = _row()
    bad = _row(**{COL_PROCESS_ID: "", "referencia_del_proceso": ""})
    normalized, failures = normalize_batch([good, bad])
    assert len(normalized) == 1
    assert len(failures) == 1


# --- contact address, added alongside db/migrations/029 -------------

def test_contact_address_built_from_city_and_department():
    r = normalize_row(_row(**{"ciudad_entidad": "Cartagena", "departamento_entidad": "Bolívar"}))
    assert r["contact_address"] == "Cartagena, Bolívar, Colombia"


def test_contact_address_falls_back_to_the_country_alone():
    r = normalize_row(_row(**{"ciudad_entidad": "", "departamento_entidad": ""}))
    assert r["contact_address"] == "Colombia"
