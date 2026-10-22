"""Tool registry — aggregates tool schemas and dispatches tool calls (§4.3 of TOOL_CALLING_DESIGN.md).

Each of tools/gym.py, tools/meal.py, tools/calendar.py, tools/news.py,
tools/reminders.py, tools/briefing.py, tools/running.py contributes a
TOOL_SCHEMAS list and named (conn, **kwargs) -> dict implementations, merged
in here domain by domain as Phase 2 progresses.

create_reminder is the one tool needing context.job_queue/chat_id (§2.5) —
build_tool_registry's telegram_context/chat_id params exist now so call
sites are already correct; a later phase will use them to bind that tool.
"""

from __future__ import annotations

import functools
import logging
import sqlite3
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field

from tools import briefing, calendar, fpl, gym, meal, news, reminders, running

logger = logging.getLogger(__name__)

ToolFunc = Callable[..., Awaitable[dict]]


def _kg(weight) -> str:
    return f" @{weight:g}kg" if weight is not None else ""


def _summarise_log_exercises(args: dict, result: dict) -> str | None:
    parts = []
    if result.get("exercises"):
        parts.append("logged " + ", ".join(
            f"{e['exercise']} {e['sets']}x{e['reps']}{_kg(e['weight_kg'])} (#{e['log_id']})"
            for e in result["exercises"]
        ))
    if result.get("skipped_duplicates"):
        parts.append("not re-logged, already logged today: " + ", ".join(
            e["exercise"] for e in result["skipped_duplicates"]
        ))
    if result.get("needs_confirmation"):
        parts.append("NOT logged, asked Ollie if correction or extra: " + ", ".join(
            f"{e['exercise']} (already #{e['already_logged']['log_id']})" for e in result["needs_confirmation"]
        ))
    return "; ".join(parts) or None


def _summarise_set_change(verb: str, key: str):
    def summarise(args: dict, result: dict) -> str:
        e = result[key]
        return f"{verb} exercise #{e['log_id']}: {e['exercise']} {e['sets']}x{e['reps']}{_kg(e['weight_kg'])}"
    return summarise


# Tools that change something, with how to describe a successful call in the
# per-turn note main.py keeps in conversation memory. Memory otherwise holds
# only text, so on the next message the model couldn't tell what it had
# already logged — and sometimes logged it again. Read-only tools are never
# recorded. A summariser returning None (nothing actually changed) records
# nothing; tools without one get a generic "tool_name(args)" note.
_WRITE_SUMMARIES: dict[str, Callable[[dict, dict], str | None] | None] = {
    "log_exercises": _summarise_log_exercises,
    "correct_exercise_log": _summarise_set_change("corrected", "after"),
    "delete_exercise_log": _summarise_set_change("deleted", "removed"),
    "log_food": lambda a, r: (
        f"logged food #{r['id']}: {r['grams']:g}g {r['food_name']}" if r.get("logged")
        else f"NOT logged, already in that slot today: #{r['existing']['id']} {r['existing']['description']}"
        if r.get("possible_duplicate") else None
    ),
    "repeat_meal": lambda a, r: (
        f"logged {r['meal_slot']} again from {r['source_date']}: "
        + ", ".join(f"#{i['id']} {i['grams']:g}g {i['food_name']}" for i in r.get("items", []))
    ) if r.get("logged") else None,
    "correct_food_log": lambda a, r: f"corrected food #{r['after']['id']}: {r['after']['description']}" if r.get("updated") else None,
    "delete_food_log": lambda a, r: f"deleted food #{r['removed']['id']}" if r.get("deleted") else None,
    "reset_daily_food_log": None,
    "set_user_food_macros": None,
    "log_run": None,
    "log_weight": None,
    "create_calendar_event": None,
    "create_reminder": None,
    "fpl_acknowledge": None,
}


@dataclass
class ToolRegistry:
    schemas: list[dict]
    dispatch: dict[str, ToolFunc] = field(default_factory=dict)
    # One line per successful write this turn — main.py stores them in memory.
    actions: list[str] = field(default_factory=list)

    async def execute(self, tool_name: str, args: dict) -> dict:
        """Run the named tool with args, per the §4.2 error convention.

        Unknown tool names return {"error": ...} rather than raising —
        services.openrouter.complete's caller (the model) sees this in the
        role:"tool" message and can recover.
        """
        func = self.dispatch.get(tool_name)
        if func is None:
            logger.warning("Tool registry: unknown tool '%s'", tool_name)
            return {"error": f"unknown tool: {tool_name}"}
        result = await func(**args)
        self._record(tool_name, args, result)
        return result

    def _record(self, tool_name: str, args: dict, result) -> None:
        """Note a successful write for conversation memory. Never raises — a
        summary is a convenience, it must not break the tool call itself."""
        if tool_name not in _WRITE_SUMMARIES or not isinstance(result, dict):
            return
        if "error" in result or "candidates" in result:
            return  # nothing was changed
        try:
            summarise = _WRITE_SUMMARIES[tool_name]
            if summarise is None:
                compact = ", ".join(f"{k}={v}" for k, v in args.items())
                note = f"{tool_name}({compact})"[:160]
            else:
                note = summarise(args, result)
            if note:
                self.actions.append(note)
        except Exception as exc:
            logger.debug("Tool registry: couldn't summarise %s: %s", tool_name, exc)


