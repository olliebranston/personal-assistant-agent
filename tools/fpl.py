"""FPL tools — Phase 1 adherence layer (PHASE1-BRIEF.md) + Phase 2
recommendation engine (PHASE2-BRIEF.md).

Business logic lives here (not in services/fpl_client.py, which only
fetches and validates): syncing gameweeks/picks/history from the API into
SQLite, computing free transfers and chip status, and shaping data for
both the LLM-facing tools below and the deterministic reminder ladder in
bot/fpl_jobs.py — the two share these helpers so the numbers never drift
apart between a `/fpl` reply and a scheduled nudge.

Phase 2 adds: get_fpl_calendar (blank/double detection), get_fpl_chips now
carries a rule-based chip-timing `signal`, and get_fpl_recommendation — the
weekly hold/single/aggressive transfer recommendation, built by
services/fpl_xp + services/fpl_optimiser and re-checked by
services/fpl_validate before it's ever returned. The LLM never picks
players or authors a rationale (PHASE2-BRIEF.md §4/§5) — every fact in the
recommendation object comes from this module's solver output.

Each tool is `async def tool_name(conn, **kwargs) -> dict`, JSON-serialisable,
and returns {"error": "..."} on failure instead of raising (§4.2 of
TOOL_CALLING_DESIGN.md).
"""

from __future__ import annotations

import collections
import json
import logging
import sqlite3
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import config
from services import fpl_calendar, fpl_client, fpl_xp
from services.fpl_optimiser import (
    HIT_COST_PER_TRANSFER,
    RECOMMENDATION_BIAS_PER_HIT,
    Candidate,
    OptimiserInfeasible,
    compute_selling_price,
    net_value,
    solve,
    verify_squad_value,
)
from services.fpl_optimiser import POS as _OPT_POS
from services.fpl_validate import validate_solve
from storage.models import (
    add_preference,
    get_active_preferences,
    get_gameweek,
    get_gameweek_shape,
    get_latest_my_picks_gw,
    get_my_picks,
    get_next_gameweek,
    log_xp_predictions,
    prune_expired_preferences,
    set_acknowledged,
    upsert_gameweek_shape,
    upsert_gameweeks,
)

logger = logging.getLogger(__name__)

_TZ = ZoneInfo("Europe/London")

_POSITION_NAMES = {1: "GKP", 2: "DEF", 3: "MID", 4: "FWD"}
_STATUS_LABELS = {
    "d": "doubtful",
    "i": "injured",
    "s": "suspended",
    "u": "unavailable",
    "n": "not available",
}
_CHIP_TYPES = ["wildcard", "freehit", "3xc", "bboost"]
_CHIP_NAMES = {"wildcard": "Wildcard", "freehit": "Free Hit", "3xc": "Triple Captain", "bboost": "Bench Boost"}
_SET1_LAST_GW = 19  # PHASE1-BRIEF.md §1: set 1 expires at the GW19 deadline

# ── Phase 2 constants ─────────────────────────────────────────────────────────

_HORIZON = 5  # gameweeks — PHASE2-BRIEF.md §3
_MIN_MINUTES_FOR_CANDIDACY = 900  # mirrors fpl_squad_v0.py's default filter
_PREFERENCE_EXPIRY_DAYS = 21  # ~3 gameweeks — long enough to matter, short enough an
# October whim doesn't quietly distort the squad in March (PHASE2-BRIEF.md §6)

_CHIP_DOCTRINE = {
    1: {
        "wildcard": "Wildcard GW5-9",
        "freehit": "Free Hit on the first genuine blank, or by GW17",
        "3xc": "Triple Captain vs a promoted side at home",
        "bboost": "Bench Boost right after the first Wildcard",
    },
    2: {
        "wildcard": "Wildcard for the GW26-30 DGW/BGW run-up",
        "freehit": "Free Hit for the biggest blank (GW33 looks likely)",
        "3xc": "Triple Captain in a double gameweek",
        "bboost": "Bench Boost in a double gameweek",
    },
}


def _not_configured() -> dict | None:
    if not config.FPL_ENABLED or not config.FPL_TEAM_ID or not config.FPL_LEAGUE_ID:
        return {"error": "FPL is not configured (FPL_ENABLED/FPL_TEAM_ID/FPL_LEAGUE_ID)"}
    return None


def now_utc() -> datetime:
    return datetime.now(tz=timezone.utc)


def element_index(data: dict) -> dict[int, dict]:
    return {e["id"]: e for e in data["elements"]}


def team_index(data: dict) -> dict[int, dict]:
    return {t["id"]: t for t in data["teams"]}


def status_flag(element: dict) -> str | None:
    """Human label if the player isn't fully available, else None."""
    status = element.get("status", "a")
    if status == "a":
        return None
    label = _STATUS_LABELS.get(status, status)
    news = element.get("news") or ""
    chance = element.get("chance_of_playing_next_round")
    parts = [label]
    if chance is not None:
        parts.append(f"{chance}% chance of playing")
    if news:
        parts.append(news)
    return " — ".join(parts)


