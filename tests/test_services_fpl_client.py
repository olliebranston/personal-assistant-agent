"""Tests for services/fpl_client.py — no real network calls.

httpx.AsyncClient is monkeypatched to a fake that returns a scripted queue
of responses, mirroring the pattern in tests/test_services_news.py.
"""

from __future__ import annotations

import json

import pytest

import services.fpl_client as fpl_client
from services.fpl_client import FPLError, parse_utc, price_to_gbp, verify_schema


# ── Fakes ─────────────────────────────────────────────────────────────────────


class _FakeResponse:
    def __init__(self, status_code: int = 200, json_data=None):
        self.status_code = status_code
        self._json = json_data if json_data is not None else {}

    def json(self):
        return self._json

    def raise_for_status(self):
        if self.status_code >= 400:
            raise Exception(f"HTTP {self.status_code}")


class _FakeAsyncClient:
    """Class-level queue of responses, popped one per .get() call."""

    responses: list[_FakeResponse] = []
    calls: list[str] = []

    def __init__(self, *args, **kwargs):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc_info):
        return False

    async def get(self, url, headers=None, params=None):
        _FakeAsyncClient.calls.append(url)
        return _FakeAsyncClient.responses.pop(0)


@pytest.fixture(autouse=True)
def _reset_fake_client(monkeypatch):
    _FakeAsyncClient.responses = []
    _FakeAsyncClient.calls = []
    monkeypatch.setattr(fpl_client.httpx, "AsyncClient", _FakeAsyncClient)

    async def _no_sleep(seconds):
        return None

    monkeypatch.setattr(fpl_client.asyncio, "sleep", _no_sleep)


@pytest.fixture(autouse=True)
def _isolate_disk_cache(tmp_path, monkeypatch):
    monkeypatch.setattr(fpl_client, "_BOOTSTRAP_CACHE_PATH", tmp_path / "fpl_bootstrap_cache.json")


_VALID_BOOTSTRAP = {
    "events": [{"id": 1, "deadline_time": "2026-08-21T17:30:00Z", "is_current": False, "is_next": True, "finished": False, "data_checked": False}],
    "teams": [{"id": 1, "name": "Arsenal", "short_name": "ARS"}],
    "elements": [{"id": 1, "web_name": "Saka", "now_cost": 100, "status": "a", "news": "", "chance_of_playing_next_round": None, "team": 1, "element_type": 3}],
}


# ── price_to_gbp / parse_utc ──────────────────────────────────────────────────


def test_price_to_gbp():
    assert price_to_gbp(155) == 15.5
    assert price_to_gbp(40) == 4.0


def test_parse_utc_handles_zulu_suffix():
    dt = parse_utc("2026-08-21T17:30:00Z")
    assert dt.year == 2026 and dt.hour == 17 and dt.tzinfo is not None


# ── verify_schema ──────────────────────────────────────────────────────────────


def test_verify_schema_passes_for_valid_data():
    verify_schema(_VALID_BOOTSTRAP)  # no raise


def test_verify_schema_raises_and_names_missing_top_level_key():
    broken = {k: v for k, v in _VALID_BOOTSTRAP.items() if k != "elements"}
    with pytest.raises(FPLError, match="elements"):
        verify_schema(broken)


def test_verify_schema_raises_and_names_missing_event_field():
    broken = json.loads(json.dumps(_VALID_BOOTSTRAP))
    del broken["events"][0]["deadline_time"]
    with pytest.raises(FPLError, match="deadline_time"):
        verify_schema(broken)


def test_verify_schema_raises_and_names_missing_element_field():
    broken = json.loads(json.dumps(_VALID_BOOTSTRAP))
    del broken["elements"][0]["now_cost"]
    with pytest.raises(FPLError, match="now_cost"):
        verify_schema(broken)


# ── retry / backoff ────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_get_retries_on_429_then_succeeds():
    _FakeAsyncClient.responses = [
        _FakeResponse(status_code=429),
        _FakeResponse(status_code=429),
        _FakeResponse(status_code=200, json_data={"ok": True}),
    ]
    resp = await fpl_client._get("fixtures/")
    assert resp.json() == {"ok": True}
    assert len(_FakeAsyncClient.calls) == 3


@pytest.mark.asyncio
async def test_get_raises_fplerror_after_max_attempts_of_500s():
    _FakeAsyncClient.responses = [_FakeResponse(status_code=500)] * 3
    with pytest.raises(FPLError):
        await fpl_client._get("fixtures/")
    assert len(_FakeAsyncClient.calls) == 3


@pytest.mark.asyncio
async def test_transfers_returns_the_json_list():
    _FakeAsyncClient.responses = [_FakeResponse(status_code=200, json_data=[{"element_in": 1, "element_in_cost": 60}])]
    result = await fpl_client.transfers(6748844)
    assert result == [{"element_in": 1, "element_in_cost": 60}]


@pytest.mark.asyncio
async def test_picks_returns_none_on_404_without_raising():
    _FakeAsyncClient.responses = [_FakeResponse(status_code=404)]
    result = await fpl_client.picks(12345, 1)
    assert result is None
    assert len(_FakeAsyncClient.calls) == 1  # 404 isn't retried


# ── bootstrap disk cache ────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_bootstrap_fetches_once_and_serves_disk_cache_within_ttl():
    _FakeAsyncClient.responses = [_FakeResponse(status_code=200, json_data=_VALID_BOOTSTRAP)]

    first = await fpl_client.bootstrap()
    second = await fpl_client.bootstrap()

    assert first == _VALID_BOOTSTRAP
    assert second == _VALID_BOOTSTRAP
    assert len(_FakeAsyncClient.calls) == 1  # second call served from disk cache


@pytest.mark.asyncio
async def test_bootstrap_force_bypasses_cache():
    _FakeAsyncClient.responses = [
        _FakeResponse(status_code=200, json_data=_VALID_BOOTSTRAP),
        _FakeResponse(status_code=200, json_data=_VALID_BOOTSTRAP),
    ]

    await fpl_client.bootstrap()
    await fpl_client.bootstrap(force=True)

    assert len(_FakeAsyncClient.calls) == 2


@pytest.mark.asyncio
async def test_bootstrap_falls_back_to_stale_cache_on_fetch_failure():
    _FakeAsyncClient.responses = [_FakeResponse(status_code=200, json_data=_VALID_BOOTSTRAP)]
    await fpl_client.bootstrap()

    _FakeAsyncClient.responses = [_FakeResponse(status_code=500)] * 3
    result = await fpl_client.bootstrap(force=True)

    assert result == _VALID_BOOTSTRAP  # served stale rather than raising
