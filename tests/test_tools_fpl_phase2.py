"""Tests for the Phase 2 FPL tools (tools/fpl.py) — PHASE2-BRIEF.md."""

from __future__ import annotations

import sqlite3

import pytest

import config
import tools.fpl as fpl_tools
from storage.models import (
    ACKNOWLEDGEMENTS_DDL,
    GAMEWEEK_DDL,
    GAMEWEEK_SHAPE_DDL,
    MY_HISTORY_DDL,
    MY_PICKS_DDL,
    NOTIFICATIONS_SENT_DDL,
    PLAYER_SNAPSHOT_DDL,
    PREFERENCE_DDL,
    RIVAL_HISTORY_DDL,
    RIVAL_PICKS_DDL,
    RIVALS_DDL,
    XP_PREDICTION_DDL,
    get_active_preferences,
    get_xp_predictions,
    replace_my_picks,
)
from services.fpl_optimiser import Candidate, SolveResult, compute_selling_price
from tools.fpl import cost_basis, get_fpl_calendar, get_fpl_chips, get_fpl_gw_review, get_fpl_league, get_fpl_recommendation, sync_gameweeks_from_bootstrap
from storage.models import get_my_picks, sync_rivals_from_standings, replace_rival_picks, upsert_my_history, upsert_rival_history

from tests.fpl_fixtures import element_id, legal_squad_ids, synthetic_bootstrap, synthetic_fixtures


def _make_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    for ddl in (
        GAMEWEEK_DDL, MY_PICKS_DDL, MY_HISTORY_DDL, PLAYER_SNAPSHOT_DDL,
        NOTIFICATIONS_SENT_DDL, ACKNOWLEDGEMENTS_DDL, XP_PREDICTION_DDL,
        PREFERENCE_DDL, GAMEWEEK_SHAPE_DDL, RIVALS_DDL, RIVAL_PICKS_DDL, RIVAL_HISTORY_DDL,
    ):
        conn.execute(ddl)
    conn.commit()
    return conn


def _seed_current_squad(conn: sqlite3.Connection, bootstrap: dict) -> set[int]:
    """Write a legal 15/valid-XI squad into my_picks for gw=1, return the squad ids."""
    elements = {e["id"]: e for e in bootstrap["elements"]}
    squad = legal_squad_ids()
    by_pos: dict[int, list[int]] = {1: [], 2: [], 3: [], 4: []}
    for eid in squad:
        by_pos[elements[eid]["element_type"]].append(eid)

    starters = set(by_pos[1][:1] + by_pos[2][:4] + by_pos[3][:4] + by_pos[4][:2])
    captain = next(iter(by_pos[3][:1]))
    vice = next(e for e in starters if e != captain)

    rows = []
    for i, eid in enumerate(sorted(squad), start=1):
        rows.append({
            "element_id": eid, "position": i,
            "is_captain": 1 if eid == captain else 0,
            "is_vice": 1 if eid == vice else 0,
            "multiplier": (2 if eid == captain else 1) if eid in starters else 0,
        })
    replace_my_picks(conn, 1, rows)
    return squad


@pytest.fixture
def _wired(monkeypatch):
    conn = _make_conn()
    monkeypatch.setattr(config, "FPL_ENABLED", True)
    monkeypatch.setattr(config, "FPL_TEAM_ID", 6748844)
    monkeypatch.setattr(config, "FPL_LEAGUE_ID", 1342398)

    bootstrap = synthetic_bootstrap(num_gws=10)
    fixtures = synthetic_fixtures(num_gws=10)
    # entry_history.value is squad market value (sum of now_cost) + bank — not
    # sell-on-fee-adjusted selling price (verify_squad_value's docstring has
    # the live confirmation) — so this fixture's reported value must include
    # the bank figure below for the money sanity check to pass.
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

    monkeypatch.setattr(fpl_tools.fpl_client, "bootstrap", _bootstrap)
    monkeypatch.setattr(fpl_tools.fpl_client, "fixtures", _fixtures)
    monkeypatch.setattr(fpl_tools.fpl_client, "entry_history", _entry_history)
    monkeypatch.setattr(fpl_tools.fpl_client, "league", _league)
    monkeypatch.setattr(fpl_tools.fpl_client, "transfers", _transfers)

    return conn, bootstrap, fixtures


# ── get_fpl_calendar ─────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_get_fpl_calendar_reports_nothing_for_a_normal_calendar(_wired):
    conn, bootstrap, fixtures = _wired
    result = await get_fpl_calendar(conn, horizon=5)
    assert result["blank_or_double_gameweeks"] == []
    assert result["changed_since_last_check"] == []


@pytest.mark.asyncio
async def test_get_fpl_calendar_detects_a_reschedule(monkeypatch, _wired):
    conn, bootstrap, fixtures = _wired
    # Postpone team 5's GW2 fixture into GW3.
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

    monkeypatch.setattr(fpl_tools.fpl_client, "fixtures", _fixtures)

    result = await get_fpl_calendar(conn, horizon=5)
    gws_reported = {g["gw"] for g in result["blank_or_double_gameweeks"]}
    assert 2 in gws_reported
    assert 3 in gws_reported
    assert 2 in result["changed_since_last_check"]
    assert 3 in result["changed_since_last_check"]

    # A second call against the same (now-cached) shape reports no new changes.
    result2 = await get_fpl_calendar(conn, horizon=5)
    assert result2["changed_since_last_check"] == []


