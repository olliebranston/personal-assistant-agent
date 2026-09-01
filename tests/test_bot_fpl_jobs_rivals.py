"""Tests for bot/fpl_jobs.py's rival sync — PHASE3-BRIEF.md Step 4 /
PHASE3-ADDENDUM.md §0. _sync_rivals doesn't need bootstrap/element data, so
these exercise it directly against a bare in-memory conn rather than going
through the full _fpl_tick machinery.
"""

from __future__ import annotations

import sqlite3
from datetime import timedelta

import pytest

import bot.fpl_jobs as fpl_jobs
import config
from storage.models import (
    GAMEWEEK_DDL,
    RIVAL_HISTORY_DDL,
    RIVAL_PICKS_DDL,
    RIVALS_DDL,
    get_active_rivals,
    get_all_rivals,
    get_rival_history_row,
    get_rival_picks,
    upsert_gameweeks,
)


def _make_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    for ddl in (GAMEWEEK_DDL, RIVALS_DDL, RIVAL_PICKS_DDL, RIVAL_HISTORY_DDL):
        conn.execute(ddl)
    conn.commit()
    return conn


def _iso(dt) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def _seed_finished_gws(conn, n: int) -> None:
    """GW1..n, all with deadlines safely in the past."""
    now = fpl_jobs.now_utc()
    upsert_gameweeks(conn, [
        {
            "gw": gw, "deadline_utc": _iso(now - timedelta(days=(n - gw + 1) * 7)),
            "is_current": 0, "is_next": 0, "finished": 1, "data_checked": 1,
        }
        for gw in range(1, n + 1)
    ])


def _picks_payload(spec: dict[int, int], points_on_bench: int = 0, active_chip=None) -> dict:
    return {
        "picks": [{"element": eid, "multiplier": m} for eid, m in spec.items()],
        "entry_history": {"points": 60, "total_points": 60, "overall_rank": 100000, "points_on_bench": points_on_bench},
        "active_chip": active_chip,
    }


@pytest.fixture
def _wired(monkeypatch):
    conn = _make_conn()
    monkeypatch.setattr(config, "FPL_TEAM_ID", 999999)  # Ollie's id — must never appear as a rival
    monkeypatch.setattr(config, "FPL_LEAGUE_ID", 1342398)
    return conn


@pytest.mark.asyncio
async def test_ollie_himself_is_excluded_from_the_rivals_table(monkeypatch, _wired):
    conn = _wired
    _seed_finished_gws(conn, 1)

    async def _league(league_id):
        return {"standings": {"results": [
            {"entry": 999999, "entry_name": "Ollie", "player_name": "Ollie Branston"},
            {"entry": 111, "entry_name": "Rival", "player_name": "A Rival"},
        ]}}

    async def _entry(entry_id):
        return {"started_event": 1}

    async def _picks(entry_id, gw):
        return _picks_payload({1: 1, 2: 2})

    monkeypatch.setattr(fpl_jobs.fpl_client, "league", _league)
    monkeypatch.setattr(fpl_jobs.fpl_client, "entry", _entry)
    monkeypatch.setattr(fpl_jobs.fpl_client, "picks", _picks)

    await fpl_jobs._sync_rivals(conn)

    ids = {r["entry_id"] for r in get_all_rivals(conn)}
    assert 999999 not in ids
    assert 111 in ids


@pytest.mark.asyncio
async def test_backfill_uses_started_event_not_first_seen_date(monkeypatch, _wired):
    """PHASE3-ADDENDUM.md §0's Oscar Holt case: a rival appearing in the
    standings for the first time this tick can have a full history behind
    them — started_event=1 must pull GW1 and GW2, not just the gw they were
    first noticed in."""
    conn = _wired
    _seed_finished_gws(conn, 2)

    async def _league(league_id):
        return {"standings": {"results": [
            {"entry": 6157646, "entry_name": "Oscar's Team", "player_name": "Oscar Holt"},
        ]}}

    async def _entry(entry_id):
        return {"started_event": 1}  # played since GW1 despite joining the league late

    fetched_gws = []

    async def _picks(entry_id, gw):
        fetched_gws.append(gw)
        return _picks_payload({1: 1}, points_on_bench=3)

    monkeypatch.setattr(fpl_jobs.fpl_client, "league", _league)
    monkeypatch.setattr(fpl_jobs.fpl_client, "entry", _entry)
    monkeypatch.setattr(fpl_jobs.fpl_client, "picks", _picks)

    await fpl_jobs._sync_rivals(conn)

    assert sorted(fetched_gws) == [1, 2]
    assert get_rival_history_row(conn, 1, 6157646) is not None
    assert get_rival_history_row(conn, 2, 6157646) is not None


