"""Meal & nutrition tools — §2.2 of TOOL_CALLING_DESIGN.md.

Each tool is `async def tool_name(conn, **kwargs) -> dict`, JSON-serialisable,
and returns {"error": "..."} on failure instead of raising.
"""

from __future__ import annotations

import logging
import random
import sqlite3
from datetime import date as _date, timedelta

from data.meals import BREAKFAST_ROTATION, LUNCH_ROTATIONS, WEEKDAY_DINNERS, WEEKEND_DINNERS
from data.recipes import RECIPES, find_recipe, get_recipes_by_category
from services.nutrition import lookup_macros
from storage.models import (
    FoodLog,
    get_daily_totals,
    get_food_logs_for_date,
    get_recent_recipe_slugs,
    get_recent_sessions,
    get_week_logs,
    get_weight_history,
    insert_food_log,
    insert_meal_plan,
    log_weight as db_log_weight,
    update_food_log,
)

logger = logging.getLogger(__name__)

PROTEIN_TARGET_G = 230
CALORIE_TARGETS = {
    "weights": 3300,
    "rest": 2950,
    "default": 3150,
}

_DINNER_CATEGORIES = ("weekday_dinner", "weekend_dinner")
_BATCH_CATEGORIES = [
    "red_lentil_dal", "lentil_tofu_salad", "tofu_egg_fried_rice",
    "black_bean_sweet_potato_stew", "quinoa_power_bowl",
]
_WEEKDAY_NAMES = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
_DINNER_SLOT_BY_WEEKDAY_NAME = {
    "Friday": "fri_dinner",
    "Saturday": "sat_dinner",
    "Sunday": "sun_dinner",
    "Monday": "mon_dinner",
}


def _is_weights_day(conn: sqlite3.Connection, date_str: str) -> bool:
    for session in get_recent_sessions(conn, limit=5):
        if session["date"] == date_str and session["session_type"] in ("push", "pull", "legs"):
            return True
    return False


def _daily_macros_dict(conn: sqlite3.Connection, date_str: str) -> dict:
    totals = get_daily_totals(conn, date_str)
    is_weights = _is_weights_day(conn, date_str)
    kcal_target = CALORIE_TARGETS["weights"] if is_weights else CALORIE_TARGETS["rest"]
    return {
        "date": date_str,
        "protein_g": totals["protein_g"],
        "kcal": totals["kcal"],
        "protein_target": PROTEIN_TARGET_G,
        "kcal_target": kcal_target,
        "protein_remaining": max(PROTEIN_TARGET_G - totals["protein_g"], 0),
        "kcal_remaining": max(kcal_target - totals["kcal"], 0),
        "is_weights_day": is_weights,
    }


def _weight_trend_per_week(history: list[dict]) -> float | None:
    """Compute kg/week trend from weight history (newest first). None if <2 entries."""
    if len(history) < 2:
        return None
    latest, oldest = history[0], history[-1]
    days = (_date.fromisoformat(latest["date"]) - _date.fromisoformat(oldest["date"])).days
    if days <= 0:
        return None
    delta = latest["weight_kg"] - oldest["weight_kg"]
    return round(delta / days * 7, 2)


# ── log_food / corrections ──────────────────────────────────────────────────


async def log_food(
    conn: sqlite3.Connection,
    food_name: str,
    grams: float,
    meal_slot: str | None = None,
) -> dict:
    """Look up macros for a food and write it to today's log immediately (§5.1)."""
    try:
        slot = meal_slot or "other"
        macros = await lookup_macros(food_name, grams)

        today = _date.today().isoformat()
        description = f"{grams:.0f}g {food_name}"
        log_id = insert_food_log(conn, FoodLog(
            date=today,
            meal_slot=slot,
            description=description,
            protein_g=macros["protein_g"],
            kcal=macros["kcal"],
            source=macros["source"],
        ))

        return {
            "logged": True,
            "id": log_id,
            "food_name": food_name,
            "grams": grams,
            "protein_g": macros["protein_g"],
            "kcal": macros["kcal"],
            "source": macros["source"],
            "meal_slot": slot,
            "daily_totals": _daily_macros_dict(conn, today),
        }
    except Exception as exc:
        logger.warning("log_food failed: %s", exc)
        return {"error": str(exc)}