# ── get_fpl_league — mini-league engine (Step 4) ────────────────────────────


@pytest.mark.asyncio
async def test_get_fpl_league_includes_rival_derived_fields_when_data_synced(monkeypatch, _wired):
    """PHASE3-BRIEF.md Step 4's "New output": template holes, my differentials,
    chips used by rivals, and captain picks of managers ranked above Ollie."""
    conn, bootstrap, fixtures = _wired
    my_squad = _seed_current_squad(conn, bootstrap)

    hole_player = element_id(16, 0)   # a player none of Ollie's squad owns
    my_unique = next(iter(my_squad))  # something Ollie owns that rivals won't

    async def _league(league_id):
        return {
            "league": {"name": "FPL Rugby league"},
            "standings": {"results": [
                {"entry": 6748844, "rank": 5, "last_rank": 5, "entry_name": "Reece lightning", "player_name": "Ollie Branston", "total": 60},
                {"entry": 1, "rank": 1, "last_rank": 1, "entry_name": "R1", "player_name": "Rival One", "total": 90},
                {"entry": 2, "rank": 2, "last_rank": 2, "entry_name": "R2", "player_name": "Rival Two", "total": 85},
                {"entry": 3, "rank": 3, "last_rank": 3, "entry_name": "R3", "player_name": "Rival Three", "total": 80},
                {"entry": 4, "rank": 4, "last_rank": 4, "entry_name": "R4", "player_name": "Rival Four", "total": 75},
            ]},
        }

    monkeypatch.setattr(fpl_tools.fpl_client, "league", _league)

    sync_rivals_from_standings(conn, [
        {"entry_id": i, "entry_name": f"R{i}", "player_name": f"Rival {i}", "started_event": 1}
        for i in (1, 2, 3, 4)
    ])
    for i in (1, 2, 3, 4):
        rows = [{"element_id": hole_player, "multiplier": 2 if i == 1 else 1}]
        replace_rival_picks(conn, gw=1, entry_id=i, rows=rows)

    result = await get_fpl_league(conn)
    assert "error" not in result

    hole_name = next(e["web_name"] for e in bootstrap["elements"] if e["id"] == hole_player)
    assert hole_name in result["template_holes"]  # owned by all 4 active rivals, not by Ollie

    my_unique_name = next(e["web_name"] for e in bootstrap["elements"] if e["id"] == my_unique)
    assert my_unique_name in result["my_differentials"]  # no rival owns it

    # Rival One (rank 1, above Ollie's rank 5) captained the hole player.
    assert {"manager_name": "Rival 1", "captain": hole_name} in result["captains_above"]
    # Rivals 2-4 also rank above Ollie but didn't captain him.
    assert len(result["captains_above"]) == 1


@pytest.mark.asyncio
async def test_get_fpl_league_omits_rival_fields_when_no_rival_data_synced(_wired):
    conn, bootstrap, fixtures = _wired
    _seed_current_squad(conn, bootstrap)
    result = await get_fpl_league(conn)
    assert "error" not in result
    assert "my_differentials" not in result
    assert "template_holes" not in result


# ── get_fpl_gw_review — PHASE3-ADDENDUM.md §B differential report ──────────


@pytest.mark.asyncio
async def test_get_fpl_gw_review_decomposes_captain_delta_against_the_leader(monkeypatch, _wired):
    """Verified against real live data in manual testing that this reproduces
    PHASE3-ADDENDUM.md §B's exact worked example (captain delta -20 vs Angus
    Robinson, Mukiele's 9 bench points) — this is a synthetic regression test
    for the wiring, not a re-derivation of that known-answer value."""
    conn, bootstrap, fixtures = _wired
    squad = _seed_current_squad(conn, bootstrap)
    my_picks_rows = get_my_picks(conn, 1)
    my_captain = next(p["element_id"] for p in my_picks_rows if p["is_captain"])
    rival_captain = next(p["element_id"] for p in my_picks_rows if p["element_id"] != my_captain and p["multiplier"] > 0)

    upsert_my_history(conn, {
        "gw": 1, "points": 50, "total_points": 50, "overall_rank": 100, "bank": 0,
        "team_value": 1000, "transfers": 0, "transfer_cost": 0, "chip": None, "points_on_bench": 5,
    })

    leader_entry = 999
    sync_rivals_from_standings(conn, [
        {"entry_id": leader_entry, "entry_name": "L", "player_name": "Leader Person", "started_event": 1},
    ])
    # Same squad shape as Ollie's, just with a different captain.
    rival_rows = [{"element_id": p["element_id"], "multiplier": p["multiplier"]} for p in my_picks_rows]
    for r in rival_rows:
        if r["element_id"] == my_captain:
            r["multiplier"] = 1
        elif r["element_id"] == rival_captain:
            r["multiplier"] = 2
    replace_rival_picks(conn, gw=1, entry_id=leader_entry, rows=rival_rows)
    upsert_rival_history(conn, {
        "gw": 1, "entry_id": leader_entry, "points": 90, "total_points": 90, "rank": 1, "chip": None, "points_on_bench": 3,
    })

    async def _league(league_id):
        return {"standings": {"results": [
            {"entry": leader_entry, "rank": 1, "last_rank": 1, "entry_name": "L", "player_name": "Leader Person", "total": 90},
            {"entry": 6748844, "rank": 2, "last_rank": 2, "entry_name": "Reece lightning", "player_name": "Ollie Branston", "total": 50},
        ]}}
    monkeypatch.setattr(fpl_tools.fpl_client, "league", _league)

    live_points = {eid: 5 for eid in squad}
    live_points[my_captain] = 10
    live_points[rival_captain] = 12

    async def _live(gw):
        return live_points
    monkeypatch.setattr(fpl_tools.fpl_client, "live", _live)

    result = await get_fpl_gw_review(conn, 1)
    assert "error" not in result
    assert result["decomposition"]["vs_rival"]["name"] == "Leader Person"
    captain_row = next(d for d in result["decomposition"]["decomposition"] if d["cause"] == "captain")
    assert captain_row["delta"] == (10 * 2) - (12 * 2)  # -4: my captain (10) x2 vs theirs (12) x2
    assert sum(d["delta"] for d in result["decomposition"]["decomposition"]) == 50 - 90