@pytest.mark.asyncio
async def test_per_rival_failure_does_not_abort_the_others(monkeypatch, _wired):
    conn = _wired
    _seed_finished_gws(conn, 1)

    async def _league(league_id):
        return {"standings": {"results": [
            {"entry": 111, "entry_name": "Broken", "player_name": "Breaks"},
            {"entry": 222, "entry_name": "Fine", "player_name": "Works"},
        ]}}

    async def _entry(entry_id):
        return {"started_event": 1}

    async def _picks(entry_id, gw):
        if entry_id == 111:
            raise fpl_jobs.fpl_client.FPLError("simulated outage for this rival only")
        return _picks_payload({5: 1})

    monkeypatch.setattr(fpl_jobs.fpl_client, "league", _league)
    monkeypatch.setattr(fpl_jobs.fpl_client, "entry", _entry)
    monkeypatch.setattr(fpl_jobs.fpl_client, "picks", _picks)

    await fpl_jobs._sync_rivals(conn)  # must not raise

    assert get_rival_picks(conn, 1, 222) != []
    assert get_rival_picks(conn, 1, 111) == []  # retried next tick, not aborted


@pytest.mark.asyncio
async def test_departed_rival_is_marked_inactive_not_deleted(monkeypatch, _wired):
    conn = _wired
    _seed_finished_gws(conn, 1)

    async def _league_with_two(league_id):
        return {"standings": {"results": [
            {"entry": 111, "entry_name": "Staying", "player_name": "A"},
            {"entry": 222, "entry_name": "Leaving", "player_name": "B"},
        ]}}

    async def _entry(entry_id):
        return {"started_event": 1}

    async def _picks(entry_id, gw):
        return _picks_payload({5: 1})

    monkeypatch.setattr(fpl_jobs.fpl_client, "league", _league_with_two)
    monkeypatch.setattr(fpl_jobs.fpl_client, "entry", _entry)
    monkeypatch.setattr(fpl_jobs.fpl_client, "picks", _picks)
    await fpl_jobs._sync_rivals(conn)
    assert {r["entry_id"] for r in get_active_rivals(conn)} == {111, 222}

    async def _league_with_one(league_id):
        return {"standings": {"results": [
            {"entry": 111, "entry_name": "Staying", "player_name": "A"},
        ]}}

    monkeypatch.setattr(fpl_jobs.fpl_client, "league", _league_with_one)
    await fpl_jobs._sync_rivals(conn)

    assert {r["entry_id"] for r in get_active_rivals(conn)} == {111}
    all_ids = {r["entry_id"] for r in get_all_rivals(conn)}
    assert all_ids == {111, 222}  # 222 still on record, just inactive


@pytest.mark.asyncio
async def test_already_synced_gameweek_is_not_refetched(monkeypatch, _wired):
    conn = _wired
    _seed_finished_gws(conn, 1)

    async def _league(league_id):
        return {"standings": {"results": [{"entry": 111, "entry_name": "R", "player_name": "P"}]}}

    async def _entry(entry_id):
        return {"started_event": 1}

    call_count = {"n": 0}

    async def _picks(entry_id, gw):
        call_count["n"] += 1
        return _picks_payload({5: 1})

    monkeypatch.setattr(fpl_jobs.fpl_client, "league", _league)
    monkeypatch.setattr(fpl_jobs.fpl_client, "entry", _entry)
    monkeypatch.setattr(fpl_jobs.fpl_client, "picks", _picks)

    await fpl_jobs._sync_rivals(conn)
    await fpl_jobs._sync_rivals(conn)

    assert call_count["n"] == 1  # picks are public and immutable once the deadline passes


@pytest.mark.asyncio
async def test_standings_outage_does_not_raise(monkeypatch, _wired):
    conn = _wired
    _seed_finished_gws(conn, 1)

    async def _broken_league(league_id):
        raise fpl_jobs.fpl_client.FPLError("simulated standings outage")

    monkeypatch.setattr(fpl_jobs.fpl_client, "league", _broken_league)

    await fpl_jobs._sync_rivals(conn)  # must not raise
