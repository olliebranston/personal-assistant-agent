"""Mini-league engine — PHASE3-BRIEF.md Step 4 + PHASE3-ADDENDUM.md.

Pure functions only, no DB/API access (same convention as services/fpl_xp.py) —
tools/fpl.py and bot/fpl_jobs.py own the live data fetch and SQLite reads/writes,
this module only computes over data handed to it.

The genuine edge FPL-CONTEXT.md §0 describes: effective ownership computed
against the ~7 people in Ollie's mini-league, not the 6.5m-manager global
field. Global ownership is the wrong denominator in a league this size — if
five of seven rivals own a player, captaining him is neutral *here* however
popular he is nationally (FPL-CONTEXT.md §2.1/§1's "What a 7-manager league
changes").
"""

from __future__ import annotations

import collections

# multiplier convention on the picks endpoint (and rival_picks/my_picks
# tables, which mirror it): 0=benched, 1=starting, 2=captain, 3=triple captain.
_CAPTAIN_MULTIPLIERS = (2, 3)


def league_eo(picks_by_entry: dict[int, list[dict]], element_id: int) -> float:
    """Effective ownership within the mini-league, as a percentage.

    picks_by_entry: {entry_id: [{"element_id":.., "multiplier":..}, ...]} for
    the *active* rivals in one gameweek (never include inactive/departed
    entries — they no longer count towards N).

    This is Σ(multiplier) / N × 100, not a plain ownership fraction — PHASE3-
    ADDENDUM.md §A's edge case: a player owned by everyone and captained by
    no one sits at exactly 100%, but captaincy weighting can push a widely-
    captained player's EO past 100% (confirmed live: B. Fernandes at 143% in
    GW2 — 6/7 owners, 4 of those captained him). Callers must not clamp to
    0-100 or assume a plain ownership fraction.
    """
    n = len(picks_by_entry)
    if n == 0:
        return 0.0
    total = sum(
        pick["multiplier"]
        for picks in picks_by_entry.values()
        for pick in picks
        if pick["element_id"] == element_id
    )
    return total / n * 100


def rival_transfers_from_diff(prev_squad: set[int], curr_squad: set[int]) -> dict:
    """{'in': [...], 'out': [...]} — set difference between two consecutive
    gameweeks' 15 for one rival. Fallback path when entry/{id}/transfers/
    isn't available for that rival; prefer the transfer log where it is
    (PHASE3-ADDENDUM.md §0 — exact prices/timestamps beat a inferred diff)."""
    return {
        "in": sorted(curr_squad - prev_squad),
        "out": sorted(prev_squad - curr_squad),
    }


def my_differentials(my_squad: set[int], rival_squads: dict[int, set[int]]) -> set[int]:
    """Players Ollie owns that no active rival owns."""
    owned_by_any_rival: set[int] = set()
    for squad in rival_squads.values():
        owned_by_any_rival |= squad
    return my_squad - owned_by_any_rival


def league_template_holes(
    my_squad: set[int], rival_squads: dict[int, set[int]], min_owners: int = 4
) -> set[int]:
    """Players owned by >= min_owners active rivals that Ollie does NOT own —
    template risk invisible to global ownership figures (FPL-CONTEXT.md §5's
    "players owned by 4+ rivals that Ollie doesn't own" output requirement)."""
    counts: collections.Counter[int] = collections.Counter()
    for squad in rival_squads.values():
        counts.update(squad)
    return {eid for eid, n in counts.items() if n >= min_owners} - my_squad


def eo_mode(is_leading: bool, gws_remaining: int) -> str:
    """FPL-CONTEXT.md §2.1's mode switch, driven by league position and
    gameweeks remaining:

    | Position | GWs left | Mode |
    |---|---|---|
    | Behind  | >15 | neutral — play the highest-xP team, ignore ownership |
    | Behind  | <10 | chase — favour low league-EO picks, accept variance |
    | Leading | <10 | protect — converge on rivals' squads |

    The 10-15 gap and "leading, >15" aren't in the doctrine's table; both
    default to neutral, matching its own stated bias ("don't let it flip to
    chase mode in September" — neutral is deliberately the safe default for
    the long middle of a season, not just the early weeks)."""
    if gws_remaining < 10:
        return "protect" if is_leading else "chase"
    return "neutral"


