"""Tests for services/fpl_calendar.py (PHASE2-BRIEF.md §1 + §8).

Per CLAUDE.md's offline-testing rule, the "against today's fixture list"
acceptance check uses a synthetic but realistic 38-gameweek, 20-team, 10-
fixtures-a-week season (a real live pull was already smoke-tested manually
against the live API) — this keeps the suite deterministic and network-free
while still exercising the exact shape the real calendar has right now: no
built-in blanks or doubles anywhere in the base schedule.
"""

from __future__ import annotations

from services.fpl_calendar import all_gameweek_shapes, gameweek_shape, is_normal_shape

_TEAMS = list(range(1, 21))  # 20 team ids


def _normal_season_fixtures() -> list[dict]:
    """A synthetic but structurally realistic season: 38 gameweeks, every team
    plays exactly once per gameweek (10 fixtures/gw), nobody blanks or doubles —
    matching PHASE2-BRIEF.md §1's description of the real 2026/27 base schedule."""
    fixtures = []
    fid = 1
    for gw in range(1, 39):
        # Rotate pairings each gw so it isn't literally the same 10 pairs every week —
        # irrelevant to shape detection, but keeps the fixture list realistic.
        shift = (gw - 1) % 19
        rotated = [_TEAMS[0]] + _TEAMS[1:][shift:] + _TEAMS[1:][:shift]
        for i in range(10):
            home, away = rotated[i], rotated[19 - i]
            fixtures.append({
                "id": fid, "event": gw, "team_h": home, "team_a": away,
                "team_h_difficulty": 3, "team_a_difficulty": 3, "finished": False,
            })
            fid += 1
    return fixtures


def _reschedule(fixtures: list[dict], gw: int, team_id: int, moved_to_gw: int) -> list[dict]:
    """Simulate a postponement: move every fixture involving team_id out of `gw`
    and into `moved_to_gw` (its opponent's fixture moves with it, creating a
    blank in `gw` for both teams and a double in `moved_to_gw` for both)."""
    out = []
    for f in fixtures:
        if f["event"] == gw and (f["team_h"] == team_id or f["team_a"] == team_id):
            f = {**f, "event": moved_to_gw}
        out.append(f)
    return out


# ── Normal season ────────────────────────────────────────────────────────────


def test_normal_season_has_10_fixtures_and_no_blanks_or_doubles_every_gw():
    fixtures = _normal_season_fixtures()
    shapes = all_gameweek_shapes(fixtures, team_ids=set(_TEAMS))

    assert len(shapes) == 38
    for gw, shape in shapes.items():
        assert shape["total_fixtures"] == 10, f"GW{gw} has {shape['total_fixtures']} fixtures"
        assert shape["blanks"] == [], f"GW{gw} unexpectedly blanks: {shape['blanks']}"
        assert shape["doubles"] == [], f"GW{gw} unexpectedly doubles: {shape['doubles']}"
        assert sum(shape["counts"].values()) == 20  # every team appears exactly once
        assert is_normal_shape(shape)


def test_gameweek_shape_counts_every_team_once_in_a_normal_round():
    fixtures = _normal_season_fixtures()
    shape = gameweek_shape(fixtures, 1, team_ids=set(_TEAMS))
    assert set(shape["counts"]) == set(_TEAMS)
    assert all(n == 1 for n in shape["counts"].values())


# ── Synthetic reschedule → blank/double ─────────────────────────────────────


def test_reschedule_creates_a_blank_and_a_double():
    fixtures = _normal_season_fixtures()
    # Team 5's GW10 fixture (vs whoever) gets postponed into GW11.
    rescheduled = _reschedule(fixtures, gw=10, team_id=5, moved_to_gw=11)

    gw10 = gameweek_shape(rescheduled, 10, team_ids=set(_TEAMS))
    gw11 = gameweek_shape(rescheduled, 11, team_ids=set(_TEAMS))

    assert 5 in gw10["blanks"]
    assert gw10["total_fixtures"] == 9
    assert not gw10["doubles"]

    assert 5 in gw11["doubles"]
    assert gw11["counts"][5] == 2
    assert gw11["total_fixtures"] == 11
    assert not is_normal_shape(gw10)
    assert not is_normal_shape(gw11)


def test_the_original_opponent_also_blanks_and_doubles():
    fixtures = _normal_season_fixtures()
    # Find team 5's actual GW10 opponent before rescheduling.
    opponent = next(
        f["team_a"] if f["team_h"] == 5 else f["team_h"]
        for f in fixtures if f["event"] == 10 and (f["team_h"] == 5 or f["team_a"] == 5)
    )

    rescheduled = _reschedule(fixtures, gw=10, team_id=5, moved_to_gw=11)
    gw10 = gameweek_shape(rescheduled, 10, team_ids=set(_TEAMS))
    gw11 = gameweek_shape(rescheduled, 11, team_ids=set(_TEAMS))

    assert opponent in gw10["blanks"]
    assert opponent in gw11["doubles"]


def test_gameweek_shape_infers_team_universe_when_not_given():
    fixtures = _normal_season_fixtures()
    shape = gameweek_shape(fixtures, 1)  # no team_ids passed
    assert set(shape["counts"]) == set(_TEAMS)
    assert shape["blanks"] == []