def build_tool_registry(
    conn: sqlite3.Connection,
    telegram_context=None,
    chat_id: int | None = None,
) -> ToolRegistry:
    """Build a per-request tool registry bound to this connection/context."""
    schemas = [
        *gym.TOOL_SCHEMAS,
        *meal.TOOL_SCHEMAS,
        *calendar.TOOL_SCHEMAS,
        *news.TOOL_SCHEMAS,
        *briefing.TOOL_SCHEMAS,
        *reminders.TOOL_SCHEMAS,
        *fpl.TOOL_SCHEMAS,
        *running.TOOL_SCHEMAS,
    ]

    # Turn-scoped, never part of the LLM-facing schema — accumulates the
    # "this turn" macro total across possibly-multiple log_food/repeat_meal
    # calls within one incoming message so the model can relay it verbatim
    # instead of re-summing tool results itself (see tools/meal.py).
    turn_totals: dict[str, float] = {"protein_g": 0.0, "kcal": 0.0}
    # Also turn-scoped: food ids logged during this message, so log_food's
    # duplicate guard only holds back items logged on an *earlier* message.
    turn_logged_ids: set[int] = set()

    dispatch: dict[str, ToolFunc] = {
        "log_exercises": functools.partial(gym.log_exercises, conn),
        "correct_exercise_log": functools.partial(gym.correct_exercise_log, conn),
        "delete_exercise_log": functools.partial(gym.delete_exercise_log, conn),
        "get_last_session": functools.partial(gym.get_last_session, conn),
        "get_exercise_history": functools.partial(gym.get_exercise_history, conn),
        "get_exercise_progression": functools.partial(gym.get_exercise_progression, conn),
        "get_next_session_type": functools.partial(gym.get_next_session_type, conn),
        "get_session_plan": functools.partial(gym.get_session_plan, conn),
        "get_weekly_gym_summary": functools.partial(gym.get_weekly_gym_summary, conn),
        "log_run": functools.partial(running.log_run, conn),
        "get_recent_runs": functools.partial(running.get_recent_runs_tool, conn),
        "get_weekly_running_summary": functools.partial(running.get_weekly_running_summary, conn),
        "log_food": functools.partial(
            meal.log_food, conn, turn_totals=turn_totals, turn_logged_ids=turn_logged_ids
        ),
        "repeat_meal": functools.partial(meal.repeat_meal, conn, turn_totals=turn_totals),
        "correct_food_log": functools.partial(meal.correct_food_log, conn),
        "delete_food_log": functools.partial(meal.delete_food_log, conn),
        "reset_daily_food_log": functools.partial(meal.reset_daily_food_log, conn),
        "set_user_food_macros": functools.partial(meal.set_user_food_macros, conn),
        "get_food_log": functools.partial(meal.get_food_log, conn),
        "get_daily_macros": functools.partial(meal.get_daily_macros, conn),
        "get_weekly_macro_summary": functools.partial(meal.get_weekly_macro_summary, conn),
        "get_recipe": functools.partial(meal.get_recipe, conn),
        "suggest_meal": functools.partial(meal.suggest_meal, conn),
        "generate_meal_plan": functools.partial(meal.generate_meal_plan, conn),
        "log_weight": functools.partial(meal.log_weight, conn),
        "get_weight_trend": functools.partial(meal.get_weight_trend, conn),
        "get_calendar_events": functools.partial(calendar.get_calendar_events, conn),
        "create_calendar_event": functools.partial(calendar.create_calendar_event, conn),
        "get_news": functools.partial(news.get_news, conn),
        "get_morning_briefing_data": functools.partial(briefing.get_morning_briefing_data, conn),
        "create_reminder": functools.partial(
            reminders.create_reminder, conn, telegram_context, chat_id
        ),
        "get_fpl_squad": functools.partial(fpl.get_fpl_squad, conn),
        "get_fpl_team": functools.partial(fpl.get_fpl_team, conn),
        "get_fpl_league": functools.partial(fpl.get_fpl_league, conn),
        "get_fpl_gw_review": functools.partial(fpl.get_fpl_gw_review, conn),
        "get_fpl_league_analysis": functools.partial(fpl.build_pre_deadline_facts, conn),
        "get_fpl_model_accuracy": functools.partial(fpl.get_fpl_model_accuracy, conn),
        "get_fpl_chips": functools.partial(fpl.get_fpl_chips, conn),
        "get_fpl_calendar": functools.partial(fpl.get_fpl_calendar, conn),
        "get_fpl_recommendation": functools.partial(fpl.get_fpl_recommendation, conn),
        "fpl_acknowledge": functools.partial(fpl.fpl_acknowledge, conn),
    }

    return ToolRegistry(schemas=schemas, dispatch=dispatch)
