"""
Pure unit tests for app/australia_normalize.py — no DB, no network.
Fixtures are shaped after real AusTender OCDS releases fetched live
on 2026-09-17 (see db/migrations/038's header for what was verified).
"""

from app.australia_normalize import is_defence_buyer, normalize_batch, normalize_release


def _release(
    buyer_name="Department of Defence",
    supplier_name="Beak Engineering (Aust) Pty Ltd",
    supplier_abn="12345678901",
    description="Pump Repair",
    classification_id="40150000",
    classification_scheme="UNSPSC",
    contract_id="CN4274566",
    release_id="prod-abc123",
    with_award=True,
    with_contracts=True,
    value_amount="115954.77",
    value_currency="AUD",
):
    parties = [
        {
            "name": buyer_name,
            "roles": ["procuringEntity"],
            "address": {},
            "contactPoint": {"email": "tenders@finance.gov.au"},
        },
        {
            "name": supplier_name,
            "roles": ["supplier"],
            "additionalIdentifiers": [{"id": supplier_abn, "scheme": "AU-ABN"}],
        },
    ]
    release = {
        "id": release_id,
        "date": "2026-09-04T07:02:00Z",
        "parties": parties,
        "awards": (
            [{"suppliers": [{"name": supplier_name}], "status": "active"}]
            if with_award
            else []
        ),
        "contracts": (
            [
                {
                    "id": contract_id,
                    "description": description,
                    "title": "5700027469",
                    "dateSigned": "2026-09-04T07:02:00Z",
                    "items": [
                        {"classification": {"scheme": classification_scheme, "id": classification_id}}
                    ]
                    if classification_id
                    else [],
                    "value": {"currency": value_currency, "amount": value_amount},
                }
            ]
            if with_contracts
            else []
        ),
    }
    return release


def test_is_defence_buyer_exact_match():
    assert is_defence_buyer("Department of Defence") is True


def test_is_defence_buyer_rejects_other_agencies():
    assert is_defence_buyer("Australian Federal Police") is False
    assert is_defence_buyer("Department of Social Services") is False
    assert is_defence_buyer(None) is False
    assert is_defence_buyer("") is False


def test_normalize_release_keeps_defence_buyer():
    record = normalize_release(_release())
    assert record is not None
    assert record["organization_name"] == "Department of Defence"
    assert record["name"] == "Pump Repair"
    assert record["classification_code"] == "40150000"
    assert record["classification_scheme"] == "UNSPSC"
    assert record["winner_name"] == "Beak Engineering (Aust) Pty Ltd"
    assert record["winner_identifier"] == "12345678901"
    assert record["external_ref"] == "CN4274566"
    assert record["country"] == "Australia"


def test_normalize_release_drops_non_defence_buyer():
    assert normalize_release(_release(buyer_name="Australian Federal Police")) is None


def test_normalize_release_returns_none_without_contracts():
    """
    A real, expected shape — a planning or amendment-only release with
    no contracts array has nothing to ingest, not a parse failure.
    """
    assert normalize_release(_release(with_contracts=False)) is None


def test_normalize_release_falls_back_to_title_when_no_description():
    release = _release(description="")
    assert normalize_release(release)["name"] == "5700027469"


def test_normalize_release_falls_back_to_release_id_when_no_contract_id():
    release = _release(contract_id="")
    record = normalize_release(release)
    assert record["external_ref"] == "prod-abc123"


def test_normalize_release_no_classification_code():
    record = normalize_release(_release(classification_id=None))
    assert record["classification_code"] is None
    assert record["classification_scheme"] is None


def test_normalize_release_winner_falls_back_to_supplier_party_when_no_award():
    record = normalize_release(_release(with_award=False))
    assert record["winner_name"] == "Beak Engineering (Aust) Pty Ltd"


def test_normalize_release_contact_fields_always_blank():
    """
    Deliberately never populated — see app/australia_normalize.py's
    docstring: the buyer's address/contactPoint were found live to be
    an empty object and a shared department-wide inbox on every row,
    never anything genuinely specific to the buying unit.
    """
    record = normalize_release(_release())
    assert record["contact_address"] is None
    assert record["contact_name"] is None
    assert record["contact_email"] is None
    assert record["contact_phone"] is None


def test_normalize_batch_filters_and_reports_no_failures_for_clean_input():
    releases = [_release(), _release(buyer_name="Department of Social Services", release_id="r2")]
    normalized, failures = normalize_batch(releases)
    assert len(normalized) == 1
    assert failures == []


def test_normalize_batch_handles_malformed_release_as_failure_not_crash():
    releases = [{"id": "bad", "parties": "not-a-list", "contracts": [{"id": "x"}]}]
    normalized, failures = normalize_batch(releases)
    assert normalized == []
    assert len(failures) == 1


def test_normalize_release_reads_contract_value():
    record = normalize_release(_release(value_amount="115954.77", value_currency="AUD"))
    assert record["value_amount"] == 115954.77
    assert record["value_currency"] == "AUD"


def test_normalize_release_no_value_when_not_published():
    record = normalize_release(_release(value_amount=None, value_currency=None))
    assert record["value_amount"] is None
    assert record["value_currency"] is None


def test_normalize_release_value_amount_bad_number_is_none_not_a_crash():
    record = normalize_release(_release(value_amount="not-a-number"))
    assert record["value_amount"] is None