def sync_gameweeks_from_bootstrap(conn: sqlite3.Connection, data: dict) -> None:
    """Upsert the gameweeks table from bootstrap-static's events list."""
    rows = [
        {
            "gw": ev["id"],
            "deadline_utc": ev["deadline_time"],
            "is_current": int(bool(ev["is_current"])),
            "is_next": int(bool(ev["is_next"])),
            "finished": int(bool(ev["finished"])),
            "data_checked": int(bool(ev["data_checked"])),
        }
        for ev in data["events"]
    ]
    upsert_gameweeks(conn, rows)


def target_gameweek(conn: sqlite3.Connection) -> dict | None:
    """The gameweek to show reminders/status for: nearest upcoming deadline, else the current one."""
    upcoming = get_next_gameweek(conn, now_utc().isoformat())
    if upcoming:
        return upcoming
    return get_gameweek(conn, _current_gw_fallback(conn))


def _current_gw_fallback(conn: sqlite3.Connection) -> int:
    from storage.models import get_current_gameweek

    current = get_current_gameweek(conn)
    return current["gw"] if current else 1


def format_countdown(deadline_utc_iso: str) -> str:
    deadline = fpl_client.parse_utc(deadline_utc_iso)
    delta = deadline - now_utc()
    if delta.total_seconds() <= 0:
        return "deadline passed"
    hours, remainder = divmod(int(delta.total_seconds()), 3600)
    minutes = remainder // 60
    if hours >= 24:
        days, hours = divmod(hours, 24)
        return f"{days}d {hours}h {minutes}m"
    return f"{hours}h {minutes}m"


def format_deadline_london(deadline_utc_iso: str) -> str:
    return fpl_client.parse_utc(deadline_utc_iso).astimezone(_TZ).strftime("%a %d %b, %H:%M")


def owned_element_ids(conn: sqlite3.Connection) -> tuple[int, list[int]] | tuple[None, list]:
    """(gw, [element_id, ...]) for the most recently synced squad, or (None, []) if none yet."""
    gw = get_latest_my_picks_gw(conn)
    if gw is None:
        return None, []
    picks = get_my_picks(conn, gw)
    return gw, [p["element_id"] for p in picks]


def build_squad_rows(picks: list[dict], elements: dict[int, dict], teams: dict[int, dict]) -> list[dict]:
    """Merge my_picks rows with bootstrap element data into a display-ready list, squad order."""
    rows = []
    for p in picks:
        el = elements.get(p["element_id"])
        if el is None:
            continue
        rows.append({
            "element_id": p["element_id"],
            "name": el["web_name"],
            "team": teams.get(el["team"], {}).get("short_name", "?"),
            "position": _POSITION_NAMES.get(el["element_type"], "?"),
            "price": fpl_client.price_to_gbp(el["now_cost"]),
            "is_captain": bool(p["is_captain"]),
            "is_vice": bool(p["is_vice"]),
            "multiplier": p["multiplier"],
            "starting": p["multiplier"] > 0,
            "flag": status_flag(el),
        })
    return rows


def normalize_history_rows(hist: dict) -> list[dict]:
    """entry/{id}/history/'s 'current' list uses 'event'/'value'/'event_transfers*' field
    names and keeps chip usage in a separate top-level 'chips' list — normalise both into
    our my_history row shape (gw, points, total_points, overall_rank, bank, team_value,
    transfers, transfer_cost, chip) so the rest of this module and bot/fpl_jobs.py only
    ever deal with one shape.
    """
    chip_by_gw = {c["event"]: c["name"] for c in hist.get("chips", [])}
    rows = []
    for r in hist.get("current", []):
        gw = r["event"]
        rows.append({
            "gw": gw,
            "points": r.get("points"),
            "total_points": r.get("total_points"),
            "overall_rank": r.get("overall_rank"),
            "bank": r.get("bank"),
            "team_value": r.get("value"),
            "transfers": r.get("event_transfers"),
            "transfer_cost": r.get("event_transfers_cost"),
            "chip": chip_by_gw.get(gw),
        })
    return rows


def compute_free_transfers(history_rows: list[dict]) -> int:
    """Simulate the rolling free-transfer balance (max 5, since the 2023/24 rule change).

    GW1 has no free-transfer concept (unlimited, no cost) so simulation starts
    at FT=1 heading into GW2. A wildcard/free hit GW doesn't consume or reset
    the balance — transfers that week are unlimited and free, so the balance
    just accrues as if 0 transfers were made.
    """
    ft = 1
    for row in sorted(history_rows, key=lambda r: r["gw"]):
        if row["gw"] < 2:
            continue
        chip = row.get("chip")
        if chip in ("wildcard", "freehit"):
            ft = min(ft + 1, 5)
            continue
        transfers_made = row.get("transfers") or 0
        cost = row.get("transfer_cost") or 0
        paid_transfers = cost // 4
        free_used = min(max(transfers_made - paid_transfers, 0), ft)
        ft = min(max(ft - free_used, 0) + 1, 5)
    return ft


