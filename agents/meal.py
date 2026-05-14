"""Meal planning agent: food logging, macro tracking, and meal suggestions."""

from __future__ import annotations

import json
import re
import sqlite3
from datetime import date, timedelta

import services.state as state_svc
from services.nutrition import lookup_macros
from services.openrouter import complete
from storage.models import (
    FoodLog,
    get_daily_totals,
    get_food_logs_for_date,
    get_recent_sessions,
    get_week_logs,
    insert_food_log,
)

# ── Targets ───────────────────────────────────────────────────────────────────

PROTEIN_TARGET_G = 230
CALORIE_TARGETS = {
    "weights":  3300,
    "rest":     2950,
    "default":  3150,
}

# ── Meal rotations (from Mealplan-CONTEXT.md) ─────────────────────────────────

_BREAKFAST_ROTATION = {
    0: "Protein smoothie — 2 scoops whey + frozen berries + 2 tbsp peanut butter + oat milk (~50g protein). 3 mins.",
    1: "4-egg omelette — mushrooms, peppers, spinach, chilli flakes (~28g protein). Add 150g Greek yoghurt on the side for +15g.",
    2: "Protein smoothie — same as Monday (~50g protein). Pre-portion bags help.",
    3: "Protein overnight oats — 80g oats + 1 scoop protein + 150g Greek yoghurt + frozen berries. Prep tonight. (~42g protein)",
    4: "4 scrambled eggs on wholegrain sourdough + hot sauce (~30g protein). Greek yoghurt side if needed.",
    5: "Weekend — rotate: eggs Benedict with smoked salmon (~35–40g) / protein pancakes + Greek yoghurt (~35g) / full scramble + avocado (~30g)",
    6: "Weekend — rotate: shakshuka 4 eggs + feta + tofu (~30–38g) / eggs Benedict (~35–40g) / full scramble (~30g)",
}

_LUNCH_ROTATIONS = [
    "Rotation A — Red Lentil Dal: red lentils, tinned tomatoes, coconut milk, spinach. ~20–25g protein. Boost with Greek yoghurt or baked tofu to hit 35–47g. 30 mins batch.",
    "Rotation B — Lentil & Baked Tofu Salad: puy lentils, roasted peppers, cucumber, cherry tomatoes, baked tofu, tahini-lemon dressing. ~30–35g protein. 40 mins batch.",
    "Rotation C — Tofu Egg Fried Rice: brown rice, 2 eggs/portion, firm tofu, edamame, soy-ginger sauce. ~28–33g protein. 25 mins batch.",
    "Rotation D — Black Bean & Sweet Potato Stew: black beans, kidney beans, sweet potato, tinned tomatoes, chipotle. ~22–26g protein. Boost with tempeh or Greek yoghurt to 32–46g. 35 mins batch.",
    "Rotation E — Quinoa Power Bowl: quinoa, roasted veg, chickpeas/lentils, wilted spinach, tahini-miso dressing. ~25–32g protein. Add tofu or tempeh to push 40g+. 35 mins batch.",
]

_WEEKDAY_DINNERS = [
    "Tofu stir fry (soy-ginger-sesame) + rice/noodles — 35–45g protein. Full 400g block firm tofu + edamame.",
    "Tofu pad thai — tamarind + lime + peanuts — 35–45g protein.",
    "Miso-glazed tofu + roasted sweet potato — 30–35g protein.",
    "Sweet potato & black bean chilli — 30–35g protein. Serve with Greek yoghurt + hot sauce.",
    "Tofu ramen (miso-mushroom broth) — 35–40g protein. Dried shiitake + soft-boiled egg.",
    "Chickpea & spinach curry — 25–30g protein. Add tempeh to push higher.",
    "Korean tofu (sundubu-style) — gochujang + silken tofu + egg — 30–38g protein.",
]

_WEEKEND_DINNERS = [
    "Wild salmon or trout + roasted veg — 40–50g protein. Miso glaze or lemon-herb. ~200g fillet.",
    "Tofu katsu curry — panko-crusted tofu, Japanese curry sauce, rice — 35–45g protein.",
    "Dal makhani — slow-cooked black lentils + kidney beans. Worth the overnight soak — 28–35g protein.",
    "Tempeh rendang — coconut + lemongrass + galangal + chilli — 38–45g protein.",
    "Shakshuka (dinner) — 4 eggs + feta + sourdough — 30–38g protein.",
]

