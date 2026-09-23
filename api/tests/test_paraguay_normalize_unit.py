"""
Pure unit tests for app/paraguay_normalize.py — no DB, no network.
Fixtures shaped after real DNCP Paraguay compiledRelease objects
fetched live on 2026-09-18 (see db/migrations/039's header).
"""

from app.paraguay_normalize import (
    _ui_link_from_ocid,
    extract_classification_from_detail,
    extract_eligibility_from_detail,
    is_defence_buyer,
    is_non_materiel_category,
    normalize_batch,
    normalize_record,
    parse_unspsc,
)


def _compiled_release(
    buyer_name="Comando del Ejercito Uoc 2 / Ministerio de Defensa Nacional",
    title="ADQUISICION DE REPUESTOS VARIOS PARA VEHICULOS COMPONENTES DEL COMANDO DEL EJERCITO",
    category="Bienes - Sistemas Equipos y Componentes de Distribucion y Acondicionamiento",
    status="En Convocatoria (Abierta)",
    classification_id="25172504-001",
    ocid="ocds-03ad3f-483403-1",
    supplier_name="Helisul Táxi Aéreo Ltda",
    with_award=True,
    with_items=True,
):
    return {
        "ocid": ocid,
        "tender": {
            "title": title,
            "statusDetails": status,
            "mainProcurementCategoryDetails": category,
            "procuringEntity": {"name": buyer_name},
            "items": (
                [{"classification": {"scheme": "catalogoNivel5DNCP", "id": classification_id}}]
                if with_items
                else []
            ),
        },
        "buyer": {"name": buyer_name},
        "awards": [{"suppliers": [{"name": supplier_name}]}] if with_award else [],
    }


def test_is_defence_buyer_matches_compound_command_names():
    assert is_defence_buyer("Comando del Ejercito Uoc 2 / Ministerio de Defensa Nacional") is True
    assert is_defence_buyer("Ministerio de Defensa Nacional (MDN)") is True


def test_is_defence_buyer_rejects_other_agencies():
    assert is_defence_buyer("Administración Nacional de Electricidad (ANDE)") is False
    assert is_defence_buyer(None) is False
    assert is_defence_buyer("") is False


def test_is_non_materiel_category_catches_kitchen_and_food_and_office():
    assert is_non_materiel_category("Bienes - Alimentos Bebidas y Tabaco") is True
    assert is_non_materiel_category("Bienes - Muebles Accesorios, Electrodomesticos y Productos Electronicos de Consumo") is True
    assert is_non_materiel_category("Bienes - Equipos Accesorios y Suministros de Oficina") is True
    assert is_non_materiel_category("Servicios - Servicios Financieros y de Seguros") is True


def test_is_non_materiel_category_catches_it_services_and_ceremonial():
    assert is_non_materiel_category("Servicios - Servicios basados en ingenieria investigacion y tecnologia") is True
    assert is_non_materiel_category(
        "Servicios - Materiales didacticos profesionales, Instrumentos musicales juegos, juguetes, artesania"
    ) is True


def test_is_non_materiel_category_keeps_real_materiel():
    assert is_non_materiel_category("Bienes - Vehiculos Comerciales, Militares y Particulares - Accesorios y Componentes") is False
    assert is_non_materiel_category("Bienes - Sistemas Equipos y Componentes de Distribucion y Acondicionamiento") is False
    assert is_non_materiel_category(None) is False


def test_parse_unspsc_strips_dncp_suffix():
    assert parse_unspsc("23151607-001") == "23151607"
    assert parse_unspsc("24101602-9999") == "24101602"


def test_parse_unspsc_bare_code_without_suffix():
    assert parse_unspsc("39121011") == "39121011"


def test_parse_unspsc_rejects_non_codes():
    assert parse_unspsc(None) is None
    assert parse_unspsc("") is None
    assert parse_unspsc("N/A") is None


def test_normalize_record_keeps_defence_materiel():
    record = normalize_record(_compiled_release())
    assert record is not None
    assert record["organization_name"] == "Comando del Ejercito Uoc 2 / Ministerio de Defensa Nacional"
    assert record["winner_name"] == "Helisul Táxi Aéreo Ltda"
    assert record["stage"] == "rfp_issued"
    assert record["country"] == "Paraguay"