def chips_used_by_set(history_rows: list[dict]) -> dict[int, set[str]]:
    """{1: {chips used in GW1-19}, 2: {chips used in GW20-38}}."""
    used: dict[int, set[str]] = {1: set(), 2: set()}
    for row in history_rows:
        chip = row.get("chip")
        if not chip or chip not in _CHIP_TYPES:
            continue
        set_num = 1 if row["gw"] <= _SET1_LAST_GW else 2
        used[set_num].add(chip)
    return used


def active_chip_set(gw: int) -> int:
    return 1 if gw <= _SET1_LAST_GW else 2


# ── LLM-facing tools ──────────────────────────────────────────────────────────


async def get_fpl_squad(conn: sqlite3.Connection) -> dict:
    """`/fpl` — squad with prices/flags, last GW points, overall rank, league position, next deadline."""
    err = _not_configured()
    if err:
        return err
    try:
        data = await fpl_client.bootstrap()
        sync_gameweeks_from_bootstrap(conn, data)
        elements = element_index(data)
        teams = team_index(data)

        gw, _ = owned_element_ids(conn)
        squad = build_squad_rows(get_my_picks(conn, gw), elements, teams) if gw else []

        hist = await fpl_client.entry_history(config.FPL_TEAM_ID)
        past = normalize_history_rows(hist)
        last_gw_row = past[-1] if past else None

        league_data = await fpl_client.league(config.FPL_LEAGUE_ID)
        my_standing = next(
            (r for r in league_data.get("standings", {}).get("results", [])
             if r["entry"] == config.FPL_TEAM_ID),
            None,
        )

        tgw = target_gameweek(conn)

        return {
            "squad": squad,
            "squad_gw": gw,
            "flagged_players": [p for p in squad if p["flag"]],
            "last_gw_points": last_gw_row.get("points") if last_gw_row else None,
            "overall_rank": last_gw_row.get("overall_rank") if last_gw_row else None,
            "bank": fpl_client.price_to_gbp(last_gw_row["bank"]) if last_gw_row and last_gw_row.get("bank") is not None else None,
            "team_value": fpl_client.price_to_gbp(last_gw_row["team_value"]) if last_gw_row and last_gw_row.get("team_value") is not None else None,
            "free_transfers": compute_free_transfers(past),
            "league_name": league_data.get("league", {}).get("name"),
            "league_rank": my_standing.get("rank") if my_standing else None,
            "league_total_players": len(league_data.get("standings", {}).get("results", [])),
            "next_gw": tgw["gw"] if tgw else None,
            "next_deadline_london": format_deadline_london(tgw["deadline_utc"]) if tgw else None,
            "next_deadline_countdown": format_countdown(tgw["deadline_utc"]) if tgw else None,
        }
    except Exception as exc:
        logger.warning("get_fpl_squad failed: %s", exc)
        return {"error": str(exc)}


async def get_fpl_team(conn: sqlite3.Connection) -> dict:
    """`/fpl team` — squad only, grouped by position, captain/vice marked."""
    err = _not_configured()
    if err:
        return err
    try:
        data = await fpl_client.bootstrap()
        sync_gameweeks_from_bootstrap(conn, data)
        elements = element_index(data)
        teams = team_index(data)

        gw, _ = owned_element_ids(conn)
        if gw is None:
            return {"found": False, "message": "No squad on record yet — I'll read it from the API once a deadline has passed."}

        squad = build_squad_rows(get_my_picks(conn, gw), elements, teams)
        by_position: dict[str, list[dict]] = {"GKP": [], "DEF": [], "MID": [], "FWD": []}
        for p in squad:
            by_position.setdefault(p["position"], []).append(p)

        return {"found": True, "gw": gw, "by_position": by_position}
    except Exception as exc:
        logger.warning("get_fpl_team failed: %s", exc)
        return {"error": str(exc)}


async def get_fpl_league(conn: sqlite3.Connection) -> dict:
    """`/fpl league` — mini-league table with rank movement since last GW."""
    err = _not_configured()
    if err:
        return err
    try:
        league_data = await fpl_client.league(config.FPL_LEAGUE_ID)
        results = league_data.get("standings", {}).get("results", [])
        table = [
            {
                "rank": r["rank"],
                "last_rank": r["last_rank"],
                "movement": (r["last_rank"] - r["rank"]) if r["last_rank"] else 0,
                "entry_id": r["entry"],
                "team_name": r["entry_name"],
                "manager_name": r["player_name"],
                "total_points": r["total"],
                "is_me": r["entry"] == config.FPL_TEAM_ID,
            }
            for r in results
        ]
        return {"league_name": league_data.get("league", {}).get("name"), "table": table}
    except Exception as exc:
        logger.warning("get_fpl_league failed: %s", exc)
        return {"error": str(exc)}


