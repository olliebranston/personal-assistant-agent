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
    update_food_log,
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

# Pescatarian-first. Meat max once a week. Fish, eggs, seafood are primary proteins.
_WEEKDAY_DINNERS = [
    "Miso-glazed salmon + roasted sweet potato — 40–45g protein. Brush fillet with miso-mirin, roast at 200°C 15 mins. Simple.",
    "Tofu stir fry (soy-ginger-sesame) + rice/noodles — 35–45g protein. Full 400g block firm tofu + edamame.",
    "Prawn pad thai — tamarind + fish sauce + lime + peanuts — 35–40g protein. 200g prawns.",
    "Tofu ramen (miso-mushroom broth) — 35–40g protein. Dried shiitake + miso + soy + soft-boiled egg.",
    "Cod with black bean sauce + bok choi — 35–40g protein. 200g fillet, steamed or pan-fried.",
    "Korean tofu (sundubu-style) — gochujang + silken tofu + egg + spring onion — 30–38g protein.",
    "Chickpea & spinach curry + Greek yoghurt raita — 25–30g protein. Add tempeh to push to 40g.",
]

_WEEKEND_DINNERS = [
    "Wild salmon or trout + roasted veg — 40–50g protein. Generous fillet (~200g). Miso glaze or lemon-herb.",
    "Mackerel (fresh or tinned) + grains — 35–45g protein. Underrated, cheap, high omega-3.",
    "Tofu katsu curry — panko-crusted tofu, Japanese curry sauce, rice — 35–45g protein.",
    "Dal makhani — slow-cooked black lentils + kidney beans. Worth the overnight soak — 28–35g protein.",
    "Tempeh rendang — coconut + lemongrass + galangal + chilli — 38–45g protein.",
    "Shakshuka (dinner) — 4 eggs + feta + sourdough — 30–38g protein.",
    "Scallops + pea purée + crispy pancetta — 30–35g protein. Weekend treat.",
]

# ── System prompts ────────────────────────────────────────────────────────────

_ROUTER_SYSTEM = """\
Classify the user's meal/nutrition message. Reply ONLY with valid JSON — no prose.

{"action": "log"}                           — logging food eaten (e.g. "had chicken and rice", "200g Greek yoghurt for breakfast")
{"action": "correct"}                       — correcting a previously logged item (e.g. "actually that tofu was 300g", "change the portion to 200g", "add 15g protein to that", "make it 350g")
{"action": "summary"}                       — wants today's macro totals
{"action": "remaining"}                     — wants to know protein/kcal left today
{"action": "history"}                       — asking about food logged yesterday or a specific past date
{"action": "suggest", "meal": "breakfast|lunch|dinner|snack"}  — wants a meal suggestion
{"action": "clarify", "question": "<one short question>"}      — intent unclear

Key distinctions:
- "actually", "change", "make it", "should be" with a food → correct
- "what did I eat", "yesterday", "last week" → history or summary
- A standalone number like "300g" or "yes" after a food log → could be correct
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
    if action == "suggest":
        meal = intent.get("meal", "")
        return _suggest_meal(meal)
    if action == "clarify":
        return intent.get("question", "Log food, check your numbers, or get a meal idea?")
    return "Log food, check your numbers, or get a meal idea?"


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

    # Fetch macros for each item before asking for confirmation.
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

    lines = ["Got it — staging:"]
    for item in enriched:
        source_flag = _source_flag(item["source"])
        lines.append(
            f"  {item['quantity_g']}g {item['name']} — "
            f"{item['protein_g']}g protein, {item['kcal']:.0f} kcal{source_flag}"
        )
    lines.append("Confirm? (yes / tell me what to adjust)")
    return "\n".join(lines)


async def _handle_food_confirmation(
    conn: sqlite3.Connection,
    text: str,
    user_id: int,
    pending: dict,
) -> str:
    """Handle Ollie's response to a staged food-log confirmation.

    Affirmative → write everything to DB, return updated daily total.
    Cancellation → clear state, acknowledge.
    Anything else → treat as an adjustment and re-parse as a new food log.
    """
    text_lower = text.lower().strip()

    if any(w in text_lower for w in ("cancel", "forget", "never mind", "nevermind", "don't log", "dont log", "no thanks")):
        state_svc.clear(user_id)
        return "No problem, not logged."

    words = set(text_lower.split())
    is_yes = bool(words & _AFFIRMATIVES) and len(text.split()) <= 8

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

        lines = [f"Logged — {total_protein:.0f}g protein, {total_kcal:.0f} kcal added."]
        lines.append(f"Today: {totals['protein_g']:.0f}g protein / {totals['kcal']:.0f} kcal (target: {cal_target} kcal)")
        if protein_remaining > 40:
            lines.append(f"{protein_remaining:.0f}g protein still to go — pre-bed shake will cover most of it.")
        elif protein_remaining > 0:
            lines.append(f"Just {protein_remaining:.0f}g protein left.")
        else:
            lines.append("Protein target hit.")
        return "\n".join(lines)

    # Not clearly yes — treat as an adjustment and re-parse.
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

    # Find the most recent matching entry (or last entry if no name given)
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
        # Re-derive the food name from the log description for the USDA lookup
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
        f"Today ({today}), {entries} item{'s' if entries != 1 else ''} logged:",
        f"  Protein:  {protein_status}",
        f"  Calories: {kcal_status}",
    ]

    if not logs:
        lines.append("Nothing logged yet.")
    if protein_gap > 40:
        lines.append("Pre-bed shake (~48g) will close most of that protein gap.")

    return "\n".join(lines)


def _history_summary(conn: sqlite3.Connection) -> str:
    """Return yesterday's macro totals."""
    yesterday = (date.today() - timedelta(days=1)).isoformat()
    totals = get_daily_totals(conn, yesterday)
    logs = get_food_logs_for_date(conn, yesterday)

    if not logs:
        return f"Nothing logged for yesterday ({yesterday})."

    lines = [f"Yesterday ({yesterday}), {len(logs)} items:"]
    lines.append(f"  Protein:  {totals['protein_g']:.0f}g")
    lines.append(f"  Calories: {totals['kcal']:.0f} kcal")
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
    lines.append(f"  Protein:  {protein_left:.0f}g")
    lines.append(f"  Calories: {kcal_left:.0f} kcal")
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


