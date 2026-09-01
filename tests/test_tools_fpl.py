"""Tests for the FPL tools (tools/fpl.py) — PHASE1-BRIEF.md."""

from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone

import pytest

import config
import tools.fpl as fpl_tools
from storage.models import (
    ACKNOWLEDGEMENTS_DDL,
    GAMEWEEK_DDL,
    MY_HISTORY_DDL,
    MY_PICKS_DDL,
    NOTIFICATIONS_SENT_DDL,
    PLAYER_SNAPSHOT_DDL,
    RIVAL_HISTORY_DDL,
    RIVAL_PICKS_DDL,
    RIVALS_DDL,
    get_gameweek,
    get_my_picks,
    is_acknowledged,
    replace_my_picks,
)
from tools.fpl import (
    active_chip_set,
    build_squad_rows,
    chips_used_by_set,
    compute_free_transfers,
    fpl_acknowledge,
    get_fpl_chips,
    get_fpl_league,
    get_fpl_squad,
    get_fpl_team,
    normalize_history_rows,
    sync_gameweeks_from_bootstrap,
    target_gameweek,
)


def _make_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute(GAMEWEEK_DDL)
    conn.execute(MY_PICKS_DDL)
    conn.execute(MY_HISTORY_DDL)
    conn.execute(PLAYER_SNAPSHOT_DDL)
    conn.execute(NOTIFICATIONS_SENT_DDL)
    conn.execute(ACKNOWLEDGEMENTS_DDL)
    conn.execute(RIVALS_DDL)
    conn.execute(RIVAL_PICKS_DDL)
    conn.execute(RIVAL_HISTORY_DDL)
    conn.commit()
    return conn


@pytest.fixture(autouse=True)
def _configure_fpl(monkeypatch):
    monkeypatch.setattr(config, "FPL_ENABLED", True)
    monkeypatch.setattr(config, "FPL_TEAM_ID", 6748844)
    monkeypatch.setattr(config, "FPL_LEAGUE_ID", 1342398)


# All deadlines are anchored relative to real "now" at test-run time (rather
# than hardcoded absolute dates) so target_gameweek() reliably resolves to
# GW1 — the nearest upcoming deadline — regardless of when the suite
# actually runs. A prior version hardcoded 21/29 Aug 2026, which quietly
# broke every test in this block once real time passed those dates.
_GW1_DEADLINE = (datetime.now(tz=timezone.utc) + timedelta(days=7)).strftime("%Y-%m-%dT%H:%M:%SZ")
_GW2_DEADLINE = (datetime.now(tz=timezone.utc) + timedelta(days=14)).strftime("%Y-%m-%dT%H:%M:%SZ")
_GW19_DEADLINE = (datetime.now(tz=timezone.utc) + timedelta(days=120)).strftime("%Y-%m-%dT%H:%M:%SZ")

_BOOTSTRAP = {
    "events": [
        {"id": 1, "deadline_time": _GW1_DEADLINE, "is_current": False, "is_next": True, "finished": False, "data_checked": False},
        {"id": 2, "deadline_time": _GW2_DEADLINE, "is_current": False, "is_next": False, "finished": False, "data_checked": False},
        {"id": 19, "deadline_time": _GW19_DEADLINE, "is_current": False, "is_next": False, "finished": False, "data_checked": False},
    ],
    "teams": [{"id": 1, "name": "Arsenal", "short_name": "ARS"}],
    "elements": [
        {"id": 100, "web_name": "Saka", "now_cost": 100, "status": "a", "news": "", "chance_of_playing_next_round": None, "team": 1, "element_type": 3},
        {"id": 101, "web_name": "Raya", "now_cost": 55, "status": "i", "news": "Knee injury", "chance_of_playing_next_round": 25, "team": 1, "element_type": 1},
    ],
}


# ── sync_gameweeks_from_bootstrap / target_gameweek ─────────────────────────────


def test_sync_gameweeks_writes_rows_and_target_gameweek_picks_nearest_upcoming():
    conn = _make_conn()
    sync_gameweeks_from_bootstrap(conn, _BOOTSTRAP)

    row = get_gameweek(conn, 1)
    assert row["deadline_utc"] == _GW1_DEADLINE
    assert row["is_next"] == 1

    tgw = target_gameweek(conn)
    assert tgw["gw"] == 1  # earliest deadline still in the future


# ── build_squad_rows ─────────────────────────────────────────────────────────