def _chip_signal(chips_status: dict, shape_this_gw: dict | None) -> dict:
    """Rule-based chip timing signal (PHASE2-BRIEF.md §7 point 5) — NOT the full
    multi-period chip planner from FPL-CONTEXT.md's Phase 5 build table. `play`
    only fires when THIS gameweek's own detected shape (from fpl_calendar) is a
    blank/double and the matching chip is still available; otherwise it falls
    back to the static doctrine plan from FPL-CONTEXT.md §2.7."""
    active_set = chips_status["active_set"]
    remaining = set(chips_status["remaining_current_set"])
    play = None
    if shape_this_gw:
        if shape_this_gw.get("blanks") and "freehit" in remaining:
            play = "freehit"
        elif shape_this_gw.get("doubles") and "bboost" in remaining:
            play = "bboost"
    doctrine = _CHIP_DOCTRINE[active_set]
    plan = "; ".join(doctrine[c] for c in _CHIP_TYPES if c in remaining) or "all chips used for this set"
    expiry = chips_status.get("days_to_set1_expiry")
    if active_set == 1 and expiry is not None:
        plan += f"; first set expires in {expiry}d"
    return {"play": play, "plan": plan}


async def get_fpl_chips(conn: sqlite3.Connection) -> dict:
    """`/fpl chips` or 'when should I use my chips' — which chips are used, which
    remain, days until the active set expires, and a rule-based timing signal."""
    err = _not_configured()
    if err:
        return err
    try:
        data = await fpl_client.bootstrap()
        sync_gameweeks_from_bootstrap(conn, data)

        hist = await fpl_client.entry_history(config.FPL_TEAM_ID)
        past = normalize_history_rows(hist)
        used = chips_used_by_set(past)

        tgw = target_gameweek(conn)
        current_gw = tgw["gw"] if tgw else 1
        active_set = active_chip_set(current_gw)

        gw19 = get_gameweek(conn, _SET1_LAST_GW)
        days_to_set1_expiry = None
        if gw19 and active_set == 1:
            delta = fpl_client.parse_utc(gw19["deadline_utc"]) - now_utc()
            days_to_set1_expiry = max(delta.days, 0)

        result = {
            "active_set": active_set,
            "used_set1": sorted(used[1]),
            "used_set2": sorted(used[2]),
            "remaining_current_set": sorted(set(_CHIP_TYPES) - used[active_set]),
            "chip_names": _CHIP_NAMES,
            "days_to_set1_expiry": days_to_set1_expiry,
            "escalate": active_set == 1 and current_gw >= 14 and bool(set(_CHIP_TYPES) - used[1]),
        }

        try:
            fixtures = await fpl_client.fixtures(gw=current_gw)
            shape = fpl_calendar.gameweek_shape(fixtures, current_gw, team_ids=set(team_index(data)))
        except Exception as exc:
            logger.warning("get_fpl_chips: calendar shape lookup failed, signal will omit it: %s", exc)
            shape = None
        result["signal"] = _chip_signal(result, shape)
        return result
    except Exception as exc:
        logger.warning("get_fpl_chips failed: %s", exc)
        return {"error": str(exc)}


async def get_fpl_calendar(conn: sqlite3.Connection, horizon: int = 8) -> dict:
    """'any blanks coming up' — blank/double gameweek report for the next
    `horizon` gameweeks, and which of them changed shape since the last check
    (a reschedule is notification-worthy — PHASE2-BRIEF.md §1)."""
    err = _not_configured()
    if err:
        return err
    try:
        data = await fpl_client.bootstrap()
        sync_gameweeks_from_bootstrap(conn, data)
        fixtures = await fpl_client.fixtures()
        teams = team_index(data)
        team_ids = set(teams)

        tgw = target_gameweek(conn)
        start_gw = tgw["gw"] if tgw else 1

        shapes = fpl_calendar.all_gameweek_shapes(fixtures, team_ids=team_ids)
        now_iso = now_utc().isoformat()

        report = []
        changed_gws = []
        for gw in range(start_gw, start_gw + horizon):
            shape = shapes.get(gw)
            if shape is None:
                continue
            blanks_json = json.dumps(shape["blanks"])
            doubles_json = json.dumps(shape["doubles"])
            prev = get_gameweek_shape(conn, gw)
            newly_notable = shape["blanks"] or shape["doubles"]
            if newly_notable and (prev is None or prev["blanks_json"] != blanks_json or prev["doubles_json"] != doubles_json):
                changed_gws.append(gw)
            upsert_gameweek_shape(conn, gw, shape["total_fixtures"], blanks_json, doubles_json, now_iso)
            if newly_notable:
                report.append({
                    "gw": gw,
                    "blanks": [teams[t]["short_name"] for t in shape["blanks"] if t in teams],
                    "doubles": [teams[t]["short_name"] for t in shape["doubles"] if t in teams],
                })

        return {
            "start_gw": start_gw,
            "horizon": horizon,
            "blank_or_double_gameweeks": report,
            "changed_since_last_check": changed_gws,
        }
    except Exception as exc:
        logger.warning("get_fpl_calendar failed: %s", exc)
        return {"error": str(exc)}


async def fpl_acknowledge(conn: sqlite3.Connection) -> dict:
    """`/fpl done` — silence the T-45m/T-15m nudges for the upcoming gameweek."""
    err = _not_configured()
    if err:
        return err
    try:
        sync_gameweeks_from_bootstrap(conn, await fpl_client.bootstrap())
        tgw = target_gameweek(conn)
        if tgw is None:
            return {"error": "no upcoming gameweek found"}
        set_acknowledged(conn, tgw["gw"], now_utc().isoformat())
        return {"acknowledged": True, "gw": tgw["gw"]}
    except Exception as exc:
        logger.warning("fpl_acknowledge failed: %s", exc)
        return {"error": str(exc)}