def test_normalize_record_never_sets_classification_from_search_shape():
    """
    /search/processes's compiledRelease carries no items at all —
    confirmed live. normalize_record must never claim a code from it;
    that only comes from a separate detail fetch (see
    extract_classification_from_detail).
    """
    record = normalize_record(_compiled_release())
    assert record["classification_code"] is None
    assert record["classification_scheme"] is None


def _detail_payload(items):
    return {"records": [{"ocid": "x", "releases": [], "compiledRelease": {"tender": {"items": items}}}]}


def test_extract_classification_from_detail_prefers_additional_classifications_unspsc():
    detail = _detail_payload([
        {
            "classification": {"scheme": "catalogoNivel5DNCP", "id": "73161607-001"},
            "additionalClassifications": [{"scheme": "UNSPSC", "id": "73161607"}],
        }
    ])
    assert extract_classification_from_detail(detail) == "73161607"


def test_extract_classification_from_detail_falls_back_to_catalogo_suffix_strip():
    detail = _detail_payload([{"classification": {"scheme": "catalogoNivel5DNCP", "id": "23151607-001"}}])
    assert extract_classification_from_detail(detail) == "23151607"


def test_extract_classification_from_detail_no_records():
    assert extract_classification_from_detail({"records": []}) is None
    assert extract_classification_from_detail({}) is None


def test_extract_classification_from_detail_no_items():
    detail = _detail_payload([])
    assert extract_classification_from_detail(detail) is None


def _detail_payload_with_eligibility(eligibility_text):
    return {"records": [{"ocid": "x", "releases": [], "compiledRelease": {"tender": {"eligibilityCriteria": eligibility_text}}}]}


def test_extract_eligibility_from_detail_ninguna_is_not_a_restriction():
    """
    Eligibility/Backup Phase 1 (2026-09) — live-verified real DNCP
    value: "Restricciones: ninguna" ("Restrictions: none") is the
    real, literal text this field carries when a tender is NOT
    restricted, checked case-insensitively for "ninguna" rather than
    an exact-string match (source text casing wasn't confirmed
    consistent across every real notice).
    """
    code, desc = extract_eligibility_from_detail(_detail_payload_with_eligibility("Restricciones: ninguna"))
    assert code is None
    assert desc is None


def test_extract_eligibility_from_detail_missing_field_is_not_a_restriction():
    code, desc = extract_eligibility_from_detail({"records": [{"compiledRelease": {"tender": {}}}]})
    assert code is None
    code2, desc2 = extract_eligibility_from_detail({"records": []})
    assert code2 is None


def test_extract_eligibility_from_detail_no_aplica_is_not_a_restriction():
    """
    A real false positive, caught by the FIRST genuine live re-
    ingestion run (2026-09-22), not assumed away: "Restricciones: NO
    APLICA" ("N/A") was being surfaced as if it were a stated
    restriction, because the original check only looked for
    "ninguna". Real DNCP data carries several different real
    "actually not restricted" phrasings, not just one.
    """
    code, desc = extract_eligibility_from_detail(_detail_payload_with_eligibility("Restricciones: NO APLICA"))
    assert code is None


def test_extract_eligibility_from_detail_standard_boilerplate_is_not_a_restriction():
    """
    Real standard Paraguayan legal boilerplate found live (2026-09-22):
    "all bidders who meet the normal requirements may participate,
    citing Ley 7021/2022 Art. 21" states the DEFAULT baseline
    eligibility everyone must meet — not a restriction to a specific
    bidder category. Also confirmed live: the SAME boilerplate appears
    both with and without Spanish accents across different real rows,
    which is why this is checked via _fold (accent-insensitive), not
    an exact string match.
    """
    accented = "Restricciones: Podrán participar todos los oferentes que cumplan con los requisitos Técnicos, legales y económicos..."
    unaccented = "Restricciones: Podran participar todos los oferentes que cumplan con los requisitos Tecnicos, legales y economicos..."
    assert extract_eligibility_from_detail(_detail_payload_with_eligibility(accented))[0] is None
    assert extract_eligibility_from_detail(_detail_payload_with_eligibility(unaccented))[0] is None


