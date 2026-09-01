"""Meal & nutrition tools — §2.2 of TOOL_CALLING_DESIGN.md.

Each tool is `async def tool_name(conn, **kwargs) -> dict`, JSON-serialisable,
and returns {"error": "..."} on failure instead of raising.
"""

from __future__ import annotations

import logging
import random
import re
import sqlite3
from datetime import date as _date, datetime, timedelta

import config
from data.meals import BREAKFAST_ROTATION, LUNCH_ROTATIONS, WEEKDAY_DINNERS, WEEKEND_DINNERS
from data.recipes import RECIPES, find_recipe, get_recipes_by_category
from services.meal_helpers import CALORIE_TARGETS, PROTEIN_TARGET_G, is_weights_day
from services.nutrition import lookup_macros
from storage.models import (
    FoodLog,
    delete_food_log as db_delete_food_log,
    delete_food_logs_for_date,
    get_daily_totals,
    get_food_logs_for_date,
    get_recent_recipe_slugs,
    get_user_food,
    get_week_logs,
    get_weight_history,
    insert_food_log,
    insert_meal_plan,
    log_weight as db_log_weight,
    update_food_log,
    upsert_user_food,
)

logger = logging.getLogger(__name__)

_TZ = config.TZ


def _today() -> _date:
    """Today's date in Europe/London — never bare date.today() (server may run in a different tz)."""
    return datetime.now(tz=_TZ).date()


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


def _daily_macros_dict(conn: sqlite3.Connection, date_str: str) -> dict:
    totals = get_daily_totals(conn, date_str)
    is_weights = is_weights_day(conn, date_str)
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
        # Pre-formatted so the model relays it verbatim instead of re-summing
        # protein_g/kcal itself in prose (that hand-computation was the actual
        # cause of "today's total" mismatches — see main.py's meal prompt).
        "summary_line": f"{totals['protein_g']:.0f}g protein / {totals['kcal']:.0f} kcal (target {kcal_target} kcal)",
    }


