"""Thin client for the FPL public API (fantasy.premierleague.com/api/).

Fetches and validates only — no business logic (that lives in tools/fpl.py
and bot/fpl_jobs.py). The API is unofficial and undocumented, so every call
here is defensive: retries with backoff on 429/5xx, a named-field schema
check on bootstrap-static, and a disk cache so a restart doesn't immediately
re-hit the 3MB bootstrap endpoint.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from datetime import datetime, timezone
from pathlib import Path

import httpx

logger = logging.getLogger(__name__)

_BASE = "https://fantasy.premierleague.com/api/"
_HEADERS = {
    "User-Agent": "RobinFPL/1.0 (personal Telegram assistant; contact: ollie.branston123@gmail.com)",
    "Accept": "application/json",
}
_TIMEOUT = 20.0
_MAX_ATTEMPTS = 3

_BOOTSTRAP_CACHE_PATH = Path("fpl_bootstrap_cache.json")
_BOOTSTRAP_TTL_SEC = 3600


class FPLError(Exception):
    """Raised when the FPL API is unreachable after retries, or its schema doesn't match what we depend on."""


def price_to_gbp(now_cost: int) -> float:
    """Prices in the API are tenths — 155 -> 15.5."""
    return now_cost / 10.0


def parse_utc(iso_str: str) -> datetime:
    """Parse an API timestamp ('2026-08-21T17:30:00Z') into a tz-aware UTC datetime."""
    return datetime.fromisoformat(iso_str.replace("Z", "+00:00")).astimezone(timezone.utc)


async def _get(path: str, params: dict | None = None) -> httpx.Response:
    """GET with exponential backoff on 429/5xx/network errors. 3 attempts, then FPLError.

    A 404 is returned as-is (not retried, not raised) — callers like picks()
    treat it as a normal pre-deadline state, not a failure.
    """
    url = f"{_BASE}{path}"
    delay = 1.0
    last_exc: Exception | None = None

    for attempt in range(1, _MAX_ATTEMPTS + 1):
        try:
            async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
                resp = await client.get(url, headers=_HEADERS, params=params)
        except httpx.HTTPError as exc:
            last_exc = exc
            logger.warning("fpl_client: request error (attempt %d/%d) %s: %s", attempt, _MAX_ATTEMPTS, url, exc)
            if attempt == _MAX_ATTEMPTS:
                break
            await asyncio.sleep(delay)
            delay *= 2
            continue

        if resp.status_code == 404:
            return resp

        if resp.status_code == 429 or resp.status_code >= 500:
            logger.warning(
                "fpl_client: %d on %s (attempt %d/%d)", resp.status_code, url, attempt, _MAX_ATTEMPTS
            )
            if attempt == _MAX_ATTEMPTS:
                raise FPLError(f"FPL API returned {resp.status_code} for {path} after {_MAX_ATTEMPTS} attempts")
            await asyncio.sleep(delay)
            delay *= 2
            continue

        resp.raise_for_status()
        return resp

    raise FPLError(f"FPL API unreachable for {path}: {last_exc}")


# ── bootstrap-static (cached) ────────────────────────────────────────────────


def _read_disk_cache() -> dict | None:
    try:
        raw = json.loads(_BOOTSTRAP_CACHE_PATH.read_text())
        if time.time() - raw["cached_at"] < _BOOTSTRAP_TTL_SEC:
            return raw["data"]
    except (FileNotFoundError, KeyError, json.JSONDecodeError) as exc:
        logger.debug("fpl_client: no usable bootstrap cache: %s", exc)
    return None


def _write_disk_cache(data: dict) -> None:
    try:
        _BOOTSTRAP_CACHE_PATH.write_text(json.dumps({"cached_at": time.time(), "data": data}))
    except OSError as exc:
        logger.warning("fpl_client: failed to write bootstrap cache: %s", exc)