def test_extract_eligibility_from_detail_surfaces_a_real_stated_restriction():
    """
    A genuinely-restricted real example was never observed live (see
    this function's own docstring) — this pins the honest fallback
    behaviour for if/when one is: the raw source text is shown as-is,
    not translated into a category this project hasn't verified.
    """
    code, desc = extract_eligibility_from_detail(_detail_payload_with_eligibility("Restricciones: solo MIPYMES"))
    assert code == "Restricciones: solo MIPYMES"
    assert desc == code


def test_normalize_record_drops_non_defence_buyer():
    assert normalize_record(_compiled_release(buyer_name="Administración Nacional de Electricidad (ANDE)")) is None


def test_normalize_record_drops_non_materiel_category_even_for_defence_buyer():
    record = _compiled_release(
        title="ADQUISICION DE UTENSILIOS DE COCINA Y COMEDOR PARA EL COMANDO DEL EJERCITO",
        category="Bienes - Muebles Accesorios, Electrodomesticos y Productos Electronicos de Consumo",
    )
    assert normalize_record(record) is None


def test_normalize_record_drops_cancelled_status():
    record = _compiled_release(status="Desierto")
    assert normalize_record(record) is None


def test_normalize_record_maps_awarded_status():
    record = _compiled_release(status="Adjudicado")
    assert normalize_record(record)["stage"] == "contract_awarded"


def test_normalize_record_falls_back_to_ocid_when_no_title():
    record = _compiled_release(title="")
    assert normalize_record(record)["name"] == "DNCP Paraguay ocds-03ad3f-483403-1"


def test_normalize_record_returns_none_without_ocid():
    release = _compiled_release()
    release["ocid"] = ""
    assert normalize_record(release) is None


def test_normalize_record_no_classification_when_no_items():
    record = normalize_record(_compiled_release(with_items=False))
    assert record["classification_code"] is None
    assert record["classification_scheme"] is None


def test_normalize_record_no_winner_when_no_award():
    record = normalize_record(_compiled_release(with_award=False))
    assert record["winner_name"] is None


def test_normalize_batch_filters_and_reports_no_failures_for_clean_input():
    releases = [
        _compiled_release(),
        _compiled_release(buyer_name="Administración Nacional de Electricidad (ANDE)", ocid="ocds-xyz-2"),
    ]
    normalized, failures = normalize_batch(releases)
    assert len(normalized) == 1
    assert failures == []


def test_normalize_batch_handles_malformed_release_as_failure_not_crash():
    releases = [{"ocid": "bad", "tender": "not-a-dict"}]
    normalized, failures = normalize_batch(releases)
    assert normalized == []
    assert len(failures) == 1


def test_ui_link_from_ocid_extracts_id_llamado():
    """
    Live-verified 2026-09: DNCP's own ocid format is
    "ocds-03ad3f-{id_llamado}-{n}" — the middle segment is the
    portal's real call ID, confirmed by loading the resulting URL
    against our own real ingested data and getting a genuine 200 with
    real licitación-cycle content back, not an error page.
    """
    assert _ui_link_from_ocid("ocds-03ad3f-483403-1") == (
        "https://www.contrataciones.gov.py/datos/visualizaciones/"
        "ciclo_licitacion/index.html?id_llamado=483403"
    )


def test_ui_link_from_ocid_none_for_malformed_or_missing_ocid():
    """
    An unexpected ocid shape must degrade to no link rather than a
    broken/guessed one — same rule AusTender's ui_link:None already
    established for this project.
    """
    assert _ui_link_from_ocid("bad") is None
    assert _ui_link_from_ocid("") is None
    assert _ui_link_from_ocid(None) is None


def test_normalize_record_sets_ui_link():
    record = normalize_record(_compiled_release())
    assert record["ui_link"] == (
        "https://www.contrataciones.gov.py/datos/visualizaciones/"
        "ciclo_licitacion/index.html?id_llamado=483403"
    )
