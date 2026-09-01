"""Tests for services/fpl_xp.py — the v0 xP heuristic (PHASE2-BRIEF.md §2 + §8)."""

from __future__ import annotations

import pytest

from services.fpl_xp import (
    availability,
    fixture_multiplier,
    horizon_xp,
    reliability,
    team_fdr_for_gw,
    xp,
)


# ── §8's exact hand-computed regression case ────────────────────────────────


def test_xp_matches_hand_computed_case_from_brief():
    result = xp(points_per_game=6.7, minutes=3065, avg_fdr=2.83, status="a")
    assert result["reliability"] == pytest.approx(1.0)
    assert result["fixture"] == pytest.approx(1.017, abs=1e-6)
    assert result["xp"] == pytest.approx(6.81, abs=0.01)


def test_xp_avail_scales_by_chance_of_playing_when_not_fully_available():
    result = xp(points_per_game=5.0, minutes=2000, avg_fdr=3.0, status="d",
                chance_of_playing_next_round=75)
    assert result["avail"] == pytest.approx(0.75)
    assert result["fixture"] == pytest.approx(1.0)  # avg_fdr == 3.0 baseline
    assert result["xp"] == pytest.approx(5.0 * result["reliability"] * 1.0 * 0.75)


def test_xp_zero_for_unavailable_player_with_no_chance_figure():
    result = xp(points_per_game=8.0, minutes=3000, avg_fdr=2.0, status="i")
    assert result["avail"] == 0.0
    assert result["xp"] == 0.0


# ── availability / reliability / fixture_multiplier in isolation ────────────


def test_availability_status_a_is_always_1():
    assert availability("a", None) == 1.0
    assert availability("a", 25) == 1.0  # status takes priority over the figure


def test_availability_doubtful_without_chance_figure_defaults_half():
    assert availability("d", None) == 0.5


def test_reliability_caps_at_1_above_2500_minutes():
    assert reliability(2500) == 1.0
    assert reliability(5000) == 1.0
    assert reliability(0) == pytest.approx(0.55)
    assert reliability(1250) == pytest.approx(0.55 + 0.45 * 0.5)


def test_fixture_multiplier_easier_than_average_scales_up():
    assert fixture_multiplier(3.0) == pytest.approx(1.0)
    assert fixture_multiplier(2.0) == pytest.approx(1.1)
    assert fixture_multiplier(4.0) == pytest.approx(0.9)


# ── team_fdr_for_gw / horizon_xp — blank/double awareness ───────────────────


_FIXTURES = [
    {"event": 10, "team_h": 1, "team_a": 2, "team_h_difficulty": 2, "team_a_difficulty": 4},
    {"event": 11, "team_h": 1, "team_a": 3, "team_h_difficulty": 3, "team_a_difficulty": 3},
    {"event": 11, "team_h": 4, "team_a": 1, "team_h_difficulty": 3, "team_a_difficulty": 5},
    # GW12: team 1 blanks entirely.
]


def test_team_fdr_for_gw_single_fixture():
    avg_fdr, count = team_fdr_for_gw(_FIXTURES, team_id=1, gw=10)
    assert avg_fdr == 2.0  # team 1 is home, team_h_difficulty=2
    assert count == 1


def test_team_fdr_for_gw_double_fixture_averages_difficulty():
    avg_fdr, count = team_fdr_for_gw(_FIXTURES, team_id=1, gw=11)
    assert count == 2
    assert avg_fdr == pytest.approx((3 + 5) / 2)  # away@3 home... team 1 away difficulty=5 in 2nd


def test_team_fdr_for_gw_blank_returns_none():
    avg_fdr, count = team_fdr_for_gw(_FIXTURES, team_id=1, gw=12)
    assert avg_fdr is None
    assert count == 0


def test_horizon_xp_blank_gw_contributes_zero():
    result = horizon_xp(
        points_per_game=6.0, minutes=3000, status="a", chance_of_playing_next_round=None,
        team_id=1, fixtures=_FIXTURES, start_gw=10, horizon=3, discount=1.0,
    )
    assert result[10] > 0
    assert result[11] > 0
    assert result[12] == 0.0  # blank


def test_horizon_xp_double_gw_is_roughly_double_single_gw_before_discount():
    result = horizon_xp(
        points_per_game=6.0, minutes=3000, status="a", chance_of_playing_next_round=None,
        team_id=1, fixtures=_FIXTURES, start_gw=10, horizon=2, discount=1.0,
    )
    # GW10: 1 fixture at fdr=2 -> fixture_mult=1.1. GW11: 2 fixtures at avg fdr=4 -> fixture_mult=0.9,
    # doubled for the fixture count.
    single_gw_equiv = 6.0 * 1.0 * 0.9  # same reliability(3000)=1.0, same avg fdr used for fixture_mult
    assert result[11] == pytest.approx(single_gw_equiv * 2, rel=1e-6)


def test_horizon_xp_discount_reduces_later_gameweeks():
    result = horizon_xp(
        points_per_game=6.0, minutes=3000, status="a", chance_of_playing_next_round=None,
        team_id=1, fixtures=_FIXTURES, start_gw=10, horizon=2, discount=0.5,
    )
    undiscounted = horizon_xp(
        points_per_game=6.0, minutes=3000, status="a", chance_of_playing_next_round=None,
        team_id=1, fixtures=_FIXTURES, start_gw=10, horizon=2, discount=1.0,
    )
    assert result[10] == pytest.approx(undiscounted[10])  # i=0, discount**0 == 1
    assert result[11] == pytest.approx(undiscounted[11] * 0.5)
