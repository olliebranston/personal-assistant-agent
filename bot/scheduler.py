"""Proactive scheduled jobs sent to Ollie via Telegram."""

from __future__ import annotations

import datetime
import json
import logging
from zoneinfo import ZoneInfo

from telegram.ext import Application

import config
from agents import meal as meal_agent
from agents.meal import _format_yesterday_slot_for_prompt
from data.recipes import RECIPES
from services.openrouter import complete
from storage.db import get_connection
from storage.models import get_daily_totals
from tools.briefing import get_morning_briefing_data

logger = logging.getLogger(__name__)

_TZ = ZoneInfo("Europe/London")
_UID = config.TELEGRAM_ALLOWED_USER_ID


# ── Batch cook recipe cards (Sunday job) ─────────────────────────────────────

_BATCH_SLUGS = [
    "red_lentil_dal",
    "lentil_tofu_salad",
    "tofu_egg_fried_rice",
    "black_bean_sweet_potato_stew",
    "quinoa_power_bowl",
]


def _get_batch_cook_message() -> str:
    """Return this week's batch cook recipe card from the recipes database."""
    idx = datetime.date.today().isocalendar()[1] % len(_BATCH_SLUGS)
    slug = _BATCH_SLUGS[idx]
    recipe = RECIPES.get(slug, {})

    name = recipe.get("name", slug)
    time_mins = recipe.get("time_mins", "?")
    protein = recipe.get("protein_g", "?")

    lines = [f"*Batch cook Sunday — {name}*",
             f"_{time_mins} mins · {protein}g protein per portion_", ""]

    lines.append("*INGREDIENTS (4 portions)*")
    for ing in recipe.get("ingredients", []):
        qty = f"{ing['qty']:g}" if isinstance(ing["qty"], (int, float)) else str(ing["qty"])
        unit = f"{ing['unit']} " if ing.get("unit") else ""
        lines.append(f"• {qty} {unit}{ing['item']}")

    lines.append("")
    lines.append("*METHOD*")
    for i, step in enumerate(recipe.get("method", []), 1):
        lines.append(f"{i}. {step}")

    lines += ["", "*ORDER OF OPS*",
              "1. Start grains/lentils first — hands off while you prep veg.",
              "2. Roast tofu/veg simultaneously.",
              "3. Make sauces/dressings while things cool.",
              "4. Portion into containers, label with day."]
    return "\n".join(lines)


# ── Briefing composition ──────────────────────────────────────────────────────

_BRIEFING_SYSTEM = """\
You are Robin, Ollie's personal assistant. Write his morning briefing \
as a tight bulletin — under 60 seconds to read. Use this exact section \
order and these exact headers:

📅 [Day] [Date e.g. Tuesday 17 Jun]

CALENDAR
[One line per event: HH:MM — Event, Location. If no events: 'Nothing \
in the calendar.']

WORLD
[3-4 bullet points, most significant stories, one line each. Direct, \
no fluff.]

TRAINING
[One line only: '{Session type} day — last trained {session type} \
{N} days ago.' If no previous session: '{Session type} day — first \
session of this type.']

NUTRITION
[One line: 'Yesterday: {protein}g protein / {kcal} kcal'. If protein \
was below target (230g): add ' — short on protein, [one short \
actionable suggestion].' If on track: just the numbers.]
[Second line: 'Target today: {kcal} kcal ({training day/rest day}.)']

CHELSEA
[3-5 bullet points, most recent first. If no news: 'Nothing fresh.']

RACING
[One line per horse with entries: '{Horse} — {Course}, {off_time}, \
{distance}, going: {going}'. If no horses running: 'No entries today.']

Tone: direct, informal, like a sharp friend who knows his routine. \
No filler, no greetings, no sign-off. Dry humour only if it fits \
naturally — never forced.\
"""