@pytest.mark.asyncio
async def test_get_fpl_gw_review_errors_when_points_not_synced_yet(_wired):
    conn, bootstrap, fixtures = _wired
    result = await get_fpl_gw_review(conn, 1)
    assert "error" in result


# ── get_fpl_chips signal ─────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_get_fpl_chips_includes_a_signal_block(_wired):
    conn, bootstrap, fixtures = _wired
    result = await get_fpl_chips(conn)
    assert "signal" in result
    assert "plan" in result["signal"]
    assert result["signal"]["play"] is None  # normal calendar, nothing forces a chip this week


@pytest.mark.asyncio
async def test_get_fpl_chips_targets_include_a_concrete_wildcard_gw(_wired):
    conn, bootstrap, fixtures = _wired
    result = await get_fpl_chips(conn)
    assert "targets" in result
    assert "wildcard" in result["targets"]
    assert 5 <= result["targets"]["wildcard"]["gw"] <= 9
    assert "aim for GW" in result["signal"]["plan"]


# ── Chip timing targets (concrete GW estimates) — pure-function unit tests ──


def test_wildcard1_target_picks_first_gw_after_international_break():
    conn = _make_conn()
    sync_gameweeks_from_bootstrap(conn, {"events": [
        {"id": gw, "deadline_time": deadline, "is_current": False, "is_next": False, "finished": False, "data_checked": False}
        for gw, deadline in [
            (5, "2026-09-25T18:30:00Z"), (6, "2026-10-02T18:30:00Z"),
            (7, "2026-10-09T18:30:00Z"), (8, "2026-10-16T18:30:00Z"), (9, "2026-10-23T18:30:00Z"),
        ]
    ]})
    gw, why = fpl_tools._wildcard1_target(conn, current_gw=2)
    assert gw == 7  # first GW5-9 deadline on/after 6 Oct 2026 (FPL-CONTEXT.md's break end)
    assert "international break" in why


def test_wildcard1_target_falls_back_to_window_end_without_break_data():
    conn = _make_conn()  # no gameweeks synced yet — nothing to compare against the break date
    gw, why = fpl_tools._wildcard1_target(conn, current_gw=2)
    assert gw == 9


def test_wildcard1_target_overdue_collapses_to_play_now():
    conn = _make_conn()
    gw, why = fpl_tools._wildcard1_target(conn, current_gw=12)
    assert gw == 12
    assert "overdue" in why


def test_first_blank_gw_finds_earliest_blank_in_range():
    shapes = {5: {"blanks": [], "doubles": []}, 6: {"blanks": [3], "doubles": []}, 7: {"blanks": [3], "doubles": []}}
    assert fpl_tools._first_blank_gw(shapes, 5, 8) == 6


def test_first_blank_gw_returns_none_when_no_blank_in_range():
    shapes = {5: {"blanks": [], "doubles": []}}
    assert fpl_tools._first_blank_gw(shapes, 5, 6) is None


def test_biggest_double_gw_picks_the_gw_with_most_doubling_teams():
    shapes = {6: {"blanks": [], "doubles": [1, 2]}, 7: {"blanks": [], "doubles": [1, 2, 3, 4]}}
    assert fpl_tools._biggest_double_gw(shapes, 6, 7) == 7


def test_biggest_double_gw_returns_none_when_no_doubles():
    shapes = {6: {"blanks": [], "doubles": []}}
    assert fpl_tools._biggest_double_gw(shapes, 6, 6) is None


def test_tc_target_picks_easiest_single_fixture_for_an_owned_premium():
    elements = {1: {"now_cost": 130, "team": 10, "web_name": "Star"}}
    fixtures = [
        {"event": 6, "team_h": 10, "team_a": 99, "team_h_difficulty": 4, "team_a_difficulty": 2},
        {"event": 7, "team_h": 10, "team_a": 98, "team_h_difficulty": 1, "team_a_difficulty": 5},
    ]
    gw, why = fpl_tools._tc_target(fixtures, elements, {1}, start_gw=6, end_gw=7, prefer_doubles=False)
    assert gw == 7  # FDR 1 beats FDR 4
    assert "Star" in why


