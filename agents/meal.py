"""Meal planning agent: food logging, macro tracking, meal suggestions, recipes, weight."""

from __future__ import annotations

import json
import random
import re
import sqlite3
from datetime import date, timedelta

import services.state as state_svc
from data.meals import (
    BREAKFAST_ROTATION as _BREAKFAST_ROTATION,
    LUNCH_ROTATIONS as _LUNCH_ROTATIONS,
    WEEKDAY_DINNERS as _WEEKDAY_DINNERS,
    WEEKEND_DINNERS as _WEEKEND_DINNERS,
)
from data.recipes import (
    PANTRY_STAPLES,
    RECIPES,
    find_recipe,
    format_recipe,
    get_recipes_by_category,
)
from services.nutrition import lookup_macros
from services.openrouter import complete
from storage.models import (
    FoodLog,
    get_daily_totals,
    get_food_logs_for_date,
    get_recent_recipe_slugs,
    get_recent_sessions,
    get_week_logs,
    insert_food_log,
    insert_meal_plan,
    log_weight,
    get_weight_history,
    update_food_log,
)

# ── Targets ───────────────────────────────────────────────────────────────────

PROTEIN_TARGET_G = 230
CALORIE_TARGETS = {
    "weights":  3300,
    "rest":     2950,
    "default":  3150,
}

# ── System prompts ────────────────────────────────────────────────────────────

_ROUTER_SYSTEM = """\
Classify the user's meal/nutrition message. Reply ONLY with valid JSON — no prose.

{"action": "log"}                           — logging food eaten
{"action": "correct"}                       — correcting a previously logged item
{"action": "summary"}                       — wants today's macro totals
{"action": "remaining"}                     — wants to know protein/kcal left today
{"action": "history"}                       — asking about food logged yesterday or a specific day
{"action": "week"}                          — asking about this week's nutrition
{"action": "weight"}                        — logging or querying body weight (e.g. "I weighed 104.2kg", "how's my weight going")
{"action": "recipe"}                        — wants a recipe, ingredients or method for a meal (e.g. "give me the recipe for miso salmon", "how do I make pad thai", "ingredients for dal")
{"action": "week_plan"}                     — wants a weekly meal plan + shopping list (e.g. "plan my week", "what am I cooking this week", "generate a meal plan")
{"action": "repeat", "slot": "breakfast|lunch"}  — wants to log the same meal as yesterday for a specific slot (e.g. "same breakfast", "same lunch as yesterday", "log same breakfast", "yes same lunch")
{"action": "suggest", "meal": "breakfast|lunch|dinner|snack"}  — wants a single meal suggestion
{"action": "clarify", "question": "<one short question>"}      — intent unclear

Key distinctions:
- "same breakfast/lunch", "log same", "repeat yesterday's" → repeat
- "recipe", "how do I make", "ingredients for" → recipe
- "plan my week", "meal plan", "shopping list" → week_plan
- "I weigh", "weighed", "my weight" → weight
- "actually", "change", "make it" with a food → correct
- "what did I eat", "yesterday" → history
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

_CORRECT_PARSER_SYSTEM = """\
Parse a food correction from the user's message. Reply ONLY with valid JSON — no prose.

{
  "food_name": "<name of food being corrected, or empty string if unclear>",
  "correction_type": "new_quantity|protein_delta|new_protein",
  "value": <number>
}

correction_type:
  "new_quantity"  — user is giving a new weight in grams (e.g. "it was 300g", "make it 250g")
  "protein_delta" — user is adjusting protein by +/- (e.g. "add 15g protein", "15g more protein")
  "new_protein"   — user is setting protein to an exact new value (e.g. "that's 40g protein")

Examples:
  "actually that tofu should be 300g"   → {"food_name": "tofu", "correction_type": "new_quantity", "value": 300}
  "add 15g protein to that"             → {"food_name": "", "correction_type": "protein_delta", "value": 15}
  "the chicken was 250g not 200g"       → {"food_name": "chicken", "correction_type": "new_quantity", "value": 250}
