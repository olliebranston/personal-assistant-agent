"""Tests for services/fpl_league.py — PHASE3-BRIEF.md Step 4 + PHASE3-ADDENDUM.md.

Verified against §A's known-answer GW2 rival data (real API, 1 Sept 2026)
rather than a synthetic squad, per the addendum's own instruction.
"""

from __future__ import annotations

from services.fpl_league import (
    eo_mode,
    gw_review_decomposition,
    league_eo,
    league_template_holes,
    my_differentials,
    rival_transfers_from_diff,
)
from tests.fixtures.rivals_gw2 import (
    CALAFIORI,
    FERNANDES,
    HAALAND,
    JOAO_PEDRO,
    KINSKY,
    MBEUMO,
    NDIAYE,
    OLLIE_GW2,
    RIVALS_GW2,
    parse_picks,
    squad_ids,
)

_PICKS_BY_ENTRY = {entry_id: parse_picks(spec) for entry_id, (_name, spec) in RIVALS_GW2.items()}
_SQUAD_BY_ENTRY = {entry_id: squad_ids(spec) for entry_id, (_name, spec) in RIVALS_GW2.items()}
_OLLIE_SQUAD = squad_ids(OLLIE_GW2)


# ── league_eo — §A's exact table ────────────────────────────────────────────


def test_league_eo_fernandes_143_percent():
    assert league_eo(_PICKS_BY_ENTRY, FERNANDES) == 100 * 10 / 7  # 142.857...
    assert round(league_eo(_PICKS_BY_ENTRY, FERNANDES)) == 143


def test_league_eo_haaland_129_percent():
    assert round(league_eo(_PICKS_BY_ENTRY, HAALAND)) == 129


def test_league_eo_joao_pedro_owned_by_everyone_captained_by_none_is_exactly_100():
    # §A's stated edge case: owning him is mandatory, captaining him is pure
    # differential — a 100% EO with zero captaincy weighting.
    assert league_eo(_PICKS_BY_ENTRY, JOAO_PEDRO) == 100.0


def test_league_eo_calafiori_86_percent():
    assert round(league_eo(_PICKS_BY_ENTRY, CALAFIORI)) == 86


def test_league_eo_mbeumo_71_percent():
    assert round(league_eo(_PICKS_BY_ENTRY, MBEUMO)) == 71


def test_league_eo_ndiaye_and_kinsky_both_57_percent():
    assert round(league_eo(_PICKS_BY_ENTRY, NDIAYE)) == 57
    assert round(league_eo(_PICKS_BY_ENTRY, KINSKY)) == 57


def test_league_eo_can_exceed_100_percent():
    # §A: "Anyone who writes this assuming a 0-100 range will produce a
    # broken progress bar or a clamped value."
    assert league_eo(_PICKS_BY_ENTRY, FERNANDES) > 100.0


def test_league_eo_zero_active_rivals_returns_zero_not_a_crash():
    assert league_eo({}, FERNANDES) == 0.0


# ── my_differentials / league_template_holes — §A's exact sets ─────────────


def test_my_differentials_matches_the_exact_six_named_players():
    # §A: Lacroix, Van Hecke, Stach, Muñoz, Mukiele, Sangaré
    assert my_differentials(_OLLIE_SQUAD, _SQUAD_BY_ENTRY) == {200, 112, 335, 201, 533, 488}


def test_league_template_holes_matches_the_exact_four_named_players():
    # §A: Calafiori, Mbeumo, Ndiaye, Kinsky — each owned by >=4 rivals, none owned by Ollie
    assert league_template_holes(_OLLIE_SQUAD, _SQUAD_BY_ENTRY) == {CALAFIORI, MBEUMO, NDIAYE, KINSKY}


def test_league_template_holes_respects_the_min_owners_threshold():
    # Loosening the threshold must only ever add players, never remove any
    # already-qualifying one.
    holes_3 = league_template_holes(_OLLIE_SQUAD, _SQUAD_BY_ENTRY, min_owners=3)
    holes_4 = league_template_holes(_OLLIE_SQUAD, _SQUAD_BY_ENTRY, min_owners=4)
    assert holes_4 <= holes_3


# ── rival_transfers_from_diff ───────────────────────────────────────────────


def test_rival_transfers_from_diff_detects_in_and_out():
    result = rival_transfers_from_diff(prev_squad={1, 2, 3}, curr_squad={2, 3, 4})
    assert result == {"in": [4], "out": [1]}


