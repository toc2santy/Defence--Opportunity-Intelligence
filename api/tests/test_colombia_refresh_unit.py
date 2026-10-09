"""Pure decision logic of the SECOP II status refresh (no network, no DB)."""

from app.colombia_refresh import plan_changes

STORED_OPEN = {"stage": "rfp_issued", "response_deadline": None}


def _fresh(stage, deadline=None):
    return {"stage": stage, "response_deadline": deadline}


def test_awarded_open_tender_gets_its_stage_corrected_and_deadline_filled():
    plan = plan_changes(STORED_OPEN, _fresh("contract_awarded", "2026-08-18T00:00:00"))
    assert plan == {"outcome": "changed", "changes": {"stage": "contract_awarded", "response_deadline": "2026-08-18T00:00:00"}}


def test_nothing_to_change_is_reported_as_unchanged():
    assert plan_changes({"stage": "contract_awarded", "response_deadline": "2026-08-18T00:00:00"},
                        _fresh("contract_awarded", "2026-08-18T00:00:00"))["outcome"] == "unchanged"


def test_a_missing_fresh_deadline_never_erases_a_stored_one():
    plan = plan_changes({"stage": "rfp_issued", "response_deadline": "2026-09-01T00:00:00"}, _fresh("rfp_issued", None))
    assert plan["outcome"] == "unchanged"


def test_rows_gone_or_cancelled_are_only_counted_not_changed():
    assert plan_changes(STORED_OPEN, None) == {"outcome": "not_found"}
    assert plan_changes(STORED_OPEN, _fresh(None)) == {"outcome": "now_cancelled_or_void"}


# ---- Paraguay fetch resilience (DNCP drops connections and truncates JSON) ----
import asyncio

import httpx

from app import source_reconcile


class _Resp:
    def __init__(self, status=200, payload=None, bad_json=False):
        self.status_code, self._payload, self._bad = status, payload, bad_json

    def raise_for_status(self):
        if self.status_code >= 400:
            raise httpx.HTTPStatusError("x", request=None, response=None)

    def json(self):
        if self._bad:
            raise ValueError("truncated")
        return self._payload


class _FlakyClient:
    def __init__(self, script):
        self.script, self.calls = list(script), 0

    async def get(self, *a, **k):
        self.calls += 1
        step = self.script.pop(0)
        if isinstance(step, Exception):
            raise step
        return step


def _run(coro):
    return asyncio.run(coro)


def test_paraguay_record_fetch_retries_a_dropped_connection_then_succeeds(monkeypatch):
    monkeypatch.setattr(source_reconcile, "PARAGUAY_RETRY_BACKOFF_SECONDS", 0)
    ok = _Resp(200, {"records": [{"compiledRelease": {"tender": {"statusDetails": "Adjudicada"}}}]})
    client = _FlakyClient([httpx.RemoteProtocolError("Server disconnected without sending a response."), _Resp(200, bad_json=True), ok])
    payload = _run(source_reconcile._paraguay_get_record(client, "ocds-x-1-1"))
    assert payload["records"] and client.calls == 3


def test_paraguay_record_that_keeps_failing_is_skipped_not_fatal(monkeypatch):
    monkeypatch.setattr(source_reconcile, "PARAGUAY_RETRY_BACKOFF_SECONDS", 0)
    client = _FlakyClient([httpx.RemoteProtocolError("down")] * source_reconcile.PARAGUAY_ATTEMPTS)
    assert _run(source_reconcile._paraguay_get_record(client, "ocds-x-1-1")) is None
    assert client.calls == source_reconcile.PARAGUAY_ATTEMPTS


def test_paraguay_404_means_the_record_is_gone_without_retrying():
    client = _FlakyClient([_Resp(404)])
    assert _run(source_reconcile._paraguay_get_record(client, "ocds-x-1-1")) is None
    assert client.calls == 1