"""

_AFFIRMATIVES = frozenset({
    "yes", "yeah", "yep", "yup", "sure", "ok", "okay", "correct", "right",
    "looks good", "go ahead", "log it", "perfect", "good", "fine", "that's right",
    "sounds right", "spot on", "exactly",
})

# ── Public entry point ────────────────────────────────────────────────────────


async def handle(conn: sqlite3.Connection, text: str, user_id: int = 0) -> str:
    """Classify the meal message and dispatch to the appropriate function."""
    pending = state_svc.get(user_id)
    if pending and pending.get("type") == "food_log":
        return await _handle_food_confirmation(conn, text, user_id, pending)

    raw = await complete([{"role": "user", "content": text}], system=_ROUTER_SYSTEM)

    try:
        intent = json.loads(_extract_json(raw))
    except (json.JSONDecodeError, ValueError):
        return "Log food, check your numbers, or get a meal idea — what do you need?"

    action = intent.get("action")

    if action == "log":
        return await _log_food(conn, text, user_id)
    if action == "correct":
        return await _correct_log(conn, text, user_id)
    if action == "summary":
        return _daily_summary(conn)
    if action == "remaining":
        return _remaining_macros(conn)
    if action == "history":
        return _history_summary(conn)
    if action == "week":
        return _week_summary(conn)
    if action == "repeat":
        slot = intent.get("slot", "").strip().lower()
        if slot not in ("breakfast", "lunch"):
            return "Which meal — breakfast or lunch?"
        return _repeat_yesterday_meal(conn, slot)
    if action == "weight":
        return await _handle_weight(conn, text)
    if action == "recipe":
        return await _get_recipe(text)
    if action == "week_plan":
        return _generate_week_plan(conn)
    if action == "suggest":
        meal = intent.get("meal", "")
        return _suggest_meal(meal)
    if action == "clarify":
        return intent.get("question", "Log food, check your numbers, or get a meal idea?")
    return "Log food, check your numbers, or get a meal idea?"


# ── Private helpers ───────────────────────────────────────────────────────────


async def _log_food(conn: sqlite3.Connection, text: str, user_id: int) -> str:
    """Parse free-text food log, look up macros, then either auto-log or stage for confirmation.

    Auto-logs immediately when ALL items are USDA-matched (high confidence).
    Stages for confirmation when any item has uncertain macros (reference or estimated source).
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

    # Auto-log when everything is USDA-confirmed — no friction needed
    if all(item["source"] == "usda" for item in enriched):
        return _write_and_confirm(conn, enriched, meal_slot)

    # Something uncertain — show what we've got and ask to confirm
    state_svc.set_state(user_id, {
        "type": "food_log",
        "meal_slot": meal_slot,
        "items": enriched,
    })

    lines = ["Heads up — some values aren't from USDA, worth a quick check:"]
    for item in enriched:
        flag = _source_flag(item["source"])
        lines.append(
            f"  {item['quantity_g']}g {item['name']} — "
            f"{item['protein_g']}g protein, {item['kcal']:.0f} kcal{flag}"
        )
    lines.append("Log it? (yes / tell me what to adjust)")
    return "\n".join(lines)


def _write_and_confirm(conn: sqlite3.Connection, enriched: list[dict], meal_slot: str) -> str:
    """Write items to DB and return a running daily total summary."""
    today = date.today().isoformat()
    total_protein = 0.0
    total_kcal = 0.0

    for item in enriched:
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

    totals = get_daily_totals(conn, today)
    cal_target = _get_calorie_target(conn)
    protein_remaining = max(PROTEIN_TARGET_G - totals["protein_g"], 0)

    lines = ["Logged:"]
    for item in enriched:
        flag = _source_flag(item["source"])
        lines.append(
            f"  {item['quantity_g']}g {item['name']} — "
            f"{item['protein_g']}g protein, {item['kcal']:.0f} kcal{flag}"
        )
    lines.append(f"\nTotal: {total_protein:.0f}g protein, {total_kcal:.0f} kcal")
    lines.append(f"Today: {totals['protein_g']:.0f}g protein / {totals['kcal']:.0f} kcal (target: {cal_target} kcal)")
    if protein_remaining > 40:
        lines.append(f"{protein_remaining:.0f}g protein to go — pre-bed shake covers most of it.")
    elif protein_remaining > 0:
        lines.append(f"Just {protein_remaining:.0f}g protein left.")
    else:
        lines.append("Protein target hit.")
    lines.append("Wrong? Say 'correct it' or e.g. 'change the chicken to 62g protein'.")
    return "\n".join(lines)


