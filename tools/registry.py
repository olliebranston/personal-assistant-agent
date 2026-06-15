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

from tools import gym

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
    schemas = [*gym.TOOL_SCHEMAS]

    dispatch: dict[str, ToolFunc] = {
        "log_exercise": functools.partial(gym.log_exercise, conn),
        "get_last_session": functools.partial(gym.get_last_session, conn),
        "get_exercise_history": functools.partial(gym.get_exercise_history, conn),
        "get_next_session_type": functools.partial(gym.get_next_session_type, conn),
        "get_session_plan": functools.partial(gym.get_session_plan, conn),
        "get_weekly_gym_summary": functools.partial(gym.get_weekly_gym_summary, conn),
    }

    return ToolRegistry(schemas=schemas, dispatch=dispatch)
