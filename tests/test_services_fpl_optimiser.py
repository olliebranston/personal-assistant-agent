"""Tests for services/fpl_optimiser.py — the transfer MILP (PHASE2-BRIEF.md §3 + §8)."""

from __future__ import annotations

import pytest

from services.fpl_optimiser import (
    RECOMMENDATION_BIAS_PER_HIT,
    Candidate,
    OptimiserInfeasible,
    compute_selling_price,
    net_value,
    solve,
    verify_squad_value,
)

_POSITIONS = ["GK", "GK", "DEF", "DEF", "DEF", "DEF", "DEF", "MID", "MID", "MID", "MID", "MID", "FWD", "FWD", "FWD"]


def _synthetic_pool() -> list[Candidate]:
    """20 teams x 15 slots (2 GK/5 DEF/5 MID/3 FWD), deterministic but varied
    cost/xp so price/value tradeoffs and the 3-per-club cap are both live."""
    pool = []
    eid = 1000
    for team in range(1, 21):
        for slot, pos in enumerate(_POSITIONS):
            base_cost = {"GK": 40, "DEF": 40, "MID": 45, "FWD": 45}[pos]
            cost = base_cost + (eid % 7) * 3 + (team % 3) * 2  # tenths, varied
            xp = 2.0 + ((eid * 7 + team * 3) % 40) / 10.0  # varied 2.0-5.9
            pool.append(Candidate(element_id=eid, team_id=team, position=pos, now_cost=cost, horizon_xp=xp))
            eid += 1
    return pool


def _by_position(candidates, squad):
    counts = {}
    lookup = {c.element_id: c for c in candidates}
    for eid in squad:
        pos = lookup[eid].position
        counts[pos] = counts.get(pos, 0) + 1
    return counts


def _club_counts(candidates, squad):
    counts = {}
    lookup = {c.element_id: c for c in candidates}
    for eid in squad:
        team = lookup[eid].team_id
        counts[team] = counts.get(team, 0) + 1
    return counts


# ── compute_selling_price (§8: bought £6.0, now £6.3 -> sells £6.1) ────────


def test_selling_price_keeps_half_of_a_rise_rounded_down():
    assert compute_selling_price(bought_price=60, current_price=63) == 61


def test_selling_price_eats_the_whole_fall():
    assert compute_selling_price(bought_price=60, current_price=55) == 55


def test_selling_price_unchanged_when_flat():
    assert compute_selling_price(bought_price=60, current_price=60) == 60


def test_selling_price_rounds_down_odd_rise():
    # rise of 5 tenths -> half is 2.5, rounded down to 2
    assert compute_selling_price(bought_price=50, current_price=55) == 52


# ── From-scratch mode: empty squad + £100.0m -> legal 15 (§8) ──────────────


def test_from_scratch_returns_a_legal_squad():
    pool = _synthetic_pool()
    result = solve(pool, current_squad=frozenset(), bank=1000, free_transfers=1)

    assert len(result.squad) == 15
    assert len(result.xi) == 11
    assert result.captain in result.xi
    assert result.vice in result.xi
    assert result.vice != result.captain

    total_cost = sum(c.now_cost for c in pool if c.element_id in result.squad)
    assert total_cost <= 1000

    positions = _by_position(pool, result.squad)
    assert positions == {"GK": 2, "DEF": 5, "MID": 5, "FWD": 3}

    xi_positions = _by_position(pool, result.xi)
    assert xi_positions.get("GK", 0) == 1
    assert 3 <= xi_positions.get("DEF", 0) <= 5
    assert 2 <= xi_positions.get("MID", 0) <= 5
    assert 1 <= xi_positions.get("FWD", 0) <= 3

    clubs = _club_counts(pool, result.squad)
    assert all(n <= 3 for n in clubs.values())

    assert result.hit == 0
    assert result.transfers_out == []
    assert sorted(result.transfers_in) == sorted(result.squad)


def test_from_scratch_is_infeasible_on_a_budget_too_small():
    pool = _synthetic_pool()
    with pytest.raises(OptimiserInfeasible):
        solve(pool, current_squad=frozenset(), bank=1, free_transfers=1)