def test_build_squad_rows_merges_picks_with_element_data():
    picks = [
        {"element_id": 100, "position": 1, "is_captain": 1, "is_vice": 0, "multiplier": 2},
        {"element_id": 101, "position": 12, "is_captain": 0, "is_vice": 0, "multiplier": 0},
    ]
    elements = fpl_tools.element_index(_BOOTSTRAP)
    teams = fpl_tools.team_index(_BOOTSTRAP)

    rows = build_squad_rows(picks, elements, teams)

    assert rows[0]["name"] == "Saka"
    assert rows[0]["is_captain"] is True
    assert rows[0]["price"] == 10.0
    assert rows[0]["starting"] is True
    assert rows[1]["starting"] is False  # bench (multiplier 0)
    assert rows[1]["flag"] is not None and "injured" in rows[1]["flag"]


# ── compute_free_transfers ─────────────────────────────────────────────────────


def test_free_transfers_rolls_up_when_unused():
    history = [
        {"gw": 1, "transfers": 0, "transfer_cost": 0, "chip": None},
        {"gw": 2, "transfers": 0, "transfer_cost": 0, "chip": None},
        {"gw": 3, "transfers": 0, "transfer_cost": 0, "chip": None},
    ]
    # Heading into GW2 FT=1 (unused) -> GW3 FT=2 (unused) -> GW4 FT=3
    assert compute_free_transfers(history) == 3


def test_free_transfers_caps_at_five():
    history = [{"gw": gw, "transfers": 0, "transfer_cost": 0, "chip": None} for gw in range(1, 10)]
    assert compute_free_transfers(history) == 5


def test_free_transfers_consumed_by_using_them():
    history = [
        {"gw": 1, "transfers": 0, "transfer_cost": 0, "chip": None},
        {"gw": 2, "transfers": 1, "transfer_cost": 0, "chip": None},  # uses the 1 FT
    ]
    # GW2: FT=1 available, 1 used, 0 paid -> next FT = 0 + 1 = 1
    assert compute_free_transfers(history) == 1


def test_free_transfers_paid_hit_does_not_consume_banked_transfers():
    history = [
        {"gw": 1, "transfers": 0, "transfer_cost": 0, "chip": None},
        {"gw": 2, "transfers": 0, "transfer_cost": 0, "chip": None},  # FT rolls to 2
        {"gw": 3, "transfers": 3, "transfer_cost": 4, "chip": None},  # 2 free + 1 paid (-4)
    ]
    # GW3: FT=2 available, 3 made, 1 paid (cost 4 // 4) -> free_used = min(3-1, 2) = 2
    # remaining = 0 -> next FT = 0 + 1 = 1
    assert compute_free_transfers(history) == 1


def test_free_transfers_wildcard_gw_accrues_without_consuming():
    history = [
        {"gw": 1, "transfers": 0, "transfer_cost": 0, "chip": None},
        {"gw": 2, "transfers": 15, "transfer_cost": 0, "chip": "wildcard"},
    ]
    assert compute_free_transfers(history) == 2  # unaffected by the 15 wildcard transfers


# ── chips_used_by_set / active_chip_set ─────────────────────────────────────────


def test_chips_used_by_set_splits_on_gw19():
    history = [
        {"gw": 5, "chip": "wildcard"},
        {"gw": 12, "chip": "bboost"},
        {"gw": 25, "chip": "freehit"},
    ]
    used = chips_used_by_set(history)
    assert used[1] == {"wildcard", "bboost"}
    assert used[2] == {"freehit"}


def test_active_chip_set_boundary():
    assert active_chip_set(19) == 1
    assert active_chip_set(20) == 2


# ── normalize_history_rows ──────────────────────────────────────────────────────


def test_normalize_history_rows_maps_event_and_chips():
    hist = {
        "current": [
            {"event": 1, "points": 65, "total_points": 65, "overall_rank": 500000, "bank": 5, "value": 1000, "event_transfers": 0, "event_transfers_cost": 0},
            {"event": 5, "points": 70, "total_points": 300, "overall_rank": 400000, "bank": 0, "value": 1005, "event_transfers": 15, "event_transfers_cost": 0},
        ],
        "chips": [{"name": "wildcard", "event": 5}],
    }
    rows = normalize_history_rows(hist)
    assert rows[0]["gw"] == 1 and rows[0]["chip"] is None
    assert rows[1]["gw"] == 5 and rows[1]["chip"] == "wildcard"
    assert rows[1]["team_value"] == 1005