async def correct_food_log(
    conn: sqlite3.Connection,
    food_name: str = "",
    field: str = "quantity_g",
    new_value: float = 0,
) -> dict:
    """Correct today's most recent matching food log entry.

    food_name matches as a substring against today's entries (case-insensitive);
    empty string matches the most recent entry. field is 'quantity_g' (re-runs
    USDA lookup at the new gram amount) or 'protein_g' (sets protein directly).
    """
    try:
        today = _date.today().isoformat()
        logs = get_food_logs_for_date(conn, today)
        if not logs:
            return {"error": "no food logged today"}

        target = None
        if food_name:
            needle = food_name.lower()
            for entry in reversed(logs):
                if needle in entry["description"].lower():
                    target = entry
                    break
            if target is None:
                return {"error": f"no matching entry found for '{food_name}'"}
        else:
            target = logs[-1]

        before = {
            "id": target["id"],
            "description": target["description"],
            "protein_g": target["protein_g"],
            "kcal": target["kcal"],
        }

        if field == "quantity_g":
            desc_parts = target["description"].split(" ", 1)
            name_for_lookup = desc_parts[1] if len(desc_parts) > 1 else target["description"]
            macros = await lookup_macros(name_for_lookup, float(new_value))
            new_desc = f"{float(new_value):.0f}g {name_for_lookup}"
            update_food_log(conn, target["id"], macros["protein_g"], macros["kcal"], new_desc)
            after = {
                "id": target["id"],
                "description": new_desc,
                "protein_g": macros["protein_g"],
                "kcal": macros["kcal"],
                "source": macros["source"],
            }
        elif field == "protein_g":
            new_protein = round(float(new_value), 1)
            update_food_log(conn, target["id"], new_protein, target["kcal"])
            after = {
                "id": target["id"],
                "description": target["description"],
                "protein_g": new_protein,
                "kcal": target["kcal"],
            }
        else:
            return {"error": f"unknown field: {field!r} (expected 'quantity_g' or 'protein_g')"}

        return {
            "updated": True,
            "before": before,
            "after": after,
            "daily_totals": _daily_macros_dict(conn, today),
        }
    except Exception as exc:
        logger.warning("correct_food_log failed: %s", exc)
        return {"error": str(exc)}


# ── Macro queries ─────────────────────────────────────────────────────────────


async def get_food_log(conn: sqlite3.Connection, date: str) -> dict:
    """Return all food log entries for a given date, plus the daily totals."""
    try:
        logs = get_food_logs_for_date(conn, date)
        totals = get_daily_totals(conn, date)
        return {
            "date": date,
            "entries": [
                {
                    "id": log["id"],
                    "description": log["description"],
                    "meal_slot": log["meal_slot"],
                    "protein_g": log["protein_g"],
                    "kcal": log["kcal"],
                    "source": log["source"],
                }
                for log in logs
            ],
            "totals": {"protein_g": totals["protein_g"], "kcal": totals["kcal"]},
        }
    except Exception as exc:
        logger.warning("get_food_log failed: %s", exc)
        return {"error": str(exc)}


async def get_daily_macros(conn: sqlite3.Connection, date: str | None = None) -> dict:
    """Return today's (or a given date's) macro totals vs targets."""
    try:
        d = date or _date.today().isoformat()
        return _daily_macros_dict(conn, d)
    except Exception as exc:
        logger.warning("get_daily_macros failed: %s", exc)
        return {"error": str(exc)}


async def get_weekly_macro_summary(conn: sqlite3.Connection) -> dict:
    """Return this week's (Monday-based) daily macro totals and averages."""
    try:
        today = _date.today()
        week_start = (today - timedelta(days=today.weekday())).isoformat()
        week_end = today.isoformat()
        days = get_week_logs(conn, week_start, week_end)

        avg_protein = sum(d["protein_g"] for d in days) / len(days) if days else 0.0
        avg_kcal = sum(d["kcal"] for d in days) / len(days) if days else 0.0

        return {
            "week_start": week_start,
            "days": [
                {"date": d["date"], "protein_g": d["protein_g"], "kcal": d["kcal"], "entries": d["entries"]}
                for d in days
            ],
            "avg_protein_g": round(avg_protein, 1),
            "avg_kcal": round(avg_kcal, 1),
            "day_count": len(days),
        }
    except Exception as exc:
        logger.warning("get_weekly_macro_summary failed: %s", exc)
        return {"error": str(exc)}