async def bootstrap(force: bool = False) -> dict:
    """bootstrap-static/ — players, prices, teams, gameweek deadlines. Disk-cached, TTL 1h.

    Never call this more than once per hour across all jobs (§1 of
    PHASE1-BRIEF.md) — the disk cache is what enforces that across process
    restarts, not just within one run.
    """
    if not force:
        cached = _read_disk_cache()
        if cached is not None:
            return cached

    try:
        resp = await _get("bootstrap-static/")
        data = resp.json()
        verify_schema(data)
        _write_disk_cache(data)
        return data
    except FPLError:
        # Fall back to a stale cache rather than fail outright — a squad
        # snapshot from 90 minutes ago beats no data at all.
        try:
            raw = json.loads(_BOOTSTRAP_CACHE_PATH.read_text())
            logger.warning("fpl_client: bootstrap fetch failed — serving stale cache from disk")
            return raw["data"]
        except (FileNotFoundError, KeyError, json.JSONDecodeError):
            raise


def verify_schema(data: dict) -> None:
    """Assert the fields this module depends on still exist. Raises FPLError naming the missing field.

    The API changes between seasons without notice — this is what stands
    between that and a silent KeyError at 18:00 on a Friday.
    """
    for top_key in ("events", "teams", "elements"):
        if top_key not in data:
            raise FPLError(f"bootstrap-static schema changed: missing top-level key '{top_key}'")

    if data["events"]:
        event_fields = ("id", "deadline_time", "is_current", "is_next", "finished", "data_checked")
        sample_event = data["events"][0]
        for field in event_fields:
            if field not in sample_event:
                raise FPLError(f"bootstrap-static schema changed: event missing field '{field}'")

    if data["elements"]:
        element_fields = ("id", "web_name", "now_cost", "status", "news", "chance_of_playing_next_round", "team", "element_type")
        sample_element = data["elements"][0]
        for field in element_fields:
            if field not in sample_element:
                raise FPLError(f"bootstrap-static schema changed: element missing field '{field}'")

    if data["teams"]:
        sample_team = data["teams"][0]
        for field in ("id", "name", "short_name"):
            if field not in sample_team:
                raise FPLError(f"bootstrap-static schema changed: team missing field '{field}'")


# ── Other endpoints ───────────────────────────────────────────────────────────


async def fixtures(gw: int | None = None) -> list[dict]:
    """fixtures/ or fixtures/?event={gw} — kickoff times, FDR, finished flags."""
    params = {"event": gw} if gw is not None else None
    resp = await _get("fixtures/", params=params)
    return resp.json()


async def entry(team_id: int) -> dict:
    """entry/{id}/ — team metadata."""
    resp = await _get(f"entry/{team_id}/")
    return resp.json()


async def entry_history(team_id: int) -> dict:
    """entry/{id}/history/ — GW-by-GW points, rank, transfers, chips used, team value."""
    resp = await _get(f"entry/{team_id}/history/")
    return resp.json()


async def picks(team_id: int, gw: int) -> dict | None:
    """entry/{id}/event/{gw}/picks/ — the actual squad for that GW.

    Returns None on 404, which is the normal state before that GW's deadline
    has passed — callers must not treat it as an error.
    """
    resp = await _get(f"entry/{team_id}/event/{gw}/picks/")
    if resp.status_code == 404:
        return None
    return resp.json()


async def league(league_id: int) -> dict:
    """leagues-classic/{id}/standings/ — mini-league table (page 1, 50 entries)."""
    resp = await _get(f"leagues-classic/{league_id}/standings/")
    return resp.json()


async def transfers(team_id: int) -> list[dict]:
    """entry/{id}/transfers/ — full transfer history, each with element_in/out and
    element_in_cost/element_out_cost (tenths). Public and unauthenticated — unlike
    `selling_price`, which only appears on the authenticated my-team endpoint Robin
    doesn't have access to (FPL-CONTEXT.md §3.3). services/fpl_optimiser.py uses this
    to derive each owned player's cost basis for the selling-price rule."""
    resp = await _get(f"entry/{team_id}/transfers/")
    return resp.json()
