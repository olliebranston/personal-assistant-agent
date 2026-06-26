"""Deterministic meal/nutrition helpers shared by bot/scheduler.py and tools/.

The LLM-routing entry point that used to live here (`handle`, the
router/parser sub-prompts) was removed — all of that is now handled by the
tool-calling path in main.py + tools/meal.py. This module keeps only the
non-LLM helpers that the scheduled jobs and tools/briefing.py still depend
on directly.
"""

from __future__ import annotations

import random
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from data.meals import (
    BREAKFAST_ROTATION as _BREAKFAST_ROTATION,
    LUNCH_ROTATIONS as _LUNCH_ROTATIONS,
)
from data.recipes import PANTRY_STAPLES, RECIPES
from storage.models import (
    get_daily_totals,
    get_food_logs_for_date,
    get_recent_recipe_slugs,
    get_recent_sessions,
    get_week_logs,
    get_weight_history,
    insert_meal_plan,
)

_TZ = ZoneInfo("Europe/London")


def _today() -> date:
    """Today's date in Europe/London — never bare _today() (server may run in a different tz)."""
    return datetime.now(tz=_TZ).date()


# ── Targets ───────────────────────────────────────────────────────────────────

PROTEIN_TARGET_G = 230
CALORIE_TARGETS = {
    "weights":  3300,
    "rest":     2950,
    "default":  3150,
}

# ── Daily summary (used by bot/scheduler.py:_end_of_day_summary) ─────────────


def _daily_summary(conn) -> str:
    """Return today's macro totals vs target."""
    today = _today().isoformat()
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


def daily_summary(conn) -> str:
    return _daily_summary(conn)


def _get_calorie_target(conn) -> int:
    """Return today's calorie target based on whether a gym session was logged."""
    today = _today().isoformat()
    sessions = get_recent_sessions(conn, limit=5)
    for s in sessions:
        if s["date"] == today and s["session_type"] in ("push", "pull", "legs"):
            return CALORIE_TARGETS["weights"]
    return CALORIE_TARGETS["rest"]


def get_breakfast(weekday: int) -> str:
    return _BREAKFAST_ROTATION.get(weekday, _BREAKFAST_ROTATION[0])


def get_lunch_rotation() -> str:
    idx = _today().isocalendar()[1] % len(_LUNCH_ROTATIONS)
    return _LUNCH_ROTATIONS[idx]


def _format_yesterday_slot_for_prompt(conn, slot: str) -> str | None:
    """Return a compact description of yesterday's logged items for a slot, or None."""
    yesterday = (_today() - timedelta(days=1)).isoformat()
    logs = get_food_logs_for_date(conn, yesterday)
    items = [l for l in logs if l["meal_slot"] == slot]
    if not items:
        return None
    total_protein = sum(l["protein_g"] for l in items)
    descriptions = ", ".join(l["description"] for l in items)
    return f"{descriptions} ({total_protein:.0f}g protein)"


# ── Week meal plan + shopping list (used by bot/scheduler.py:_friday_meal_plan) ─

_DINNER_CATEGORIES = ("weekday_dinner", "weekend_dinner")
_BATCH_CATEGORIES = [
    "red_lentil_dal", "lentil_tofu_salad", "tofu_egg_fried_rice",
    "black_bean_sweet_potato_stew", "quinoa_power_bowl",
]


def _generate_week_plan(conn) -> str:
    """Generate a weekly meal plan + shopping list and store it in the DB."""
    today = _today()
    week_start = (today - timedelta(days=today.weekday())).isoformat()

    recent_slugs = set(get_recent_recipe_slugs(conn, weeks=2))

    batch_idx = today.isocalendar()[1] % len(_BATCH_CATEGORIES)
    batch_slug = _BATCH_CATEGORIES[batch_idx]

    all_dinners = [
        (slug, r) for slug, r in RECIPES.items()
        if r.get("category") in _DINNER_CATEGORIES and slug not in recent_slugs
    ]
    if len(all_dinners) < 4:
        all_dinners = [(slug, r) for slug, r in RECIPES.items()
                        if r.get("category") in _DINNER_CATEGORIES]

    chosen_dinners = random.sample(all_dinners, min(4, len(all_dinners)))
    dinner_slots = ["fri_dinner", "sat_dinner", "sun_dinner", "mon_dinner"]

    conn.execute("DELETE FROM meal_plans WHERE week_start = ?", (week_start,))
    conn.commit()
    insert_meal_plan(conn, week_start, "batch_lunch", batch_slug)
    for slot, (slug, _) in zip(dinner_slots, chosen_dinners):
        insert_meal_plan(conn, week_start, slot, slug)

    batch_recipe = RECIPES[batch_slug]
    lines = ["**THIS WEEK'S MEAL PLAN**", ""]
    lines.append("LUNCHES (Mon–Thu batch cook)")
    lines.append(f"• {batch_recipe['name']} — {batch_recipe['protein_g']}g protein, {batch_recipe['time_mins']} mins for 4 portions")
    lines.append("")
    lines.append("DINNERS")
    slot_labels = {"fri_dinner": "Friday", "sat_dinner": "Saturday",
                   "sun_dinner": "Sunday", "mon_dinner": "Monday"}
    all_plan_recipes = [(batch_slug, batch_recipe)]
    for slot, (slug, r) in zip(dinner_slots, chosen_dinners):
        lines.append(f"• {slot_labels[slot]}: {r['name']} ({r['protein_g']}g protein)")
        all_plan_recipes.append((slug, r))

    lines.append("")
    lines.append("**SHOPPING LIST**")
    shopping = _derive_shopping_list(all_plan_recipes)
    for category, items in shopping.items():
        if items:
            lines.append(f"\n{category}")
            for item, qty_str in items:
                lines.append(f"• {item}: {qty_str}")

    lines.append("")
    lines.append("_Say 'swap Friday dinner for X' to change a slot._")
    return "\n".join(lines)


def _derive_shopping_list(plan_recipes: list[tuple[str, dict]]) -> dict[str, list[tuple[str, str]]]:
    """Aggregate ingredients across all planned recipes, grouped by category."""
    accumulated: dict[str, list[str]] = {}

    for slug, recipe in plan_recipes:
        for ing in recipe.get("ingredients", []):
            item = ing["item"].lower()
            if any(staple in item for staple in PANTRY_STAPLES):
                continue
            qty = ing["qty"]
            unit = ing.get("unit", "")
            qty_str = f"{qty:g} {unit}".strip() if unit else f"{qty:g}"
            if item not in accumulated:
                accumulated[item] = []
            accumulated[item].append(qty_str)

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
        qty_display = " + ".join(dict.fromkeys(qtys))
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


def build_friday_summary(conn) -> str:
    """Generate the Friday week summary + next week's meal plan + derived shopping list."""
    today = _today()
    week_start = (today - timedelta(days=today.weekday())).isoformat()
    week_end = today.isoformat()

    days = get_week_logs(conn, week_start, week_end)
    weight_history = get_weight_history(conn, limit=4)

    lines = ["**FRIDAY SUMMARY**", ""]

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

    plan_section = _generate_week_plan(conn)
    lines.append(plan_section)

    return "\n".join(lines)