# ── System prompts ────────────────────────────────────────────────────────────

_ROUTER_SYSTEM = """\
Classify the user's meal/nutrition message. Reply ONLY with valid JSON — no prose.

{"action": "log"}                           — logging food eaten
{"action": "summary"}                       — wants today's macro totals
{"action": "remaining"}                     — wants to know protein/kcal left today
{"action": "suggest", "meal": "breakfast|lunch|dinner|snack"}  — wants a meal suggestion
{"action": "clarify", "question": "<one short question>"}      — intent unclear
"""

_FOOD_PARSER_SYSTEM = """\
Extract every food item from the user's message. Reply ONLY with valid JSON — no prose.

{
  "meal_slot": "breakfast|snack|lunch|shake|dinner|alcohol|other",
  "items": [
    {
      "name": "<food name, suitable for USDA search>",
      "quantity_g": <estimated grams as a number>,
      "notes": "<any useful context, or empty string>"
    }
  ]
}

Rules:
- Default to LARGE portions. This is a 105kg active male. A chicken breast = 200g. A bowl of rice = 220g cooked. A pint of beer = 568ml. When in doubt, go larger.
- Convert non-gram units to grams: 1 pint = 568ml (treat ml ≈ g for liquids), 1 tbsp ≈ 15g, 1 scoop protein powder ≈ 33g.
- For mixed dishes (e.g. "stir fry"), break into main components.
- For protein shakes: name = "whey protein powder", quantity_g = 66 (2 scoops).
- Infer meal_slot from context (e.g. "breakfast smoothie" → breakfast, "pint at the pub" → alcohol).
- For alcohol, use the drink name directly (e.g. "pint of lager", "glass of red wine").
"""

_AFFIRMATIVES = frozenset({
    "yes", "yeah", "yep", "yup", "sure", "ok", "okay", "correct", "right",
    "looks good", "go ahead", "log it", "perfect", "good", "fine", "that's right",
    "sounds right", "spot on", "exactly",
})

# ── Public entry point ────────────────────────────────────────────────────────


async def handle(conn: sqlite3.Connection, text: str, user_id: int = 0) -> str:
    """Classify the meal message and dispatch to the appropriate function.

    Checks for a pending food-log confirmation first — if one exists, the
    incoming message is treated as a response to it regardless of routing.
    """
    # Pending food-log confirmation takes priority over normal routing.
    pending = state_svc.get(user_id)
    if pending and pending.get("type") == "food_log":
        return await _handle_food_confirmation(conn, text, user_id, pending)

    raw = await complete([{"role": "user", "content": text}], system=_ROUTER_SYSTEM)

    try:
        intent = json.loads(_extract_json(raw))
    except (json.JSONDecodeError, ValueError):
        return "What do you need — logging food, today's numbers, or a meal idea?"

    action = intent.get("action")

    if action == "log":
        return await _log_food(conn, text, user_id)
    if action == "summary":
        return _daily_summary(conn)
    if action == "remaining":
        return _remaining_macros(conn)
    if action == "suggest":
        meal = intent.get("meal", "")
        return _suggest_meal(meal)
    if action == "clarify":
        return intent.get("question", "What do you need — logging food, today's numbers, or a meal idea?")
    return "What do you need — logging food, today's numbers, or a meal idea?"


# ── Private helpers ───────────────────────────────────────────────────────────