def test_tc_target_none_without_an_owned_premium():
    elements = {1: {"now_cost": 55, "team": 10, "web_name": "Cheap"}}  # below the £9.0m premium threshold
    fixtures = [{"event": 6, "team_h": 10, "team_a": 99, "team_h_difficulty": 2, "team_a_difficulty": 2}]
    assert fpl_tools._tc_target(fixtures, elements, {1}, start_gw=6, end_gw=6, prefer_doubles=False) is None


def test_tc_target_prefers_a_double_when_set2_doctrine_applies():
    elements = {1: {"now_cost": 130, "team": 10, "web_name": "Star"}}
    fixtures = [
        {"event": 6, "team_h": 10, "team_a": 99, "team_h_difficulty": 1, "team_a_difficulty": 5},  # easy single, gw6
        {"event": 7, "team_h": 10, "team_a": 98, "team_h_difficulty": 3, "team_a_difficulty": 3},  # double leg 1
        {"event": 7, "team_h": 97, "team_a": 10, "team_h_difficulty": 3, "team_a_difficulty": 3},  # double leg 2
    ]
    gw, why = fpl_tools._tc_target(fixtures, elements, {1}, start_gw=6, end_gw=7, prefer_doubles=True)
    assert gw == 7
    assert "double" in why


# ── candidacy minutes floor — early-season transfer availability ───────────
#
# Regression coverage for a real bug found live at GW3: the candidacy filter
# used fpl_squad_v0.py's 900-minute floor (calibrated against a full *prior*
# season) against bootstrap-static's *this-season* minutes, which is near
# zero for everyone in the season's first ~10 gameweeks. That silently
# excluded every non-owned player from the candidate pool, making `single`/
# `aggressive` infeasible (no player left to bring in) — get_fpl_recommendation
# returned "no legal squad found" even for a completely legal owned squad.


def test_candidacy_minutes_floor_is_zero_before_any_gameweek_has_played():
    assert fpl_tools._candidacy_minutes_floor(start_gw=1) == 0


def test_candidacy_minutes_floor_scales_with_season_progress():
    # GW3: 2 gameweeks played, 180 possible minutes -> half of that as the bar
    assert fpl_tools._candidacy_minutes_floor(start_gw=3) == 90


def test_candidacy_minutes_floor_caps_at_the_full_season_default():
    # Well into the season, the floor settles back at the original 900 bar.
    assert fpl_tools._candidacy_minutes_floor(start_gw=21) == 900


@pytest.mark.asyncio
async def test_early_season_low_minutes_player_is_a_valid_transfer_target(_wired):
    """An available, not-yet-owned player with realistic early-season minutes
    (not fpl_squad_v0.py's full-season 900+) must still be transferable in —
    this is the exact scenario that was broken live at GW3."""
    conn, bootstrap, fixtures = _wired
    squad = _seed_current_squad(conn, bootstrap)
    elements = {e["id"]: e for e in bootstrap["elements"]}

    # Drop every non-owned player's minutes to an early-GW3-realistic figure.
    for el in bootstrap["elements"]:
        if el["id"] not in squad:
            el["minutes"] = 160

    result = await get_fpl_recommendation(conn)
    # The old bug raised OptimiserInfeasible out of solve(transfer_count=1) /
    # solve(transfer_count=2) here, since the candidate pool held nothing but
    # the current squad — get_fpl_recommendation returned {"error": "no legal
    # squad found ..."} instead of this three-option shape.
    assert "error" not in result
    assert {o["id"] for o in result["options"]} == {"hold", "single", "aggressive"}


# ── Lineup and captain sections — Step 1/2 output-contract helpers ─────────
# (PHASE3-BRIEF.md) — pure-function unit tests against hand-built SolveResult/
# Candidate inputs, independent of the MILP itself.


def _cand(eid: int, team: int, pos: str, xp: float) -> Candidate:
    return Candidate(element_id=eid, team_id=team, position=pos, now_cost=50, horizon_xp=xp)


def test_captain_section_margin_clear_when_gap_is_two_or_more():
    elements = {1: {"team": 1, "web_name": "A"}, 2: {"team": 2, "web_name": "B"}, 3: {"team": 3, "web_name": "C"}}
    teams = {1: {"short_name": "AAA"}, 2: {"short_name": "BBB"}, 3: {"short_name": "CCC"}}
    candidate_by_id = {1: _cand(1, 1, "MID", 8.0), 2: _cand(2, 2, "MID", 6.0), 3: _cand(3, 3, "FWD", 5.0)}
    result = SolveResult(squad={1, 2, 3}, xi={1, 2, 3}, captain=1, vice=2)
    cap = fpl_tools._captain_section(result, candidate_by_id, elements, teams, [], gw=1)
    assert cap["margin"] == "clear"
    assert cap["pick"] == 1
    assert cap["xp"] == 8.0
    assert cap["alternatives"][0]["element"] == 2