# ── LLM-facing tools ──────────────────────────────────────────────────────────


@pytest.fixture
def _patch_client(monkeypatch):
    async def _bootstrap(force=False):
        return _BOOTSTRAP

    async def _entry_history(team_id):
        return {
            "current": [{"event": 1, "points": 60, "total_points": 60, "overall_rank": 100000, "bank": 5, "value": 1000, "event_transfers": 0, "event_transfers_cost": 0}],
            "chips": [],
        }

    async def _league(league_id):
        return {
            "league": {"name": "FPL Rugby league"},
            "standings": {"results": [
                {"entry": 6748844, "rank": 2, "last_rank": 3, "entry_name": "Reece lightning", "player_name": "Ollie Branston", "total": 60},
                {"entry": 111, "rank": 1, "last_rank": 1, "entry_name": "Other", "player_name": "Other Manager", "total": 65},
            ]},
        }

    async def _fixtures(gw=None):
        return []

    monkeypatch.setattr(fpl_tools.fpl_client, "bootstrap", _bootstrap)
    monkeypatch.setattr(fpl_tools.fpl_client, "entry_history", _entry_history)
    monkeypatch.setattr(fpl_tools.fpl_client, "league", _league)
    monkeypatch.setattr(fpl_tools.fpl_client, "fixtures", _fixtures)


@pytest.mark.asyncio
async def test_get_fpl_squad_returns_error_when_not_configured(monkeypatch):
    monkeypatch.setattr(config, "FPL_ENABLED", False)
    conn = _make_conn()
    result = await get_fpl_squad(conn)
    assert "error" in result


@pytest.mark.asyncio
async def test_get_fpl_squad_no_squad_on_record_yet(_patch_client):
    conn = _make_conn()
    result = await get_fpl_squad(conn)
    assert result["squad"] == []
    assert result["squad_gw"] is None
    assert result["next_gw"] == 1
    assert result["league_rank"] == 2
    assert result["free_transfers"] == 1


@pytest.mark.asyncio
async def test_get_fpl_squad_with_synced_picks_flags_injured_player(_patch_client):
    conn = _make_conn()
    replace_my_picks(conn, 1, [
        {"element_id": 100, "position": 1, "is_captain": 1, "is_vice": 0, "multiplier": 2},
        {"element_id": 101, "position": 2, "is_captain": 0, "is_vice": 1, "multiplier": 1},
    ])
    result = await get_fpl_squad(conn)
    assert result["squad_gw"] == 1
    assert len(result["flagged_players"]) == 1
    assert result["flagged_players"][0]["name"] == "Raya"


@pytest.mark.asyncio
async def test_get_fpl_team_reports_not_found_when_no_squad_synced(_patch_client):
    conn = _make_conn()
    result = await get_fpl_team(conn)
    assert result["found"] is False


@pytest.mark.asyncio
async def test_get_fpl_team_groups_by_position(_patch_client):
    conn = _make_conn()
    replace_my_picks(conn, 1, [
        {"element_id": 100, "position": 1, "is_captain": 1, "is_vice": 0, "multiplier": 2},
        {"element_id": 101, "position": 2, "is_captain": 0, "is_vice": 1, "multiplier": 1},
    ])
    result = await get_fpl_team(conn)
    assert result["found"] is True
    assert len(result["by_position"]["MID"]) == 1
    assert len(result["by_position"]["GKP"]) == 1


@pytest.mark.asyncio
async def test_get_fpl_league_marks_is_me_and_movement(_patch_client):
    conn = _make_conn()
    result = await get_fpl_league(conn)
    me = next(r for r in result["table"] if r["is_me"])
    assert me["movement"] == 1  # last_rank 3 -> rank 2


@pytest.mark.asyncio
async def test_get_fpl_chips_reports_remaining_and_expiry(_patch_client):
    conn = _make_conn()
    result = await get_fpl_chips(conn)
    assert result["active_set"] == 1
    assert set(result["remaining_current_set"]) == {"wildcard", "freehit", "3xc", "bboost"}
    assert result["days_to_set1_expiry"] is not None


@pytest.mark.asyncio
async def test_fpl_acknowledge_writes_and_is_queryable(_patch_client):
    conn = _make_conn()
    result = await fpl_acknowledge(conn)
    assert result["acknowledged"] is True
    assert result["gw"] == 1
    assert is_acknowledged(conn, 1) is True
    assert is_acknowledged(conn, 2) is False
