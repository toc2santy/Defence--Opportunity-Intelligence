"""
Unit tests for the eTenders South Africa fetch layer — no network.

These exist because of a real, silent outage: every run of this source
from 2026-08-24 onwards returned zero records, reporting only
`request failed — ` with nothing after the dash. Three separate
defects were behind it, and each has a test here so the same silence
cannot come back unnoticed.
"""

import asyncio
import sys
from pathlib import Path

import httpx
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.south_africa_ingestion import (  # noqa: E402
    SOUTH_AFRICA_MAX_PAGES,
    SOUTH_AFRICA_PAGE_SIZE,
    SOUTH_AFRICA_TIMEOUT_SECONDS,
    _decode,
    _request_error,
    fetch_all_releases,
)


def test_timeout_and_page_size_stay_within_the_measured_envelope():
    """
    Measured live against the API: PageSize=200 never returned (still
    timing out at 130s), while PageSize=50 answered in 59.4s. So the
    page size must stay well under 200 and the timeout well over a
    minute — the original 200/45s combination could not succeed.
    """
    assert SOUTH_AFRICA_PAGE_SIZE <= 50
    assert SOUTH_AFRICA_TIMEOUT_SECONDS >= 90
    assert SOUTH_AFRICA_MAX_PAGES >= 1


def test_request_error_never_reports_an_empty_reason():
    """
    The original bug's visible symptom: httpx timeout exceptions can
    carry an empty str(), so the run recorded `request failed — ` and
    gave no way to tell a timeout from a DNS failure.
    """
    empty = httpx.ReadTimeout("")
    message = _request_error(empty)
    assert message.strip().endswith("ReadTimeout")
    assert not message.strip().endswith("—")

    detailed = httpx.ConnectError("nodename nor servname provided")
    assert "nodename nor servname provided" in _request_error(detailed)


def test_decode_turns_a_non_json_200_into_a_readable_error():
    """
    Observed live: page 3 of a 30-day window answered HTTP 200 with a
    body that was not JSON. json.JSONDecodeError is neither an
    HTTPStatusError nor a RequestError, so it escaped both handlers
    and took the whole run down instead of being recorded as the
    partial failure it is.
    """
    response = httpx.Response(200, text="<html>Service Unavailable</html>")
    with pytest.raises(ValueError) as excinfo:
        _decode(response)
    assert "not JSON" in str(excinfo.value)

    ok = httpx.Response(200, json={"releases": []})
    assert _decode(ok) == {"releases": []}


def test_a_broken_later_page_keeps_the_pages_already_fetched(monkeypatch):
    """
    The partial-failure contract every source in this project shares:
    losing page 3 must not throw away pages 1 and 2.
    """
    request = httpx.Request("GET", "https://ocds-api.etenders.gov.za/api/OCDSReleases")
    pages = [
        httpx.Response(200, json={"releases": [{"ocid": "za-1"}], "links": {"next": "u2"}}, request=request),
        httpx.Response(200, json={"releases": [{"ocid": "za-2"}], "links": {"next": "u3"}}, request=request),
        httpx.Response(200, text="not json at all", request=request),
    ]
    calls = {"n": 0}

    async def fake_get(self, url, **kwargs):
        response = pages[calls["n"]]
        calls["n"] += 1
        return response

    monkeypatch.setattr(httpx.AsyncClient, "get", fake_get)
    releases, error = asyncio.run(fetch_all_releases("2026-08-16", "2026-09-15"))

    assert [r["ocid"] for r in releases] == ["za-1", "za-2"]
    assert error is not None and "not JSON" in error


def test_pagination_stops_at_the_page_cap(monkeypatch):
    """
    At ~60s per page an unbounded crawl would hold one HTTP request
    open for many minutes.
    """
    request = httpx.Request("GET", "https://ocds-api.etenders.gov.za/api/OCDSReleases")

    async def fake_get(self, url, **kwargs):
        return httpx.Response(
            200, json={"releases": [{"ocid": "za"}], "links": {"next": "next-url"}}, request=request
        )

    monkeypatch.setattr(httpx.AsyncClient, "get", fake_get)
    releases, error = asyncio.run(fetch_all_releases("2026-08-16", "2026-09-15"))

    assert error is None
    assert len(releases) == SOUTH_AFRICA_MAX_PAGES