def test_rival_transfers_from_diff_empty_when_squad_unchanged():
    result = rival_transfers_from_diff(prev_squad={1, 2, 3}, curr_squad={1, 2, 3})
    assert result == {"in": [], "out": []}


# ── eo_mode — FPL-CONTEXT.md §2.1's table ───────────────────────────────────


def test_eo_mode_stays_neutral_with_a_68_point_deficit_and_36_gameweeks_left():
    # PHASE3-BRIEF.md Step 4's explicit acceptance case.
    assert eo_mode(is_leading=False, gws_remaining=36) == "neutral"


def test_eo_mode_chase_when_behind_and_under_ten_gameweeks_left():
    assert eo_mode(is_leading=False, gws_remaining=9) == "chase"


def test_eo_mode_protect_when_leading_and_under_ten_gameweeks_left():
    assert eo_mode(is_leading=True, gws_remaining=9) == "protect"


def test_eo_mode_neutral_when_leading_early_season():
    assert eo_mode(is_leading=True, gws_remaining=30) == "neutral"


def test_eo_mode_neutral_in_the_undefined_ten_to_fifteen_gameweek_band():
    assert eo_mode(is_leading=False, gws_remaining=12) == "neutral"


# ── gw_review_decomposition — §B's exact worked example, real GW2 scores ───
#
# Confirmed live against event/2/live/ on 1 Sept 2026 (still matching the
# addendum's snapshot): Haaland 13, B.Fernandes 23, Mukiele 9.

_NAMES = {411: "Haaland", 426: "B.Fernandes", 533: "Mukiele"}
_MY_LIVE_POINTS_GW2 = {411: 13, 426: 3, 533: 9}  # only what the decomposition actually reads
_RIVAL_LIVE_POINTS_GW2 = {426: 23, 411: 1}


def test_gw_review_decomposition_captain_delta_matches_addendum_exactly():
    my_picks = parse_picks(OLLIE_GW2)  # 411 is captain (multiplier 2) per the fixture
    rival_picks = parse_picks(RIVALS_GW2[1896251][1])  # Angus Robinson, 426 captain

    result = gw_review_decomposition(
        my_points=77, my_picks=my_picks, my_live_points=_MY_LIVE_POINTS_GW2,
        rival_name="Angus Robinson", rival_points=127,
        rival_picks=rival_picks, rival_live_points=_RIVAL_LIVE_POINTS_GW2,
        names=_NAMES,
    )

    captain_row = next(d for d in result["decomposition"] if d["cause"] == "captain")
    assert captain_row["delta"] == -20  # (13*2) - (23*2), the brief's exact worked example
    assert "Haaland" in captain_row["detail"]
    assert "B.Fernandes" in captain_row["detail"]
    assert "You owned both" in captain_row["detail"]  # Ollie owned Fernandes too, per §A


def test_gw_review_decomposition_identifies_mukiele_as_top_bench_scorer():
    my_picks = parse_picks(OLLIE_GW2)
    rival_picks = parse_picks(RIVALS_GW2[1896251][1])

    result = gw_review_decomposition(
        my_points=77, my_picks=my_picks, my_live_points=_MY_LIVE_POINTS_GW2,
        rival_name="Angus Robinson", rival_points=127,
        rival_picks=rival_picks, rival_live_points=_RIVAL_LIVE_POINTS_GW2,
        names=_NAMES,
    )

    bench_row = next(d for d in result["decomposition"] if d["cause"] == "bench")
    assert "Mukiele scored 9" in bench_row["detail"]


def test_gw_review_decomposition_three_causes_sum_exactly_to_the_real_gap():
    my_picks = parse_picks(OLLIE_GW2)
    rival_picks = parse_picks(RIVALS_GW2[1896251][1])

    result = gw_review_decomposition(
        my_points=77, my_picks=my_picks, my_live_points=_MY_LIVE_POINTS_GW2,
        rival_name="Angus Robinson", rival_points=127,
        rival_picks=rival_picks, rival_live_points=_RIVAL_LIVE_POINTS_GW2,
        names=_NAMES,
    )

    assert result["vs_rival"]["gap"] == 77 - 127
    assert sum(d["delta"] for d in result["decomposition"]) == result["vs_rival"]["gap"]
