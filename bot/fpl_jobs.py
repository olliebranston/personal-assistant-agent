"""FPL adherence ladder — Phase 1 (PHASE1-BRIEF.md §3).

A single 5-minute tick checks the gameweeks table for what's due, rather
than one job-queue timer per notification kind — simpler and more robust
against missed windows (deploys, restarts) than exact-time timers, per the
brief's own stated preference. Every message here is deterministic string
formatting, no LLM call — same pattern as every job in bot/scheduler.py
(TOOL_CALLING_DESIGN.md §4.4, Option A).
"""

from __future__ import annotations

import datetime
import logging
from zoneinfo import ZoneInfo

from telegram.ext import Application

import config
from services import fpl_client
from services.fpl_optimiser import compute_selling_price, verify_squad_value
from storage.db import get_connection
from storage.models import (
    get_all_gameweeks,
    get_latest_snapshot,
    get_my_history,
    get_unreviewed_finished_gameweeks,
    insert_player_snapshots,
    is_acknowledged,
    mark_notification_sent,
    replace_my_picks,
    upsert_my_history,
    was_notification_sent,
)
from tools.fpl import (
    cost_basis,
    element_index,
    format_countdown,
    format_deadline_london,
    get_fpl_calendar,
    get_fpl_chips,
    get_fpl_recommendation,
    get_fpl_squad,
    now_utc,
    owned_element_ids,
    status_flag,
    sync_gameweeks_from_bootstrap,
    target_gameweek,
)
from utils.telegram_format import send_formatted

logger = logging.getLogger(__name__)

_TZ = ZoneInfo("Europe/London")
_UID = config.TELEGRAM_ALLOWED_USER_ID
_TICK_INTERVAL_SEC = 300
_CHIP_NAMES = {"wildcard": "Wildcard", "freehit": "Free Hit", "3xc": "Triple Captain", "bboost": "Bench Boost"}


def _squad_table(squad: list[dict]) -> str:
    lines = []
    for p in squad:
        marker = " (C)" if p["is_captain"] else (" (V)" if p["is_vice"] else "")
        bench = "  bench" if not p["starting"] else ""
        lines.append(f"{p['position']:<4}{p['name']:<18}£{p['price']:.1f}m{marker}{bench}")
    return "```\n" + "\n".join(lines) + "\n```"


# ── Message builders ─────────────────────────────────────────────────────────


async def _send_t24(context, conn) -> None:
    """PHASE2-BRIEF.md §7's upgraded T-24h message: recommendation first, status
    dump only as a fallback when there's no squad yet or the recommendation
    fails validation — adherence still matters even with nothing to recommend."""
    rec = await get_fpl_recommendation(conn)
    if "error" in rec:
        logger.info("FPL T-24h: no recommendation available (%s) — falling back to status", rec["error"])
        await _send_t24_status_fallback(context, conn)
    else:
        await _send_t24_recommendation(context, conn, rec)
    await _snapshot_squad_for_t3_diff(conn)


async def _send_t24_recommendation(context, conn, rec: dict) -> None:
    tgw = target_gameweek(conn)
    data = await fpl_client.bootstrap()
    elements = element_index(data)

    def _name(eid: int) -> str:
        el = elements.get(eid)
        return el["web_name"] if el else str(eid)

    by_id = {o["id"]: o for o in rec["options"]}
    chosen = by_id[rec["recommended"]]

    lines = [
        f"FPL — GW{rec['gameweek']} deadline {rec['deadline_local']} "
        f"(in {format_countdown(tgw['deadline_utc'])})",
        "",
        f"Recommended: {chosen['label']}",
        chosen["rationale"],
    ]

    others = [o for o in rec["options"] if o["id"] != rec["recommended"]]
    if others:
        lines.append("")
        lines.append("Other options:")
        for o in others:
            hit_note = f", -{o['hit']}" if o["hit"] else ""
            lines.append(f"- {o['label']}: {o['xp_delta']:+.1f} xP{hit_note}")

    cap = rec["captain"]
    vice_note = f" (vice: {_name(cap['alternatives'][0])})" if cap["alternatives"] else ""
    lines += ["", f"Captain: {_name(cap['pick'])}{vice_note}"]

    chip = rec["chip"]
    chip_line = f"Chips: {chip['plan']}"
    if chip["play"]:
        chip_line += f" — play {_CHIP_NAMES.get(chip['play'], chip['play'])} this week"
    lines += ["", chip_line]

    if rec["warnings"]:
        lines.append("")
        lines.append("Warnings:")
        for w in rec["warnings"]:
            lines.append(f"- {w}")

    try:
        calendar = await get_fpl_calendar(conn, horizon=3)
        if "error" not in calendar and calendar["changed_since_last_check"]:
            gws = ", ".join(f"GW{g}" for g in calendar["changed_since_last_check"])
            lines += ["", f"Calendar change: {gws} shape changed this week."]
    except Exception as exc:
        logger.warning("FPL T-24h: calendar check failed: %s", exc)

    await send_formatted(context.bot, _UID, "\n".join(lines))


