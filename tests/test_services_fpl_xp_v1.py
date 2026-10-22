"""Tests for services/fpl_xp_v1.py — the scoring-rule-based xP model (Oct 2026)."""

from __future__ import annotations

import math

import pytest

from services import fpl_xp_v1 as v1

_SCORING = v1.scoring_from_bootstrap({})  # the official 2026/27 fallback table


def _el(**overrides) -> dict:
    """A regular starter: 7 starts from 7 gameweeks, 90 minutes each, no stats."""
    base = {
        "team": 1, "status": "a", "chance_of_playing_next_round": None,
        "minutes": 630, "starts": 7, "points_per_game": "0",
        "expected_goals_per_90": 0, "expected_assists_per_90": 0,
        "expected_goals_conceded_per_90": 0, "saves_per_90": 0,
        "defensive_contribution_per_90": 0, "bonus": 0, "yellow_cards": 0,
        "red_cards": 0, "own_goals": 0, "penalties_missed": 0, "penalties_saved": 0,
    }
    return {**base, **overrides}


# ── Probability helpers ──────────────────────────────────────────────────────


def test_poisson_at_least_matches_known_values():
    assert v1.poisson_at_least(10.0, 10) == pytest.approx(0.5421, abs=1e-4)
    assert v1.poisson_at_least(2.0, 0) == 1.0
    assert v1.poisson_at_least(0.0, 1) == 0.0


def test_expected_floor_div_counts_only_complete_groups():
    # Two saves never earn the "1 per 3 saves" point; E[floor] is tiny at a
    # low rate and approaches lam/3 at a high one.
    assert v1.expected_floor_div(0.0, 3) == 0.0
    assert v1.expected_floor_div(0.5, 3) < 0.02
    # The discarded remainder averages ~(3-1)/2 = 1, so ~(30 - 1) / 3.
    assert v1.expected_floor_div(30.0, 3) == pytest.approx((30 - 1) / 3, abs=0.1)


# ── Scoring rules come from FPL itself ───────────────────────────────────────


def test_scoring_is_read_from_fpls_game_config():
    data = {"game_config": {"scoring": {"goals_scored": {"GKP": 99, "DEF": 6, "MID": 5, "FWD": 4}}}}
    scoring = v1.scoring_from_bootstrap(data)
    assert scoring["goals_scored"]["GKP"] == 99  # whatever FPL says wins
    assert scoring["assists"] == 3  # missing fields fall back to the 2026/27 table


def test_fallback_table_is_the_official_2026_27_scoring():
    # As FPL's own game_config.scoring read on 22 Oct 2026.
    assert _SCORING["goals_scored"] == {"GKP": 10, "DEF": 6, "MID": 5, "FWD": 4}
    assert _SCORING["clean_sheets"] == {"GKP": 4, "DEF": 4, "MID": 1, "FWD": 0}
    assert _SCORING["defensive_contribution"]["DEF"] == 2
    assert (_SCORING["yellow_cards"], _SCORING["red_cards"], _SCORING["own_goals"]) == (-1, -3, -2)
    assert (_SCORING["penalties_missed"], _SCORING["penalties_saved"]) == (-2, 5)


# ── Each scoring component ──────────────────────────────────────────────────


def test_a_player_who_never_plays_scores_nothing():
    c = v1.fixture_xp(_el(minutes=0, starts=0, points_per_game="0"), "MID", 3, _SCORING, 7)
    assert c["xp"] == 0.0


def test_regular_starter_gets_full_appearance_points():
    c = v1.fixture_xp(_el(), "MID", 3, _SCORING, 7)
    assert c["appearance"] == pytest.approx(2.0)


def test_a_doubtful_player_is_roughly_halved():
    full = v1.fixture_xp(_el(expected_goals_per_90=0.5), "FWD", 3, _SCORING, 7)
    half = v1.fixture_xp(_el(expected_goals_per_90=0.5, status="d", chance_of_playing_next_round=50),
                         "FWD", 3, _SCORING, 7)
    assert half["xp"] == pytest.approx(full["xp"] * 0.5, rel=0.01)


def test_goals_use_the_positions_points_per_goal():
    for pos, pts in [("GKP", 10), ("DEF", 6), ("MID", 5), ("FWD", 4)]:
        c = v1.fixture_xp(_el(expected_goals_per_90=0.5), pos, 3, _SCORING, 7)
        assert c["goals"] == pytest.approx(0.5 * pts)


def test_clean_sheet_is_poisson_zero_conceded_and_worth_nothing_to_a_forward():
    defender = v1.fixture_xp(_el(expected_goals_conceded_per_90=1.0), "DEF", 3, _SCORING, 7)
    forward = v1.fixture_xp(_el(expected_goals_conceded_per_90=1.0), "FWD", 3, _SCORING, 7)
    assert defender["clean_sheet"] == pytest.approx(4 * math.exp(-1.0))
    assert forward["clean_sheet"] == 0.0


