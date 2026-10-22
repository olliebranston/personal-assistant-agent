"""Tests for services/fpl_league.py — PHASE3-BRIEF.md Step 4 + PHASE3-ADDENDUM.md.

Verified against §A's known-answer GW2 rival data (real API, 1 Sept 2026)
rather than a synthetic squad, per the addendum's own instruction.
"""

from __future__ import annotations

from services.fpl_league import (
    best_bench_swap,
    eo_mode,
    gw_review_decomposition,
    league_tilt,
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
    # The brief's worked example said -20, (13*2) - (23*2). Since Oct 2026 the
    # captain line is only the *extra* captaincy points, 13 - 23 = -10 — the
    # base points sit in the position lines. The old figure double-counted:
    # Ollie owned Fernandes too, so -20 overstated what the choice cost.
    assert captain_row["delta"] == -10
    assert "Haaland" in captain_row["detail"]
    assert "B.Fernandes" in captain_row["detail"]
    assert "You owned both" in captain_row["detail"]  # Ollie owned Fernandes too, per §A


def test_gw_review_decomposition_identifies_mukiele_as_top_bench_scorer():
    # Bench is reported separately now, not as a cause of the gap: bench
    # points don't count for either side, so "my bench minus theirs" never
    # explained anything (Ollie's GW7 feedback, Oct 2026).
    my_picks = parse_picks(OLLIE_GW2)
    rival_picks = parse_picks(RIVALS_GW2[1896251][1])

    result = gw_review_decomposition(
        my_points=77, my_picks=my_picks, my_live_points=_MY_LIVE_POINTS_GW2,
        rival_name="Angus Robinson", rival_points=127,
        rival_picks=rival_picks, rival_live_points=_RIVAL_LIVE_POINTS_GW2,
        names=_NAMES,
    )

    assert result["bench"]["top_name"] == "Mukiele"
    assert result["bench"]["top_points"] == 9
    assert all(d["cause"] != "bench" for d in result["decomposition"])


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


# ── Starting XI by position + bench decisions (Ollie's GW7 feedback) ────────
# GW7, real scores: the "squad" cause named bench players (Diop) and lumped
# everything together; the "bench" cause compared bench points that count
# for neither side. Starters only now, split by position with head-to-heads.

_POS = {1: "GKP", 2: "GKP", 3: "DEF", 4: "DEF", 5: "DEF", 6: "DEF", 7: "MID", 8: "MID", 9: "FWD",
        10: "DEF", 11: "MID", 12: "FWD"}
_NAMES_XI = {1: "Raya", 2: "Verbruggen", 3: "Gvardiol", 4: "Calafiori", 5: "Hall", 6: "Konsa",
             7: "Palmer", 8: "Rogers", 9: "Haaland", 10: "Diop", 11: "Janelt", 12: "Isak"}
_LIVE_XI = {1: 9, 2: 2, 3: 0, 4: 6, 5: 4, 6: 10, 7: 2, 8: 2, 9: 13, 10: 3, 11: -1, 12: 8}


def _picks(starters: dict[int, int], bench: list[int]) -> list[dict]:
    return ([{"element_id": e, "multiplier": m} for e, m in starters.items()]
            + [{"element_id": e, "multiplier": 0} for e in bench])


def test_decomposition_splits_starters_by_position_and_never_names_bench_players():
    mine = _picks({1: 1, 3: 1, 4: 1, 5: 1, 7: 2, 8: 1, 9: 1}, bench=[10, 11])
    theirs = _picks({2: 1, 4: 1, 5: 1, 6: 1, 7: 1, 8: 1, 9: 2, 12: 1}, bench=[3])
    my_points = sum(_LIVE_XI[p["element_id"]] * p["multiplier"] for p in mine)
    their_points = sum(_LIVE_XI[p["element_id"]] * p["multiplier"] for p in theirs)

    result = gw_review_decomposition(
        my_points=my_points, my_picks=mine, my_live_points=_LIVE_XI,
        rival_name="Angus", rival_points=their_points, rival_picks=theirs,
        rival_live_points=_LIVE_XI, names=_NAMES_XI, positions=_POS,
    )

    by_cause = {d["cause"]: d for d in result["decomposition"]}
    assert by_cause["defence"]["delta"] == 0 - 10  # your Gvardiol 0 vs his Konsa 10 (shared DEFs cancel)
    assert "Konsa (10)" in by_cause["defence"]["detail"] and "Gvardiol (0)" in by_cause["defence"]["detail"]
    assert by_cause["goalkeeper"]["delta"] == 9 - 2
    # Both started Haaland; he captained him. Only the extra 13 is the
    # captain line — the base 13s cancel in attack, leaving his Isak.
    assert by_cause["captain"]["delta"] == 2 - 13
    assert by_cause["attack"]["delta"] == -8 and "Isak (8)" in by_cause["attack"]["detail"]
    assert "midfield" not in by_cause  # same midfield starters both sides
    assert "Diop" not in " ".join(d["detail"] for d in result["decomposition"])
    assert "bench" not in by_cause
    assert sum(d["delta"] for d in result["decomposition"]) == result["vs_rival"]["gap"]


def test_best_bench_swap_matches_ollies_gw7_reading():
    # "my lowest scoring player I did select scored 0 points, so [Diop's 3 on
    # the bench] only cost me 3 points".
    mine = _picks({1: 1, 3: 1, 4: 1, 5: 1, 7: 2, 8: 1, 9: 1}, bench=[10, 11])

    swap = best_bench_swap(mine, _LIVE_XI, _POS)

    assert (swap["bench"], swap["starter"], swap["cost"]) == (10, 3, 3)  # Diop for Gvardiol


def test_best_bench_swap_respects_formation_rules():
    # Only 3 DEF starting, so bench Isak (8) can't replace Gvardiol (0) — that
    # would leave 2 defenders. He can replace Janelt (-1): 3 MID -> 2 is legal.
    mine = _picks({1: 1, 3: 1, 4: 1, 5: 1, 7: 1, 8: 1, 11: 1, 9: 2}, bench=[12])

    swap = best_bench_swap(mine, _LIVE_XI, _POS)

    assert (swap["bench"], swap["starter"], swap["cost"]) == (12, 11, 9)


def test_best_bench_swap_keeps_goalkeepers_to_goalkeepers():
    # A bench GK on 15 could "beat" Gvardiol's 0 by 15, but only the starting
    # GK (Raya, 9) is a real alternative: cost 6.
    mine = _picks({1: 1, 3: 1, 4: 1, 5: 1, 7: 2, 8: 1, 9: 1}, bench=[2])

    swap = best_bench_swap(mine, {**_LIVE_XI, 2: 15}, _POS)

    assert (swap["bench"], swap["starter"], swap["cost"]) == (2, 1, 6)


def test_best_bench_swap_none_when_the_bench_scored_less():
    mine = _picks({1: 1, 4: 1, 5: 1, 6: 1, 7: 2}, bench=[3])  # bench Gvardiol scored 0

    assert best_bench_swap(mine, _LIVE_XI, _POS) is None


# ── league_tilt (W5) ────────────────────────────────────────────────────────


def test_league_tilt_leaves_xp_alone_in_neutral_mode():
    assert league_tilt(6.0, 100.0, "neutral") == 6.0
    assert league_tilt(6.0, 0.0, None) == 6.0


def test_league_tilt_protect_favours_what_the_league_owns():
    assert league_tilt(6.0, 100.0, "protect") == 6.0 * 1.10
    assert league_tilt(6.0, 0.0, "protect") == 6.0 * 0.90


def test_league_tilt_chase_favours_what_the_league_does_not_own():
    assert league_tilt(6.0, 0.0, "chase") == 6.0 * 1.10
    assert league_tilt(6.0, 143.0, "chase") == 6.0 * 0.90  # >100% EO clipped