# ── Recipes / meal suggestions ────────────────────────────────────────────────


async def get_recipe(conn: sqlite3.Connection, recipe_name: str) -> dict:
    """Look up a recipe by name (fuzzy match on slug/name/keywords)."""
    try:
        result = find_recipe(recipe_name)
        if result is None:
            return {
                "found": False,
                "query": recipe_name,
                "available_weekday_dinners": [r["name"] for _, r in get_recipes_by_category("weekday_dinner")],
                "available_weekend_dinners": [r["name"] for _, r in get_recipes_by_category("weekend_dinner")],
            }

        slug, recipe = result
        return {
            "found": True,
            "name": recipe["name"],
            "slug": slug,
            "category": recipe.get("category"),
            "serves": recipe.get("serves", 1),
            "time_mins": recipe["time_mins"],
            "protein_g": recipe["protein_g"],
            "kcal": recipe.get("kcal"),
            "ingredients": recipe["ingredients"],
            "method": recipe["method"],
        }
    except Exception as exc:
        logger.warning("get_recipe failed: %s", exc)
        return {"error": str(exc)}


async def suggest_meal(conn: sqlite3.Connection, meal_type: str) -> dict:
    """Suggest a meal from the rotation for breakfast/lunch/dinner/snack."""
    try:
        mt = meal_type.lower().strip()
        today = _date.today()
        weekday = today.weekday()

        if "breakfast" in mt:
            return {
                "meal_type": "breakfast",
                "suggestion": BREAKFAST_ROTATION[weekday],
                "recipe_slug": None,
                "rotation_day": today.strftime("%A"),
            }

        if "lunch" in mt:
            idx = today.isocalendar()[1] % len(LUNCH_ROTATIONS)
            return {
                "meal_type": "lunch",
                "suggestion": LUNCH_ROTATIONS[idx],
                "recipe_slug": None,
                "rotation_day": f"Week {today.isocalendar()[1]}",
            }

        if "dinner" in mt:
            if weekday >= 5:
                return {
                    "meal_type": "dinner",
                    "suggestion": random.choice(WEEKEND_DINNERS),
                    "recipe_slug": None,
                    "rotation_day": "weekend",
                }
            idx = weekday % len(WEEKDAY_DINNERS)
            return {
                "meal_type": "dinner",
                "suggestion": WEEKDAY_DINNERS[idx],
                "recipe_slug": None,
                "rotation_day": today.strftime("%A"),
            }

        if "snack" in mt:
            return {
                "meal_type": "snack",
                "suggestion": (
                    "Protein bar (protein > sugar), 150g Greek yoghurt + berries (~15g protein), "
                    "3 tbsp hemp seeds (+10g, tasteless), handful edamame (~11g/100g)."
                ),
                "recipe_slug": None,
                "rotation_day": None,
            }

        return {"error": f"unknown meal_type: {meal_type!r} (expected breakfast/lunch/dinner/snack)"}
    except Exception as exc:
        logger.warning("suggest_meal failed: %s", exc)
        return {"error": str(exc)}


