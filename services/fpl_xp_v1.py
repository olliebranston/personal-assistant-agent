"""Expected-points model v1 (Oct 2026) — built from how FPL actually scores.

v0 (services/fpl_xp.py) is points_per_game x a reliability squash x a
fixture nudge. v1 instead estimates each scoring event for one fixture and
prices it with FPL's own scoring table, read live from bootstrap-static's
`game_config.scoring` (so a rule change next season is picked up without a
code change; _FALLBACK_SCORING is only for a payload missing it):

    appearance       P(starts) x [P(60+ | starts) x long_play + rest x short_play]
    goals / assists  per-90 xG / xA x minutes x opponent factor x points per goal (by position) / assist
    clean sheet      P(60+) x P(team concedes 0) — Poisson on the team's xGC while he plays
    goals conceded   E[floor(conceded / 2)] x the per-2 deduction (GKP/DEF)
    saves            E[floor(saves / 3)] x the per-3 save point (GKP)
    defensive contr. P(actions >= 10 DEF / 12 MID-FWD) x 2, actions ~ Poisson(per-90 rate)
    bonus            bonus per start, shrunk towards a league-typical rate
    discipline etc.  yellow/red cards, own goals, penalties missed/saved — shrunk rate per start

then blended lightly with points_per_game for early-season stability.
Pure functions, no DB/API access — same convention as services/fpl_xp.py.

Simplifications, deliberately: minutes come from season averages (no
rotation model); opponent strength is FPL's 1-5 fixture difficulty, not a
team-strength model (bookmaker odds were ruled out, Oct 2026); xG already
includes penalties, so penalty *taking* isn't modelled separately.
"""

from __future__ import annotations

import math

from services.fpl_xp import availability

MODEL_VERSION = "v1"

# Official 2026/27 values, as in FPL's own game_config.scoring (checked
# 22 Oct 2026). Only used if a bootstrap payload lacks game_config.
_FALLBACK_SCORING: dict = {
    "long_play": 2, "short_play": 1, "assists": 3, "saves": 1, "bonus": 1,
    "goals_scored": {"GKP": 10, "DEF": 6, "MID": 5, "FWD": 4},
    "clean_sheets": {"GKP": 4, "DEF": 4, "MID": 1, "FWD": 0},
    "goals_conceded": {"GKP": -1, "DEF": -1, "MID": 0, "FWD": 0},
    "defensive_contribution": {"GKP": 0, "DEF": 2, "MID": 2, "FWD": 2},
    "penalties_saved": 5, "penalties_missed": -2, "yellow_cards": -1,
    "red_cards": -3, "own_goals": -2,
}
# Not in game_config — Premier League, "What's new in 2025/26 Fantasy:
# defensive contributions" (unchanged for 2026/27): 10 clearances/blocks/
# interceptions/tackles for a defender; 12 including ball recoveries for a
# midfielder or forward. Goalkeepers can't earn them.
DC_THRESHOLD = {"DEF": 10, "MID": 12, "FWD": 12}
_SAVES_PER_POINT = 3
_CONCEDED_PER_DEDUCTION = 2

PPG_BLEND = 0.2  # weight on points_per_game x availability
ATTACK_PER_FDR_STEP = 0.12  # +/- attacking output per difficulty step from 3
CONCEDE_PER_FDR_STEP = 0.15  # +/- goals conceded per difficulty step from 3

# Per-start rates for rarer events are shrunk towards a league-typical
# rate, as if the player had already played `pseudo_starts` extra games at
# it: 7 games can't tell a 1-in-7 penalty saver from a lucky one (live GW8
# check: Raya's single penalty save was worth +0.71 xP a game unshrunk).
# Priors are rough Premier League per-start averages (approximate, by design
# only a pull towards typical): ~6 bonus points per match over ~22 starters.
RATE_PRIORS: dict[str, tuple[float, float]] = {
    # field: (prior per start, pseudo_starts)
    "bonus": (0.27, 5),
    "yellow_cards": (0.15, 5),
    "red_cards": (0.01, 20),
    "own_goals": (0.01, 20),
    "penalties_missed": (0.0, 20),
    "penalties_saved": (0.03, 20),
}

_PER_90_FIELDS = ("expected_goals_per_90", "expected_assists_per_90",
                  "expected_goals_conceded_per_90", "starts")


def scoring_from_bootstrap(data: dict) -> dict:
    """FPL's live scoring table, falling back field by field to the 2026/27 values."""
    live = (data.get("game_config") or {}).get("scoring") or {}
    return {k: live.get(k, v) for k, v in _FALLBACK_SCORING.items()}


def has_v1_inputs(element: dict) -> bool:
    """v1 needs FPL's per-90 stats; a payload without them is scored by v0."""
    return all(k in element for k in _PER_90_FIELDS)


def poisson_at_least(lam: float, k: int) -> float:
    """P(X >= k) for X ~ Poisson(lam)."""
    if k <= 0:
        return 1.0
    if lam <= 0:
        return 0.0
    term, cdf = math.exp(-lam), 0.0
    for i in range(k):
        cdf += term
        term *= lam / (i + 1)
    return max(0.0, 1.0 - cdf)