async def _send_t24_status_fallback(context, conn) -> None:
    """Phase 1's status dump — used when get_fpl_recommendation can't produce
    one (no squad synced yet, or it failed live validation)."""
    tgw = target_gameweek(conn)
    summary = await get_fpl_squad(conn)
    if "error" in summary:
        logger.warning("FPL T-24h: get_fpl_squad failed: %s", summary["error"])
        return

    lines = [
        f"FPL — GW{tgw['gw']} deadline {format_deadline_london(tgw['deadline_utc'])} "
        f"(in {format_countdown(tgw['deadline_utc'])})",
        "",
    ]

    if summary["squad"]:
        lines.append(_squad_table(summary["squad"]))
        if summary["flagged_players"]:
            lines.append("")
            lines.append("Flagged:")
            for p in summary["flagged_players"]:
                lines.append(f"- {p['name']}: {p['flag']}")
    else:
        lines.append("No squad on record yet — I'll read it from the API once this deadline passes.")

    ft = summary.get("free_transfers")
    bank = summary.get("bank")
    ft_line = f"Free transfers: {ft if ft is not None else '?'}"
    if bank is not None:
        ft_line += f"  |  Bank: £{bank:.1f}m"
    lines += ["", ft_line]

    chips = await get_fpl_chips(conn)
    if "error" not in chips:
        remaining = ", ".join(_CHIP_NAMES[c] for c in chips["remaining_current_set"]) or "none"
        expiry = f" ({chips['days_to_set1_expiry']}d to expiry)" if chips.get("days_to_set1_expiry") is not None else ""
        lines.append(f"Chips left this set: {remaining}{expiry}")

    await send_formatted(context.bot, _UID, "\n".join(lines))


async def _snapshot_squad_for_t3_diff(conn) -> None:
    gw, element_ids = owned_element_ids(conn)
    if not element_ids:
        return
    data = await fpl_client.bootstrap()
    elements = element_index(data)
    rows = [
        {
            "element_id": eid,
            "now_cost": elements[eid]["now_cost"],
            "status": elements[eid]["status"],
            "chance_next": elements[eid].get("chance_of_playing_next_round"),
            "news": elements[eid].get("news") or "",
        }
        for eid in element_ids
        if eid in elements
    ]
    insert_player_snapshots(conn, now_utc().isoformat(), rows)


async def _send_t3_if_changed(context, conn) -> None:
    tgw = target_gameweek(conn)
    gw, element_ids = owned_element_ids(conn)
    if not element_ids:
        return

    data = await fpl_client.bootstrap()
    elements = element_index(data)

    changes = []
    for eid in element_ids:
        el = elements.get(eid)
        if el is None:
            continue
        prev = get_latest_snapshot(conn, eid)
        if prev is None:
            continue
        news_changed = (prev["news"] or "") != (el.get("news") or "")
        if prev["status"] != el["status"] or news_changed:
            detail = f"{prev['status']} -> {el['status']}"
            if el.get("news"):
                detail += f" — {el['news']}"
            changes.append(f"- {el['web_name']}: {detail}")

    if not changes:
        return  # silence is the feature — nothing changed since T-24h

    text = f"FPL team news — GW{tgw['gw']}:\n" + "\n".join(changes)
    await send_formatted(context.bot, _UID, text)