def _deterministic_briefing(data: dict) -> str:
    """Simple fallback format when LLM composition fails."""
    now = datetime.datetime.now(tz=_TZ)
    date_str = now.strftime("%A %-d %b")
    lines = [f"📅 {date_str}", ""]

    lines.append("CALENDAR")
    if data.get("calendar_today"):
        for ev in data["calendar_today"]:
            t = ev.get("start_time", "")[:5] or "all day"
            loc = f", {ev['location']}" if ev.get("location") else ""
            lines.append(f"{t} — {ev['summary']}{loc}")
    else:
        lines.append("Nothing in the calendar.")

    lines += ["", "WORLD"]
    for h in data.get("world_headlines", [])[:4]:
        lines.append(f"• {h['title']}")

    gym = data.get("gym", {})
    lines += ["", "TRAINING"]
    nxt = (gym.get("next_session_type") or "push").title()
    days = gym.get("days_since_last_session")
    if days is None:
        lines.append(f"{nxt} day — first session of this type.")
    else:
        lines.append(f"{nxt} day — last trained {nxt.lower()} {days} days ago.")

    nut = data.get("nutrition", {})
    lines += ["", "NUTRITION"]
    p = nut.get("yesterday_protein_g", 0)
    k = nut.get("yesterday_kcal", 0)
    lines.append(f"Yesterday: {p:.0f}g protein / {k:.0f} kcal")
    today_target = nut.get("today_kcal_target", 2950)
    day_type = "training day" if nut.get("is_training_day") else "rest day"
    lines.append(f"Target today: {today_target} kcal ({day_type}.)")

    lines += ["", "CHELSEA"]
    chelsea = data.get("chelsea", [])
    if chelsea:
        for c in chelsea[:5]:
            lines.append(f"• {c['title']}")
    else:
        lines.append("Nothing fresh.")

    lines += ["", "RACING"]
    horses = data.get("horses", {})
    entries = horses.get("entries", {})
    if horses.get("rate_limited"):
        lines.append("API quota reached — data unavailable.")
    elif entries:
        for horse, races in entries.items():
            for r in races:
                lines.append(f"{horse} — {r['course']}, {r['off_time']}, {r['distance']}, going: {r['going']}")
    else:
        lines.append("No entries today.")

    return "\n".join(lines)


# ── Job callbacks ─────────────────────────────────────────────────────────────


async def _morning_briefing(context) -> None:
    """7:45 AM daily: smart morning brief via LLM composition."""
    weekday = datetime.date.today().weekday()

    conn = get_connection()
    try:
        data = await get_morning_briefing_data(conn)

        try:
            briefing = await complete(
                messages=[
                    {
                        "role": "user",
                        "content": f"Write today's briefing from this data:\n{json.dumps(data, indent=2)}",
                    }
                ],
                system=_BRIEFING_SYSTEM,
            )
        except Exception as exc:
            logger.warning("Morning briefing LLM call failed (%s) — using fallback", exc)
            briefing = _deterministic_briefing(data)

        await context.bot.send_message(chat_id=_UID, text=briefing)

        # Breakfast prompt (unchanged — Tue/Wed/Thu repeat-meal shortcut)
        if weekday in (1, 2, 3):
            yesterday_breakfast = _format_yesterday_slot_for_prompt(conn, "breakfast")
            if yesterday_breakfast:
                await context.bot.send_message(
                    chat_id=_UID,
                    text=f"Same breakfast as yesterday?\n{yesterday_breakfast}\nSay 'same breakfast' to log it.",
                )
    finally:
        conn.close()


async def _lunch_prompt(context) -> None:
    """12:30 PM Tue/Wed/Thu: suggest logging same lunch as yesterday if it was logged."""
    conn = get_connection()
    try:
        yesterday_lunch = _format_yesterday_slot_for_prompt(conn, "lunch")
    finally:
        conn.close()

    if yesterday_lunch:
        await context.bot.send_message(
            chat_id=_UID,
            text=f"Lunch time. Same as yesterday?\n{yesterday_lunch}\nSay 'same lunch' to log it.",
        )
    else:
        # Nothing logged yesterday — send generic batch cook reminder
        rotation = meal_agent.get_lunch_rotation()
        await context.bot.send_message(
            chat_id=_UID,
            text=f"Lunch time. Batch cook:\n{rotation.split('—')[0].strip()} — log it when you've had it.",
        )


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

    await context.bot.send_message(
        chat_id=_UID,
        text=(
            f"Mid-morning: {totals['protein_g']:.0f}g protein logged.\n"
            "Breakfast done? 60g by 11am keeps the day on track."
        ),
    )


