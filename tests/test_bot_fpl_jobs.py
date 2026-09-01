"""Tests for the FPL reminder ladder (bot/fpl_jobs.py) — PHASE1-BRIEF.md §3 + §5.

Deadlines are simulated by injecting fake bootstrap-static event data with a
deadline_time close to "now" rather than waiting for a real gameweek —
per the brief's explicit acceptance criterion ("write a test that injects a
fake deadline_utc — do not wait a week to find out it's broken").
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone

import pytest

import bot.fpl_jobs as fpl_jobs
import config
from storage.models import (
    ACKNOWLEDGEMENTS_DDL,
    GAMEWEEK_DDL,
    MY_HISTORY_DDL,
    MY_PICKS_DDL,
    NOTIFICATIONS_SENT_DDL,
    PLAYER_SNAPSHOT_DDL,
    get_my_history,
    get_my_picks,
    mark_notification_sent,
    set_acknowledged,
)


# ── Fixtures / fakes ─────────────────────────────────────────────────────────


class _NoCloseConn:
    """Wraps a sqlite3.Connection so `.close()` is a no-op — the tick under
    test opens/closes 'a connection' each call, but the test needs the same
    in-memory database to persist across multiple ticks."""

    def __init__(self, conn: sqlite3.Connection):
        self._conn = conn

    def __getattr__(self, name):
        return getattr(self._conn, name)

    def close(self):
        pass


def _make_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    for ddl in (GAMEWEEK_DDL, MY_PICKS_DDL, MY_HISTORY_DDL, PLAYER_SNAPSHOT_DDL, NOTIFICATIONS_SENT_DDL, ACKNOWLEDGEMENTS_DDL):
        conn.execute(ddl)
    conn.commit()
    return conn


class _FakeBot:
    def __init__(self):
        self.messages: list[str] = []

    async def send_message(self, chat_id, text, parse_mode=None):
        self.messages.append(text)


class _FakeContext:
    def __init__(self, bot: _FakeBot):
        self.bot = bot


def _iso(dt) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def _bootstrap(gw1_deadline_iso: str, gw1_finished: bool = False, gw1_data_checked: bool = False) -> dict:
    return {
        "events": [
            {"id": 1, "deadline_time": gw1_deadline_iso, "is_current": not gw1_finished, "is_next": False,
             "finished": gw1_finished, "data_checked": gw1_data_checked},
            {"id": 2, "deadline_time": "2026-08-29T10:00:00Z", "is_current": False, "is_next": not gw1_finished,
             "finished": False, "data_checked": False},
            {"id": 19, "deadline_time": "2027-01-02T13:30:00Z", "is_current": False, "is_next": False,
             "finished": False, "data_checked": False},
        ],
        "teams": [{"id": 1, "name": "Arsenal", "short_name": "ARS"}],
        "elements": [
            {"id": 100, "web_name": "Saka", "now_cost": 100, "status": "a", "news": "",
             "chance_of_playing_next_round": None, "team": 1, "element_type": 3},
        ],
    }


@pytest.fixture
def _wired(monkeypatch):
    """Configure FPL, wire bot.fpl_jobs.get_connection to a persistent in-memory
    conn, and stub out the network-facing fpl_client functions. Returns the conn
    plus setter helpers for tests to override bootstrap/picks/history per-case."""
    conn = _make_conn()
    monkeypatch.setattr(config, "FPL_ENABLED", True)
    monkeypatch.setattr(config, "FPL_TEAM_ID", 6748844)
    monkeypatch.setattr(config, "FPL_LEAGUE_ID", 1342398)
    monkeypatch.setattr(fpl_jobs, "get_connection", lambda: _NoCloseConn(conn))

    state = {"bootstrap": None, "picks": {}}

    async def _bootstrap_fn(force=False):
        return state["bootstrap"]

    async def _entry_history_fn(team_id):
        return {"current": [], "chips": []}

    async def _league_fn(league_id):
        return {"league": {"name": "FPL Rugby league"}, "standings": {"results": []}}

    async def _picks_fn(team_id, gw):
        return state["picks"].get(gw)

    async def _transfers_fn(team_id):
        return []

    async def _fixtures_fn(gw=None):
        return []

    monkeypatch.setattr(fpl_jobs.fpl_client, "bootstrap", _bootstrap_fn)
    monkeypatch.setattr(fpl_jobs.fpl_client, "entry_history", _entry_history_fn)
    monkeypatch.setattr(fpl_jobs.fpl_client, "league", _league_fn)
    monkeypatch.setattr(fpl_jobs.fpl_client, "picks", _picks_fn)
    monkeypatch.setattr(fpl_jobs.fpl_client, "transfers", _transfers_fn)
    monkeypatch.setattr(fpl_jobs.fpl_client, "fixtures", _fixtures_fn)

    return conn, state


# ── Step 3: main briefing trigger (Thursday-evening anchor) ────────────────
#
# PHASE3-BRIEF.md Step 3: the later of (a) 18:00 the Thursday before the
# deadline, or (b) T-48h — capped at T-20h. Fixed, hand-picked deadlines
# (not "now"-relative) so these are deterministic regardless of which day
# the suite actually runs on.


def test_main_briefing_trigger_friday_deadline_anchors_to_thursday_evening():
    # Fri 4 Sep 2026, 18:30 BST -> Thu 3 Sep, 18:00 BST (17:00 UTC, BST = UTC+1)
    deadline = datetime(2026, 9, 4, 17, 30, tzinfo=timezone.utc)
    trigger = fpl_jobs.main_briefing_trigger(deadline)
    assert trigger == datetime(2026, 9, 3, 17, 0, tzinfo=timezone.utc)


def test_main_briefing_trigger_saturday_deadline_fires_thursday_not_friday():
    # PHASE3-BRIEF.md's own acceptance case: Sat 11:00 deadline must fire
    # Thursday evening, not Friday morning (mid-workday, easy to miss).
    deadline = datetime(2026, 9, 5, 10, 0, tzinfo=timezone.utc)  # Sat 5 Sep, 11:00 BST
    trigger = fpl_jobs.main_briefing_trigger(deadline)
    assert trigger == datetime(2026, 9, 3, 17, 0, tzinfo=timezone.utc)  # Thu 3 Sep 18:00 BST
    assert trigger.astimezone(fpl_jobs._TZ).date().isoformat() == "2026-09-03"


def test_main_briefing_trigger_midweek_deadline_falls_back_to_t48h():
    # Tue deadline: the "Thursday before" is the previous week's Thursday —
    # far more than 48h out — so T-48h becomes the binding constraint
    # instead of reaching back across the weekend.
    deadline = datetime(2026, 9, 8, 17, 30, tzinfo=timezone.utc)  # Tue 8 Sep, 18:30 BST
    trigger = fpl_jobs.main_briefing_trigger(deadline)
    assert trigger == deadline - timedelta(hours=48)


def test_main_briefing_trigger_thursday_deadline_capped_at_t20h():
    # A deadline that falls ON Thursday: the naive Thursday-18:00 anchor
    # would be only 30 minutes before deadline, so T-20h caps it instead.
    deadline = datetime(2026, 9, 3, 17, 30, tzinfo=timezone.utc)  # Thu 3 Sep, 18:30 BST
    trigger = fpl_jobs.main_briefing_trigger(deadline)
    assert trigger == deadline - timedelta(hours=20)


# ── T-24h dedup ──────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_t24_fires_once_even_when_tick_runs_twice(_wired):
    conn, state = _wired
    deadline = _iso(fpl_jobs.now_utc() + timedelta(hours=20))
    state["bootstrap"] = _bootstrap(deadline)

    bot = _FakeBot()
    context = _FakeContext(bot)

    await fpl_jobs._fpl_tick(context)
    await fpl_jobs._fpl_tick(context)

    deadline_messages = [m for m in bot.messages if "GW1 deadline" in m]
    assert len(deadline_messages) == 1


@pytest.mark.asyncio
async def test_no_ladder_message_sent_before_t24_window(_wired):
    # 14 days is deliberately generous, not 48h: under Step 3's rule T-48h is
    # itself a valid trigger point (the later of the Thursday anchor or
    # T-48h), so a deadline exactly 48h out is sometimes already due. 14 days
    # keeps this test's "too early" premise true regardless of which weekday
    # it happens to run on.
    conn, state = _wired
    deadline = _iso(fpl_jobs.now_utc() + timedelta(days=14))
    state["bootstrap"] = _bootstrap(deadline)

    bot = _FakeBot()
    await fpl_jobs._fpl_tick(_FakeContext(bot))

    assert bot.messages == []


# ── Acknowledgement suppresses the hard nudge ────────────────────────────────


@pytest.mark.asyncio
async def test_acknowledged_gameweek_gets_no_hard_nudge(_wired):
    conn, state = _wired
    deadline = _iso(fpl_jobs.now_utc() + timedelta(minutes=30))
    state["bootstrap"] = _bootstrap(deadline)

    # Simulate T-24h/T-3h already handled by earlier ticks, and Ollie has
    # acknowledged — only the T-45m/T-15m hard nudge is under test here.
    mark_notification_sent(conn, 1, "T24", "x")
    mark_notification_sent(conn, 1, "T3", "x")
    set_acknowledged(conn, 1, "x")

    bot = _FakeBot()
    await fpl_jobs._fpl_tick(_FakeContext(bot))

    assert not any("Deadline in" in m for m in bot.messages)


@pytest.mark.asyncio
async def test_unacknowledged_gameweek_gets_hard_nudge(_wired):
    conn, state = _wired
    deadline = _iso(fpl_jobs.now_utc() + timedelta(minutes=30))
    state["bootstrap"] = _bootstrap(deadline)

    mark_notification_sent(conn, 1, "T24", "x")
    mark_notification_sent(conn, 1, "T3", "x")

    bot = _FakeBot()
    await fpl_jobs._fpl_tick(_FakeContext(bot))

    assert any("Deadline in 45 minutes" in m for m in bot.messages)


# ── Picks/history auto-populate after the deadline passes ───────────────────


@pytest.mark.asyncio
async def test_picks_and_history_populate_automatically_after_deadline(_wired):
    conn, state = _wired
    past_deadline = _iso(fpl_jobs.now_utc() - timedelta(hours=2))
    state["bootstrap"] = _bootstrap(past_deadline)
    state["picks"][1] = {
        "picks": [{"element": 100, "position": 1, "multiplier": 2, "is_captain": True, "is_vice_captain": False}],
        "entry_history": {
            "points": 70, "total_points": 70, "overall_rank": 90000,
            "bank": 5, "value": 105, "event_transfers": 0, "event_transfers_cost": 0,
        },
        "active_chip": None,
    }

    await fpl_jobs._fpl_tick(_FakeContext(_FakeBot()))

    picks_rows = get_my_picks(conn, 1)
    assert len(picks_rows) == 1
    assert picks_rows[0]["element_id"] == 100
    assert picks_rows[0]["is_captain"] == 1

    hist = get_my_history(conn, 1)
    assert hist["points"] == 70
    assert hist["overall_rank"] == 90000


@pytest.mark.asyncio
async def test_money_sanity_check_logs_loudly_on_mismatch(_wired, caplog):
    """PHASE2-BRIEF.md follow-up: a wrong selling-price computation must be a
    loud, visible failure at sync time, not a silent drift."""
    conn, state = _wired
    past_deadline = _iso(fpl_jobs.now_utc() - timedelta(hours=2))
    state["bootstrap"] = _bootstrap(past_deadline)
    state["picks"][1] = {
        "picks": [{"element": 100, "position": 1, "multiplier": 2, "is_captain": True, "is_vice_captain": False}],
        "entry_history": {
            "points": 70, "total_points": 70, "overall_rank": 90000,
            # Saka's now_cost is 100 tenths + bank 5 -> computed 105,
            # deliberately mismatched against a reported 500.
            "bank": 5, "value": 500, "event_transfers": 0, "event_transfers_cost": 0,
        },
        "active_chip": None,
    }

    with caplog.at_level("ERROR"):
        await fpl_jobs._fpl_tick(_FakeContext(_FakeBot()))

    assert any("money sanity check failed" in r.message for r in caplog.records)
    # The sync itself still succeeds despite the mismatched money check.
    assert len(get_my_picks(conn, 1)) == 1


@pytest.mark.asyncio
async def test_money_sanity_check_silent_when_values_agree(_wired, caplog):
    conn, state = _wired
    past_deadline = _iso(fpl_jobs.now_utc() - timedelta(hours=2))
    state["bootstrap"] = _bootstrap(past_deadline)
    state["picks"][1] = {
        "picks": [{"element": 100, "position": 1, "multiplier": 2, "is_captain": True, "is_vice_captain": False}],
        "entry_history": {
            "points": 70, "total_points": 70, "overall_rank": 90000,
            # Saka's now_cost is 100 tenths + bank 5 -> computed 105, matching.
            "bank": 5, "value": 105, "event_transfers": 0, "event_transfers_cost": 0,
        },
        "active_chip": None,
    }

    with caplog.at_level("ERROR"):
        await fpl_jobs._fpl_tick(_FakeContext(_FakeBot()))

    assert not any("money sanity check failed" in r.message for r in caplog.records)


@pytest.mark.asyncio
async def test_picks_sync_retries_next_tick_while_api_still_404s(_wired):
    conn, state = _wired
    past_deadline = _iso(fpl_jobs.now_utc() - timedelta(hours=2))
    state["bootstrap"] = _bootstrap(past_deadline)
    state["picks"][1] = None  # still 404 — normal pre-lockdown state

    await fpl_jobs._fpl_tick(_FakeContext(_FakeBot()))
    assert get_my_picks(conn, 1) == []

    state["picks"][1] = {
        "picks": [{"element": 100, "position": 1, "multiplier": 2, "is_captain": True, "is_vice_captain": False}],
        "entry_history": {"points": 55, "total_points": 55, "overall_rank": 500000,
                           "bank": 0, "value": 100, "event_transfers": 0, "event_transfers_cost": 0},
        "active_chip": None,
    }
    await fpl_jobs._fpl_tick(_FakeContext(_FakeBot()))
    assert len(get_my_picks(conn, 1)) == 1


# ── GW review only after data_checked ────────────────────────────────────────


@pytest.mark.asyncio
async def test_no_review_before_data_checked(_wired):
    conn, state = _wired
    past_deadline = _iso(fpl_jobs.now_utc() - timedelta(days=3))
    state["bootstrap"] = _bootstrap(past_deadline, gw1_finished=True, gw1_data_checked=False)

    bot = _FakeBot()
    await fpl_jobs._fpl_tick(_FakeContext(bot))

    assert not any("GW1 review" in m for m in bot.messages)


@pytest.mark.asyncio
async def test_review_sent_once_data_checked_and_not_repeated(_wired):
    conn, state = _wired
    past_deadline = _iso(fpl_jobs.now_utc() - timedelta(days=3))
    state["bootstrap"] = _bootstrap(past_deadline, gw1_finished=True, gw1_data_checked=True)
    state["picks"][1] = {
        "picks": [{"element": 100, "position": 1, "multiplier": 2, "is_captain": True, "is_vice_captain": False}],
        "entry_history": {"points": 63, "total_points": 63, "overall_rank": 200000,
                           "bank": 0, "value": 100, "event_transfers": 0, "event_transfers_cost": 0},
        "active_chip": None,
    }

    bot = _FakeBot()
    await fpl_jobs._fpl_tick(_FakeContext(bot))
    await fpl_jobs._fpl_tick(_FakeContext(bot))

    review_messages = [m for m in bot.messages if "GW1 review" in m]
    assert len(review_messages) == 1
    assert "63" in review_messages[0]


# ── Resilience ────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_tick_survives_bootstrap_outage_without_crashing_or_messaging(monkeypatch):
    conn = _make_conn()
    monkeypatch.setattr(config, "FPL_ENABLED", True)
    monkeypatch.setattr(config, "FPL_TEAM_ID", 6748844)
    monkeypatch.setattr(config, "FPL_LEAGUE_ID", 1342398)
    monkeypatch.setattr(fpl_jobs, "get_connection", lambda: _NoCloseConn(conn))

    async def _broken_bootstrap(force=False):
        raise fpl_jobs.fpl_client.FPLError("simulated outage")

    monkeypatch.setattr(fpl_jobs.fpl_client, "bootstrap", _broken_bootstrap)

    bot = _FakeBot()
    await fpl_jobs._fpl_tick(_FakeContext(bot))  # must not raise

    assert bot.messages == []


@pytest.mark.asyncio
async def test_disabled_flag_skips_tick_entirely(monkeypatch):
    monkeypatch.setattr(config, "FPL_ENABLED", False)
    calls = []
    monkeypatch.setattr(fpl_jobs, "get_connection", lambda: calls.append(1))

    await fpl_jobs._fpl_tick(_FakeContext(_FakeBot()))

    assert calls == []  # never even opened a connection
