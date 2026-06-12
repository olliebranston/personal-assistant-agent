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

# ── Batch cook recipe cards (Sunday job) ─────────────────────────────────────

_RECIPES = {
    "A": {
        "name": "Red Lentil Dal",
        "protein": "~35–47g (with tofu or yoghurt boost)",
        "time": "30 mins",
        "ingredients": (
            "250g red lentils, 1 tin tomatoes, 1 tin coconut milk, "
            "150g spinach, 1 onion, 4 garlic cloves, 2cm ginger, "
            "1 tsp cumin, 1 tsp turmeric, 1 tsp garam masala, chilli to taste"
        ),
        "method": (
            "1. Fry onion 5 mins, add garlic + ginger + spices, 2 mins.\n"
            "2. Add lentils, tomatoes, coconut milk + 300ml water. Simmer 20 mins.\n"
            "3. Stir in spinach until wilted. Season hard.\n"
            "4. Portion into 4 containers. Add 150g baked tofu or dollop Greek yoghurt per portion."
        ),
    },
    "B": {
        "name": "Lentil & Baked Tofu Salad",
        "protein": "~30–41g",
        "time": "40 mins",
        "ingredients": (
            "250g puy lentils, 400g firm tofu, roasted red peppers (jar fine), "
            "cucumber, cherry tomatoes, parsley, "
            "dressing: 3 tbsp tahini, 2 tbsp lemon juice, 1 tbsp soy, water to loosen"
        ),
        "method": (
            "1. Cook lentils 20 mins, drain, cool.\n"
            "2. Cube tofu, toss in soy + sesame oil, roast 200°C 20 mins (crispy edges).\n"
            "3. Chop veg, mix everything together.\n"
            "4. Dress to coat. Portion into 4 containers. Hemp seeds on top optional (+6g protein)."
        ),
    },
    "C": {
        "name": "Tofu Egg Fried Rice",
        "protein": "~28–33g",
        "time": "25 mins",
        "ingredients": (
            "300g brown rice (dry), 400g firm tofu, 8 eggs, 200g edamame (frozen), "
            "soy sauce, sesame oil, ginger, 4 spring onions, 2 garlic cloves, chilli"
        ),
        "method": (
            "1. Cook rice, spread on tray to cool (stops clumping).\n"
            "2. Crumble tofu into pan with oil, fry until golden. Set aside.\n"
            "3. Scramble eggs in same pan, add cold rice, fry until separated.\n"
            "4. Add edamame, tofu, soy + sesame oil, spring onions. Toss. Portion ×4."
        ),
    },
    "D": {
        "name": "Black Bean & Sweet Potato Stew",
        "protein": "~32–46g (with tempeh or yoghurt)",
        "time": "35 mins",
        "ingredients": (
            "2 tins black beans, 1 tin kidney beans, 2 sweet potatoes, "
            "1 tin tomatoes, 1 tsp smoked paprika, 1 tsp cumin, 1 chipotle (or 1 tsp paste), "
            "lime, coriander"
        ),
        "method": (
            "1. Dice sweet potato, roast 200°C 20 mins.\n"
            "2. Fry onion + spices 5 mins, add beans + tomatoes + 200ml water.\n"
            "3. Simmer 15 mins, add sweet potato + lime juice.\n"
            "4. Portion ×4. Serve with Greek yoghurt + hot sauce, or sliced tempeh on top."
        ),
    },
    "E": {
        "name": "Quinoa Power Bowl",
        "protein": "~25–40g (with tofu or tempeh)",
        "time": "35 mins",
        "ingredients": (
            "300g quinoa (dry), 1 tin chickpeas, mixed roast veg (whatever's in the fridge), "
            "200g spinach, dressing: miso paste, tahini, rice vinegar, sesame oil, chilli flakes"
        ),
        "method": (
            "1. Cook quinoa 15 mins, cool.\n"
            "2. Roast veg + chickpeas at 200°C 25 mins (season well).\n"
            "3. Wilt spinach in a pan with garlic.\n"
            "4. Whisk dressing, assemble bowls. Add baked tofu or tempeh to push protein to 40g+."
        ),
    },
}


def _get_batch_cook_suggestion() -> str:
    """Return this week's batch cook rotation with a full recipe card."""
    idx = datetime.date.today().isocalendar()[1] % len(_RECIPES)
    key = list(_RECIPES.keys())[idx]
    r = _RECIPES[key]

    lines = [
        f"*Batch cook Sunday — Rotation {key}: {r['name']}*",
        f"Protein: {r['protein']}  |  Time: {r['time']}",
        "",
        "*Ingredients (4 portions):*",
        r["ingredients"],
        "",
        "*Method:*",
        r["method"],
        "",
        "*Order of ops:*",
        "  1. Start grains/lentils first — hands off while you prep veg.",
        "  2. Roast tofu/veg simultaneously in the oven.",
        "  3. Make sauces/dressings while things cool.",
        "  4. Portion into containers, label with day.",
    ]
    return "\n".join(lines)


# ── Job callbacks ─────────────────────────────────────────────────────────────


async def _morning_briefing(context) -> None:
    """7:45 AM daily: breakfast suggestion + next gym session."""
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
        "Rest day — 2,950 kcal target."
        if is_weekend
        else f"Gym day target: 3,300 kcal (if lifting). 2,950 if rest."
    )

    lines = [
        f"{day_name}.",
        "",
        f"Breakfast: {breakfast}",
        "",
        f"Next session: {next_session.title()} day. {cal_note}",
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
            f"Mid-morning: {logged:.0f}g protein logged.\n"
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


async def _friday_shopping_list(context) -> None:
    """5:00 PM Friday: week summary + next week's shopping list."""
    conn = get_connection()
    try:
        summary = meal_agent.build_friday_summary(conn)
    finally:
        conn.close()

    await context.bot.send_message(chat_id=_UID, text=summary, parse_mode="Markdown")


async def _sunday_batch_cook(context) -> None:
    """10:00 AM Sunday: batch cook recipe card for this week's rotation."""
    recipe_msg = _get_batch_cook_suggestion()
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
        days=(0, 1, 2, 3, 4),  # weekdays only
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
        _friday_shopping_list,
        time=datetime.time(17, 0, tzinfo=_TZ),
        days=(4,),  # Friday
    )
    jq.run_daily(
        _sunday_batch_cook,
        time=datetime.time(10, 0, tzinfo=_TZ),
        days=(6,),  # Sunday
    )

    logger.info("Scheduled jobs registered: morning 7:45, mid-morning 10:30, evening 21:00, EOD 23:00, Friday 17:00, Sunday 10:00")