def expected_floor_div(lam: float, d: int) -> float:
    """E[floor(X / d)] for X ~ Poisson(lam) — "1 point per 3 saves",
    "-1 per 2 goals conceded" (FPL discards the remainder per match)."""
    if lam <= 0:
        return 0.0
    return sum(poisson_at_least(lam, d * n) for n in range(1, 40))


def _f(x) -> float:
    try:
        return float(x or 0)
    except (TypeError, ValueError):
        return 0.0


def shrunk_rate(count: float, starts: float, field: str) -> float:
    """A player's per-start rate of an event, pulled towards the league prior."""
    prior, pseudo = RATE_PRIORS[field]
    return (count + prior * pseudo) / (starts + pseudo)


def playing_profile(element: dict, gws_played: int) -> dict:
    """P(starts), P(plays 60+) and minutes per start, from the season so far."""
    avail = availability(element.get("status", "a"), element.get("chance_of_playing_next_round"))
    starts, minutes = _f(element.get("starts")), _f(element.get("minutes"))
    if starts <= 0 or gws_played <= 0:
        return {"p_start": 0.0, "p60": 0.0, "mins": 0.0, "avail": avail}
    mins = min(90.0, minutes / starts)
    p_start = min(1.0, starts / gws_played) * avail
    p60 = p_start * min(1.0, max(0.0, (mins - 30.0) / 45.0))
    return {"p_start": p_start, "p60": p60, "mins": mins, "avail": avail}


def fixture_xp(element: dict, pos: str, fdr: float, scoring: dict, gws_played: int) -> dict:
    """Expected points for one fixture, by scoring component (so callers and
    tests can see where a number comes from), plus `model` and `xp` totals."""
    prof = playing_profile(element, gws_played)
    p, p60, m = prof["p_start"], prof["p60"], prof["mins"] / 90.0
    starts = _f(element.get("starts"))
    attack = 1.0 + (3.0 - fdr) * ATTACK_PER_FDR_STEP
    concede_rate = _f(element.get("expected_goals_conceded_per_90")) * (1.0 + (fdr - 3.0) * CONCEDE_PER_FDR_STEP)
    conceded_while_on = max(concede_rate, 0.0) * m

    def per_start(field: str) -> float:
        return shrunk_rate(_f(element.get(field)), starts, field)

    c = {
        "appearance": p60 * scoring["long_play"] + (p - p60) * scoring["short_play"],
        "goals": p * _f(element.get("expected_goals_per_90")) * m * attack * scoring["goals_scored"][pos],
        "assists": p * _f(element.get("expected_assists_per_90")) * m * attack * scoring["assists"],
        "clean_sheet": p60 * math.exp(-conceded_while_on) * scoring["clean_sheets"][pos],
        "goals_conceded": (p * scoring["goals_conceded"][pos]
                           * expected_floor_div(conceded_while_on, _CONCEDED_PER_DEDUCTION)),
        "saves": (p * scoring["saves"] * expected_floor_div(_f(element.get("saves_per_90")) * m, _SAVES_PER_POINT)
                  if pos == "GKP" else 0.0),
        "defensive_contribution": (
            p * scoring["defensive_contribution"].get(pos, 0)
            * poisson_at_least(_f(element.get("defensive_contribution_per_90")) * m, DC_THRESHOLD[pos])
            if pos in DC_THRESHOLD else 0.0
        ),
        "bonus": p * per_start("bonus") * scoring["bonus"],
        "discipline": p * (per_start("yellow_cards") * scoring["yellow_cards"]
                           + per_start("red_cards") * scoring["red_cards"]
                           + per_start("own_goals") * scoring["own_goals"]
                           + per_start("penalties_missed") * scoring["penalties_missed"]),
        "penalties_saved": (p * per_start("penalties_saved") * scoring["penalties_saved"]
                            if pos == "GKP" else 0.0),
    }
    model = sum(c.values())
    ppg = _f(element.get("points_per_game")) * prof["avail"]
    return {**c, "model": model, "xp": (1.0 - PPG_BLEND) * model + PPG_BLEND * ppg}


def team_fixture_fdrs(fixtures: list[dict], team_id: int, gw: int) -> list[float]:
    """This team's difficulty for each of its fixtures in a gameweek (empty on a blank)."""
    fdrs = []
    for f in fixtures:
        if f.get("event") != gw:
            continue
        if f["team_h"] == team_id:
            fdrs.append(f["team_h_difficulty"])
        elif f["team_a"] == team_id:
            fdrs.append(f["team_a_difficulty"])
    return fdrs


def horizon_xp(
    element: dict, pos: str, fixtures: list[dict], start_gw: int, horizon: int,
    scoring: dict, gws_played: int, discount: float = 1.0,
) -> dict[int, float]:
    """Per-GW xp across [start_gw, start_gw + horizon): 0 on a blank, the sum
    of both fixtures on a double, discounted further out (same shape as v0)."""
    result: dict[int, float] = {}
    for i in range(horizon):
        gw = start_gw + i
        total = sum(fixture_xp(element, pos, fdr, scoring, gws_played)["xp"]
                    for fdr in team_fixture_fdrs(fixtures, element["team"], gw))
        result[gw] = total * (discount ** i)
    return result