async def _send_hard_nudge(context, conn, minutes: int) -> None:
    tgw = target_gameweek(conn)
    if is_acknowledged(conn, tgw["gw"]):
        return

    data = await fpl_client.bootstrap()
    elements = element_index(data)
    _, element_ids = owned_element_ids(conn)

    flagged = []
    for eid in element_ids:
        el = elements.get(eid)
        if el is None:
            continue
        flag = status_flag(el)
        if flag:
            flagged.append(f"- {el['web_name']}: {flag}")

    lines = [f"Deadline in {minutes} minutes — GW{tgw['gw']}."]
    if flagged:
        lines.append("Flagged in your squad:")
        lines.extend(flagged)
    lines.append("Reply '/fpl done' once you've sorted your team.")
    await send_formatted(context.bot, _UID, "\n".join(lines))


async def _send_review(context, conn, gw: int) -> None:
    hist_row = get_my_history(conn, gw)
    league_data = await fpl_client.league(config.FPL_LEAGUE_ID)
    results = league_data.get("standings", {}).get("results", [])
    my_standing = next((r for r in results if r["entry"] == config.FPL_TEAM_ID), None)

    lines = [f"GW{gw} review:"]
    if hist_row and hist_row.get("points") is not None:
        rank_str = f"{hist_row['overall_rank']:,}" if hist_row.get("overall_rank") else "?"
        lines.append(f"Points: {hist_row['points']}  |  Overall rank: {rank_str}")
    else:
        lines.append("Points: not yet synced — check again shortly.")

    if my_standing:
        last_rank = my_standing.get("last_rank")
        movement = (last_rank - my_standing["rank"]) if last_rank else 0
        move_str = f" ({'up' if movement > 0 else 'down'} {abs(movement)})" if movement else ""
        lines.append(f"Mini-league: {my_standing['rank']}/{len(results)}{move_str}")

    await send_formatted(context.bot, _UID, "\n".join(lines))


# ── Silent sync (no message) ─────────────────────────────────────────────────


async def _sync_passed_deadlines(conn, data: dict) -> None:
    """For every gameweek whose deadline has passed, read the real squad back from the
    API and write it — Robin never has to trust Ollie to report what he did (§4.5 of
    FPL-CONTEXT.md). picks() 404s until the API catches up; that's a normal state
    (§1 of PHASE1-BRIEF.md), so a miss here just retries next tick.

    Also runs the selling-price money sanity check (verify_squad_value) right
    while we have a freshly-synced squad and FPL's own reported team value in
    hand — the earliest point a cost-basis bug could be caught, before it ever
    reaches a recommendation.
    """
    now = now_utc()
    elements = element_index(data)
    for gw_row in get_all_gameweeks(conn):
        gw = gw_row["gw"]
        if fpl_client.parse_utc(gw_row["deadline_utc"]) >= now:
            continue
        if was_notification_sent(conn, gw, "picks_synced"):
            continue

        picks_data = await fpl_client.picks(config.FPL_TEAM_ID, gw)
        if picks_data is None:
            continue

        rows = [
            {
                "element_id": p["element"],
                "position": p["position"],
                "is_captain": int(bool(p["is_captain"])),
                "is_vice": int(bool(p["is_vice_captain"])),
                "multiplier": p["multiplier"],
            }
            for p in picks_data.get("picks", [])
        ]
        replace_my_picks(conn, gw, rows)

        eh = picks_data.get("entry_history", {})
        upsert_my_history(conn, {
            "gw": gw,
            "points": eh.get("points"),
            "total_points": eh.get("total_points"),
            "overall_rank": eh.get("overall_rank"),
            "bank": eh.get("bank"),
            "team_value": eh.get("value"),
            "transfers": eh.get("event_transfers"),
            "transfer_cost": eh.get("event_transfers_cost"),
            "chip": picks_data.get("active_chip"),
        })
        mark_notification_sent(conn, gw, "picks_synced", now_utc().isoformat())
        logger.info("FPL: synced picks/history for GW%d", gw)

        reported_value = eh.get("value")
        if reported_value is not None:
            squad_ids = {r["element_id"] for r in rows}
            try:
                transfer_rows = await fpl_client.transfers(config.FPL_TEAM_ID)
                basis = cost_basis(squad_ids, transfer_rows, elements)
                now_cost = {eid: elements[eid]["now_cost"] for eid in squad_ids if eid in elements}
                selling_price = {eid: compute_selling_price(basis[eid], now_cost[eid]) for eid in squad_ids if eid in now_cost}
                ok, diff = verify_squad_value(selling_price, squad_ids, reported_value)
                if not ok:
                    logger.error(
                        "FPL money sanity check failed for GW%d: computed selling-price sum "
                        "£%.1fm vs FPL-reported value £%.1fm (diff %+.1fm) — cost-basis logic "
                        "may be wrong",
                        gw, sum(selling_price.values()) / 10, reported_value / 10, diff / 10,
                    )
            except Exception as exc:
                logger.warning("FPL money sanity check errored for GW%d, skipping: %s", gw, exc)