# ── Phase 2: recommendation engine ───────────────────────────────────────────
#
# get_fpl_recommendation assembles a candidate pool (services/fpl_xp for
# per-player horizon xP), solves hold/single/aggressive with
# services/fpl_optimiser, re-checks the chosen option with
# services/fpl_validate against the same live data it solved against, and
# only then returns the §4 output contract. If validation fails, the
# recommendation is not returned — the tool returns {"error": ...} instead,
# same convention as everywhere else, so the caller never sees a squad this
# module isn't sure about.


def cost_basis(current_squad: set[int], transfer_rows: list[dict], elements: dict[int, dict]) -> dict[int, int]:
    """Purchase price per currently-owned player.

    A player who appears in the public transfer log (entry/{id}/transfers/) has
    an exact, dated element_in_cost — use that. A player absent from the log is
    NOT an unknown: they're an initial-squad pick Robin has never seen a
    transfer for, and bootstrap-static's cost_change_start gives exactly how
    much their price has moved since the season started (now_cost = start_cost
    + cost_change_start, per the FPL API), so start_cost = now_cost -
    cost_change_start is their real bought price.

    Using now_cost directly here was a bug: it silently overstated available
    funds for any player who'd risen, since it ignored the 50% sell-on fee —
    and since this fallback covers every player never transferred, it hit an
    entire freshly-built squad (all 15, day one), not an edge case.

    Caveat: cost_change_start's sign wasn't verified against a real mover
    before this was written — no player had moved price yet this preseason
    (checked live: 0/595 elements had a nonzero cost_change_start). It's
    implemented per the documented convention above. The money self-check
    (verify_squad_value, called from get_fpl_recommendation and the
    post-deadline sync in bot/fpl_jobs.py) is the live tripwire: if the sign
    were wrong, it fires loudly the first time any owned player's price
    actually changes.
    """
    basis = {}
    for eid in current_squad:
        el = elements.get(eid, {})
        now_cost = el.get("now_cost", 0)
        cost_change_start = el.get("cost_change_start") or 0
        basis[eid] = now_cost - cost_change_start
    for t in sorted(transfer_rows, key=lambda r: r.get("time", "")):
        if t.get("element_in") in current_squad:
            basis[t["element_in"]] = t["element_in_cost"]
    return basis


def _build_candidate_pool(
    data: dict,
    fixtures: list[dict],
    start_gw: int,
    horizon: int,
    current_squad: set[int],
    force_in: set[int] = frozenset(),
) -> tuple[list[Candidate], dict[int, dict[int, float]]]:
    """Candidate pool for the optimiser, plus the raw (undiscounted) per-GW xp
    breakdown per candidate — the latter is what gets logged to xp_predictions,
    since a discounted planning value isn't a 'prediction' for a specific GW."""
    candidates: list[Candidate] = []
    raw_predictions: dict[int, dict[int, float]] = {}

    for el in data["elements"]:
        eid = el["id"]
        minutes = el.get("minutes") or 0
        avail = fpl_xp.availability(el["status"], el.get("chance_of_playing_next_round"))
        keep = eid in current_squad or eid in force_in or (avail > 0.74 and minutes >= _MIN_MINUTES_FOR_CANDIDACY)
        if not keep:
            continue

        ppg = float(el.get("points_per_game") or 0.0)
        raw_by_gw = fpl_xp.horizon_xp(
            points_per_game=ppg, minutes=minutes, status=el["status"],
            chance_of_playing_next_round=el.get("chance_of_playing_next_round"),
            team_id=el["team"], fixtures=fixtures, start_gw=start_gw, horizon=horizon,
            discount=1.0,
        )
        discounted = sum(v * (fpl_xp.HORIZON_DISCOUNT**i) for i, v in enumerate(raw_by_gw.values()))

        raw_predictions[eid] = raw_by_gw
        candidates.append(Candidate(
            element_id=eid, team_id=el["team"], position=_OPT_POS[el["element_type"]],
            now_cost=el["now_cost"], horizon_xp=discounted,
        ))

    return candidates, raw_predictions


def _resolve_names(names: list[str], elements: dict[int, dict]) -> tuple[set[int], list[str]]:
    """Match by web_name, case-insensitive. Ambiguous or missing names are reported
    as warnings rather than guessed — guessing here is exactly how an agent ends up
    recommending the wrong player (mirrors fpl_squad_v0.py's `match()`)."""
    ids: set[int] = set()
    problems: list[str] = []
    for name in names:
        hits = [eid for eid, el in elements.items() if el["web_name"].lower() == name.strip().lower()]
        if not hits:
            problems.append(f"couldn't find a player named '{name}' — not applied")
        elif len(hits) > 1:
            problems.append(f"'{name}' is ambiguous ({len(hits)} players share that name) — not applied")
        else:
            ids.add(hits[0])
    return ids, problems