def test_captain_section_margin_close_between_half_and_two():
    elements = {1: {"team": 1, "web_name": "A"}, 2: {"team": 2, "web_name": "B"}}
    teams = {1: {"short_name": "AAA"}, 2: {"short_name": "BBB"}}
    candidate_by_id = {1: _cand(1, 1, "MID", 7.0), 2: _cand(2, 2, "MID", 6.0)}
    result = SolveResult(squad={1, 2}, xi={1, 2}, captain=1, vice=2)
    cap = fpl_tools._captain_section(result, candidate_by_id, elements, teams, [], gw=1)
    assert cap["margin"] == "close"


def test_captain_section_margin_coin_flip_under_half():
    elements = {1: {"team": 1, "web_name": "A"}, 2: {"team": 2, "web_name": "B"}}
    teams = {1: {"short_name": "AAA"}, 2: {"short_name": "BBB"}}
    candidate_by_id = {1: _cand(1, 1, "MID", 6.2), 2: _cand(2, 2, "MID", 6.0)}
    result = SolveResult(squad={1, 2}, xi={1, 2}, captain=1, vice=2)
    cap = fpl_tools._captain_section(result, candidate_by_id, elements, teams, [], gw=1)
    assert cap["margin"] == "coin-flip"


def test_captain_section_vice_prefers_a_different_day_fixture():
    """A lower-xP starter on a different day beats a higher-xP starter sharing
    the captain's day — PHASE3-BRIEF.md Step 2 rule 2: covers a late benching."""
    elements = {1: {"team": 1, "web_name": "Cap"}, 2: {"team": 2, "web_name": "SameDay"}, 3: {"team": 3, "web_name": "OtherDay"}}
    teams = {1: {"short_name": "T1"}, 2: {"short_name": "T2"}, 3: {"short_name": "T3"}}
    fixtures = [
        {"event": 1, "team_h": 1, "team_a": 9, "team_h_difficulty": 3, "team_a_difficulty": 3, "kickoff_time": "2026-09-05T14:00:00Z"},
        {"event": 1, "team_h": 2, "team_a": 8, "team_h_difficulty": 3, "team_a_difficulty": 3, "kickoff_time": "2026-09-05T16:30:00Z"},
        {"event": 1, "team_h": 3, "team_a": 7, "team_h_difficulty": 3, "team_a_difficulty": 3, "kickoff_time": "2026-09-06T14:00:00Z"},
    ]
    candidate_by_id = {1: _cand(1, 1, "MID", 8.0), 2: _cand(2, 2, "MID", 6.0), 3: _cand(3, 3, "MID", 5.0)}
    result = SolveResult(squad={1, 2, 3}, xi={1, 2, 3}, captain=1, vice=2)
    cap = fpl_tools._captain_section(result, candidate_by_id, elements, teams, fixtures, gw=1)
    assert cap["vice"] == 3  # OtherDay, despite lower xP than SameDay


def test_captain_section_vice_falls_back_to_best_alternative_without_kickoff_data():
    elements = {1: {"team": 1, "web_name": "Cap"}, 2: {"team": 2, "web_name": "Best"}, 3: {"team": 3, "web_name": "Worse"}}
    teams = {1: {"short_name": "T1"}, 2: {"short_name": "T2"}, 3: {"short_name": "T3"}}
    candidate_by_id = {1: _cand(1, 1, "MID", 8.0), 2: _cand(2, 2, "MID", 6.0), 3: _cand(3, 3, "MID", 5.0)}
    result = SolveResult(squad={1, 2, 3}, xi={1, 2, 3}, captain=1, vice=2)
    cap = fpl_tools._captain_section(result, candidate_by_id, elements, teams, [], gw=1)
    assert cap["vice"] == 2  # best available starter — no kickoff data to compare days on


# ── Captain EO tiebreak — Step 4 / FPL-CONTEXT.md §2.3 ──────────────────────


def test_captain_eo_tiebreak_prefers_higher_eo_in_neutral_mode_when_close():
    # xp gap is 1.0 (< 2.0, "close") — solver picked 1 (xp 7.0), but 2 (xp 6.0,
    # higher league EO) should win in neutral mode, matching the field.
    elements = {1: {"team": 1, "web_name": "LowerEO"}, 2: {"team": 2, "web_name": "HigherEO"}}
    teams = {1: {"short_name": "T1"}, 2: {"short_name": "T2"}}
    candidate_by_id = {1: _cand(1, 1, "MID", 7.0), 2: _cand(2, 2, "MID", 6.0)}
    result = SolveResult(squad={1, 2}, xi={1, 2}, captain=1, vice=2)
    cap = fpl_tools._captain_section(
        result, candidate_by_id, elements, teams, [], gw=1,
        eo_by_element={1: 40.0, 2: 90.0}, mode="neutral",
    )
    assert cap["pick"] == 2
    assert cap["margin"] == "close"
    assert "tiebreak" in cap["rationale"].lower()