async def _log_food(conn: sqlite3.Connection, text: str, user_id: int) -> str:
    """Parse free-text food log, look up USDA macros, stage a confirmation request.

    Does NOT write to the DB yet. Stores the parsed items in pending state and
    returns a confirmation message showing quantities and macros. The actual
    write happens in _handle_food_confirmation() after Ollie confirms.
    """
    raw = await complete([{"role": "user", "content": text}], system=_FOOD_PARSER_SYSTEM)

    try:
        parsed = json.loads(_extract_json(raw))
    except (json.JSONDecodeError, ValueError):
        return "Couldn't parse that. Try: 'had 200g tofu stir fry and a bowl of rice'"

    items = parsed.get("items", [])
    if not items:
        return "No food items found. Try: 'had 200g tofu stir fry and a bowl of rice'"

    meal_slot = parsed.get("meal_slot", "other")

    # Fetch USDA macros for each item before asking for confirmation.
    enriched = []
    for item in items:
        macros = await lookup_macros(item["name"], item["quantity_g"])
        enriched.append({
            "name": item["name"],
            "quantity_g": item["quantity_g"],
            "protein_g": macros["protein_g"],
            "kcal": macros["kcal"],
            "source": macros["source"],
        })

    state_svc.set_state(user_id, {
        "type": "food_log",
        "meal_slot": meal_slot,
        "items": enriched,
    })

    lines = ["I'll log:"]
    for item in enriched:
        flag = " (estimated — no USDA match)" if item["source"] == "estimated" else ""
        lines.append(
            f"  {item['quantity_g']}g {item['name']} — "
            f"{item['protein_g']}g protein, {item['kcal']:.0f} kcal{flag}"
        )
    lines.append("Look right? (yes to confirm, or tell me what to adjust)")
    return "\n".join(lines)


async def _handle_food_confirmation(
    conn: sqlite3.Connection,
    text: str,
    user_id: int,
    pending: dict,
) -> str:
    """Handle Ollie's response to a staged food-log confirmation.

    Affirmative → write everything to DB, return running total.
    Cancellation → clear state, acknowledge.
    Anything else → treat as an adjustment and re-parse as a new food log.
    """
    text_lower = text.lower().strip()

    if any(w in text_lower for w in ("cancel", "forget", "never mind", "nevermind", "don't log", "dont log", "no thanks")):
        state_svc.clear(user_id)
        return "No problem, not logged."

    words = set(text_lower.split())
    is_yes = bool(words & _AFFIRMATIVES) and len(text.split()) <= 6

    if is_yes:
        today = date.today().isoformat()
        meal_slot = pending["meal_slot"]
        total_protein = 0.0
        total_kcal = 0.0

        for item in pending["items"]:
            insert_food_log(conn, FoodLog(
                date=today,
                meal_slot=meal_slot,
                description=f"{item['quantity_g']}g {item['name']}",
                protein_g=item["protein_g"],
                kcal=item["kcal"],
                source=item["source"],
            ))
            total_protein += item["protein_g"]
            total_kcal += item["kcal"]

        state_svc.clear(user_id)

        totals = get_daily_totals(conn, today)
        cal_target = _get_calorie_target(conn)
        protein_remaining = max(PROTEIN_TARGET_G - totals["protein_g"], 0)

        lines = [f"Logged. {total_protein:.0f}g protein, {total_kcal:.0f} kcal."]
        lines.append(f"Running today: {totals['protein_g']:.0f}g protein / {totals['kcal']:.0f} kcal (target: {cal_target})")
        if protein_remaining > 40:
            lines.append(f"Still {protein_remaining:.0f}g protein short — pre-bed shake will close most of it.")
        elif protein_remaining > 0:
            lines.append(f"Just {protein_remaining:.0f}g protein left for the day.")
        return "\n".join(lines)

    # Not clearly yes — treat as an adjustment and re-parse.
    state_svc.clear(user_id)
    return await _log_food(conn, text, user_id)


def _daily_summary(conn: sqlite3.Connection) -> str:
    """Return today's macro totals vs target."""
    today = date.today().isoformat()
    totals = get_daily_totals(conn, today)
    cal_target = _get_calorie_target(conn)
    logs = get_food_logs_for_date(conn, today)

    protein_gap = PROTEIN_TARGET_G - totals["protein_g"]
    kcal_gap = cal_target - totals["kcal"]
    entries = len(logs)

    protein_status = f"{totals['protein_g']:.0f}g / {PROTEIN_TARGET_G}g" + (f" ({protein_gap:.0f}g short)" if protein_gap > 0 else " ✓")
    kcal_status = f"{totals['kcal']:.0f} / {cal_target} kcal" + (f" ({kcal_gap:.0f} to go)" if kcal_gap > 0 else " ✓")

    lines = [
        f"Today ({today}), {entries} item{'s' if entries != 1 else ''} logged:",
        f"  Protein:  {protein_status}",
        f"  Calories: {kcal_status}",
    ]

    if not logs:
        lines.append("Nothing logged yet.")
    if protein_gap > 40:
        lines.append("Pre-bed shake (~48g) will close most of that protein gap.")

    return "\n".join(lines)


