"""The recommendation firewall (PHASE2-BRIEF.md §4).

Sits between services/fpl_optimiser.solve() and the message. Re-checks
everything against LIVE bootstrap data rather than trusting anything the
solver claims — the solver may have run against a bootstrap snapshot that's
since drifted (price change, injury flag). If this rejects, the message
does not send. A confidently wrong recommendation at T-45m is worse than no
recommendation at all — this is the whole point of the architecture.
"""

from __future__ import annotations

import collections
from dataclasses import dataclass, field

from services.fpl_optimiser import POS, SQUAD_LIMITS
from services.fpl_optimiser import SolveResult


@dataclass
class ValidationResult:
    valid: bool
    errors: list[str] = field(default_factory=list)


def validate_solve(
    result: SolveResult,
    elements: dict[int, dict],
    selling_price: dict[int, int],
    bank: int,
    warned_element_ids: set[int] = frozenset(),
    expected_prices: dict[int, int] | None = None,
) -> ValidationResult:
    """elements: live bootstrap element_id -> element dict (freshly fetched, not the
    snapshot the solver saw). warned_element_ids: incoming players already covered by
    a warning the caller built — an unavailable incoming player not in this set fails
    validation, per §4's 'no incoming player is flagged unavailable without it being
    in warnings'."""
    errors: list[str] = []

    for eid in sorted(result.squad | set(result.transfers_in)):
        if eid not in elements:
            errors.append(f"player {eid} does not exist")

    if any("does not exist" in e for e in errors):
        return ValidationResult(valid=False, errors=errors)  # can't check formation against a phantom player

    if expected_prices:
        for eid, price in expected_prices.items():
            live = elements.get(eid)
            if live is not None and live["now_cost"] != price:
                errors.append(f"player {eid} price drifted: expected {price}, live {live['now_cost']}")

    if len(result.squad) != 15:
        errors.append(f"squad has {len(result.squad)} players, not 15")

    positions = collections.Counter(POS[elements[eid]["element_type"]] for eid in result.squad)
    for pos, n in SQUAD_LIMITS.items():
        if positions.get(pos, 0) != n:
            errors.append(f"{pos} count is {positions.get(pos, 0)}, expected {n}")

    clubs = collections.Counter(elements[eid]["team"] for eid in result.squad)
    for club, n in clubs.items():
        if n > 3:
            errors.append(f"club {club} has {n} players, max is 3")

    sell_proceeds = sum(selling_price.get(eid, 0) for eid in result.transfers_out)
    buy_cost = sum(elements[eid]["now_cost"] for eid in result.transfers_in)
    if bank + sell_proceeds < buy_cost:
        errors.append(f"unaffordable: bank {bank} + proceeds {sell_proceeds} < cost {buy_cost}")

    for eid in result.transfers_in:
        el = elements.get(eid)
        if el and el.get("status") != "a" and eid not in warned_element_ids:
            errors.append(f"incoming player {eid} is flagged ({el.get('status')}) with no warning")

    if result.captain not in result.xi:
        errors.append("captain is not in the starting XI")

    return ValidationResult(valid=len(errors) == 0, errors=errors)