async def _handle_food_confirmation(
    conn: sqlite3.Connection,
    text: str,
    user_id: int,
    pending: dict,
) -> str:
    """Handle the yes/adjust/cancel response to a staged uncertain food log."""
    text_lower = text.lower().strip()

    if any(w in text_lower for w in ("cancel", "forget", "never mind", "nevermind", "don't log", "dont log", "no thanks")):
        state_svc.clear(user_id)
        return "No problem, not logged."

    words = set(text_lower.split())
    is_yes = bool(words & _AFFIRMATIVES) and len(text.split()) <= 8

    if is_yes:
        state_svc.clear(user_id)
        return _write_and_confirm(conn, pending["items"], pending["meal_slot"])

    # Not yes — treat as an adjustment and re-parse
    state_svc.clear(user_id)
    return await _log_food(conn, text, user_id)


async def _correct_log(conn: sqlite3.Connection, text: str, user_id: int) -> str:
    """Update the most recent food log entry matching the correction."""
    raw = await complete([{"role": "user", "content": text}], system=_CORRECT_PARSER_SYSTEM)

    try:
        parsed = json.loads(_extract_json(raw))
    except (json.JSONDecodeError, ValueError):
        return "Couldn't parse the correction. Try: 'the tofu was 300g' or 'add 15g protein'"

    food_name = parsed.get("food_name", "").strip().lower()
    correction_type = parsed.get("correction_type", "")
    value = float(parsed.get("value", 0))

    today = date.today().isoformat()
    logs = get_food_logs_for_date(conn, today)

    if not logs:
        return "Nothing logged today to correct."

    target = None
    if food_name:
        for entry in reversed(logs):
            if food_name in entry["description"].lower():
                target = entry
                break
    if target is None:
        target = logs[-1]

    if correction_type == "protein_delta":
        new_protein = round(target["protein_g"] + value, 1)
        update_food_log(conn, target["id"], new_protein, target["kcal"])
        totals = get_daily_totals(conn, today)
        return (
            f"Updated — {target['description']}: {target['protein_g']}g → {new_protein}g protein.\n"
            f"Today: {totals['protein_g']:.0f}g protein / {totals['kcal']:.0f} kcal"
        )

    if correction_type == "new_protein":
        update_food_log(conn, target["id"], round(value, 1), target["kcal"])
        totals = get_daily_totals(conn, today)
        return (
            f"Updated — {target['description']}: protein set to {value:.0f}g.\n"
            f"Today: {totals['protein_g']:.0f}g protein / {totals['kcal']:.0f} kcal"
        )

    if correction_type == "new_quantity":
        desc_parts = target["description"].split(" ", 1)
        name_for_lookup = desc_parts[1] if len(desc_parts) > 1 else target["description"]
        macros = await lookup_macros(name_for_lookup, value)
        new_desc = f"{value:.0f}g {name_for_lookup}"
        update_food_log(conn, target["id"], macros["protein_g"], macros["kcal"], new_desc)
        totals = get_daily_totals(conn, today)
        return (
            f"Updated — {target['description']} → {new_desc}: "
            f"{macros['protein_g']}g protein, {macros['kcal']:.0f} kcal.\n"
            f"Today: {totals['protein_g']:.0f}g protein / {totals['kcal']:.0f} kcal"
        )

    return "Couldn't work out what to correct. Try: 'the tofu was 300g' or 'add 15g protein'"


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
        f"TODAY · {entries} item{'s' if entries != 1 else ''} logged",
        f"• Protein:  {protein_status}",
        f"• Calories: {kcal_status}",
    ]

    if not logs:
        lines.append("Nothing logged yet.")
    if protein_gap > 40:
        lines.append("Pre-bed shake (~48g) will close most of that gap.")

    return "\n".join(lines)


def _history_summary(conn: sqlite3.Connection) -> str:
    """Return yesterday's food log with individual items grouped by meal slot."""
    yesterday = (date.today() - timedelta(days=1)).isoformat()
    day_name = date.fromisoformat(yesterday).strftime("%A")
    totals = get_daily_totals(conn, yesterday)
    logs = get_food_logs_for_date(conn, yesterday)

    if not logs:
        return f"Nothing logged for yesterday ({day_name})."

    lines = [f"YESTERDAY · {day_name} {yesterday}"]
    current_slot = None
    for log in logs:
        if log["meal_slot"] != current_slot:
            current_slot = log["meal_slot"]
            lines.append(f"\n{current_slot.upper()}")
        lines.append(f"• {log['description']} — {log['protein_g']:.0f}g protein, {log['kcal']:.0f} kcal")

    lines.append(f"\nTOTAL: {totals['protein_g']:.0f}g protein / {totals['kcal']:.0f} kcal")
    return "\n".join(lines)


