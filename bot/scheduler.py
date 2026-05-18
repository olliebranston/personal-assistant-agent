"""Proactive scheduled jobs sent to Ollie via Telegram."""

from __future__ import annotations

import datetime
import logging
from zoneinfo import ZoneInfo

from telegram.ext import Application

import config
from agents import gym as gym_agent
from agents import meal as meal_agent
from storage.db import get_connection
from storage.models import get_daily_totals

logger = logging.getLogger(__name__)

_TZ = ZoneInfo("Europe/London")
_UID = config.TELEGRAM_ALLOWED_USER_ID

# ── Batch cook tips (Sunday job) ─────────────────────────────────────────────

_BATCH_COOK_TIPS = """\
Batch cook order of operations (fastest first):
  1. Start any lentils/grains — they need the most time, hands-off.
  2. Prep veg while they cook (chop, season, roast at 200°C).
  3. Press tofu / cook eggs / fry aromatics in parallel.
  4. Make sauces/dressings while everything cools.
  5. Portion into containers once cool — label with day.
Goal: enough for 4–5 lunches + midweek dinners covered."""


# ── Job callbacks ─────────────────────────────────────────────────────────────


async def _morning_briefing(context) -> None:
    """7:30 AM daily: breakfast + next gym session + calorie note."""
    today = datetime.date.today()
    weekday = today.weekday()

    breakfast = meal_agent.get_breakfast(weekday)

    conn = get_connection()
    try:
        next_session = gym_agent.get_next_session_type(conn)
    finally:
        conn.close()

    day_name = today.strftime("%A")
    is_weekend = weekday >= 5
    cal_note = (
        "Rest day target: 2,950 kcal"
        if is_weekend
        else "Training target: 3,300 kcal (if lifting today) or 2,950 (rest)"
    )

    lines = [
        f"Morning, Ollie. {day_name}.",
        "",
        f"Breakfast: {breakfast}",
        "",
        f"Next gym session: {next_session.title()} day.",
        cal_note,
        "",
        "Protein target: 230g. Start strong.",
    ]
    await context.bot.send_message(chat_id=_UID, text="\n".join(lines))


async def _midmorning_checkin(context) -> None:
    """10:30 AM weekdays: nudge if fewer than 60g protein logged."""
    today = datetime.date.today().isoformat()
    conn = get_connection()
    try:
        totals = get_daily_totals(conn, today)
    finally:
        conn.close()

    if totals["protein_g"] >= 60:
        return

    logged = totals["protein_g"]
    await context.bot.send_message(
        chat_id=_UID,
        text=(
            f"Mid-morning check: {logged:.0f}g protein logged so far.\n"
            "Breakfast done? If not, get it in — 60g by 11 AM keeps the day on track."
        ),
    )


async def _evening_checkin(context) -> None:
    """7:00 PM daily: prompt to log dinner."""
    await context.bot.send_message(
        chat_id=_UID,
        text="Evening — what did you have for dinner? Send me the details and I'll log it.",
    )


async def _end_of_day_summary(context) -> None:
    """9:30 PM daily: full macro summary."""
    conn = get_connection()
    try:
        summary = meal_agent.daily_summary(conn)
    finally:
        conn.close()

    await context.bot.send_message(
        chat_id=_UID,
        text=f"End of day:\n\n{summary}",
    )


async def _friday_shopping_list(context) -> None:
    """4:00 PM Friday: week summary + shopping list prompt."""
    conn = get_connection()
    try:
        summary = meal_agent.build_friday_summary(conn)
    finally:
        conn.close()

    await context.bot.send_message(chat_id=_UID, text=summary)


async def _sunday_batch_cook(context) -> None:
    """10:00 AM Sunday: this week's lunch rotation + batch cook order of ops."""
    rotation = meal_agent.get_lunch_rotation()

    lines = [
        "Batch cook Sunday. This week's lunch rotation:",
        "",
        rotation,
        "",
        _BATCH_COOK_TIPS,
    ]
    await context.bot.send_message(chat_id=_UID, text="\n".join(lines))


# ── Registration ──────────────────────────────────────────────────────────────


def register_jobs(app: Application) -> None:
    """Register all scheduled jobs with the application's job queue."""
    jq = app.job_queue

    jq.run_daily(
        _morning_briefing,
        time=datetime.time(7, 30, tzinfo=_TZ),
    )
    jq.run_daily(
        _midmorning_checkin,
        time=datetime.time(10, 30, tzinfo=_TZ),
        days=(0, 1, 2, 3, 4),  # weekdays only
    )
    jq.run_daily(
        _evening_checkin,
        time=datetime.time(19, 0, tzinfo=_TZ),
    )
    jq.run_daily(
        _end_of_day_summary,
        time=datetime.time(21, 30, tzinfo=_TZ),
    )
    jq.run_daily(
        _friday_shopping_list,
        time=datetime.time(16, 0, tzinfo=_TZ),
        days=(4,),  # Friday
    )
    jq.run_daily(
        _sunday_batch_cook,
        time=datetime.time(10, 0, tzinfo=_TZ),
        days=(6,),  # Sunday
    )

    logger.info("Scheduled jobs registered: morning, mid-morning, evening, EOD, Friday, Sunday")