async def generate_meal_plan(conn: sqlite3.Connection, week_start: str | None = None) -> dict:
    """Generate (and persist) a weekly breakfast/lunch/dinner plan."""
    try:
        if week_start:
            start = _date.fromisoformat(week_start)
        else:
            today = _date.today()
            start = today - timedelta(days=today.weekday())
        week_start_str = start.isoformat()

        recent_slugs = set(get_recent_recipe_slugs(conn, weeks=2))

        iso_week = start.isocalendar()[1]
        batch_slug = _BATCH_CATEGORIES[iso_week % len(_BATCH_CATEGORIES)]
        batch_recipe = RECIPES[batch_slug]

        all_dinners = [
            (slug, r) for slug, r in RECIPES.items()
            if r.get("category") in _DINNER_CATEGORIES and slug not in recent_slugs
        ]
        if len(all_dinners) < 4:
            all_dinners = [(slug, r) for slug, r in RECIPES.items() if r.get("category") in _DINNER_CATEGORIES]

        chosen_dinners = random.sample(all_dinners, min(4, len(all_dinners)))
        dinner_slot_order = ["fri_dinner", "sat_dinner", "sun_dinner", "mon_dinner"]
        dinner_by_slot = dict(zip(dinner_slot_order, chosen_dinners))

        conn.execute("DELETE FROM meal_plans WHERE week_start = ?", (week_start_str,))
        conn.commit()
        insert_meal_plan(conn, week_start_str, "batch_lunch", batch_slug)
        for slot, (slug, _recipe) in dinner_by_slot.items():
            insert_meal_plan(conn, week_start_str, slot, slug)

        days: dict[str, dict] = {}
        for weekday_idx, day_name in enumerate(_WEEKDAY_NAMES):
            breakfast = BREAKFAST_ROTATION[weekday_idx]
            lunch = f"{batch_recipe['name']} (batch cook)" if weekday_idx <= 3 else "Leftovers / flexible"

            slot_key = _DINNER_SLOT_BY_WEEKDAY_NAME.get(day_name)
            if slot_key:
                dinner = dinner_by_slot[slot_key][1]["name"]
            else:
                dinner = WEEKDAY_DINNERS[weekday_idx % len(WEEKDAY_DINNERS)]

            days[day_name] = {"breakfast": breakfast, "lunch": lunch, "dinner": dinner}

        return {"week_start": week_start_str, "days": days}
    except Exception as exc:
        logger.warning("generate_meal_plan failed: %s", exc)
        return {"error": str(exc)}


# ── Weight tracking ────────────────────────────────────────────────────────────


async def log_weight(conn: sqlite3.Connection, weight_kg: float) -> dict:
    """Log today's body weight. Rejects implausible values outside 50-250kg."""
    try:
        if not (50 <= weight_kg <= 250):
            return {"error": f"weight {weight_kg}kg is outside the plausible range (50-250kg)"}

        today = _date.today().isoformat()
        db_log_weight(conn, today, weight_kg)

        history = get_weight_history(conn, limit=8)
        return {
            "logged": True,
            "date": today,
            "weight_kg": weight_kg,
            "trend_kg_per_week": _weight_trend_per_week(history),
        }
    except Exception as exc:
        logger.warning("log_weight failed: %s", exc)
        return {"error": str(exc)}


async def get_weight_trend(conn: sqlite3.Connection, limit: int = 8) -> dict:
    """Return recent weight history (newest first) and the kg/week trend."""
    try:
        history = get_weight_history(conn, limit=limit)
        return {
            "entries": [{"date": h["date"], "weight_kg": h["weight_kg"]} for h in history],
            "trend_kg_per_week": _weight_trend_per_week(history),
            "latest_weight_kg": history[0]["weight_kg"] if history else None,
        }
    except Exception as exc:
        logger.warning("get_weight_trend failed: %s", exc)
        return {"error": str(exc)}


# ── Tool schemas (OpenAI function-calling format) ───────────────────────────

