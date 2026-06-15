"""Proactive scheduled jobs sent to Ollie via Telegram."""

from __future__ import annotations

import datetime
import logging
from zoneinfo import ZoneInfo

from telegram.ext import Application

import config
from agents import gym as gym_agent
from agents import meal as meal_agent
from agents.meal import _format_yesterday_slot_for_prompt
from data.recipes import RECIPES
from services import news as news_svc
from services.google_calendar import get_service, list_events
from storage.db import get_connection
from storage.models import get_daily_totals

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


# ── Helpers ───────────────────────────────────────────────────────────────────


def _fmt_event_time(dt_str: str) -> str:
    """Format an ISO datetime string as 'HH:MM' in London time."""
    if not dt_str:
        return ""
    try:
        dt = datetime.datetime.fromisoformat(dt_str)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=_TZ)
        else:
            dt = dt.astimezone(_TZ)
        return dt.strftime("%H:%M")
    except ValueError:
        return dt_str[:10]  # just the date portion if parsing fails


def _get_today_calendar_events() -> list[str]:
    """Return formatted strings for today's calendar events. Returns [] on any failure."""
    try:
        service = get_service()
        now = datetime.datetime.now(tz=_TZ)
        today_start = now.replace(hour=0, minute=0, second=0, microsecond=0).isoformat()
        today_end = now.replace(hour=23, minute=59, second=59, microsecond=0).isoformat()
        events = list_events(service, today_start, today_end)
        result = []
        for ev in events:
            time_str = _fmt_event_time(ev["start"])
            loc = f" @ {ev['location']}" if ev.get("location") else ""
            result.append(f"• {time_str} — {ev['summary']}{loc}" if time_str else f"• {ev['summary']}{loc}")
        return result
    except Exception as exc:
        logger.debug("Calendar fetch for morning briefing failed: %s", exc)
        return []


def _get_horses_today() -> list[str]:
    """Return bullet strings for any horses running today. Returns [] if none or API unavailable."""
    try:
        # Use cached result — don't make a fresh API call at 7:45am
        from services.news import _get_cache
        cached = _get_cache("horse_entries")
        if not cached or cached.get("_rate_limited"):
            return []
        result = []
        for horse_key, entries in cached.items():
            if horse_key.startswith("_"):
                continue
            for entry in entries:
                if entry.get("day_label") == "today":
                    from services.news import _fmt_dist
                    dist = _fmt_dist(entry.get("distance_f", ""))
                    result.append(
                        f"• {horse_key.title()} — {entry['course']}, "
                        f"off {entry['off_time']}, {dist}, {entry['going']}"
                    )
        return result
    except Exception as exc:
        logger.debug("Horses today fetch failed: %s", exc)
        return []


def _get_gym_targets(conn) -> list[str]:
    """Return progression target lines for the next gym session."""
    try:
        next_type = gym_agent.get_next_session_type(conn)
        last = gym_agent._get_last_session_of_type(conn, next_type)
        if not last:
            return [f"Next: {next_type.title()} day — no previous session logged."]

        days_ago = (datetime.date.today() - datetime.date.fromisoformat(last["date"])).days
        age = f"{days_ago}d ago" if days_ago > 0 else "today"
        lines = [f"Next: {next_type.title()} day (last was {age})"]

        # Progression for every exercise in the last session
        for ex in last.get("sets", []):
            weight = ex.get("weight_kg")
            notes = (ex.get("notes") or "").lower()
            failed = any(w in notes for w in ("fail", "missed", "short", "couldn't"))
            if weight is None:
                next_reps = ex["reps"] if failed else ex["reps"] + 1
                lines.append(f"• {ex['exercise']} BW {ex['sets']}×{ex['reps']} → aim {ex['sets']}×{next_reps}")
            else:
                next_w = weight if failed else round((weight + 2.5) * 2) / 2
                lines.append(f"• {ex['exercise']} {weight}kg → try {next_w}kg")
        return lines
    except Exception as exc:
        logger.debug("Gym targets failed: %s", exc)
        return []


def _get_chelsea_headline() -> str | None:
    """Return the most recent Chelsea news headline if it's less than 12h old."""
    try:
        from services.news import _get_cache
        cached = _get_cache("chelsea")
        if not cached:
            return None
        import time
        now = time.time()
        for item in cached:
            if now - item.get("published", 0) < 43200:  # 12 hours
                return item.get("title", "")
        return None
    except Exception:
        return None


async def _get_world_headlines(limit: int = 3) -> list[str]:
    """Return up to `limit` top BBC World headlines. Returns [] on any failure."""
    try:
        items = await news_svc.fetch_world_news_items()
        return [f"• {item['title']}" for item in items[:limit]]
    except Exception as exc:
        logger.debug("World news fetch for morning briefing failed: %s", exc)
        return []


# ── Job callbacks ─────────────────────────────────────────────────────────────


async def _morning_briefing(context) -> None:
    """7:45 AM daily: smart morning brief — calendar, training targets, horses, news, breakfast."""
    today = datetime.date.today()
    weekday = today.weekday()
    day_name = today.strftime("%A")

    conn = get_connection()
    try:
        sections: list[str] = [day_name + "."]

        # Calendar
        events = _get_today_calendar_events()
        if events:
            sections.append("\nTODAY")
            sections.extend(events)

        # Training
        gym_lines = _get_gym_targets(conn)
        if gym_lines:
            sections.append("\nTRAINING")
            sections.extend(gym_lines)

        # Horses
        horses = _get_horses_today()
        if horses:
            sections.append("\nHORSES")
            sections.extend(horses)

        # Chelsea headline (only if fresh)
        chelsea = _get_chelsea_headline()
        if chelsea:
            sections.append(f"\nCHELSEA\n• {chelsea}")

        # World news headlines
        world_headlines = await _get_world_headlines()
        if world_headlines:
            sections.append("\nWORLD NEWS")
            sections.extend(world_headlines)

        # Breakfast — on Tue/Wed/Thu offer to repeat yesterday's if logged
        if weekday in (1, 2, 3):  # Tue, Wed, Thu
            yesterday_breakfast = _format_yesterday_slot_for_prompt(conn, "breakfast")
            if yesterday_breakfast:
                sections.append(f"\nBREAKFAST\nSame as yesterday? {yesterday_breakfast}\nSay 'same breakfast' to log it.")
            else:
                breakfast = meal_agent.get_breakfast(weekday)
                sections.append(f"\nBREAKFAST\n{breakfast}")
        else:
            breakfast = meal_agent.get_breakfast(weekday)
            sections.append(f"\nBREAKFAST\n{breakfast}")

        # Calorie note
        is_weekend = weekday >= 5
        sections.append(
            "\n2,950 kcal target (rest day)."
            if is_weekend
            else "\n3,300 kcal if lifting today. Protein target: 230g."
        )

        await context.bot.send_message(chat_id=_UID, text="\n".join(sections))
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