def _remaining_macros(conn: sqlite3.Connection) -> str:
    """Return how much protein and kcal are left to hit today's targets."""
    today = date.today().isoformat()
    totals = get_daily_totals(conn, today)
    cal_target = _get_calorie_target(conn)

    protein_left = max(PROTEIN_TARGET_G - totals["protein_g"], 0)
    kcal_left = max(cal_target - totals["kcal"], 0)

    if protein_left == 0 and kcal_left == 0:
        return "Ollie, you've hit both targets today. Job done."

    lines = [f"Still to hit today (target: {PROTEIN_TARGET_G}g protein / {cal_target} kcal):"]
    lines.append(f"  Protein:  {protein_left:.0f}g")
    lines.append(f"  Calories: {kcal_left:.0f} kcal")
    if protein_left > 0:
        gap_after_shake = max(protein_left - 48, 0)
        if gap_after_shake == 0:
            lines.append("Pre-bed shake (~48g) will get you there.")
        else:
            lines.append(f"Pre-bed shake covers ~48g — still {gap_after_shake:.0f}g short after that.")

    return "\n".join(lines)


def _suggest_meal(meal: str) -> str:
    """Return a meal suggestion from the appropriate rotation."""
    today = date.today()
    weekday = today.weekday()

    if "breakfast" in meal:
        return f"Breakfast today:\n{_BREAKFAST_ROTATION[weekday]}"

    if "lunch" in meal:
        idx = today.isocalendar()[1] % len(_LUNCH_ROTATIONS)
        return f"Lunch (batch cook):\n{_LUNCH_ROTATIONS[idx]}"

    if "dinner" in meal:
        if weekday >= 5:
            import random
            return f"Dinner (weekend):\n{random.choice(_WEEKEND_DINNERS)}"
        idx = weekday % len(_WEEKDAY_DINNERS)
        return f"Dinner:\n{_WEEKDAY_DINNERS[idx]}"

    if "snack" in meal:
        return (
            "Snack options (15–20g protein target): protein bar (protein > sugar on the label), "
            "150g Greek yoghurt + berries (~15g), 3 tbsp hemp seeds on anything (+10g, tasteless), "
            "handful edamame (~11g/100g)."
        )

    return f"Which meal — breakfast, lunch, dinner, or snack? (Today is {'a weekend' if weekday >= 5 else 'a weekday'}.)"


def _get_calorie_target(conn: sqlite3.Connection) -> int:
    """Return today's calorie target based on whether a gym session was logged."""
    today = date.today().isoformat()
    sessions = get_recent_sessions(conn, limit=5)
    for s in sessions:
        if s["date"] == today and s["session_type"] in ("push", "pull", "legs"):
            return CALORIE_TARGETS["weights"]
    return CALORIE_TARGETS["rest"]


def _extract_json(text: str) -> str:
    match = re.search(r"\{.*\}", text, re.DOTALL)
    return match.group() if match else text


# ── Scheduler-facing functions (called by bot/scheduler.py) ──────────────────


def build_friday_summary(conn: sqlite3.Connection) -> str:
    """Generate the Friday week summary. Called by the scheduler, not handle()."""
    today = date.today()
    week_start = (today - timedelta(days=today.weekday())).isoformat()
    week_end = today.isoformat()

    days = get_week_logs(conn, week_start, week_end)
    if not days:
        return "No food logged this week yet."

    avg_protein = sum(d["protein_g"] for d in days) / len(days)
    avg_kcal = sum(d["kcal"] for d in days) / len(days)

    lines = [
        f"Week so far ({week_start} → {week_end}), {len(days)} day(s) logged:",
        f"  Avg protein: {avg_protein:.0f}g / {PROTEIN_TARGET_G}g target",
        f"  Avg calories: {avg_kcal:.0f} kcal",
        "",
        "Reply with what's in the fridge and I'll build the weekend shopping list.",
    ]
    return "\n".join(lines)