async def _evening_dinner_prompt(context) -> None:
    """9:00 PM daily: prompt to log dinner."""
    await context.bot.send_message(
        chat_id=_UID,
        text="Evening — what did you have for dinner? Send me the details and I'll log it.",
    )


async def _end_of_day_summary(context) -> None:
    """11:00 PM daily: full macro summary."""
    conn = get_connection()
    try:
        summary = meal_agent.daily_summary(conn)
    finally:
        conn.close()

    await context.bot.send_message(
        chat_id=_UID,
        text=f"End of day:\n\n{summary}",
    )


async def _friday_meal_plan(context) -> None:
    """5:00 PM Friday: week summary + next week's meal plan + derived shopping list."""
    conn = get_connection()
    try:
        summary = meal_agent.build_friday_summary(conn)
    finally:
        conn.close()

    # Telegram has a 4096-char limit — split if needed
    if len(summary) <= 4096:
        await context.bot.send_message(chat_id=_UID, text=summary, parse_mode="Markdown")
    else:
        # Split at the shopping list section
        split_marker = "*SHOPPING LIST*"
        idx = summary.find(split_marker)
        if idx > 0:
            part1, part2 = summary[:idx].strip(), summary[idx:].strip()
        else:
            part1, part2 = summary[:4090], summary[4090:]
        await context.bot.send_message(chat_id=_UID, text=part1, parse_mode="Markdown")
        await context.bot.send_message(chat_id=_UID, text=part2, parse_mode="Markdown")


async def _sunday_batch_cook(context) -> None:
    """10:00 AM Sunday: batch cook recipe card for this week's rotation."""
    recipe_msg = _get_batch_cook_message()
    await context.bot.send_message(chat_id=_UID, text=recipe_msg, parse_mode="Markdown")


# ── Registration ──────────────────────────────────────────────────────────────


def register_jobs(app: Application) -> None:
    """Register all scheduled jobs with the application's job queue."""
    jq = app.job_queue

    jq.run_daily(
        _morning_briefing,
        time=datetime.time(7, 45, tzinfo=_TZ),
    )
    jq.run_daily(
        _midmorning_checkin,
        time=datetime.time(10, 30, tzinfo=_TZ),
        days=(1, 2, 3, 4, 5),  # weekdays only (PTB: 0=Sun..6=Sat)
    )
    jq.run_daily(
        _lunch_prompt,
        time=datetime.time(12, 30, tzinfo=_TZ),
        days=(2, 3, 4),  # Tue, Wed, Thu only (PTB: 0=Sun..6=Sat)
    )
    jq.run_daily(
        _evening_dinner_prompt,
        time=datetime.time(21, 0, tzinfo=_TZ),
    )
    jq.run_daily(
        _end_of_day_summary,
        time=datetime.time(23, 0, tzinfo=_TZ),
    )
    jq.run_daily(
        _friday_meal_plan,
        time=datetime.time(17, 0, tzinfo=_TZ),
        days=(5,),  # Friday (PTB: 0=Sun..6=Sat)
    )
    jq.run_daily(
        _sunday_batch_cook,
        time=datetime.time(10, 0, tzinfo=_TZ),
        days=(0,),  # Sunday (PTB: 0=Sun..6=Sat)
    )

    logger.info(
        "Jobs registered: morning 07:45, mid-morning 10:30 (Mon-Fri), "
        "lunch prompt 12:30 (Tue-Thu), evening 21:00, EOD 23:00, Friday 17:00, Sunday 10:00"
    )