def test_captain_eo_tiebreak_prefers_lower_eo_in_chase_mode_when_close():
    elements = {1: {"team": 1, "web_name": "HigherXP"}, 2: {"team": 2, "web_name": "Differential"}}
    teams = {1: {"short_name": "T1"}, 2: {"short_name": "T2"}}
    candidate_by_id = {1: _cand(1, 1, "MID", 7.0), 2: _cand(2, 2, "MID", 6.0)}
    result = SolveResult(squad={1, 2}, xi={1, 2}, captain=1, vice=2)
    cap = fpl_tools._captain_section(
        result, candidate_by_id, elements, teams, [], gw=1,
        eo_by_element={1: 90.0, 2: 10.0}, mode="chase",
    )
    assert cap["pick"] == 2  # lower EO preferred when chasing


def test_captain_eo_tiebreak_never_overrides_a_clear_margin():
    # xp gap is 5.0 (>= 2.0, "clear") — EO must not override a settled call.
    elements = {1: {"team": 1, "web_name": "ClearBest"}, 2: {"team": 2, "web_name": "Other"}}
    teams = {1: {"short_name": "T1"}, 2: {"short_name": "T2"}}
    candidate_by_id = {1: _cand(1, 1, "MID", 10.0), 2: _cand(2, 2, "MID", 5.0)}
    result = SolveResult(squad={1, 2}, xi={1, 2}, captain=1, vice=2)
    cap = fpl_tools._captain_section(
        result, candidate_by_id, elements, teams, [], gw=1,
        eo_by_element={1: 10.0, 2: 95.0}, mode="neutral",
    )
    assert cap["pick"] == 1
    assert cap["margin"] == "clear"


def test_captain_eo_tiebreak_no_op_without_eo_data():
    elements = {1: {"team": 1, "web_name": "A"}, 2: {"team": 2, "web_name": "B"}}
    teams = {1: {"short_name": "T1"}, 2: {"short_name": "T2"}}
    candidate_by_id = {1: _cand(1, 1, "MID", 7.0), 2: _cand(2, 2, "MID", 6.0)}
    result = SolveResult(squad={1, 2}, xi={1, 2}, captain=1, vice=2)
    cap = fpl_tools._captain_section(result, candidate_by_id, elements, teams, [], gw=1)
    assert cap["pick"] == 1  # falls back to the solver's own top-xP pick


def test_lineup_changes_empty_when_current_matches_recommended():
    elements = {1: {"element_type": 3}, 2: {"element_type": 2}}
    assert fpl_tools._lineup_changes({1, 2}, {1, 2}, elements, {}, [], {}, gw=1) == []


def test_lineup_changes_detects_a_position_matched_bench_promotion():
    elements = {
        1: {"element_type": 2, "team": 1, "web_name": "Starter"},
        2: {"element_type": 2, "team": 2, "web_name": "BenchPromo"},
    }
    teams = {1: {"short_name": "T1"}, 2: {"short_name": "T2"}}
    candidate_by_id = {1: _cand(1, 1, "DEF", 2.0), 2: _cand(2, 2, "DEF", 6.0)}
    changes = fpl_tools._lineup_changes({1}, {2}, elements, candidate_by_id, [], teams, gw=1)
    assert changes == [{
        "in": 2, "in_name": "BenchPromo", "out": 1, "out_name": "Starter",
        "reason": "BenchPromo projects 6.0 xP (no fixture) vs Starter's 2.0 xP (no fixture)",
    }]


def test_lineup_changes_pairs_across_positions_when_formation_shape_changes():
    elements = {
        1: {"element_type": 2, "team": 1, "web_name": "DroppedDef"},
        2: {"element_type": 3, "team": 2, "web_name": "PromotedMid"},
    }
    teams = {1: {"short_name": "T1"}, 2: {"short_name": "T2"}}
    candidate_by_id = {1: _cand(1, 1, "DEF", 2.0), 2: _cand(2, 2, "MID", 6.0)}
    changes = fpl_tools._lineup_changes({1}, {2}, elements, candidate_by_id, [], teams, gw=1)
    assert len(changes) == 1
    assert changes[0]["in"] == 2
    assert changes[0]["in_name"] == "PromotedMid"
    assert changes[0]["out"] == 1
    assert changes[0]["out_name"] == "DroppedDef"


def test_lineup_section_formation_string_and_bench_ordering():
    elements = {
        1: {"element_type": 1, "team": 1, "web_name": "GK1"},
        2: {"element_type": 1, "team": 1, "web_name": "GK2"},
        3: {"element_type": 2, "team": 2, "web_name": "DEF1"},
        4: {"element_type": 2, "team": 2, "web_name": "DEF2"},
        5: {"element_type": 3, "team": 3, "web_name": "MID1"},
        6: {"element_type": 4, "team": 4, "web_name": "FWD1"},
    }
    teams = {1: {"short_name": "T1"}, 2: {"short_name": "T2"}, 3: {"short_name": "T3"}, 4: {"short_name": "T4"}}
    candidate_by_id = {
        eid: _cand(eid, el["team"], fpl_tools._OPT_POS[el["element_type"]], float(eid))
        for eid, el in elements.items()
    }
    result = SolveResult(squad=set(elements), xi={1, 3, 4, 5, 6}, captain=5, vice=6)
    lineup = fpl_tools._lineup_section(
        result, current_starting=set(), elements=elements, teams=teams, fixtures=[], gw=1, candidate_by_id=candidate_by_id,
    )
    assert lineup["formation"] == "2-1-1"
    assert len(lineup["xi"]) == 5
    assert lineup["bench"] == [{"element": 2, "name": "GK2", "pos": "GK", "fixture": "no fixture", "difficulty": None, "order": 1}]


