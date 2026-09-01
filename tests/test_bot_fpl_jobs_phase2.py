"""Tests for the Phase 2 T-24h message upgrade (bot/fpl_jobs.py — PHASE2-BRIEF.md §7)."""

from __future__ import annotations

import sqlite3

import pytest

import bot.fpl_jobs as fpl_jobs
import config
from storage.models import (
    ACKNOWLEDGEMENTS_DDL,
    GAMEWEEK_DDL,
    GAMEWEEK_SHAPE_DDL,
    MY_HISTORY_DDL,
    MY_PICKS_DDL,
    NOTIFICATIONS_SENT_DDL,
    PLAYER_SNAPSHOT_DDL,
    PREFERENCE_DDL,
    XP_PREDICTION_DDL,
    replace_my_picks,
)

from tests.fpl_fixtures import legal_squad_ids, synthetic_bootstrap, synthetic_fixtures


def _make_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    for ddl in (
        GAMEWEEK_DDL, MY_PICKS_DDL, MY_HISTORY_DDL, PLAYER_SNAPSHOT_DDL,
        NOTIFICATIONS_SENT_DDL, ACKNOWLEDGEMENTS_DDL, XP_PREDICTION_DDL,
        PREFERENCE_DDL, GAMEWEEK_SHAPE_DDL,
    ):
        conn.execute(ddl)
    conn.commit()
    return conn


def _seed_current_squad(conn, bootstrap: dict) -> set[int]:
    elements = {e["id"]: e for e in bootstrap["elements"]}
    squad = legal_squad_ids()
    by_pos: dict[int, list[int]] = {1: [], 2: [], 3: [], 4: []}
    for eid in squad:
        by_pos[elements[eid]["element_type"]].append(eid)
    starters = set(by_pos[1][:1] + by_pos[2][:4] + by_pos[3][:4] + by_pos[4][:2])
    captain = next(iter(by_pos[3][:1]))
    vice = next(e for e in starters if e != captain)
    rows = [
        {"element_id": eid, "position": i,
         "is_captain": 1 if eid == captain else 0,
         "is_vice": 1 if eid == vice else 0,
         "multiplier": (2 if eid == captain else 1) if eid in starters else 0}
        for i, eid in enumerate(sorted(squad), start=1)
    ]
    replace_my_picks(conn, 1, rows)
    return squad


class _FakeBot:
    def __init__(self):
        self.messages: list[str] = []

    async def send_message(self, chat_id, text, parse_mode=None):
        self.messages.append(text)


class _FakeContext:
    def __init__(self, bot: _FakeBot):
        self.bot = bot


@pytest.fixture
def _wired(monkeypatch):
    conn = _make_conn()
    monkeypatch.setattr(config, "FPL_ENABLED", True)
    monkeypatch.setattr(config, "FPL_TEAM_ID", 6748844)
    monkeypatch.setattr(config, "FPL_LEAGUE_ID", 1342398)
    monkeypatch.setattr(fpl_jobs, "get_connection", lambda: conn)

    bootstrap = synthetic_bootstrap(num_gws=10)
    fixtures = synthetic_fixtures(num_gws=10)
    # entry_history.value is squad market value (sum of now_cost) + bank —
    # see verify_squad_value's docstring — so the reported value here must
    # include the bank figure below for the money sanity check to pass.
    _squad_now_cost = {e["id"]: e["now_cost"] for e in bootstrap["elements"]}
    _squad_bank = 5
    _squad_value = sum(_squad_now_cost[eid] for eid in legal_squad_ids()) + _squad_bank

    async def _bootstrap(force=False):
        return bootstrap

    async def _fixtures(gw=None):
        if gw is None:
            return fixtures
        return [f for f in fixtures if f["event"] == gw]

    async def _entry_history(team_id):
        return {"current": [{"event": 1, "points": 60, "total_points": 60, "overall_rank": 100000,
                              "bank": _squad_bank, "value": _squad_value, "event_transfers": 0, "event_transfers_cost": 0}],
                "chips": []}

    async def _league(league_id):
        return {"league": {"name": "FPL Rugby league"}, "standings": {"results": []}}

    async def _transfers(team_id):
        return []

    monkeypatch.setattr(fpl_jobs.fpl_client, "bootstrap", _bootstrap)
    monkeypatch.setattr(fpl_jobs.fpl_client, "fixtures", _fixtures)
    monkeypatch.setattr(fpl_jobs.fpl_client, "entry_history", _entry_history)
    monkeypatch.setattr(fpl_jobs.fpl_client, "league", _league)
    monkeypatch.setattr(fpl_jobs.fpl_client, "transfers", _transfers)

    return conn, bootstrap, fixtures


@pytest.mark.asyncio
async def test_t24_with_a_synced_squad_uses_the_recommendation(_wired):
    conn, bootstrap, fixtures = _wired
    _seed_current_squad(conn, bootstrap)

    bot = _FakeBot()
    await fpl_jobs._send_t24(_FakeContext(bot), conn)

    assert len(bot.messages) == 1
    msg = bot.messages[0]
    assert "Recommended:" in msg
    assert "Other options:" in msg
    assert "Captain:" in msg
    assert "Chips:" in msg


@pytest.mark.asyncio
async def test_t24_falls_back_to_status_dump_without_a_squad(_wired):
    conn, bootstrap, fixtures = _wired
    # No my_picks seeded — get_fpl_recommendation errors, falls back.

    bot = _FakeBot()
    await fpl_jobs._send_t24(_FakeContext(bot), conn)

    assert len(bot.messages) == 1
    msg = bot.messages[0]
    assert "Recommended:" not in msg
    assert "No squad on record yet" in msg


@pytest.mark.asyncio
async def test_t24_surfaces_a_calendar_change(monkeypatch, _wired):
    conn, bootstrap, fixtures = _wired
    _seed_current_squad(conn, bootstrap)

    # Reschedule team 5's GW2 fixture into GW3 — GW2 is the target gameweek here.
    rescheduled = []
    for f in fixtures:
        if f["event"] == 2 and 5 in (f["team_h"], f["team_a"]):
            rescheduled.append({**f, "event": 3})
        else:
            rescheduled.append(f)

    async def _fixtures(gw=None):
        if gw is None:
            return rescheduled
        return [f for f in rescheduled if f["event"] == gw]

    monkeypatch.setattr(fpl_jobs.fpl_client, "fixtures", _fixtures)

    bot = _FakeBot()
    await fpl_jobs._send_t24(_FakeContext(bot), conn)

    assert "Calendar change" in bot.messages[0]


@pytest.mark.asyncio
async def test_t24_snapshots_squad_regardless_of_which_path_fired(_wired):
    from storage.models import get_latest_snapshot

    conn, bootstrap, fixtures = _wired
    squad = _seed_current_squad(conn, bootstrap)

    await fpl_jobs._send_t24(_FakeContext(_FakeBot()), conn)

    some_id = next(iter(squad))
    assert get_latest_snapshot(conn, some_id) is not None
