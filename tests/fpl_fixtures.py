"""Shared synthetic FPL bootstrap/fixtures data for Phase 2 tool tests.

Not a test module (no test_ prefix — pytest won't collect it). 20 teams x 15
slots (2 GK/5 DEF/5 MID/3 FWD) each, structurally realistic bootstrap-static
and fixtures shapes without hitting the real API.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

_POSITIONS = [1, 1, 2, 2, 2, 2, 2, 3, 3, 3, 3, 3, 4, 4, 4]  # element_type per slot


def element_id(team: int, slot_idx: int) -> int:
    return (team - 1) * 15 + slot_idx + 1


def synthetic_bootstrap(num_gws: int = 10) -> dict:
    teams = [{"id": t, "name": f"Team{t}", "short_name": f"T{t}"} for t in range(1, 21)]
    elements = []
    for team in range(1, 21):
        for slot_idx, element_type in enumerate(_POSITIONS):
            eid = element_id(team, slot_idx)
            elements.append({
                "id": eid,
                "web_name": f"Player{eid}",
                "team": team,
                "element_type": element_type,
                "now_cost": 40 + (eid % 10) * 3,
                "status": "a",
                "news": "",
                "chance_of_playing_next_round": None,
                "points_per_game": str(round(2.0 + (eid * 7 % 40) / 10.0, 1)),
                "minutes": 2500,
                "selected_by_percent": "5.0",
            })
    # GW1's deadline is safely in the past (matching finished=True) and each
    # subsequent gw is a week later, relative to real "now" at test-run time —
    # so target_gameweek() reliably resolves to GW2 regardless of when the
    # suite actually runs.
    anchor = datetime.now(tz=timezone.utc) - timedelta(days=7)
    events = [
        {
            "id": gw,
            "deadline_time": (anchor + timedelta(days=7 * (gw - 1))).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "is_current": gw == 1,
            "is_next": gw == 2,
            "finished": gw == 1,
            "data_checked": gw == 1,
        }
        for gw in range(1, num_gws + 1)
    ]
    return {"events": events, "teams": teams, "elements": elements}


def synthetic_fixtures(num_gws: int = 10) -> list[dict]:
    fixtures = []
    fid = 1
    teams = list(range(1, 21))
    for gw in range(1, num_gws + 1):
        shift = (gw - 1) % 19
        rotated = [teams[0]] + teams[1:][shift:] + teams[1:][:shift]
        for i in range(10):
            home, away = rotated[i], rotated[19 - i]
            fixtures.append({
                "id": fid, "event": gw, "team_h": home, "team_a": away,
                "team_h_difficulty": 3, "team_a_difficulty": 3, "finished": gw == 1,
            })
            fid += 1
    return fixtures


def legal_squad_ids() -> set[int]:
    """15 players, one per club (well under the 3-per-club cap), legal 2/5/5/3 shape."""
    gk = [element_id(1, 0), element_id(2, 0)]
    df = [element_id(t, 2) for t in (3, 4, 5, 6, 7)]
    md = [element_id(t, 7) for t in (8, 9, 10, 11, 12)]
    fw = [element_id(t, 12) for t in (13, 14, 15)]
    return set(gk + df + md + fw)