def _pair_transfers(transfers_out: list[int], transfers_in: list[int], elements: dict[int, dict]) -> list[dict]:
    """Pair drops with adds by position — squad position counts are exact in the
    solver, so each position's drop/add counts always match 1:1."""
    out_by_pos: dict[str, list[int]] = collections.defaultdict(list)
    for eid in transfers_out:
        out_by_pos[_OPT_POS[elements[eid]["element_type"]]].append(eid)
    in_by_pos: dict[str, list[int]] = collections.defaultdict(list)
    for eid in transfers_in:
        in_by_pos[_OPT_POS[elements[eid]["element_type"]]].append(eid)

    pairs = []
    for pos, outs in out_by_pos.items():
        for o, i in zip(sorted(outs), sorted(in_by_pos.get(pos, []))):
            pairs.append({"out": o, "in": i})
    return pairs


def _option_label(opt_id: str, pairs: list[dict], elements: dict[int, dict], hit: int) -> str:
    if opt_id == "hold" or not pairs:
        return "No transfer"
    label = ", ".join(f"{elements[p['out']]['web_name']} → {elements[p['in']]['web_name']}" for p in pairs)
    if hit:
        label += f" (-{hit})"
    return label


def _option_rationale(opt_id: str, pairs: list[dict], hit: int, xp_delta: float, free_transfers: int, elements: dict[int, dict]) -> str:
    if opt_id == "hold" or not pairs:
        if free_transfers >= 5:
            return "Free transfers are already capped at 5 — use one soon or a rolled transfer goes to waste."
        return f"Bank the transfer; {free_transfers} free transfer(s) available next week."

    names_in = ", ".join(elements[p["in"]]["web_name"] for p in pairs)
    line = f"{names_in} projects {xp_delta:+.1f} xP over the next {_HORIZON} gameweeks"
    if hit:
        bar = HIT_COST_PER_TRANSFER + RECOMMENDATION_BIAS_PER_HIT
        net = xp_delta - hit
        if net <= RECOMMENDATION_BIAS_PER_HIT:
            line += f", for a -{hit} hit — below the {bar:.0f}-point bar, shown for completeness, not advised."
        else:
            line += f", for a -{hit} hit — clears the bar for taking it."
    else:
        line += ", using a free transfer."
    return line


def _persist_preference(conn: sqlite3.Connection, kind: str, element_id: int) -> None:
    now = now_utc()
    expires = (now + timedelta(days=_PREFERENCE_EXPIRY_DAYS)).isoformat()
    add_preference(conn, kind, str(element_id), now.isoformat(), expires)


def _active_preference_ids(conn: sqlite3.Connection, kind: str) -> set[int]:
    prune_expired_preferences(conn, now_utc().isoformat())
    rows = get_active_preferences(conn, now_utc().isoformat())
    return {int(r["value"]) for r in rows if r["kind"] == kind}