# ── Evolving an existing squad: hold / single / transfer count ─────────────


def _make_current_squad(pool):
    """Pick a legal 15 as 'current squad' via an unconstrained solve, then treat
    those as already-owned with selling_price == now_cost (freshly bought)."""
    base = solve(pool, current_squad=frozenset(), bank=1000, free_transfers=1)
    selling_price = {eid: c.now_cost for c in pool if c.element_id in base.squad for eid in [c.element_id]}
    bank = 1000 - sum(c.now_cost for c in pool if c.element_id in base.squad)
    return base.squad, selling_price, bank


def test_hold_forces_zero_transfers_and_keeps_the_same_squad():
    pool = _synthetic_pool()
    current, selling_price, bank = _make_current_squad(pool)

    hold = solve(pool, current_squad=current, selling_price=selling_price, bank=bank,
                 free_transfers=1, transfer_count=0)

    assert hold.squad == current
    assert hold.transfers_out == []
    assert hold.transfers_in == []
    assert hold.hit == 0


def test_single_transfer_option_makes_exactly_one_change():
    pool = _synthetic_pool()
    current, selling_price, bank = _make_current_squad(pool)

    single = solve(pool, current_squad=current, selling_price=selling_price, bank=bank,
                    free_transfers=1, transfer_count=1)

    assert len(single.transfers_out) == 1
    assert len(single.transfers_in) == 1
    assert single.hit == 0  # covered by the 1 free transfer


def test_transfer_beyond_free_allowance_costs_a_hit():
    pool = _synthetic_pool()
    current, selling_price, bank = _make_current_squad(pool)

    aggressive = solve(pool, current_squad=current, selling_price=selling_price, bank=bank,
                        free_transfers=1, transfer_count=2)

    assert len(aggressive.transfers_out) == 2
    assert aggressive.hit == 4  # 1 free + 1 paid


def test_transfer_count_forced_even_when_zero_would_score_higher():
    # A pool with only one improving swap available still solves cleanly for
    # transfer_count=1 rather than silently returning 0 transfers.
    pool = _synthetic_pool()
    current, selling_price, bank = _make_current_squad(pool)
    single = solve(pool, current_squad=current, selling_price=selling_price, bank=bank,
                    free_transfers=1, transfer_count=1)
    assert len(single.squad - current) == 1


# ── Money constraint ─────────────────────────────────────────────────────────


def test_unaffordable_forced_transfer_is_infeasible():
    pool = _synthetic_pool()
    current, selling_price, bank = _make_current_squad(pool)
    # Force in the single most expensive candidate not already owned, with only
    # £0 bank and one cheap transfer out — should be unaffordable.
    priciest = max((c for c in pool if c.element_id not in current), key=lambda c: c.now_cost)

    with pytest.raises(OptimiserInfeasible):
        solve(pool, current_squad=current, selling_price=selling_price, bank=0,
              free_transfers=1, transfer_count=1, force_in={priciest.element_id})


# ── 3-per-club adversarial case ──────────────────────────────────────────────


def test_incoming_player_from_a_full_club_displaces_one_of_the_three():
    pool = _synthetic_pool()
    club = 7
    club_players = sorted((c for c in pool if c.team_id == club), key=lambda c: -c.horizon_xp)
    # Force a squad that already owns exactly 3 from `club`.
    three = {c.element_id for c in club_players[:3]}
    # Build a legal-shape current squad manually is fiddly — instead constrain
    # an unconstrained solve to include exactly these 3, then treat its output
    # as "current".
    base = solve(pool, current_squad=frozenset(), bank=1000, free_transfers=1,
                 force_in=three)
    current = base.squad
    selling_price = {eid: next(c.now_cost for c in pool if c.element_id == eid) for eid in current}
    bank = 1000 - sum(next(c.now_cost for c in pool if c.element_id == e) for e in current)
    assert _club_counts(pool, current).get(club, 0) == 3

    # A 4th club-7 player forced in must displace one of the existing three —
    # total club-7 count in the result still <= 3. A single transfer can only
    # swap within the same position (squad position counts are exact), so pick
    # a 4th club-7 candidate sharing a position with one of the three.
    three_positions = {c.element_id: c.position for c in club_players[:3]}
    incoming = next(
        c for c in club_players[3:]
        if c.element_id not in current and c.position in three_positions.values()
    )
    result = solve(pool, current_squad=current, selling_price=selling_price, bank=bank,
                   free_transfers=1, transfer_count=1, force_in={incoming.element_id})

    assert incoming.element_id in result.squad
    assert _club_counts(pool, result.squad).get(club, 0) <= 3
    # one of the original three must have left, since transfer_count=1 and the
    # incoming player is forced — the club cap can only be respected by
    # dropping an existing club-7 player.
    assert len(three & set(result.transfers_out)) == 1