def _week_summary(conn: sqlite3.Connection) -> str:
    """Return this week's nutrition summary — averages and any low days."""
    today = date.today()
    week_start = (today - timedelta(days=today.weekday())).isoformat()
    week_end = today.isoformat()

    days = get_week_logs(conn, week_start, week_end)

    if not days:
        return "Nothing logged this week yet."

    avg_protein = sum(d["protein_g"] for d in days) / len(days)
    avg_kcal = sum(d["kcal"] for d in days) / len(days)
    protein_ok = avg_protein >= PROTEIN_TARGET_G * 0.9

    lines = [f"THIS WEEK · {len(days)} day{'s' if len(days) != 1 else ''} logged"]
    lines.append(
        f"• avg protein: {avg_protein:.0f}g / {PROTEIN_TARGET_G}g"
        + (" ✓" if protein_ok else f" — {PROTEIN_TARGET_G - avg_protein:.0f}g short on average")
    )
    lines.append(f"• avg calories: {avg_kcal:.0f} kcal")

    low_days = [d for d in days if d["protein_g"] < PROTEIN_TARGET_G * 0.75]
    if low_days:
        lines.append("")
        lines.append("LOW PROTEIN DAYS")
        for d in low_days:
            day_name = date.fromisoformat(d["date"]).strftime("%a")
            lines.append(f"• {day_name} — {d['protein_g']:.0f}g protein")

    return "\n".join(lines)


def _remaining_macros(conn: sqlite3.Connection) -> str:
    """Return how much protein and kcal are left to hit today's targets."""
    today = date.today().isoformat()
    totals = get_daily_totals(conn, today)
    cal_target = _get_calorie_target(conn)

    protein_left = max(PROTEIN_TARGET_G - totals["protein_g"], 0)
    kcal_left = max(cal_target - totals["kcal"], 0)

    if protein_left == 0 and kcal_left == 0:
        return "Both targets hit today. Solid."

    lines = [f"Still to hit (target: {PROTEIN_TARGET_G}g protein / {cal_target} kcal):"]
    lines.append(f"• Protein:  {protein_left:.0f}g")
    lines.append(f"• Calories: {kcal_left:.0f} kcal")
    if protein_left > 0:
        gap_after_shake = max(protein_left - 48, 0)
        if gap_after_shake == 0:
            lines.append("Pre-bed shake gets you there.")
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
            "Snack options (15–20g protein): protein bar (protein > sugar), "
            "150g Greek yoghurt + berries (~15g), 3 tbsp hemp seeds (+10g, tasteless), "
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


def _repeat_yesterday_meal(conn: sqlite3.Connection, slot: str) -> str:
    """Re-log yesterday's entries for a given meal slot into today's log."""
    yesterday = (date.today() - timedelta(days=1)).isoformat()
    yesterday_logs = get_food_logs_for_date(conn, yesterday)
    slot_items = [l for l in yesterday_logs if l["meal_slot"] == slot]

    if not slot_items:
        return f"Nothing logged for {slot} yesterday — nothing to repeat."

    today = date.today().isoformat()
    total_protein = 0.0
    total_kcal = 0.0
    for item in slot_items:
        insert_food_log(conn, FoodLog(
            date=today,
            meal_slot=slot,
            description=item["description"],
            protein_g=item["protein_g"],
            kcal=item["kcal"],
            source=item["source"],
        ))
        total_protein += item["protein_g"]
        total_kcal += item["kcal"]

    totals = get_daily_totals(conn, today)
    cal_target = _get_calorie_target(conn)
    protein_remaining = max(PROTEIN_TARGET_G - totals["protein_g"], 0)

    lines = [f"Logged — same {slot} as yesterday: {total_protein:.0f}g protein, {total_kcal:.0f} kcal."]
    lines.append(f"Today: {totals['protein_g']:.0f}g protein / {totals['kcal']:.0f} kcal (target: {cal_target} kcal)")
    if protein_remaining > 0:
        lines.append(f"{protein_remaining:.0f}g protein to go.")
    return "\n".join(lines)


