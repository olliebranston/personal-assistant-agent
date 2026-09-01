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
    XP_PREDICTION_DDL,
    get_active_preferences,
    get_xp_predictions,
    replace_my_picks,
)
from services.fpl_optimiser import compute_selling_price
from tools.fpl import cost_basis, get_fpl_calendar, get_fpl_chips, get_fpl_recommendation, sync_gameweeks_from_bootstrap

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
        "gameweek", "deadline_local", "recommended", "options", "captain", "chip", "warnings",
    }
    assert result["recommended"] in {"hold", "single", "aggressive"}
    option_ids = {o["id"] for o in result["options"]}
    assert option_ids == {"hold", "single", "aggressive"}
    assert "hold" in option_ids  # §8: hold appears in every option set
    for opt in result["options"]:
        assert set(opt.keys()) == {"id", "label", "transfers", "xp_delta", "hit", "rationale"}
    assert set(result["captain"].keys()) == {"pick", "alternatives", "rationale"}
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
    assert captain_id not in result["captain"]["alternatives"]
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