TOOL_SCHEMAS: list[dict] = [
    {
        "type": "function",
        "function": {
            "name": "log_food",
            "description": (
                "Log one food item to today's nutrition log. Writes immediately — no "
                "confirmation step. Call once per distinct food item mentioned, e.g. for "
                "'200g Greek yoghurt and 80g oats' call this twice. Returns the computed "
                "macros plus today's running totals vs target, so you don't need a "
                "separate get_daily_macros call to report progress."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "food_name": {
                        "type": "string",
                        "description": "Food name suitable for a nutrition lookup, e.g. 'Greek yoghurt', 'chicken breast'.",
                    },
                    "grams": {
                        "type": "number",
                        "description": (
                            "Quantity in grams. Convert other units: 1 pint ≈ 568g/ml, 1 tbsp ≈ 15g, "
                            "1 scoop protein powder ≈ 33g. Use sensible default portions for a 105kg "
                            "active male (e.g. chicken breast = 200g, bowl of rice = 220g cooked) — "
                            "only ask if genuinely ambiguous."
                        ),
                    },
                    "meal_slot": {
                        "type": ["string", "null"],
                        "enum": ["breakfast", "snack", "lunch", "shake", "dinner", "alcohol", "other", None],
                        "description": "Which meal this belongs to. Infer from context if not stated.",
                    },
                },
                "required": ["food_name", "grams"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "correct_food_log",
            "description": (
                "Correct a food item already logged today — e.g. 'actually the yoghurt was "
                "300g' or 'that chicken was more like 50g protein'. Matches food_name as a "
                "substring against today's entries (case-insensitive); leave food_name empty "
                "to correct the most recently logged item. Returns before/after macros and "
                "the updated daily totals."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "food_name": {
                        "type": "string",
                        "description": "Substring to match against today's logged entries, e.g. 'yoghurt'. Leave empty for the most recent entry.",
                    },
                    "field": {
                        "type": "string",
                        "enum": ["quantity_g", "protein_g"],
                        "description": (
                            "'quantity_g' — correct the gram amount and re-run the nutrition lookup "
                            "at the new amount. 'protein_g' — set the protein value directly (e.g. "
                            "user states an exact protein content)."
                        ),
                    },
                    "new_value": {
                        "type": "number",
                        "description": "The corrected value — grams for 'quantity_g', grams of protein for 'protein_g'.",
                    },
                },
                "required": ["field", "new_value"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_food_log",
            "description": (
                "Get every food entry logged for a given date, plus that day's totals. Use "
                "this to answer 'what did I eat yesterday', to find an entry's id for a "
                "correction, or to repeat a previous day's meal."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "date": {
                        "type": "string",
                        "description": "Date in YYYY-MM-DD format.",
                    },
                },
                "required": ["date"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_daily_macros",
            "description": (
                "Get protein/calorie totals vs targets for a date (defaults to today). Use "
                "this for 'what's my protein today', 'how many calories left', or 'am I on "
                "track'. Note: log_food already returns today's totals — only call this "
                "separately when no log_food call was just made, or for a different date."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "date": {
                        "type": ["string", "null"],
                        "description": "Date in YYYY-MM-DD format. Omit or null for today.",
                    },
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_weekly_macro_summary",
            "description": (
                "Get this week's (Monday-based) daily protein/calorie totals and averages. "
                "Use this for 'how's my week looking nutrition-wise'."
            ),
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_recipe",
            "description": (
                "Look up a recipe by name — ingredients, method, time, and protein/kcal per "
                "serving. Use this for 'how do I make X' or 'recipe for X'. If not found, "
                "returns lists of available weekday/weekend dinners instead."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "recipe_name": {
                        "type": "string",
                        "description": "Recipe or dish name to search for, e.g. 'miso salmon', 'pad thai'.",
                    },
                },
                "required": ["recipe_name"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "suggest_meal",
            "description": (
                "Suggest a meal from the rotation for breakfast, lunch, dinner, or snack — "
                "based on today's date (weekday/weekend, week number). Use this for 'what "
                "should I have for lunch' / 'suggest dinner'."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "meal_type": {
                        "type": "string",
                        "enum": ["breakfast", "lunch", "dinner", "snack"],
                        "description": "Which meal to suggest.",
                    },
                },
                "required": ["meal_type"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "generate_meal_plan",
            "description": (
                "Generate (and save) a full weekly meal plan — breakfast/lunch/dinner for "
                "every day of the week. Use this for 'plan my week' or 'what am I cooking "
                "this week'."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "week_start": {
                        "type": ["string", "null"],
                        "description": "Monday's date (YYYY-MM-DD) for the week to plan. Omit or null for the current week.",
                    },
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "log_weight",
            "description": (
                "Log today's body weight in kg. Use when the user states a weight reading, "
                "e.g. 'weighed 104.2kg this morning'. Rejects values outside 50-250kg."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "weight_kg": {
                        "type": "number",
                        "description": "Body weight in kg.",
                    },
                },
                "required": ["weight_kg"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_weight_trend",
            "description": (
                "Get recent body weight history (newest first) and the kg/week trend. Use "
                "this for 'how's my weight going' or 'what's my weight trend'."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "limit": {
                        "type": "integer",
                        "description": "Max number of recent entries to return. Defaults to 8.",
                    },
                },
                "required": [],
            },
        },
    },
]