def _source_flag(source: str) -> str:
    """Return a short source indicator string for the confirmation message."""
    if source == "estimated":
        return " ⚠️ no match — check manually"
    if source == "reference":
        return " (ref values)"
    return ""


def _extract_json(text: str) -> str:
    match = re.search(r"\{.*\}", text, re.DOTALL)
    return match.group() if match else text


# ── Scheduler-facing functions (called by bot/scheduler.py) ──────────────────


def daily_summary(conn: sqlite3.Connection) -> str:
    """Public wrapper around _daily_summary for the scheduler."""
    return _daily_summary(conn)


def get_breakfast(weekday: int) -> str:
    """Return today's breakfast from the rotation. weekday: Monday=0, Sunday=6."""
    return _BREAKFAST_ROTATION.get(weekday, _BREAKFAST_ROTATION[0])


def get_lunch_rotation() -> str:
    """Return the current week's lunch rotation based on ISO week number."""
    idx = date.today().isocalendar()[1] % len(_LUNCH_ROTATIONS)
    return _LUNCH_ROTATIONS[idx]


def build_friday_summary(conn: sqlite3.Connection) -> str:
    """Generate the Friday week summary + shopping list. Called by the scheduler."""
    today = date.today()
    week_start = (today - timedelta(days=today.weekday())).isoformat()
    week_end = today.isoformat()

    days = get_week_logs(conn, week_start, week_end)

    lines = ["*Friday summary*", ""]

    if days:
        avg_protein = sum(d["protein_g"] for d in days) / len(days)
        avg_kcal = sum(d["kcal"] for d in days) / len(days)
        lines += [
            f"Week ({week_start} → {week_end}), {len(days)} day(s) tracked:",
            f"  Avg protein: {avg_protein:.0f}g / {PROTEIN_TARGET_G}g target"
            + (" ✓" if avg_protein >= PROTEIN_TARGET_G * 0.9 else " — short"),
            f"  Avg calories: {avg_kcal:.0f} kcal",
            "",
        ]
    else:
        lines += ["No food logged this week.", ""]

    # Next week's lunch rotation
    next_week_idx = (today.isocalendar()[1] + 1) % len(_LUNCH_ROTATIONS)
    next_rotation = _LUNCH_ROTATIONS[next_week_idx]

    lines += [
        "*Next week's batch cook:*",
        next_rotation,
        "",
        "*Weekend shopping list:*",
        "Protein anchor (pick 1–2):",
        "  • Wild salmon fillets ~400g",
        "  • Prawns 300g",
        "  • Eggs ×12 (if running low)",
        "  • Extra-firm tofu ×2 blocks",
        "",
        "Batch cook ingredients for " + next_rotation.split("—")[0].strip() + ":",
        "  (Check your cupboards for lentils/beans/rice — top up as needed)",
        "  • Fresh aromatics: garlic, ginger, spring onions, chillies",
        "  • Fresh veg: whatever's needed for the rotation above",
        "",
        "Fridge restocks: Greek yoghurt 500g, oat milk, fresh spinach, peppers.",
    ]

    return "\n".join(lines)
