"""Blank/double gameweek detection (PHASE2-BRIEF.md §1).

Pure functions over the FPL `fixtures/` payload — detect, never hardcode.
No caching or DB access here (that's business logic, kept in tools/fpl.py
per the Phase 1 layering: services/ fetches and computes, tools/ persists
and reacts). A normal round is 10 fixtures, every team once; anything else
is reschedule-driven and becomes visible the moment the fixture feed changes.
"""

from __future__ import annotations

import collections


def gameweek_shape(fixtures: list[dict], gw: int, team_ids: set[int] | None = None) -> dict:
    """Per-team fixture count for one gameweek.

    team_ids: the full set of club ids to check for blanks against. If not
    given, it's inferred as every team appearing anywhere in `fixtures` —
    correct when called with the full-season fixture list (the intended
    real usage), but pass it explicitly if `fixtures` has already been
    filtered to a subset of gameweeks.

    Returns {'blanks': [team_ids with 0], 'doubles': [team_ids with 2+],
             'counts': {team_id: n}, 'total_fixtures': n}.
    """
    gw_fixtures = [f for f in fixtures if f.get("event") == gw]

    counts: collections.Counter = collections.Counter()
    for f in gw_fixtures:
        counts[f["team_h"]] += 1
        counts[f["team_a"]] += 1

    if team_ids is None:
        team_ids = {f["team_h"] for f in fixtures} | {f["team_a"] for f in fixtures}

    blanks = sorted(t for t in team_ids if counts.get(t, 0) == 0)
    doubles = sorted(t for t, n in counts.items() if n >= 2)

    return {
        "blanks": blanks,
        "doubles": doubles,
        "counts": dict(counts),
        "total_fixtures": len(gw_fixtures),
    }


def all_gameweek_shapes(fixtures: list[dict], team_ids: set[int] | None = None) -> dict[int, dict]:
    """gameweek_shape() for every gameweek present in `fixtures`, keyed by gw."""
    if team_ids is None:
        team_ids = {f["team_h"] for f in fixtures} | {f["team_a"] for f in fixtures}

    gws = sorted({f["event"] for f in fixtures if f.get("event") is not None})
    return {gw: gameweek_shape(fixtures, gw, team_ids=team_ids) for gw in gws}


def is_normal_shape(shape: dict, expected_teams: int = 20) -> bool:
    """A normal round: every team plays exactly once, 10 fixtures for 20 teams."""
    return (
        not shape["blanks"]
        and not shape["doubles"]
        and shape["total_fixtures"] == expected_teams // 2
    )
