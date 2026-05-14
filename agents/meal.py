"""Meal planning agent: food logging, macro tracking, and meal suggestions."""

from __future__ import annotations

import json
import re
import sqlite3
from datetime import date, timedelta

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
    "weights":  3300,   # gym session day — upper end of 3,200–3,400 range
    "rest":     2950,   # rest day — middle of 2,900–3,000 range
    "default":  3150,   # TDEE recomp target when day type unknown
}

# ── Meal rotations (from Mealplan-CONTEXT.md) ─────────────────────────────────

# Keyed by weekday number: Monday=0, Sunday=6
_BREAKFAST_ROTATION = {
    0: "Protein smoothie — 2 scoops whey + frozen berries + 2 tbsp peanut butter + oat milk (~50g protein). 3 mins.",
    1: "4-egg omelette — mushrooms, peppers, spinach, chilli flakes (~28g protein). Add 150g Greek yoghurt on the side for +15g.",
    2: "Protein smoothie — same as Monday. Pre-portion bags help (prep Sunday). (~50g protein)",
    3: "Protein overnight oats — 80g oats + 1 scoop protein + 150g Greek yoghurt + frozen berries. Prep tonight. (~42g protein)",
    4: "4 scrambled eggs on wholegrain sourdough + hot sauce (~30g protein). Add Greek yoghurt side if needed.",
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
    "Miso-glazed tofu + roasted sweet potato — 30–35g protein. Easy miso-mirin glaze.",
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

# ── Public entry point ────────────────────────────────────────────────────────


async def handle(conn: sqlite3.Connection, text: str, user_id: int = 0) -> str:
    """Classify the meal message and dispatch to the appropriate function."""
    raw = await complete([{"role": "user", "content": text}], system=_ROUTER_SYSTEM)

    try:
        intent = json.loads(_extract_json(raw))
    except (json.JSONDecodeError, ValueError):
        return "Log food, check today's totals, or ask for a meal suggestion?"

    action = intent.get("action")

    if action == "log":
        return await _log_food(conn, text)
    if action == "summary":
        return _daily_summary(conn)
    if action == "remaining":
        return _remaining_macros(conn)
    if action == "suggest":
        meal = intent.get("meal", "")
        return _suggest_meal(meal)
    if action == "clarify":
        return intent.get("question", "Log food, check today's totals, or ask for a meal suggestion?")
    return "Log food, check today's totals, or ask for a meal suggestion?"


# ── Private helpers ───────────────────────────────────────────────────────────


async def _log_food(conn: sqlite3.Connection, text: str) -> str:
    """Parse free-text food log, look up macros via USDA, insert rows, return summary.

    Each food item gets its own FoodLog row so per-item history is queryable.
    Macros are fetched from USDA (source='usda') or flagged as estimated if
    the search returns nothing.
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
    today = date.today().isoformat()

    log_lines = []
    for item in items:
        macros = await lookup_macros(item["name"], item["quantity_g"])
        insert_food_log(conn, FoodLog(
            date=today,
            meal_slot=meal_slot,
            description=f"{item['quantity_g']}g {item['name']}",
            protein_g=macros["protein_g"],
            kcal=macros["kcal"],
            source=macros["source"],
        ))
        flag = " [estimated — USDA no match]" if macros["source"] == "estimated" else ""
        log_lines.append(
            f"  {item['quantity_g']}g {item['name']} — "
            f"{macros['protein_g']}g protein, {macros['kcal']:.0f} kcal{flag}"
        )

    totals = get_daily_totals(conn, today)
    cal_target = _get_calorie_target(conn)
    protein_remaining = PROTEIN_TARGET_G - totals["protein_g"]
    kcal_remaining = cal_target - totals["kcal"]

    lines = [f"Logged ({meal_slot}):", *log_lines, ""]
    lines.append(
        f"Today so far: {totals['protein_g']:.0f}g protein / {totals['kcal']:.0f} kcal"
    )
    lines.append(
        f"Remaining:    {max(protein_remaining, 0):.0f}g protein / {max(kcal_remaining, 0):.0f} kcal"
    )
    if protein_remaining > 0:
        lines.append(f"Pre-bed shake ({protein_remaining:.0f}g to go — 2 scoops = ~48g).")

    return "\n".join(lines)


def _daily_summary(conn: sqlite3.Connection) -> str:
    """Return today's macro totals vs target, with a gap flag."""
    today = date.today().isoformat()
    totals = get_daily_totals(conn, today)
    cal_target = _get_calorie_target(conn)
    logs = get_food_logs_for_date(conn, today)

    protein_gap = PROTEIN_TARGET_G - totals["protein_g"]
    kcal_gap = cal_target - totals["kcal"]
    entries = len(logs)

    lines = [
        f"Today ({today}) — {entries} entr{'y' if entries == 1 else 'ies'} logged",
        f"  Protein: {totals['protein_g']:.0f}g / {PROTEIN_TARGET_G}g"
        + (f"  ({protein_gap:.0f}g remaining)" if protein_gap > 0 else "  ✓"),
        f"  Calories: {totals['kcal']:.0f} / {cal_target} kcal"
        + (f"  ({kcal_gap:.0f} remaining)" if kcal_gap > 0 else "  ✓"),
    ]

    if not logs:
        lines.append("Nothing logged yet.")
    if protein_gap > 40:
        lines.append(f"Protein short — pre-bed shake will cover ~48g.")

    return "\n".join(lines)


def _remaining_macros(conn: sqlite3.Connection) -> str:
    """Return how much protein and kcal are left to hit today's targets."""
    today = date.today().isoformat()
    totals = get_daily_totals(conn, today)
    cal_target = _get_calorie_target(conn)

    protein_left = max(PROTEIN_TARGET_G - totals["protein_g"], 0)
    kcal_left = max(cal_target - totals["kcal"], 0)

    if protein_left == 0 and kcal_left == 0:
        return "You've hit both targets for today."

    lines = [f"Remaining today (target: {cal_target} kcal / {PROTEIN_TARGET_G}g protein):"]
    lines.append(f"  Protein: {protein_left:.0f}g left")
    lines.append(f"  Calories: {kcal_left:.0f} kcal left")
    if protein_left > 0:
        lines.append(f"  Pre-bed shake covers ~48g — {max(protein_left - 48, 0):.0f}g gap after that.")

    return "\n".join(lines)


def _suggest_meal(meal: str) -> str:
    """Return a meal suggestion from the appropriate rotation."""
    today = date.today()
    weekday = today.weekday()  # 0=Monday, 6=Sunday

    if "breakfast" in meal:
        return f"Breakfast suggestion:\n{_BREAKFAST_ROTATION[weekday]}"

    if "lunch" in meal:
        # Pick a rotation based on ISO week number so it advances weekly
        idx = today.isocalendar()[1] % len(_LUNCH_ROTATIONS)
        return f"Lunch suggestion (batch cook):\n{_LUNCH_ROTATIONS[idx]}"

    if "dinner" in meal:
        if weekday >= 5:  # Sat or Sun
            import random
            return f"Dinner suggestion (weekend):\n{random.choice(_WEEKEND_DINNERS)}"
        idx = weekday % len(_WEEKDAY_DINNERS)
        return f"Dinner suggestion:\n{_WEEKDAY_DINNERS[idx]}"

    if "snack" in meal:
        return (
            "Snack options (target 15–20g protein):\n"
            "  • Protein bar (check label: protein > sugar)\n"
            "  • 150g Greek yoghurt + berries (~15g protein)\n"
            "  • 3 tbsp hemp seeds on anything (+10g, tasteless)\n"
            "  • Handful edamame (~11g/100g)"
        )

    return (
        "Which meal? breakfast / lunch / dinner / snack\n"
        f"(Today is {'a weekend' if weekday >= 5 else 'a weekday'}.)"
    )


def _get_calorie_target(conn: sqlite3.Connection) -> int:
    """Return today's calorie target based on whether a gym session was logged.

    Weights day → 3,300 kcal. No session logged → 2,950 kcal (rest day default).
    Cycling commute and sport days aren't tracked yet — that's a later refinement.
    """
    today = date.today().isoformat()
    sessions = get_recent_sessions(conn, limit=5)
    for s in sessions:
        if s["date"] == today and s["session_type"] in ("push", "pull", "legs"):
            return CALORIE_TARGETS["weights"]
    return CALORIE_TARGETS["rest"]


def _extract_json(text: str) -> str:
    """Extract the first {...} block from an LLM response."""
    match = re.search(r"\{.*\}", text, re.DOTALL)
    return match.group() if match else text


# ── Scheduler-facing functions (called by bot/scheduler.py) ──────────────────


def build_friday_summary(conn: sqlite3.Connection) -> str:
    """Generate the Friday week summary + shopping list prompt.

    Called by the scheduler job, not by handle(). Returns a formatted string
    ready to send as a Telegram message.
    """
    today = date.today()
    week_start = (today - timedelta(days=today.weekday())).isoformat()  # Monday
    week_end = today.isoformat()

    days = get_week_logs(conn, week_start, week_end)
    if not days:
        return "No food logged this week yet."

    avg_protein = sum(d["protein_g"] for d in days) / len(days)
    avg_kcal = sum(d["kcal"] for d in days) / len(days)

    lines = [
        f"Week summary ({week_start} → {week_end}):",
        f"  Avg protein: {avg_protein:.0f}g / {PROTEIN_TARGET_G}g target",
        f"  Avg calories: {avg_kcal:.0f} kcal",
        f"  Days logged: {len(days)}/5",
        "",
        "Weekend shopping — reply with what's in the fridge and I'll build the list.",
    ]
    return "\n".join(lines)