def _accumulate_turn_totals(turn_totals: dict | None, protein_g: float, kcal: float) -> dict | None:
    """Add one item's macros to the caller-supplied turn-scoped total (bound
    per incoming message via tools/registry.py, never part of the LLM-facing
    schema) and return a ready-made summary. None if no turn_totals dict was
    supplied (e.g. direct/test calls)."""
    if turn_totals is None:
        return None
    turn_totals["protein_g"] += protein_g
    turn_totals["kcal"] += kcal
    return {
        "protein_g": round(turn_totals["protein_g"], 1),
        "kcal": round(turn_totals["kcal"], 0),
        "summary_line": f"{turn_totals['protein_g']:.0f}g protein, {turn_totals['kcal']:.0f} kcal",
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


async def _lookup_with_user_override(conn: sqlite3.Connection, food_name: str, grams: float) -> dict:
    """Check Ollie's calibrated user_foods table first — it always wins over
    USDA/the hardcoded table since it's his own confirmed value. Falls
    through to services.nutrition.lookup_macros otherwise."""
    override = get_user_food(conn, food_name)
    if override:
        protein_per_100g, kcal_per_100g = override
        scale = grams / 100.0
        return {
            "description": food_name,
            "quantity_g": grams,
            "protein_g": round(protein_per_100g * scale, 1),
            "kcal": round(kcal_per_100g * scale, 0),
            "source": "user_defined",
        }
    return await lookup_macros(food_name, grams)


def _name_from_description(description: str) -> str:
    """Legacy fallback for pre-migration rows with no food_name column —
    parses the name back out of the '{grams}g {name}' display string."""
    parts = description.split(" ", 1)
    return parts[1] if len(parts) > 1 else description


def _matches_at_word_start(needle: str, description: str) -> bool:
    """True if needle occurs in description starting at a word boundary —
    unlike a plain substring test, this rejects e.g. needle='oat' matching
    inside 'goat cheese' while still matching 'oats'/'oatmeal' or a
    multi-word phrase like 'greek yoghurt'. Used for delete_food_log, where
    an unintended match is irreversible (correct_food_log's plain substring
    match is lower-stakes — it only edits a value, never removes data)."""
    return re.search(r"\b" + re.escape(needle.lower()), description.lower()) is not None


def _grams_from_description(description: str) -> float:
    """Legacy fallback for pre-migration rows with no grams column."""
    token = description.split(" ", 1)[0]
    try:
        return float(token.rstrip("g"))
    except ValueError:
        return 0.0


async def log_food(
    conn: sqlite3.Connection,
    food_name: str,
    grams: float,
    meal_slot: str | None = None,
    turn_totals: dict | None = None,
) -> dict:
    """Look up macros for a food and write it to today's log immediately (§5.1).

    turn_totals is an optional dict (protein_g/kcal keys) bound per incoming
    message by tools/registry.py — never part of the LLM-facing schema — used
    to accumulate an exact "this turn" total across multiple log_food calls
    without the model having to re-sum them itself.
    """
    try:
        slot = meal_slot or "other"
        macros = await _lookup_with_user_override(conn, food_name, grams)

        today = _today().isoformat()
        description = f"{grams:.0f}g {food_name}"
        log_id = insert_food_log(conn, FoodLog(
            date=today,
            meal_slot=slot,
            description=description,
            protein_g=macros["protein_g"],
            kcal=macros["kcal"],
            grams=grams,
            food_name=food_name,
            source=macros["source"],
        ))

        result = {
            "logged": True,
            "id": log_id,
            "food_name": food_name,
            "grams": grams,
            "protein_g": macros["protein_g"],
            "kcal": macros["kcal"],
            "source": macros["source"],
            "meal_slot": slot,
            "daily_totals": _daily_macros_dict(conn, today),
            "turn_totals": _accumulate_turn_totals(turn_totals, macros["protein_g"], macros["kcal"]),
        }
        if macros["source"] == "estimated":
            # Total miss (pre-lookup, USDA, and the fallback table all failed) —
            # logged as 0g/0kcal so it doesn't block, but flagged so the model
            # asks Ollie directly rather than letting a silent 0 sit in the log.
            result["needs_input"] = True
        return result
    except Exception as exc:
        logger.warning("log_food failed: %s", exc)
        return {"error": str(exc)}


async def set_user_food_macros(
    conn: sqlite3.Connection,
    food_name: str,
    protein_per_100g: float,
    kcal_per_100g: float,
) -> dict:
    """Store Ollie's calibrated macros for a food (checked first on every
    future lookup) and fix today's most recent matching log entry — the one
    that triggered the 'couldn't find reliable data' prompt."""
    try:
        upsert_user_food(
            conn, food_name, protein_per_100g, kcal_per_100g,
            datetime.now(tz=_TZ).isoformat(),
        )

        today = _today().isoformat()
        logs = get_food_logs_for_date(conn, today)
        needle = food_name.lower()
        target = None
        for entry in reversed(logs):
            entry_name = (entry["food_name"] or _name_from_description(entry["description"])).lower()
            if needle in entry_name or entry_name in needle:
                target = entry
                break

        if target is None:
            return {"stored": True, "food_key": needle.strip(), "updated_log": None}

        grams = target["grams"] if target["grams"] is not None else _grams_from_description(target["description"])
        scale = grams / 100.0
        new_protein = round(protein_per_100g * scale, 1)
        new_kcal = round(kcal_per_100g * scale, 0)
        update_food_log(conn, target["id"], new_protein, new_kcal, grams=grams)

        return {
            "stored": True,
            "food_key": needle.strip(),
            "updated_log": {
                "id": target["id"],
                "description": target["description"],
                "protein_g": new_protein,
                "kcal": new_kcal,
            },
            "daily_totals": _daily_macros_dict(conn, today),
        }
    except Exception as exc:
        logger.warning("set_user_food_macros failed: %s", exc)
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
        today = _today().isoformat()
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
            name_for_lookup = target["food_name"] or _name_from_description(target["description"])
            macros = await _lookup_with_user_override(conn, name_for_lookup, float(new_value))
            new_desc = f"{float(new_value):.0f}g {name_for_lookup}"
            update_food_log(
                conn, target["id"], macros["protein_g"], macros["kcal"], new_desc,
                grams=float(new_value),
            )
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


async def delete_food_log(
    conn: sqlite3.Connection,
    log_id: int | None = None,
    food_name: str = "",
) -> dict:
    """Remove one food log entry entirely — for duplicates or mistaken
    entries, never for correcting a value (use correct_food_log for that).

    Prefer log_id (already visible from a prior log_food/get_food_log/
    correct_food_log result) — it's exact and never ambiguous. If only
    food_name is given and more than one of today's entries match, this
    deliberately does NOT guess: it returns the candidates instead of
    deleting anything, since guessing wrong on a delete is unrecoverable.
    """
    try:
        today = _today().isoformat()
        logs = get_food_logs_for_date(conn, today)
        if not logs:
            return {"error": "no food logged today"}

        if log_id is not None:
            target = next((entry for entry in logs if entry["id"] == log_id), None)
            if target is None:
                return {"error": f"no entry with id {log_id} logged today"}
        elif food_name:
            matches = [entry for entry in logs if _matches_at_word_start(food_name, entry["description"])]
            if not matches:
                return {"error": f"no matching entry found for '{food_name}'"}
            if len(matches) > 1:
                return {
                    "error": f"{len(matches)} entries match '{food_name}' — specify log_id",
                    "candidates": [
                        {"id": m["id"], "description": m["description"], "protein_g": m["protein_g"], "kcal": m["kcal"]}
                        for m in matches
                    ],
                }
            target = matches[0]
        else:
            return {"error": "provide log_id or food_name to identify which entry to delete"}

        removed = {
            "id": target["id"],
            "description": target["description"],
            "protein_g": target["protein_g"],
            "kcal": target["kcal"],
        }
        db_delete_food_log(conn, target["id"])

        return {
            "deleted": True,
            "removed": removed,
            "daily_totals": _daily_macros_dict(conn, today),
        }
    except Exception as exc:
        logger.warning("delete_food_log failed: %s", exc)
        return {"error": str(exc)}


async def reset_daily_food_log(conn: sqlite3.Connection, date: str | None = None) -> dict:
    """Delete every food log entry for a date (defaults to today) — for
    'scrap today's log, I'll start again'. Irreversible; the caller must
    confirm with Ollie before invoking this."""
    try:
        d = date or _today().isoformat()
        removed_count = delete_food_logs_for_date(conn, d)
        return {
            "reset": True,
            "date": d,
            "removed_count": removed_count,
            "daily_totals": _daily_macros_dict(conn, d),
        }
    except Exception as exc:
        logger.warning("reset_daily_food_log failed: %s", exc)
        return {"error": str(exc)}


async def repeat_meal(
    conn: sqlite3.Connection,
    meal_slot: str,
    source_date: str | None = None,
    turn_totals: dict | None = None,
) -> dict:
    """Re-log a previous day's meal for a specific slot (e.g. 'same lunch as
    yesterday'). Matches strictly on meal_slot — never on recency or list
    position — so an unplanned snack logged after lunch can't get picked up
    as 'lunch' by mistake. Re-runs the nutrition lookup per item so any
    calibration since source_date is picked up.

    turn_totals — see log_food's docstring; same optional accumulator."""
    try:
        src_date = source_date or (_today() - timedelta(days=1)).isoformat()
        source_logs = get_food_logs_for_date(conn, src_date)
        items = [entry for entry in source_logs if entry["meal_slot"] == meal_slot]
        if not items:
            return {"error": f"no {meal_slot} logged on {src_date}"}

        today = _today().isoformat()
        logged_items = []
        turn_protein = 0.0
        turn_kcal = 0.0
        for item in items:
            food_name = item["food_name"] or _name_from_description(item["description"])
            grams = item["grams"] if item["grams"] is not None else _grams_from_description(item["description"])
            macros = await _lookup_with_user_override(conn, food_name, grams)
            description = f"{grams:.0f}g {food_name}"
            log_id = insert_food_log(conn, FoodLog(
                date=today,
                meal_slot=meal_slot,
                description=description,
                protein_g=macros["protein_g"],
                kcal=macros["kcal"],
                grams=grams,
                food_name=food_name,
                source=macros["source"],
            ))
            logged_items.append({
                "id": log_id,
                "food_name": food_name,
                "grams": grams,
                "protein_g": macros["protein_g"],
                "kcal": macros["kcal"],
                "source": macros["source"],
            })
            turn_protein += macros["protein_g"]
            turn_kcal += macros["kcal"]

        return {
            "logged": True,
            "meal_slot": meal_slot,
            "source_date": src_date,
            "items": logged_items,
            "daily_totals": _daily_macros_dict(conn, today),
            "turn_totals": _accumulate_turn_totals(turn_totals, turn_protein, turn_kcal),
        }
    except Exception as exc:
        logger.warning("repeat_meal failed: %s", exc)
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
        d = date or _today().isoformat()
        return _daily_macros_dict(conn, d)
    except Exception as exc:
        logger.warning("get_daily_macros failed: %s", exc)
        return {"error": str(exc)}


async def get_weekly_macro_summary(conn: sqlite3.Connection) -> dict:
    """Return this week's (Monday-based) daily macro totals and averages."""
    try:
        today = _today()
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
        today = _today()
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
            today = _today()
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

        today = _today().isoformat()
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
                "separate get_daily_macros call to report progress. If the result has "
                "needs_input=true, no reliable data was found (logged as 0g/0kcal so it "
                "doesn't block) — ask Ollie for protein/kcal per 100g and call "
                "set_user_food_macros with his answer."
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
                        "description": (
                            "Which meal this belongs to. Only set this from what Ollie actually "
                            "said or an unambiguous context clue (e.g. a direct reply to a "
                            "breakfast/lunch/dinner prompt) — never guess from time-of-day or "
                            "what was logged last. If it's genuinely unclear which meal this is, "
                            "ask rather than guess."
                        ),
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
            "name": "delete_food_log",
            "description": (
                "Remove a food entry from today's log entirely — for a duplicate "
                "logged by accident, or 'delete that', 'remove the yoghurt', 'that "
                "wasn't meant to be logged'. NEVER use this to fix a wrong value — "
                "that's correct_food_log. Prefer log_id when you have it (from a "
                "recent log_food/get_food_log/correct_food_log result) since it's "
                "exact. If you only have a name and more than one entry matches, "
                "this returns the candidates instead of deleting anything — show "
                "them to Ollie and ask which one rather than guessing."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "log_id": {
                        "type": ["integer", "null"],
                        "description": "The exact entry id to delete, if known.",
                    },
                    "food_name": {
                        "type": "string",
                        "description": "Substring to match against today's entries, only used if log_id is omitted.",
                    },
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "reset_daily_food_log",
            "description": (
                "Delete EVERY food entry for a date (defaults to today) — for "
                "'scrap today's log', 'restart today's total', 'clear everything "
                "I've logged today'. Irreversible. Confirm with Ollie first "
                "(state what will be wiped and wait for a yes), unless he's just "
                "explicitly and unambiguously asked for exactly this."
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
            "name": "set_user_food_macros",
            "description": (
                "Store Ollie's own protein/kcal-per-100g for a food and fix today's most "
                "recent matching log entry. Call this only after log_food returned "
                "needs_input=true and Ollie has answered the follow-up question with numbers "
                "— never guess these values yourself. Once stored, this food is checked "
                "before USDA on every future lookup, so it only needs calibrating once."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "food_name": {
                        "type": "string",
                        "description": "The food name, matching what was passed to log_food.",
                    },
                    "protein_per_100g": {
                        "type": "number",
                        "description": "Protein in grams per 100g, as given by Ollie.",
                    },
                    "kcal_per_100g": {
                        "type": "number",
                        "description": "Calories per 100g, as given by Ollie.",
                    },
                },
                "required": ["food_name", "protein_per_100g", "kcal_per_100g"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_food_log",
            "description": (
                "Get every food entry logged for a given date, plus that day's totals. Use "
                "this to answer 'what did I eat yesterday' or to find an entry's id for a "
                "correction. To repeat a previous day's meal (e.g. 'same lunch as "
                "yesterday'), use repeat_meal instead — don't reconstruct it by hand from "
                "this."
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
            "name": "repeat_meal",
            "description": (
                "Re-log a previous day's meal for a specific slot — use this for 'same "
                "breakfast/lunch as yesterday', 'repeat yesterday's lunch', or replies to "
                "the 'Same as yesterday?' prompts. Matches strictly on meal_slot (never on "
                "recency or list position), so an unplanned snack logged after lunch can't "
                "get picked up as 'lunch' by mistake. Re-runs the nutrition lookup per item, "
                "so it reflects any calibration since then. Returns the same itemised "
                "breakdown as log_food."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "meal_slot": {
                        "type": "string",
                        "enum": ["breakfast", "snack", "lunch", "shake", "dinner", "alcohol", "other"],
                        "description": "Exact slot to repeat, e.g. 'lunch' for 'same lunch'.",
                    },
                    "source_date": {
                        "type": ["string", "null"],
                        "description": "Date to copy from, YYYY-MM-DD. Omit or null for yesterday.",
                    },
                },
                "required": ["meal_slot"],
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