# ── Forced inclusion + quoting its xP cost ──────────────────────────────────


def test_forced_inclusion_costs_xp_relative_to_unconstrained_best():
    pool = _synthetic_pool()
    unconstrained = solve(pool, current_squad=frozenset(), bank=1000, free_transfers=1)

    # Force in the single worst-value player in the pool (by xp) at a starter position.
    worst = min(pool, key=lambda c: c.horizon_xp)
    forced = solve(pool, current_squad=frozenset(), bank=1000, free_transfers=1,
                   force_in={worst.element_id})

    assert worst.element_id in forced.squad
    cost = unconstrained.total_horizon_xp - forced.total_horizon_xp
    assert cost >= 0  # forcing a suboptimal player never helps the unconstrained optimum


# ── net_value / recommendation bias (§8: +5 not recommended, +7 is) ────────


def test_net_value_hit_worth_5_is_not_worth_it_after_bias():
    class _Result:
        total_horizon_xp = 5.0
        hit = 4

    net = net_value(_Result(), hold_horizon_xp=0.0, biased=True)
    assert net < 0  # 5 - 4 - 2 = -1


def test_net_value_hit_worth_7_clears_the_bar():
    class _Result:
        total_horizon_xp = 7.0
        hit = 4

    net = net_value(_Result(), hold_horizon_xp=0.0, biased=True)
    assert net > 0  # 7 - 4 - 2 = +1


def test_net_value_unbiased_ignores_the_synthetic_penalty():
    class _Result:
        total_horizon_xp = 5.0
        hit = 4

    net = net_value(_Result(), hold_horizon_xp=0.0, biased=False)
    assert net == pytest.approx(1.0)  # 5 - 4, no bias


def test_recommendation_bias_constant_is_two():
    assert RECOMMENDATION_BIAS_PER_HIT == 2.0


# ── verify_squad_value — the money self-check ────────────────────────────────
#
# now_cost + bank against FPL's reported team value, not selling price — see
# verify_squad_value's docstring for why (confirmed live against a real team
# that the sell-on-fee-adjusted comparison was wrong on GW2's actual data).


def test_verify_squad_value_matches_within_tolerance():
    ok, diff = verify_squad_value({1: 61, 2: 57}, {1, 2}, bank=0, reported_value=118)
    assert ok is True
    assert diff == 0


def test_verify_squad_value_includes_bank():
    # market value 118 + bank 5 = 123, matches reported exactly
    ok, diff = verify_squad_value({1: 61, 2: 57}, {1, 2}, bank=5, reported_value=123)
    assert ok is True
    assert diff == 0


def test_verify_squad_value_allows_exactly_the_tolerance():
    # computed 118, reported 116 -> diff 2 tenths (£0.2m), the boundary must pass
    ok, diff = verify_squad_value({1: 61, 2: 57}, {1, 2}, bank=0, reported_value=116)
    assert ok is True
    assert diff == 2


def test_verify_squad_value_fails_beyond_tolerance():
    ok, diff = verify_squad_value({1: 61, 2: 57}, {1, 2}, bank=0, reported_value=100)
    assert ok is False
    assert diff == 18  # computed 118 - reported 100


def test_verify_squad_value_ignores_players_outside_current_squad():
    ok, diff = verify_squad_value({1: 61, 2: 57, 3: 999}, {1, 2}, bank=0, reported_value=118)
    assert ok is True
    assert diff == 0
