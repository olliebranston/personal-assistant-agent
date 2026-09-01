"""Transfer MILP (PHASE2-BRIEF.md §3) — extends fpl_squad_v0.py's from-scratch
squad solver to evolving the squad you already have.

Every recommendation this produces is a legal squad by construction — budget,
2/5/5/3, max 3 per club, legal XI formation are hard constraints in the
solver. That's the hallucination firewall from FPL-CONTEXT.md §4.1: the LLM
never picks players, this does. services/fpl_validate.py re-checks the
result against live data before anything is sent.

Design note on the horizon: PHASE2-BRIEF.md §3 asks for buy/sell/own/start/
captain decision variables "per player per gameweek over a 4-5 gameweek
horizon." Modelled literally that's a full multi-period transfer plan (buy
in GW3, sell in GW5, ...). This implementation instead solves *this week's*
transfer decision once, evaluated against a discounted, blank/double-aware
xP sum over the horizon (services/fpl_xp.horizon_xp) — i.e. "what should I
do now, accounting for the next 4-5 gameweeks," not "here is a 5-gameweek
transfer plan." That matches the §4 output contract (three options for
*this* gameweek) and §8's acceptance criteria, which are all single-decision
checks (hit threshold, hold-always-present, forced inclusion cost) — none
of them requires a genuine rolling multi-period plan. Keeping the v0 model
crude, per the brief's own instruction.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import pulp

POS = {1: "GK", 2: "DEF", 3: "MID", 4: "FWD"}
SQUAD_LIMITS = {"GK": 2, "DEF": 5, "MID": 5, "FWD": 3}
XI_MIN = {"GK": 1, "DEF": 3, "MID": 2, "FWD": 1}
XI_MAX = {"GK": 1, "DEF": 5, "MID": 5, "FWD": 3}

BENCH_WEIGHT = 0.15
HIT_COST_PER_TRANSFER = 4
# §2.4 of FPL-CONTEXT.md / §3 of PHASE2-BRIEF.md: a hit needs a projected gain of
# >6 points, not >4. Implemented as this extra synthetic penalty on top of the
# real -4 cost, applied only when choosing `recommended` among finished options
# (services/fpl_optimiser.select_recommended) — not inside the solve itself,
# since each option solve is for a *fixed* transfer count.
RECOMMENDATION_BIAS_PER_HIT = 2.0


class OptimiserInfeasible(Exception):
    """The MILP had no feasible solution under the given constraints."""


def compute_selling_price(bought_price: int, current_price: int) -> int:
    """FPL's sell rule, in tenths of £1m: keep half of any rise (rounded down),
    eat the whole of any fall. This is the primary path, not a fallback — an
    unauthenticated client never gets a live `selling_price` for a pick (see
    tools/fpl.py:cost_basis for how bought_price is derived instead)."""
    if current_price > bought_price:
        return bought_price + (current_price - bought_price) // 2
    return current_price


def verify_squad_value(now_cost: dict[int, int], current_squad: set[int], bank: int, reported_value: int, tolerance_tenths: int = 2) -> tuple[bool, int]:
    """Cross-check the squad's current market value against FPL's own reported
    team value (entry_history.value).

    Confirmed live against team 6748844 on 1 Sept 2026 (GW2): `value` tracks
    the *current market price* of the 15 (sum of now_cost) plus bank — not the
    sell-on-fee-adjusted selling price. Comparing against the selling-price
    sum (this function's original implementation) was wrong: the sell-on fee
    means selling price is *always* somewhat below market value for anyone
    who's ever risen, so that version failed on every squad with a single
    riser, which is any squad a few gameweeks in. cost_basis/
    compute_selling_price are still correct and still needed — for actual
    transfer affordability (bank + sell proceeds vs buy cost) — just not as
    the input to this particular check, which only needs squad membership and
    live prices, not bought-price reconstruction.

    A disagreement now most likely means the squad we hold on record doesn't
    match the one FPL has (a sync bug), or now_cost data is stale. That's
    still worth refusing a real recommendation over, and the post-deadline
    sync should log it loudly either way.

    Returns (ok, diff_tenths) where diff_tenths = computed - reported (signed,
    tenths of £1m). ok is True when |diff_tenths| <= tolerance_tenths (default
    2 = £0.2m — a small buffer for prices ticking over between the two API
    calls this check straddles, not for genuine drift).
    """
    computed = sum(now_cost.get(eid, 0) for eid in current_squad) + bank
    diff = computed - reported_value
    return abs(diff) <= tolerance_tenths, diff


@dataclass(frozen=True)
class Candidate:
    element_id: int
    team_id: int
    position: str  # GK/DEF/MID/FWD
    now_cost: int  # tenths
    horizon_xp: float  # discounted sum of per-GW xp over the planning horizon


@dataclass
class SolveResult:
    squad: set[int]
    xi: set[int]
    captain: int
    vice: int
    transfers_out: list[int] = field(default_factory=list)
    transfers_in: list[int] = field(default_factory=list)
    hit: int = 0
    objective: float = 0.0
    total_horizon_xp: float = 0.0


def solve(
    candidates: list[Candidate],
    current_squad: set[int] = frozenset(),
    selling_price: dict[int, int] | None = None,
    bank: int = 1000,
    free_transfers: int = 1,
    transfer_count: int | None = None,
    force_in: set[int] = frozenset(),
    force_out: set[int] = frozenset(),
    min_club: dict[int, int] | None = None,
    hit_cost_per_transfer: int = HIT_COST_PER_TRANSFER,
) -> SolveResult:
    """Solve for the best 15/XI/captain.

    current_squad empty -> from-scratch mode (§8: empty squad + £100.0m must
    return a legal 15). Non-empty -> evolve-the-squad mode: `bank` is the
    cash in the bank (tenths), `selling_price` must cover every current
    squad member, and `transfer_count`, if given, forces exactly that many
    transfers (used to solve the hold/single/aggressive option tiers).
    """
    selling_price = selling_price or {}
    min_club = min_club or {}

    by_id = {c.element_id: i for i, c in enumerate(candidates)}
    idx = range(len(candidates))

    missing_current = current_squad - set(by_id)
    if missing_current:
        raise ValueError(f"current squad player(s) missing from candidate pool: {sorted(missing_current)}")
    missing_prices = current_squad - set(selling_price)
    if missing_prices:
        raise ValueError(f"missing selling_price for current squad player(s): {sorted(missing_prices)}")

    prob = pulp.LpProblem("fpl_transfer", pulp.LpMaximize)
    sq = pulp.LpVariable.dicts("squad", idx, cat="Binary")  # in the resulting 15
    st = pulp.LpVariable.dicts("start", idx, cat="Binary")  # in the XI
    cp = pulp.LpVariable.dicts("capt", idx, cat="Binary")  # captain

    prob += pulp.lpSum(
        candidates[i].horizon_xp * (st[i] + cp[i] + BENCH_WEIGHT * (sq[i] - st[i]))
        for i in idx
    )

    prob += pulp.lpSum(sq[i] for i in idx) == 15
    prob += pulp.lpSum(st[i] for i in idx) == 11
    prob += pulp.lpSum(cp[i] for i in idx) == 1
    for i in idx:
        prob += st[i] <= sq[i]
        prob += cp[i] <= st[i]

    for pos, n in SQUAD_LIMITS.items():
        prob += pulp.lpSum(sq[i] for i in idx if candidates[i].position == pos) == n
    for pos in XI_MIN:
        prob += pulp.lpSum(st[i] for i in idx if candidates[i].position == pos) >= XI_MIN[pos]
        prob += pulp.lpSum(st[i] for i in idx if candidates[i].position == pos) <= XI_MAX[pos]

    for team in {c.team_id for c in candidates}:
        prob += pulp.lpSum(sq[i] for i in idx if candidates[i].team_id == team) <= 3

    current_idx = {by_id[e] for e in current_squad}
    new_candidate_idx = set(idx) - current_idx

    # Money: bank + proceeds from anyone dropped >= cost of anyone newly bought.
    # Players who stay owned don't change hands, so they touch neither side.
    prob += (
        bank
        + pulp.lpSum(selling_price[candidates[i].element_id] * (1 - sq[i]) for i in current_idx)
        >= pulp.lpSum(candidates[i].now_cost * sq[i] for i in new_candidate_idx)
    )

    if transfer_count is not None:
        if not current_idx and transfer_count != 0:
            raise ValueError("transfer_count requires a non-empty current_squad")
        if current_idx:
            prob += pulp.lpSum(1 - sq[i] for i in current_idx) == transfer_count

    for eid in force_in:
        i = by_id.get(eid)
        if i is None:
            raise ValueError(f"force_in player not in candidate pool: {eid}")
        prob += sq[i] == 1
    for eid in force_out:
        i = by_id.get(eid)
        if i is not None:
            prob += sq[i] == 0

    for team, n in min_club.items():
        prob += pulp.lpSum(sq[i] for i in idx if candidates[i].team_id == team) >= n

    prob.solve(pulp.PULP_CBC_CMD(msg=0))
    if pulp.LpStatus[prob.status] != "Optimal":
        raise OptimiserInfeasible(pulp.LpStatus[prob.status])

    squad = {candidates[i].element_id for i in idx if sq[i].value() > 0.5}
    xi = {candidates[i].element_id for i in idx if st[i].value() > 0.5}
    captain = next(candidates[i].element_id for i in idx if cp[i].value() > 0.5)

    starters_by_xp = sorted(
        (candidates[by_id[e]] for e in xi if e != captain),
        key=lambda c: -c.horizon_xp,
    )
    vice = starters_by_xp[0].element_id if starters_by_xp else captain

    transfers_out = sorted(current_squad - squad)
    transfers_in = sorted(squad - current_squad)
    hit = max(0, len(transfers_out) - free_transfers) * hit_cost_per_transfer

    total_xp = sum(
        candidates[by_id[e]].horizon_xp * (2 if e == captain else 1) for e in xi
    ) + BENCH_WEIGHT * sum(candidates[by_id[e]].horizon_xp for e in squad - xi)

    return SolveResult(
        squad=squad,
        xi=xi,
        captain=captain,
        vice=vice,
        transfers_out=transfers_out,
        transfers_in=transfers_in,
        hit=hit,
        objective=pulp.value(prob.objective),
        total_horizon_xp=total_xp,
    )


def net_value(result: SolveResult, hold_horizon_xp: float, biased: bool = False) -> float:
    """xp gained over the hold baseline, minus the hit — the number `recommended`
    is chosen on. `biased` adds the 2-point-per-hit bias so a hit needs >6xP
    gain (4 real + 2 bias) to look better than holding, not just >4."""
    delta = result.total_horizon_xp - hold_horizon_xp
    penalty = result.hit + (RECOMMENDATION_BIAS_PER_HIT if biased and result.hit > 0 else 0.0)
    return delta - penalty