def _side_breakdown(picks: list[dict], live_points: dict[int, int]) -> dict:
    """One entry's captain (raw score + multiplier) and bench contribution for
    one gameweek. picks: [{"element_id","multiplier"}] for the 15."""
    captain = next((p for p in picks if p["multiplier"] in _CAPTAIN_MULTIPLIERS), None)
    bench_scores = [
        (p["element_id"], live_points.get(p["element_id"], 0)) for p in picks if p["multiplier"] == 0
    ]
    top_bench_element, top_bench_points = max(bench_scores, key=lambda t: t[1], default=(None, 0))
    return {
        "captain_element": captain["element_id"] if captain else None,
        "captain_multiplier": captain["multiplier"] if captain else 0,
        "captain_raw": live_points.get(captain["element_id"], 0) if captain else 0,
        "bench_points": sum(s for _, s in bench_scores),
        "top_bench_element": top_bench_element,
        "top_bench_points": top_bench_points,
    }


def gw_review_decomposition(
    my_points: int,
    my_picks: list[dict],
    my_live_points: dict[int, int],
    rival_name: str,
    rival_points: int,
    rival_picks: list[dict],
    rival_live_points: dict[int, int],
    names: dict[int, str],
) -> dict:
    """PHASE3-ADDENDUM.md §B: decompose the points gap to one rival into
    captain, bench, and residual-squad causes.

    my_picks/rival_picks: [{"element_id","multiplier"}] for each side's 15.
    my_live_points/rival_live_points: {element_id: actual points that
    gameweek} from fpl_client.live(gw) — captain/bench scores aren't in
    entry_history, only the final total is, so this needs the per-player feed.
    names: element_id -> web_name, for the detail strings.

    Verified live against team 6748844 vs Angus Robinson's GW2 (the brief's
    own worked example): captain delta -20 (Haaland 13x2 vs Fernandes 23x2),
    Mukiele identified as the 9-point top bench scorer, squad delta -16 —
    residual = gap - captain_delta - bench_delta, so the three always sum
    exactly back to the real points gap.
    """
    mine = _side_breakdown(my_picks, my_live_points)
    theirs = _side_breakdown(rival_picks, rival_live_points)

    def name(eid: int | None) -> str:
        return names.get(eid, str(eid)) if eid is not None else "nobody"

    captain_delta = (mine["captain_raw"] * mine["captain_multiplier"]) - (
        theirs["captain_raw"] * theirs["captain_multiplier"]
    )
    bench_delta = mine["bench_points"] - theirs["bench_points"]
    gap = my_points - rival_points
    squad_delta = gap - captain_delta - bench_delta

    my_squad = {p["element_id"] for p in my_picks}
    rival_squad = {p["element_id"] for p in rival_picks}
    rival_only = sorted(rival_squad - my_squad, key=lambda e: -rival_live_points.get(e, 0))[:2]
    my_only = sorted(my_squad - rival_squad, key=lambda e: -my_live_points.get(e, 0))[:2]

    both_owned_note = ""
    if mine["captain_element"] in rival_squad and mine["captain_element"] != theirs["captain_element"]:
        both_owned_note = " You owned both."

    return {
        "my_points": my_points,
        "vs_rival": {"name": rival_name, "points": rival_points, "gap": gap},
        "decomposition": [
            {
                "cause": "captain",
                "delta": captain_delta,
                "detail": (
                    f"You captained {name(mine['captain_element'])} ({mine['captain_raw']}). "
                    f"{rival_name} captained {name(theirs['captain_element'])} ({theirs['captain_raw']})."
                    f"{both_owned_note}"
                ),
            },
            {
                "cause": "bench",
                "delta": bench_delta,
                "detail": (
                    f"{name(mine['top_bench_element'])} scored {mine['top_bench_points']} from your bench."
                    if mine["top_bench_points"] > 0
                    else "Nothing significant scored from your bench."
                ),
            },
            {
                "cause": "squad",
                "delta": squad_delta,
                "detail": (
                    f"{rival_name} owns {', '.join(name(e) for e in rival_only) or 'nothing you don’t'}; "
                    f"you own {', '.join(name(e) for e in my_only) or 'nothing they don’t'}."
                ),
            },
        ],
    }
