"""Tests for services/fpl_validate.py — the firewall (PHASE2-BRIEF.md §4 + §8).

Written to try to BREAK the validator, per the brief's own instruction —
each test corrupts one specific thing and checks it's caught, plus one test
confirming a genuinely legal result passes clean.
"""

from __future__ import annotations

from services.fpl_optimiser import SolveResult
from services.fpl_validate import validate_solve

_ELEMENTS = {
    1: {"id": 1, "team": 1, "element_type": 1, "now_cost": 45, "status": "a"},  # GK
    2: {"id": 2, "team": 1, "element_type": 1, "now_cost": 40, "status": "a"},  # GK
    3: {"id": 3, "team": 2, "element_type": 2, "now_cost": 50, "status": "a"},  # DEF
    4: {"id": 4, "team": 3, "element_type": 2, "now_cost": 55, "status": "a"},  # DEF
    5: {"id": 5, "team": 4, "element_type": 2, "now_cost": 45, "status": "a"},  # DEF
    6: {"id": 6, "team": 5, "element_type": 2, "now_cost": 45, "status": "a"},  # DEF
    7: {"id": 7, "team": 6, "element_type": 2, "now_cost": 40, "status": "a"},  # DEF
    8: {"id": 8, "team": 7, "element_type": 3, "now_cost": 70, "status": "a"},  # MID
    9: {"id": 9, "team": 8, "element_type": 3, "now_cost": 55, "status": "a"},  # MID
    10: {"id": 10, "team": 9, "element_type": 3, "now_cost": 50, "status": "a"},  # MID
    11: {"id": 11, "team": 10, "element_type": 3, "now_cost": 50, "status": "a"},  # MID
    12: {"id": 12, "team": 11, "element_type": 3, "now_cost": 45, "status": "a"},  # MID
    13: {"id": 13, "team": 12, "element_type": 4, "now_cost": 90, "status": "a"},  # FWD
    14: {"id": 14, "team": 13, "element_type": 4, "now_cost": 60, "status": "a"},  # FWD
    15: {"id": 15, "team": 14, "element_type": 4, "now_cost": 55, "status": "a"},  # FWD
    99: {"id": 99, "team": 2, "element_type": 2, "now_cost": 55, "status": "i", "news": "Hamstring"},
}

_LEGAL_SQUAD = set(range(1, 16))


def _legal_result(**overrides) -> SolveResult:
    base = dict(
        squad=set(_LEGAL_SQUAD),
        xi={1, 3, 4, 5, 8, 9, 10, 11, 13, 14, 15},
        captain=13,
        vice=8,
        transfers_out=[],
        transfers_in=[],
        hit=0,
        objective=42.0,
        total_horizon_xp=42.0,
    )
    base.update(overrides)
    return SolveResult(**base)


# ── A genuinely legal result passes ──────────────────────────────────────────


def test_legal_squad_passes_validation():
    result = _legal_result()
    outcome = validate_solve(result, _ELEMENTS, selling_price={}, bank=0)
    assert outcome.valid is True
    assert outcome.errors == []


# ── Try to break it ──────────────────────────────────────────────────────────


def test_rejects_a_phantom_player_not_in_bootstrap():
    result = _legal_result(squad=(_LEGAL_SQUAD - {15}) | {12345})
    outcome = validate_solve(result, _ELEMENTS, selling_price={}, bank=0)
    assert outcome.valid is False
    assert any("does not exist" in e for e in outcome.errors)


def test_rejects_four_players_from_one_club():
    # Swap element 15 (club 14) for a 2nd club-2 player -> club 2 has 2, still legal.
    # Instead directly corrupt club counts: give four different elements team=2.
    corrupted_elements = dict(_ELEMENTS)
    for eid in (3, 4, 5, 6):
        corrupted_elements[eid] = {**corrupted_elements[eid], "team": 2}
    result = _legal_result()
    outcome = validate_solve(result, corrupted_elements, selling_price={}, bank=0)
    assert outcome.valid is False
    assert any("max is 3" in e for e in outcome.errors)


def test_rejects_wrong_position_counts():
    corrupted_elements = dict(_ELEMENTS)
    corrupted_elements[15] = {**corrupted_elements[15], "element_type": 3}  # FWD masquerading as MID
    result = _legal_result()
    outcome = validate_solve(result, corrupted_elements, selling_price={}, bank=0)
    assert outcome.valid is False
    assert any("FWD count is 2" in e for e in outcome.errors)
    assert any("MID count is 6" in e for e in outcome.errors)


def test_rejects_unaffordable_transfer():
    # £4.0m sold, £0 bank, but the "incoming" player costs £9.0m.
    result = _legal_result(transfers_out=[7], transfers_in=[13])
    outcome = validate_solve(result, _ELEMENTS, selling_price={7: 40}, bank=0)
    assert outcome.valid is False
    assert any("unaffordable" in e for e in outcome.errors)


def test_rejects_price_drift_from_expected():
    result = _legal_result()
    outcome = validate_solve(result, _ELEMENTS, selling_price={}, bank=0,
                              expected_prices={13: 85})  # solver saw 8.5m, live is 9.0m
    assert outcome.valid is False
    assert any("price drifted" in e for e in outcome.errors)


def test_rejects_flagged_incoming_player_with_no_warning():
    result = _legal_result(squad=(_LEGAL_SQUAD - {6}) | {99}, transfers_out=[6], transfers_in=[99])
    outcome = validate_solve(result, _ELEMENTS, selling_price={6: 45}, bank=100)
    assert outcome.valid is False
    assert any("flagged" in e for e in outcome.errors)


def test_flagged_incoming_player_passes_when_warned():
    result = _legal_result(squad=(_LEGAL_SQUAD - {6}) | {99}, transfers_out=[6], transfers_in=[99])
    outcome = validate_solve(result, _ELEMENTS, selling_price={6: 45}, bank=100,
                              warned_element_ids={99})
    assert outcome.valid is True


def test_rejects_captain_not_in_starting_xi():
    result = _legal_result(captain=12)  # 12 is on the bench in _legal_result's xi
    outcome = validate_solve(result, _ELEMENTS, selling_price={}, bank=0)
    assert outcome.valid is False
    assert any("captain" in e.lower() for e in outcome.errors)


def test_rejects_wrong_squad_size():
    result = _legal_result(squad=_LEGAL_SQUAD - {15})
    outcome = validate_solve(result, _ELEMENTS, selling_price={}, bank=0)
    assert outcome.valid is False
    assert any("not 15" in e for e in outcome.errors)