def test_goalkeeper_gets_saves_and_goals_conceded_deductions():
    gk = v1.fixture_xp(_el(saves_per_90=4.0, expected_goals_conceded_per_90=1.5), "GKP", 3, _SCORING, 7)
    assert gk["saves"] > 0.8  # ~1 point per 3 saves at 4 saves a game
    assert gk["goals_conceded"] < 0  # -1 per 2 conceded
    outfield = v1.fixture_xp(_el(saves_per_90=4.0), "DEF", 3, _SCORING, 7)
    assert outfield["saves"] == 0.0


def test_defensive_contribution_uses_the_position_threshold():
    # 11 actions per 90: a defender usually clears 10; a midfielder rarely 12.
    defender = v1.fixture_xp(_el(defensive_contribution_per_90=11), "DEF", 3, _SCORING, 7)
    midfielder = v1.fixture_xp(_el(defensive_contribution_per_90=11), "MID", 3, _SCORING, 7)
    keeper = v1.fixture_xp(_el(defensive_contribution_per_90=11), "GKP", 3, _SCORING, 7)
    assert defender["defensive_contribution"] == pytest.approx(2 * v1.poisson_at_least(11, 10))
    assert midfielder["defensive_contribution"] == pytest.approx(2 * v1.poisson_at_least(11, 12))
    assert defender["defensive_contribution"] > midfielder["defensive_contribution"]
    assert keeper["defensive_contribution"] == 0.0


def test_discipline_and_penalties_are_shrunk_per_start_rates():
    # 7 starts: 2 yellows, 1 red, 1 own goal, 1 penalty missed — each rate is
    # pulled towards a league-typical one so a handful of games can't dominate.
    el = _el(yellow_cards=2, red_cards=1, own_goals=1, penalties_missed=1)
    c = v1.fixture_xp(el, "MID", 3, _SCORING, 7)
    expected = (v1.shrunk_rate(2, 7, "yellow_cards") * -1 + v1.shrunk_rate(1, 7, "red_cards") * -3
                + v1.shrunk_rate(1, 7, "own_goals") * -2 + v1.shrunk_rate(1, 7, "penalties_missed") * -2)
    assert c["discipline"] == pytest.approx(expected)


def test_one_penalty_save_in_seven_games_does_not_dominate():
    # Live GW8 check, 22 Oct 2026: Raya's 1 penalty save in 7 starts was
    # worth +0.71 xP every game unshrunk — more than his save points.
    gk = v1.fixture_xp(_el(penalties_saved=1), "GKP", 3, _SCORING, 7)
    assert gk["penalties_saved"] < 0.35
    assert gk["penalties_saved"] > 0  # still some credit


def test_shrunk_rate_moves_from_the_prior_towards_the_players_own_rate():
    prior = v1.shrunk_rate(0, 0, "bonus")
    assert prior == pytest.approx(v1.RATE_PRIORS["bonus"][0])
    assert v1.shrunk_rate(14, 7, "bonus") > v1.shrunk_rate(14, 7, "bonus") * 0.5 > prior
    assert v1.shrunk_rate(400, 200, "bonus") == pytest.approx(2.0, abs=0.1)  # lots of data: own rate


def test_bonus_is_shrunk_bonus_per_start():
    c = v1.fixture_xp(_el(bonus=14), "FWD", 3, _SCORING, 7)
    assert c["bonus"] == pytest.approx(v1.shrunk_rate(14, 7, "bonus"))


def test_an_easier_fixture_raises_attack_and_lowers_goals_conceded():
    el = _el(expected_goals_per_90=0.6, expected_goals_conceded_per_90=1.2)
    easy = v1.fixture_xp(el, "DEF", 2, _SCORING, 7)
    hard = v1.fixture_xp(el, "DEF", 5, _SCORING, 7)
    assert easy["goals"] > hard["goals"]
    assert easy["clean_sheet"] > hard["clean_sheet"]


def test_points_per_game_is_only_a_light_blend():
    c = v1.fixture_xp(_el(points_per_game="10"), "MID", 3, _SCORING, 7)
    assert c["xp"] == pytest.approx(0.8 * c["model"] + 0.2 * 10)


# ── Horizon: blanks, doubles, discount ──────────────────────────────────────


def _fixtures():
    return [
        {"event": 1, "team_h": 1, "team_a": 2, "team_h_difficulty": 3, "team_a_difficulty": 3},
        # GW2 blank for team 1; GW3 double.
        {"event": 3, "team_h": 1, "team_a": 3, "team_h_difficulty": 3, "team_a_difficulty": 3},
        {"event": 3, "team_h": 4, "team_a": 1, "team_h_difficulty": 3, "team_a_difficulty": 3},
    ]


def test_horizon_scores_zero_on_a_blank_and_double_on_a_double():
    el = _el(expected_goals_per_90=0.5)
    by_gw = v1.horizon_xp(el, "FWD", _fixtures(), start_gw=1, horizon=3, scoring=_SCORING, gws_played=7)
    assert by_gw[2] == 0.0
    assert by_gw[3] == pytest.approx(2 * by_gw[1])


def test_has_v1_inputs_needs_the_per_90_fields():
    assert v1.has_v1_inputs(_el())
    assert not v1.has_v1_inputs({"points_per_game": "5", "minutes": 900})