async def get_fpl_recommendation(
    conn: sqlite3.Connection,
    force_in: list[str] | None = None,
    force_out: list[str] | None = None,
) -> dict:
    """'what should I do this week' / '/fpl' recommendation — the §4 output
    contract: hold/single/aggressive options, one chosen by the solver (not the
    LLM), captain, chip signal, warnings. force_in/force_out are player names
    from THIS turn's message; they're merged with any still-active preferences
    from earlier turns and persisted for `_PREFERENCE_EXPIRY_DAYS`.

    Returns {"error": ...} instead of a recommendation if there's no squad to
    evolve yet, or if the chosen option fails live validation — this module
    never returns a recommendation it isn't sure about (PHASE2-BRIEF.md §4).
    """
    err = _not_configured()
    if err:
        return err
    try:
        data = await fpl_client.bootstrap()
        sync_gameweeks_from_bootstrap(conn, data)
        elements = element_index(data)

        tgw = target_gameweek(conn)
        if tgw is None:
            return {"error": "no upcoming gameweek found"}
        gw = tgw["gw"]

        _, owned = owned_element_ids(conn)
        current_squad = set(owned)
        if not current_squad:
            return {
                "error": "no squad on record yet — recommendations start once your "
                         "squad syncs after a deadline passes (see get_fpl_team)"
            }

        # Only fetch fixtures/transfers once we know there's a squad to evolve —
        # cheaper, and keeps the "no squad" path down to a single bootstrap call.
        fixtures = await fpl_client.fixtures()

        history = await fpl_client.entry_history(config.FPL_TEAM_ID)
        past = normalize_history_rows(history)
        free_transfers = compute_free_transfers(past)
        last_row = past[-1] if past else None
        bank = last_row["bank"] if last_row and last_row.get("bank") is not None else 0

        now_cost = {eid: el["now_cost"] for eid, el in elements.items()}
        transfer_rows = await fpl_client.transfers(config.FPL_TEAM_ID)
        basis = cost_basis(current_squad, transfer_rows, elements)
        selling_price = {eid: compute_selling_price(basis[eid], now_cost[eid]) for eid in current_squad}

        # Cross-check against FPL's own reported team value (entry_history.value
        # = sum of selling prices across the 15) before this money math feeds a
        # real decision — a silent drift here compounds every week it goes
        # unnoticed. Refuse rather than recommend on numbers we can't trust.
        reported_value = last_row.get("team_value") if last_row else None
        if reported_value is not None:
            ok, diff = verify_squad_value(selling_price, current_squad, reported_value)
            if not ok:
                logger.error(
                    "FPL money sanity check failed: computed selling-price sum £%.1fm vs "
                    "FPL-reported value £%.1fm (diff %+.1fm) — refusing to recommend",
                    sum(selling_price.values()) / 10, reported_value / 10, diff / 10,
                )
                return {
                    "error": (
                        f"selling-price sanity check failed: computed £{sum(selling_price.values())/10:.1f}m "
                        f"vs FPL-reported £{reported_value/10:.1f}m — refusing to recommend until this is resolved"
                    )
                }

        this_turn_in, in_problems = _resolve_names(force_in or [], elements)
        this_turn_out, out_problems = _resolve_names(force_out or [], elements)
        for eid in this_turn_in:
            _persist_preference(conn, "force_in", eid)
        for eid in this_turn_out:
            _persist_preference(conn, "force_out", eid)
        force_in_ids = this_turn_in | _active_preference_ids(conn, "force_in")
        force_out_ids = this_turn_out | _active_preference_ids(conn, "force_out")
        # A player can't be forced both in and out — an explicit ask this turn wins.
        force_out_ids -= this_turn_in
        force_in_ids -= this_turn_out

        candidates, raw_predictions = _build_candidate_pool(
            data, fixtures, gw, _HORIZON, current_squad, force_in=force_in_ids,
        )
        candidate_ids = {c.element_id for c in candidates}
        missing_force_in = force_in_ids - candidate_ids
        if missing_force_in:
            in_problems.append(
                f"{', '.join(elements[e]['web_name'] for e in missing_force_in if e in elements)} "
                "not available to force in"
            )
            force_in_ids -= missing_force_in

        log_xp_predictions(
            conn, gw, fpl_xp.MODEL_VERSION, now_utc().isoformat(),
            {eid: by_gw.get(gw, 0.0) for eid, by_gw in raw_predictions.items()},
        )

        solve_kwargs = dict(
            candidates=candidates, current_squad=current_squad, selling_price=selling_price,
            bank=bank, free_transfers=free_transfers,
        )
        try:
            # Forced preferences don't apply to hold — "do nothing" can't include a
            # not-yet-owned player by definition, and forcing a currently-owned
            # player out would make transfer_count=0 infeasible for the same
            # reason. That contradiction is exactly what the cost-of-preference
            # comparison below measures — hold isn't broken by it, it's just exempt.
            hold = solve(**solve_kwargs, transfer_count=0)
            single = solve(**solve_kwargs, transfer_count=1, force_in=force_in_ids, force_out=force_out_ids)
            aggressive = solve(**solve_kwargs, transfer_count=2, force_in=force_in_ids, force_out=force_out_ids)
        except OptimiserInfeasible as exc:
            logger.error("get_fpl_recommendation: solver infeasible: %s", exc)
            return {"error": f"no legal squad found ({exc}) — check forced preferences aren't contradictory"}

        hold_xp = hold.total_horizon_xp
        results_by_id = {"hold": hold, "single": single, "aggressive": aggressive}
        recommended_id = max(results_by_id, key=lambda k: net_value(results_by_id[k], hold_xp, biased=True))

        warnings = list(in_problems) + list(out_problems)
        options = []
        warned_incoming: set[int] = set()
        for opt_id, result in results_by_id.items():
            pairs = _pair_transfers(result.transfers_out, result.transfers_in, elements)
            xp_delta = result.total_horizon_xp - hold_xp
            for p in pairs:
                flag = status_flag(elements[p["in"]])
                if flag:
                    warned_incoming.add(p["in"])
            options.append({
                "id": opt_id,
                "label": _option_label(opt_id, pairs, elements, result.hit),
                "transfers": pairs,
                "xp_delta": round(xp_delta, 1),
                "hit": result.hit,
                "rationale": _option_rationale(opt_id, pairs, result.hit, xp_delta, free_transfers, elements),
            })
        for eid in warned_incoming:
            warnings.append(f"{elements[eid]['web_name']} flagged: {status_flag(elements[eid])}")

        if force_in_ids:
            try:
                baseline_best = max(
                    (solve(**solve_kwargs, transfer_count=n) for n in (0, 1, 2)),
                    key=lambda r: net_value(r, hold_xp, biased=True),
                )
                forced_best = max(results_by_id.values(), key=lambda r: net_value(r, hold_xp, biased=True))
                cost = max(baseline_best.total_horizon_xp - forced_best.total_horizon_xp, 0.0)
                if cost > 0.05:
                    names = ", ".join(elements[e]["web_name"] for e in force_in_ids if e in elements)
                    warnings.append(f"Forcing {names} in costs {cost:.1f} xP over the horizon vs the unconstrained best.")
            except OptimiserInfeasible:
                pass  # cost-of-preference is informational only; don't fail the whole recommendation over it

        recommended_result = results_by_id[recommended_id]
        candidate_by_id = {c.element_id: c for c in candidates}
        starters_by_xp = sorted(
            (eid for eid in recommended_result.xi if eid != recommended_result.captain),
            key=lambda eid: -candidate_by_id[eid].horizon_xp,
        )
        captain_section = {
            "pick": recommended_result.captain,
            "alternatives": starters_by_xp[:2],
            "rationale": (
                f"{elements[recommended_result.captain]['web_name']} projects the highest horizon xP "
                f"among starters ({candidate_by_id[recommended_result.captain].horizon_xp:.1f})."
            ),
        }

        chips_status = await get_fpl_chips(conn)
        chip_info = chips_status.get("signal", {"play": None, "plan": ""}) if "error" not in chips_status else {"play": None, "plan": ""}

        expected_prices = {c.element_id: c.now_cost for c in candidates}
        outcome = validate_solve(
            recommended_result, elements, selling_price, bank,
            warned_element_ids=warned_incoming, expected_prices=expected_prices,
        )
        if not outcome.valid:
            logger.error("FPL recommendation failed validation, not returning it: %s", outcome.errors)
            return {"error": "recommendation failed validation, not sending: " + "; ".join(outcome.errors)}

        return {
            "gameweek": gw,
            "deadline_local": format_deadline_london(tgw["deadline_utc"]),
            "recommended": recommended_id,
            "options": options,
            "captain": captain_section,
            "chip": chip_info,
            "warnings": warnings,
        }
    except Exception as exc:
        logger.warning("get_fpl_recommendation failed: %s", exc)
        return {"error": str(exc)}