# ── get_fpl_recommendation ──────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_get_fpl_recommendation_errors_without_a_synced_squad(_wired):
    conn, bootstrap, fixtures = _wired
    result = await get_fpl_recommendation(conn)
    assert "error" in result


@pytest.mark.asyncio
async def test_get_fpl_recommendation_returns_the_output_contract_shape(_wired):
    conn, bootstrap, fixtures = _wired
    _seed_current_squad(conn, bootstrap)

    result = await get_fpl_recommendation(conn)

    assert set(result.keys()) == {
        "gameweek", "deadline_local", "recommended", "options", "lineup", "captain", "chip", "warnings",
    }
    assert result["recommended"] in {"hold", "single", "aggressive"}
    option_ids = {o["id"] for o in result["options"]}
    assert option_ids == {"hold", "single", "aggressive"}
    assert "hold" in option_ids  # §8: hold appears in every option set
    for opt in result["options"]:
        assert set(opt.keys()) == {"id", "label", "transfers", "xp_delta", "hit", "rationale"}
        for pair in opt["transfers"]:
            assert set(pair.keys()) == {"out", "out_name", "in", "in_name"}
    assert set(result["lineup"].keys()) == {"xi", "bench", "changes_from_current", "formation"}
    assert len(result["lineup"]["xi"]) == 11
    assert len(result["lineup"]["bench"]) == 4
    for row in result["lineup"]["xi"]:
        assert set(row.keys()) == {"element", "name", "pos", "fixture", "difficulty"}
    for row in result["lineup"]["bench"]:
        assert set(row.keys()) == {"element", "name", "pos", "fixture", "difficulty", "order"}
    for change in result["lineup"]["changes_from_current"]:
        assert set(change.keys()) == {"in", "in_name", "out", "out_name", "reason"}
    assert set(result["captain"].keys()) == {
        "pick", "pick_name", "xp", "alternatives", "vice", "vice_name", "rationale", "margin",
    }
    assert result["captain"]["margin"] in {"clear", "close", "coin-flip"}
    for alt in result["captain"]["alternatives"]:
        assert set(alt.keys()) == {"element", "name", "xp", "fixture", "difficulty"}
    assert set(result["chip"].keys()) == {"play", "plan"}
    assert isinstance(result["warnings"], list)


@pytest.mark.asyncio
async def test_get_fpl_recommendation_hold_has_zero_xp_delta_and_no_transfers(_wired):
    conn, bootstrap, fixtures = _wired
    _seed_current_squad(conn, bootstrap)

    result = await get_fpl_recommendation(conn)
    hold = next(o for o in result["options"] if o["id"] == "hold")
    assert hold["xp_delta"] == 0.0
    assert hold["transfers"] == []
    assert hold["hit"] == 0


@pytest.mark.asyncio
async def test_get_fpl_recommendation_logs_xp_predictions(_wired):
    conn, bootstrap, fixtures = _wired
    _seed_current_squad(conn, bootstrap)

    await get_fpl_recommendation(conn)

    preds = get_xp_predictions(conn, gw=2, model_version="v0")
    assert len(preds) > 0
    assert all(v >= 0 for v in preds.values())


@pytest.mark.asyncio
async def test_get_fpl_recommendation_captain_is_in_the_recommended_options_xi_flavour(_wired):
    conn, bootstrap, fixtures = _wired
    _seed_current_squad(conn, bootstrap)

    result = await get_fpl_recommendation(conn)
    captain_id = result["captain"]["pick"]
    alt_ids = {a["element"] for a in result["captain"]["alternatives"]}
    assert captain_id not in alt_ids
    assert len(result["captain"]["alternatives"]) <= 2


@pytest.mark.asyncio
async def test_forced_inclusion_appears_in_the_squad_and_quotes_a_cost(_wired):
    conn, bootstrap, fixtures = _wired
    squad = _seed_current_squad(conn, bootstrap)
    elements = {e["id"]: e for e in bootstrap["elements"]}

    # The single worst-xP MID not currently owned — virtually guaranteed to cost
    # something relative to the unconstrained best, so the cost warning is testable.
    target = min(
        (eid for eid, el in elements.items() if el["element_type"] == 3 and eid not in squad),
        key=lambda eid: float(elements[eid]["points_per_game"]),
    )
    name = elements[target]["web_name"]

    result = await get_fpl_recommendation(conn, force_in=[name])

    single_or_aggressive_has_target = any(
        any(pair["in"] == target for pair in opt["transfers"])
        for opt in result["options"] if opt["id"] in ("single", "aggressive")
    )
    assert single_or_aggressive_has_target
    # §6: a forced inclusion quotes its xP cost relative to the unconstrained best.
    assert any("costs" in w and "xP" in w for w in result["warnings"])