def _format_yesterday_slot_for_prompt(conn: sqlite3.Connection, slot: str) -> str | None:
    """Return a compact description of yesterday's logged items for a slot, or None."""
    yesterday = (date.today() - timedelta(days=1)).isoformat()
    logs = get_food_logs_for_date(conn, yesterday)
    items = [l for l in logs if l["meal_slot"] == slot]
    if not items:
        return None
    total_protein = sum(l["protein_g"] for l in items)
    descriptions = ", ".join(l["description"] for l in items)
    return f"{descriptions} ({total_protein:.0f}g protein)"


def _source_flag(source: str) -> str:
    if source == "estimated":
        return " no match — check manually"
    if source == "reference":
        return " (ref values)"
    return ""


# ── Recipe lookup ──────────────────────────────────────────────────────────────

_RECIPE_EXTRACT_SYSTEM = """\
Extract the meal name the user is asking about. Reply ONLY with a short meal name — no prose.
Examples: "give me the recipe for miso salmon" → "miso salmon"
          "how do I make pad thai" → "pad thai"
          "ingredients for red lentil dal" → "red lentil dal"
"""


async def _get_recipe(text: str) -> str:
    """Find and format a recipe matching the user's request."""
    raw = await complete([{"role": "user", "content": text}], system=_RECIPE_EXTRACT_SYSTEM)
    query = raw.strip().strip('"').strip("'")

    result = find_recipe(query)
    if result:
        slug, _ = result
        return format_recipe(slug)

    # No match — suggest closest categories
    weekday = [r["name"] for _, r in get_recipes_by_category("weekday_dinner")]
    weekend = [r["name"] for _, r in get_recipes_by_category("weekend_dinner")]
    return (
        f"Don't have a recipe for '{query}'. Here's what I've got:\n\n"
        f"Weekday dinners: {', '.join(weekday)}\n"
        f"Weekend dinners: {', '.join(weekend)}"
    )


# ── Weight logging ─────────────────────────────────────────────────────────────

_WEIGHT_EXTRACT_SYSTEM = """\
Extract the body weight in kg from the user's message. Reply ONLY with a number — no units, no prose.
"I weighed 104.2kg this morning" → 104.2
"104.5" → 104.5
"weighed 103.8 today" → 103.8
If the message is a query (not a log), reply with exactly: query
"""


async def _handle_weight(conn: sqlite3.Connection, text: str) -> str:
    """Log body weight or return trend."""
    raw = await complete([{"role": "user", "content": text}], system=_WEIGHT_EXTRACT_SYSTEM)
    raw = raw.strip()

    if raw == "query":
        return _weight_trend(conn)

    try:
        kg = float(raw)
    except ValueError:
        return "Couldn't parse that weight. Try: '104.2kg' or '104.2 this morning'"

    if not (50 <= kg <= 250):
        return f"That doesn't look right ({kg}kg). Try again."

    today = date.today().isoformat()
    log_weight(conn, today, kg)
    return f"Weight logged: {kg}kg.\n{_weight_trend(conn)}"


def _weight_trend(conn: sqlite3.Connection) -> str:
    """Return a concise weight trend from recent history."""
    history = get_weight_history(conn, limit=8)
    if not history:
        return "No weight logged yet."

    latest = history[0]
    lines = [f"Latest: {latest['weight_kg']}kg ({latest['date']})"]

    if len(history) >= 2:
        oldest = history[-1]
        delta = latest["weight_kg"] - oldest["weight_kg"]
        direction = "down" if delta < 0 else "up"
        lines.append(f"{direction} {abs(delta):.1f}kg over {len(history)} readings")

    if len(history) >= 3:
        lines.append("Recent: " + " → ".join(
            f"{h['weight_kg']}kg" for h in reversed(history[:4])
        ))

    return "\n".join(lines)


# ── Week meal plan + shopping list ────────────────────────────────────────────

# Day slots for the week plan
_WEEK_SLOTS = [
    ("fri_dinner",  "Fri dinner"),
    ("sat_dinner",  "Sat dinner"),
    ("sun_dinner",  "Sun dinner"),
    ("mon_dinner",  "Mon dinner"),
    ("mon_lunch",   "Mon–Thu lunch (batch cook)"),
]

