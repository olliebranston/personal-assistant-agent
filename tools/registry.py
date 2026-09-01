"""Tool registry — aggregates tool schemas and dispatches tool calls (§4.3 of TOOL_CALLING_DESIGN.md).

Each of tools/gym.py, tools/meal.py, tools/calendar.py, tools/news.py,
tools/reminders.py, tools/briefing.py contributes a TOOL_SCHEMAS list and
named (conn, **kwargs) -> dict implementations, merged in here domain by
domain as Phase 2 progresses.

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

from tools import briefing, calendar, fpl, gym, meal, news, reminders

logger = logging.getLogger(__name__)

ToolFunc = Callable[..., Awaitable[dict]]


@dataclass
class ToolRegistry:
    schemas: list[dict]
    dispatch: dict[str, ToolFunc] = field(default_factory=dict)

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
        return await func(**args)


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
    ]

    # Turn-scoped, never part of the LLM-facing schema — accumulates the
    # "this turn" macro total across possibly-multiple log_food/repeat_meal
    # calls within one incoming message so the model can relay it verbatim
    # instead of re-summing tool results itself (see tools/meal.py).
    turn_totals: dict[str, float] = {"protein_g": 0.0, "kcal": 0.0}

    dispatch: dict[str, ToolFunc] = {
        "log_exercises": functools.partial(gym.log_exercises, conn),
        "get_last_session": functools.partial(gym.get_last_session, conn),
        "get_exercise_history": functools.partial(gym.get_exercise_history, conn),
        "get_exercise_progression": functools.partial(gym.get_exercise_progression, conn),
        "get_next_session_type": functools.partial(gym.get_next_session_type, conn),
        "get_session_plan": functools.partial(gym.get_session_plan, conn),
        "get_weekly_gym_summary": functools.partial(gym.get_weekly_gym_summary, conn),
        "log_food": functools.partial(meal.log_food, conn, turn_totals=turn_totals),
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
        "get_fpl_chips": functools.partial(fpl.get_fpl_chips, conn),
        "get_fpl_calendar": functools.partial(fpl.get_fpl_calendar, conn),
        "get_fpl_recommendation": functools.partial(fpl.get_fpl_recommendation, conn),
        "fpl_acknowledge": functools.partial(fpl.fpl_acknowledge, conn),
    }

    return ToolRegistry(schemas=schemas, dispatch=dispatch)