# ── Tick ──────────────────────────────────────────────────────────────────────


async def _fpl_tick(context) -> None:
    if not config.FPL_ENABLED:
        return

    conn = get_connection()
    try:
        try:
            data = await fpl_client.bootstrap()
        except fpl_client.FPLError as exc:
            logger.warning("FPL tick: bootstrap unavailable, skipping this run: %s", exc)
            return
        sync_gameweeks_from_bootstrap(conn, data)

        # Sync picks/history before the review loop, so a GW whose deadline
        # and data_checked flip true in the same tick still reviews against
        # fresh points rather than an empty my_history row.
        await _sync_passed_deadlines(conn, data)

        for gw_row in get_unreviewed_finished_gameweeks(conn):
            await _send_review(context, conn, gw_row["gw"])
            mark_notification_sent(conn, gw_row["gw"], "review", now_utc().isoformat())

        tgw = target_gameweek(conn)
        if tgw is None:
            return
        deadline = fpl_client.parse_utc(tgw["deadline_utc"])
        delta_hours = (deadline - now_utc()).total_seconds() / 3600
        if delta_hours <= 0:
            return  # deadline passed — _sync_passed_deadlines handles it above

        gw = tgw["gw"]
        if delta_hours <= 24 and not was_notification_sent(conn, gw, "T24"):
            await _send_t24(context, conn)
            mark_notification_sent(conn, gw, "T24", now_utc().isoformat())

        if delta_hours <= 3 and not was_notification_sent(conn, gw, "T3"):
            await _send_t3_if_changed(context, conn)
            mark_notification_sent(conn, gw, "T3", now_utc().isoformat())

        if delta_hours <= 0.75 and not was_notification_sent(conn, gw, "T45"):
            await _send_hard_nudge(context, conn, 45)
            mark_notification_sent(conn, gw, "T45", now_utc().isoformat())

        if delta_hours <= 0.25 and not was_notification_sent(conn, gw, "T15"):
            await _send_hard_nudge(context, conn, 15)
            mark_notification_sent(conn, gw, "T15", now_utc().isoformat())
    except Exception as exc:
        logger.error("FPL tick failed: %s", exc, exc_info=True)
    finally:
        conn.close()


async def _fpl_weekly_chip_status(context) -> None:
    """Wednesday 19:00: chip status, escalating from GW14 if first-half chips are unused."""
    if not config.FPL_ENABLED:
        return
    conn = get_connection()
    try:
        chips = await get_fpl_chips(conn)
        if "error" in chips:
            logger.warning("FPL weekly chip status: %s", chips["error"])
            return

        remaining = ", ".join(_CHIP_NAMES[c] for c in chips["remaining_current_set"]) or "none"
        lines = [f"FPL chip status (set {chips['active_set']}): {remaining} remaining."]
        if chips.get("days_to_set1_expiry") is not None:
            lines.append(f"{chips['days_to_set1_expiry']} days until the set 1 deadline (GW19, 2 Jan 2027).")
        if chips.get("escalate"):
            lines.append("Unused first-half chips — use them or lose them at the GW19 deadline.")

        await send_formatted(context.bot, _UID, "\n".join(lines))
    finally:
        conn.close()


# ── Registration ──────────────────────────────────────────────────────────────


def register_fpl_jobs(app: Application) -> None:
    if not config.FPL_ENABLED:
        logger.info("FPL disabled (FPL_ENABLED=false) — skipping FPL jobs.")
        return

    jq = app.job_queue
    jq.run_repeating(_fpl_tick, interval=_TICK_INTERVAL_SEC, first=10)
    jq.run_daily(
        _fpl_weekly_chip_status,
        time=datetime.time(19, 0, tzinfo=_TZ),
        days=(3,),  # Wednesday (PTB: 0=Sun..6=Sat)
    )
    logger.info("FPL jobs registered: 5-min adherence tick, weekly chip status Wed 19:00")