# Categories eligible for the 4 dinner slots
_DINNER_CATEGORIES = ("weekday_dinner", "weekend_dinner")


def _generate_week_plan(conn: sqlite3.Connection) -> str:
    """Generate a weekly meal plan + shopping list and store it in the DB."""
    today = date.today()
    week_start = (today - timedelta(days=today.weekday())).isoformat()

    # Get recently used recipes to avoid repetition
    recent_slugs = set(get_recent_recipe_slugs(conn, weeks=2))

    # Pick this week's batch cook rotation (existing logic)
    batch_idx = today.isocalendar()[1] % 5
    batch_categories = ["red_lentil_dal", "lentil_tofu_salad", "tofu_egg_fried_rice",
                        "black_bean_sweet_potato_stew", "quinoa_power_bowl"]
    batch_slug = batch_categories[batch_idx]

    # Pick 4 dinners avoiding recent repeats
    all_dinners = [
        (slug, r) for slug, r in RECIPES.items()
        if r.get("category") in _DINNER_CATEGORIES and slug not in recent_slugs
    ]
    if len(all_dinners) < 4:
        # If not enough fresh options, open it up
        all_dinners = [(slug, r) for slug, r in RECIPES.items()
                       if r.get("category") in _DINNER_CATEGORIES]

    chosen_dinners = random.sample(all_dinners, min(4, len(all_dinners)))
    dinner_slots = ["fri_dinner", "sat_dinner", "sun_dinner", "mon_dinner"]

    # Store plan in DB
    conn.execute("DELETE FROM meal_plans WHERE week_start = ?", (week_start,))
    conn.commit()
    insert_meal_plan(conn, week_start, "batch_lunch", batch_slug)
    for slot, (slug, _) in zip(dinner_slots, chosen_dinners):
        insert_meal_plan(conn, week_start, slot, slug)

    # Build output
    batch_recipe = RECIPES[batch_slug]
    lines = ["*THIS WEEK'S MEAL PLAN*", ""]
    lines.append(f"LUNCHES (Mon–Thu batch cook)")
    lines.append(f"• {batch_recipe['name']} — {batch_recipe['protein_g']}g protein, {batch_recipe['time_mins']} mins for 4 portions")
    lines.append("")
    lines.append("DINNERS")
    slot_labels = {"fri_dinner": "Friday", "sat_dinner": "Saturday",
                   "sun_dinner": "Sunday", "mon_dinner": "Monday"}
    all_plan_recipes = [(batch_slug, batch_recipe)]
    for slot, (slug, r) in zip(dinner_slots, chosen_dinners):
        lines.append(f"• {slot_labels[slot]}: {r['name']} ({r['protein_g']}g protein)")
        all_plan_recipes.append((slug, r))

    # Generate shopping list
    lines.append("")
    lines.append("*SHOPPING LIST*")
    shopping = _derive_shopping_list(all_plan_recipes)
    for category, items in shopping.items():
        if items:
            lines.append(f"\n{category}")
            for item, qty_str in items:
                lines.append(f"• {item}: {qty_str}")

    lines.append("")
    lines.append("_Say 'swap Friday dinner for X' to change a slot._")
    return "\n".join(lines)


