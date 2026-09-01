"""Expected-points v0 heuristic (PHASE2-BRIEF.md §2).

Same maths as fpl_squad_v0.py's `load()`, ported to run off live
bootstrap-static + fixtures instead of CSV snapshots — deliberately not
improved here (that's Phase 4's Dixon-Coles work). Pure functions only, no
DB access — `tools/fpl.py` orchestrates the live-data lookup and logs
predictions to `xp_predictions`.

    avail       = 1.0 if status == 'a' else chance_of_playing_next_round / 100
    reliability = 0.55 + 0.45 * min(1, minutes / 2500)
    fixture     = 1.0 + (3.0 - avg_fdr_over_horizon) * 0.10
    xp          = points_per_game * reliability * fixture * avail
"""

from __future__ import annotations

MODEL_VERSION = "v0"
HORIZON_DISCOUNT = 0.9  # per-GW discount further out the horizon — §3's "discounted xP"


def availability(status: str, chance_of_playing_next_round: float | int | None) -> float:
    """status: a=available, d=doubtful, i=injured, s=suspended, u=unavailable, n=on loan."""
    if status == "a":
        return 1.0
    if chance_of_playing_next_round not in (None, ""):
        return float(chance_of_playing_next_round) / 100.0
    if status == "d":
        return 0.5
    return 0.0


def reliability(minutes: float) -> float:
    """How much to trust points_per_game — a 300-minute sample is a coin flip, 3000+ is real."""
    return 0.55 + 0.45 * min(1.0, minutes / 2500.0)


def fixture_multiplier(avg_fdr: float) -> float:
    """Easier fixtures than average (3.0) scale points up, harder scale down."""
    return 1.0 + (3.0 - avg_fdr) * 0.10


def xp(
    points_per_game: float,
    minutes: float,
    avg_fdr: float,
    status: str,
    chance_of_playing_next_round: float | int | None = None,
) -> dict:
    """The single-fixture v0 heuristic. Returns the components alongside the
    final figure so callers/tests can check the maths, not just the output."""
    avail = availability(status, chance_of_playing_next_round)
    rel = reliability(minutes)
    fix = fixture_multiplier(avg_fdr)
    return {
        "avail": avail,
        "reliability": rel,
        "fixture": fix,
        "xp": points_per_game * rel * fix * avail,
    }


def team_fdr_for_gw(fixtures: list[dict], team_id: int, gw: int) -> tuple[float | None, int]:
    """(avg_fdr, fixture_count) for one team in one gameweek. avg_fdr is None on a blank."""
    difficulties = []
    for f in fixtures:
        if f.get("event") != gw:
            continue
        if f["team_h"] == team_id:
            difficulties.append(f["team_h_difficulty"])
        elif f["team_a"] == team_id:
            difficulties.append(f["team_a_difficulty"])
    if not difficulties:
        return None, 0
    return sum(difficulties) / len(difficulties), len(difficulties)


def horizon_xp(
    points_per_game: float,
    minutes: float,
    status: str,
    chance_of_playing_next_round: float | int | None,
    team_id: int,
    fixtures: list[dict],
    start_gw: int,
    horizon: int = 5,
    discount: float = HORIZON_DISCOUNT,
) -> dict[int, float]:
    """Per-GW xp across [start_gw, start_gw + horizon) — blank/double and fixture-difficulty
    aware, discounted further out. A blank gw contributes 0; a double contributes ~2x the
    single-fixture xp for that week (roughly, via the fixture-count multiplier)."""
    avail = availability(status, chance_of_playing_next_round)
    rel = reliability(minutes)

    result: dict[int, float] = {}
    for i in range(horizon):
        gw = start_gw + i
        avg_fdr, fixture_count = team_fdr_for_gw(fixtures, team_id, gw)
        if fixture_count == 0:
            result[gw] = 0.0
            continue
        fix = fixture_multiplier(avg_fdr)
        base = points_per_game * rel * fix * avail
        result[gw] = base * fixture_count * (discount**i)
    return result


def total_horizon_xp(*args, **kwargs) -> float:
    return sum(horizon_xp(*args, **kwargs).values())