@pytest.mark.asyncio
async def test_force_in_preference_persists_to_next_call(_wired):
    conn, bootstrap, fixtures = _wired
    _seed_current_squad(conn, bootstrap)
    elements = {e["id"]: e for e in bootstrap["elements"]}
    target = next(iter(elements))
    name = elements[target]["web_name"]

    await get_fpl_recommendation(conn, force_in=[name])
    rows = get_active_preferences(conn, fpl_tools.now_utc().isoformat())
    assert any(r["kind"] == "force_in" and r["value"] == str(target) for r in rows)


@pytest.mark.asyncio
async def test_unresolvable_force_in_name_reported_as_warning_not_a_crash(_wired):
    conn, bootstrap, fixtures = _wired
    _seed_current_squad(conn, bootstrap)

    result = await get_fpl_recommendation(conn, force_in=["Definitely Not A Real Player"])
    assert "error" not in result
    assert any("Definitely Not A Real Player" in w for w in result["warnings"])


# ── Validator wiring: a failed validation blocks the recommendation ────────


@pytest.mark.asyncio
async def test_recommendation_not_returned_when_validation_fails(monkeypatch, _wired):
    conn, bootstrap, fixtures = _wired
    _seed_current_squad(conn, bootstrap)

    from services.fpl_validate import ValidationResult
    monkeypatch.setattr(
        fpl_tools, "validate_solve",
        lambda *a, **kw: ValidationResult(valid=False, errors=["synthetic failure for this test"]),
    )

    result = await get_fpl_recommendation(conn)
    assert "error" in result
    assert "synthetic failure" in result["error"]


# ── cost_basis: the initial-squad (never-transferred) path ─────────────────
#
# A player absent from the transfer log isn't an unknown — they're an initial
# squad pick, and bootstrap-static's cost_change_start gives their exact
# season-start price. Using now_cost as the fallback (the old bug) overstated
# funds for anyone who'd risen, since it silently dropped the 50% sell-on fee.


def test_cost_basis_initial_squad_path_handles_a_riser():
    # £6.0m -> £6.3m: cost_change_start=+3 (risen 3 tenths since season start).
    elements = {1: {"id": 1, "now_cost": 63, "cost_change_start": 3}}
    basis = cost_basis({1}, transfer_rows=[], elements=elements)
    assert basis[1] == 60  # start-of-season price, not now_cost

    selling_price = compute_selling_price(basis[1], elements[1]["now_cost"])
    assert selling_price == 61  # keeps half the rise, rounded down — not 63


def test_cost_basis_initial_squad_path_handles_a_faller():
    # £6.0m -> £5.7m: cost_change_start=-3 (fallen 3 tenths since season start).
    elements = {2: {"id": 2, "now_cost": 57, "cost_change_start": -3}}
    basis = cost_basis({2}, transfer_rows=[], elements=elements)
    assert basis[2] == 60  # start-of-season price, not now_cost

    selling_price = compute_selling_price(basis[2], elements[2]["now_cost"])
    assert selling_price == 57  # eats the whole fall


def test_cost_basis_initial_squad_path_handles_a_riser_and_a_faller_together():
    elements = {
        1: {"id": 1, "now_cost": 63, "cost_change_start": 3},   # £6.0m -> £6.3m
        2: {"id": 2, "now_cost": 57, "cost_change_start": -3},  # £6.0m -> £5.7m
    }
    basis = cost_basis({1, 2}, transfer_rows=[], elements=elements)
    assert basis == {1: 60, 2: 60}

    selling_price = {eid: compute_selling_price(basis[eid], elements[eid]["now_cost"]) for eid in (1, 2)}
    assert selling_price == {1: 61, 2: 57}


def test_cost_basis_prefers_a_real_transfer_over_cost_change_start():
    # element 3 was transferred in at £5.5m (65 tenths would be wrong to use).
    elements = {3: {"id": 3, "now_cost": 60, "cost_change_start": 10}}  # would wrongly imply start=50
    transfer_rows = [{"element_in": 3, "element_in_cost": 55, "time": "2026-09-01T00:00:00Z"}]
    basis = cost_basis({3}, transfer_rows, elements)
    assert basis[3] == 55  # the real recorded buy price, not the cost_change_start fallback


def test_cost_basis_missing_cost_change_start_defaults_to_no_drift():
    elements = {4: {"id": 4, "now_cost": 50}}  # field absent entirely
    basis = cost_basis({4}, transfer_rows=[], elements=elements)
    assert basis[4] == 50


# ── Money self-check wired into get_fpl_recommendation ──────────────────────


@pytest.mark.asyncio
async def test_get_fpl_recommendation_refuses_when_reported_value_disagrees(monkeypatch, _wired):
    conn, bootstrap, fixtures = _wired
    _seed_current_squad(conn, bootstrap)

    async def _bad_entry_history(team_id):
        return {"current": [{"event": 1, "points": 60, "total_points": 60, "overall_rank": 100000,
                              "bank": 5, "value": 1, "event_transfers": 0, "event_transfers_cost": 0}],
                "chips": []}

    monkeypatch.setattr(fpl_tools.fpl_client, "entry_history", _bad_entry_history)

    result = await get_fpl_recommendation(conn)
    assert "error" in result
    assert "sanity check failed" in result["error"]