def _derive_shopping_list(
    plan_recipes: list[tuple[str, dict]],
) -> dict[str, list[tuple[str, str]]]:
    """Aggregate ingredients across all planned recipes, grouped by category."""
    # Accumulate raw ingredient quantities
    accumulated: dict[str, list[str]] = {}

    for slug, recipe in plan_recipes:
        serves = recipe.get("serves", 1)
        for ing in recipe.get("ingredients", []):
            item = ing["item"].lower()
            # Skip pantry staples
            if any(staple in item for staple in PANTRY_STAPLES):
                continue
            qty = ing["qty"]
            unit = ing.get("unit", "")
            # Scale batch cook (4 serves already)
            qty_str = f"{qty:g} {unit}".strip() if unit else f"{qty:g}"
            if item not in accumulated:
                accumulated[item] = []
            accumulated[item].append(qty_str)

    # Categorise
    protein_keywords = {"salmon", "cod", "mackerel", "prawn", "scallop", "tofu",
                        "tempeh", "egg", "chicken", "whey", "protein"}
    dairy_keywords = {"yoghurt", "yogurt", "feta", "cream", "butter", "milk", "cheese"}
    veg_keywords = {"spinach", "pepper", "courgette", "tomato", "onion", "leek",
                    "spring onion", "bok choi", "cucumber", "avocado", "sweet potato",
                    "carrot", "potato", "broccoli", "mushroom", "lemon", "lime",
                    "coriander", "parsley", "dill", "mint", "chive", "shallot",
                    "lemongrass", "banana", "berry", "berries"}
    pantry_extra = {"noodle", "rice", "bread", "sourdough", "naan", "muffin",
                    "panko", "flour", "roux", "lentil", "bean", "chickpea",
                    "coconut milk", "honey", "peanut", "tahini", "hemp"}

    categories: dict[str, list[tuple[str, str]]] = {
        "PROTEIN & FISH": [],
        "VEG & FRESH": [],
        "DAIRY": [],
        "FRIDGE / FREEZER": [],
        "STORE CUPBOARD": [],
    }

    for item, qtys in sorted(accumulated.items()):
        qty_display = " + ".join(dict.fromkeys(qtys))  # dedupe while preserving order
        entry = (item, qty_display)

        if any(kw in item for kw in protein_keywords):
            categories["PROTEIN & FISH"].append(entry)
        elif any(kw in item for kw in dairy_keywords):
            categories["DAIRY"].append(entry)
        elif any(kw in item for kw in veg_keywords):
            categories["VEG & FRESH"].append(entry)
        elif any(kw in item for kw in pantry_extra):
            categories["STORE CUPBOARD"].append(entry)
        else:
            categories["FRIDGE / FREEZER"].append(entry)

    return categories


def _extract_json(text: str) -> str:
    match = re.search(r"\{.*\}", text, re.DOTALL)
    return match.group() if match else text


# ── Scheduler-facing functions (called by bot/scheduler.py) ──────────────────


def daily_summary(conn: sqlite3.Connection) -> str:
    return _daily_summary(conn)


def get_breakfast(weekday: int) -> str:
    return _BREAKFAST_ROTATION.get(weekday, _BREAKFAST_ROTATION[0])


def get_lunch_rotation() -> str:
    idx = date.today().isocalendar()[1] % len(_LUNCH_ROTATIONS)
    return _LUNCH_ROTATIONS[idx]


def build_friday_summary(conn: sqlite3.Connection) -> str:
    """Generate the Friday week summary + next week's meal plan + derived shopping list."""
    today = date.today()
    week_start = (today - timedelta(days=today.weekday())).isoformat()
    week_end = today.isoformat()

    days = get_week_logs(conn, week_start, week_end)
    weight_history = get_weight_history(conn, limit=4)

    lines = ["*FRIDAY SUMMARY*", ""]

    if days:
        avg_protein = sum(d["protein_g"] for d in days) / len(days)
        avg_kcal = sum(d["kcal"] for d in days) / len(days)
        protein_ok = avg_protein >= PROTEIN_TARGET_G * 0.9
        lines += [
            f"This week — {len(days)} day(s) tracked:",
            f"• Avg protein: {avg_protein:.0f}g / {PROTEIN_TARGET_G}g"
            + (" ✓" if protein_ok else f" — {PROTEIN_TARGET_G - avg_protein:.0f}g short on average"),
            f"• Avg calories: {avg_kcal:.0f} kcal",
        ]
        low_days = [d for d in days if d["protein_g"] < PROTEIN_TARGET_G * 0.75]
        if low_days:
            low_names = [date.fromisoformat(d["date"]).strftime("%a") for d in low_days]
            lines.append(f"• Low days: {', '.join(low_names)}")
    else:
        lines.append("No food logged this week.")

    if weight_history:
        latest = weight_history[0]
        if len(weight_history) >= 2:
            delta = weight_history[0]["weight_kg"] - weight_history[-1]["weight_kg"]
            trend = f" ({'+' if delta > 0 else ''}{delta:.1f}kg trend)"
        else:
            trend = ""
        lines.append(f"• Latest weight: {latest['weight_kg']}kg{trend}")

    lines.append("")

    # Generate next week's plan + shopping list
    plan_section = _generate_week_plan(conn)
    lines.append(plan_section)

    return "\n".join(lines)