# ── Tool schemas (OpenAI function-calling format) ───────────────────────────

TOOL_SCHEMAS: list[dict] = [
    {
        "type": "function",
        "function": {
            "name": "get_fpl_squad",
            "description": (
                "Get Ollie's Fantasy Premier League overview: his squad with prices and "
                "injury/availability flags, points from the last completed gameweek, overall "
                "rank, mini-league position, free transfers available, bank, and the next "
                "deadline with a countdown. Use this for '/fpl' or general FPL status questions."
            ),
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_fpl_team",
            "description": (
                "Get just Ollie's FPL squad, grouped by position (GKP/DEF/MID/FWD), with "
                "captain and vice-captain marked. Use for '/fpl team' or 'what's my FPL squad'."
            ),
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_fpl_league",
            "description": (
                "Get the FPL mini-league table with each manager's rank movement since last "
                "gameweek. Use for '/fpl league' or 'how's the mini-league looking'."
            ),
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_fpl_chips",
            "description": (
                "Get FPL chip status: which of Wildcard/Free Hit/Triple Captain/Bench Boost "
                "have been used in the current chip set, which remain, days left before the "
                "first set expires (GW19 deadline, 2 Jan 2027), and a 'signal' block with a "
                "rule-based play-this-week flag plus the doctrine plan for remaining chips. "
                "Use for '/fpl chips', 'what chips do I have left', or 'when should I use my "
                "chips' — this already covers that question, no need to also call "
                "get_fpl_calendar for it."
            ),
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_fpl_calendar",
            "description": (
                "Get upcoming blank/double gameweeks — teams with zero or 2+ fixtures in a "
                "gameweek, detected live from the fixture list (never hardcoded), plus which "
                "gameweeks changed shape since the last check (an early reschedule warning). "
                "Use for 'any blanks coming up', 'when's the next double gameweek', or similar."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "horizon": {
                        "type": "integer",
                        "description": "How many gameweeks ahead to check, starting from the next deadline. Defaults to 8.",
                    },
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_fpl_recommendation",
            "description": (
                "Get this week's FPL transfer recommendation: three options (hold/single/"
                "aggressive) with one chosen by the solver, captain pick with alternatives, "
                "chip signal, and warnings. THE SOLVER PICKS 'recommended' AND WRITES EVERY "
                "RATIONALE — never state a player, price, or xP figure that isn't in this "
                "tool's result, never change which option is recommended, never invent your "
                "own reasoning beyond paraphrasing what's returned. Use for 'what should I do "
                "this week', 'should I captain X' (see the captain section), or any transfer "
                "question. To force a specific player in or out (e.g. 'get me Palmer in'), "
                "pass their name in force_in/force_out — the result's warnings will quote the "
                "xP cost of that preference if there is one. Preferences persist for future "
                "weeks automatically; no need to repeat them."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "force_in": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Player web_names Ollie wants forced into the squad, e.g. ['Palmer'].",
                    },
                    "force_out": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Player web_names Ollie wants forced out of the squad.",
                    },
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "fpl_acknowledge",
            "description": (
                "Mark that Ollie has sorted his FPL team for the upcoming deadline — stops the "
                "T-45min/T-15min deadline nudges for that gameweek. Call this for '/fpl done' "
                "or when Ollie confirms he's made his transfers/checked his team."
            ),
            "parameters": {"type": "object", "properties": {}},
        },
    },
]
