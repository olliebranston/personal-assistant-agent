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


_POSITION_CAUSES = (("GKP", "goalkeeper"), ("DEF", "defence"), ("MID", "midfield"), ("FWD", "attack"))
_FORMATION_MIN = {"DEF": 3, "MID": 2, "FWD": 1}


def gw_review_decomposition(
    my_points: int,
    my_picks: list[dict],
    my_live_points: dict[int, int],
    rival_name: str,
    rival_points: int,
    rival_picks: list[dict],
    rival_live_points: dict[int, int],
    names: dict[int, str],
    positions: dict[int, str] | None = None,
) -> dict:
    """PHASE3-ADDENDUM.md §B: decompose the points gap to one rival into its
    causes — the captaincy (only the *extra* points the armband added), then
    each side's starting XI at base points split by position, then "other"
    for whatever the starters don't explain (transfer hits, auto-subs) so
    the causes always sum exactly back to the real gap.

    my_picks/rival_picks: [{"element_id","multiplier"}] for each side's 15.
    my_live_points/rival_live_points: {element_id: actual points that
    gameweek} from fpl_client.live(gw). names: element_id -> web_name.
    positions: element_id -> "GKP"/"DEF"/"MID"/"FWD"; without it the
    starters are one "squad" line.

    Reworked Oct 2026 after Ollie's GW7 read of the first version: its
    "bench" cause (my bench points minus theirs) explained nothing — bench
    points count for neither side — and its "squad" cause compared whole
    15-man squads, naming bench players like Diop. Bench is now reported
    separately (`bench`, plus best_bench_swap for the decision it cost).

    The captain line was (raw x multiplier) per side — the brief's GW2 example
    gave -20 for Haaland (13x2) vs Fernandes (23x2). That double-counted
    whenever both sides started the same player: in GW7 both started
    Haaland, so his base 13 showed up as "Attack +15" for Ollie while the
    captain line said -22. Now the captain line is the armband's extra
    points only (GW2: 13 - 23 = -10; GW7: 2 - 13 = -11) and base points sit
    in the position lines, where shared starters cancel.
    """
    mine = _side_breakdown(my_picks, my_live_points)
    theirs = _side_breakdown(rival_picks, rival_live_points)

    def name(eid: int | None) -> str:
        return names.get(eid, str(eid)) if eid is not None else "nobody"

    def armband_extra(side: dict) -> int:
        return side["captain_raw"] * max(side["captain_multiplier"] - 1, 0)

    captain_delta = armband_extra(mine) - armband_extra(theirs)
    gap = my_points - rival_points
    my_squad = {p["element_id"] for p in my_picks}
    rival_squad = {p["element_id"] for p in rival_picks}

    both_owned_note = ""
    if mine["captain_element"] in rival_squad and mine["captain_element"] != theirs["captain_element"]:
        both_owned_note = " You owned both."
    causes = [{
        "cause": "captain",
        "delta": captain_delta,
        "detail": (
            f"You captained {name(mine['captain_element'])} ({mine['captain_raw']}). "
            f"{rival_name} captained {name(theirs['captain_element'])} ({theirs['captain_raw']})."
            f"{both_owned_note} This line is just the armband's extra points."
        ),
    }]

    def xi(picks: list[dict], live: dict[int, int]) -> dict[int, int]:
        """Every starter (captain included) -> base points, before the armband."""
        return {p["element_id"]: live.get(p["element_id"], 0) for p in picks if p["multiplier"] > 0}

    my_xi, rival_xi = xi(my_picks, my_live_points), xi(rival_picks, rival_live_points)
    my_starting, rival_starting = set(my_xi), set(rival_xi)

    def headline(mine_only: list[int], theirs_only: list[int]) -> str:
        his = ", ".join(f"{name(e)} ({rival_xi[e]})" for e in theirs_only[:2])
        yours = ", ".join(f"{name(e)} ({my_xi[e]})" for e in mine_only[:2])
        if his and yours:
            return f"His {his} vs your {yours}."
        if his:
            return f"He started {his}; you had no one extra there."
        if yours:
            return f"You started {yours}; he had no one extra there."
        return "Same starters here."

    groups = _POSITION_CAUSES if positions else (("ALL", "squad"),)
    for pos, cause in groups:
        def in_group(eid: int) -> bool:
            return pos == "ALL" or (positions or {}).get(eid) == pos

        mine_pos = {e: v for e, v in my_xi.items() if in_group(e)}
        theirs_pos = {e: v for e, v in rival_xi.items() if in_group(e)}
        delta = sum(mine_pos.values()) - sum(theirs_pos.values())
        if delta == 0:
            continue
        mine_only = sorted((e for e in mine_pos if e not in rival_starting), key=lambda e: -mine_pos[e])
        theirs_only = sorted((e for e in theirs_pos if e not in my_starting), key=lambda e: -theirs_pos[e])
        causes.append({"cause": cause, "delta": delta, "detail": headline(mine_only, theirs_only)})

    other = gap - sum(c["delta"] for c in causes)
    if other:
        causes.append({
            "cause": "other",
            "delta": other,
            "detail": "Transfer hits, auto-subs or chip effects not visible in the starting line-ups.",
        })

    return {
        "my_points": my_points,
        "vs_rival": {"name": rival_name, "points": rival_points, "gap": gap},
        "decomposition": causes,
        "bench": {
            "points": mine["bench_points"],
            "top_name": name(mine["top_bench_element"]) if mine["top_bench_points"] > 0 else None,
            "top_points": mine["top_bench_points"],
        },
        "squads_differ_by": len(my_squad ^ rival_squad) // 2,
    }


def best_bench_swap(picks: list[dict], live_points: dict[int, int], positions: dict[int, str]) -> dict | None:
    """The costliest single bench decision: the bench player whose swap
    with a starter would have scored most, within FPL's formation rules (GK
    only for GK; at least 3 DEF, 2 MID, 1 FWD). The captain isn't swappable
    here — that's the captain decision, reported separately. None if no
    bench player outscored a legal swap. Returns ids plus the cost.

    Ollie (GW7): "my lowest scoring player I did select scored 0 points, so
    [Diop's 3 on the bench] only cost me 3 points".
    """
    starters = [p for p in picks if p["multiplier"] > 0]
    bench = [p for p in picks if p["multiplier"] == 0]
    counts = collections.Counter(positions.get(p["element_id"]) for p in starters)
    best = None
    for b in bench:
        b_pos, b_pts = positions.get(b["element_id"]), live_points.get(b["element_id"], 0)
        for s in starters:
            if s["multiplier"] in _CAPTAIN_MULTIPLIERS:
                continue
            s_pos = positions.get(s["element_id"])
            if (b_pos == "GKP") != (s_pos == "GKP"):
                continue
            if b_pos != s_pos:
                after = counts.copy()
                after[s_pos] -= 1
                after[b_pos] += 1
                if any(after[pos] < n for pos, n in _FORMATION_MIN.items()):
                    continue
            cost = b_pts - live_points.get(s["element_id"], 0)
            if cost > 0 and (best is None or cost > best["cost"]):
                best = {"bench": b["element_id"], "bench_points": b_pts, "starter": s["element_id"],
                        "starter_points": live_points.get(s["element_id"], 0), "cost": cost}
    return best
