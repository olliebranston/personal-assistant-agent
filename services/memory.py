"""Rolling 10-message conversation memory per user — in-memory, not persisted across restarts."""

from __future__ import annotations

from collections import deque

# 5 full exchanges — §3.1 of TOOL_CALLING_DESIGN.md. Each exchange is a user
# and an assistant message, plus a system note when the turn logged
# something (main._handle_tool_calling), so up to 3 entries per exchange.
_MAX_MESSAGES = 15
_store: dict[int, deque[dict]] = {}


def get(user_id: int) -> list[dict]:
    """Return conversation history as a list of {role, content} dicts, oldest first."""
    return list(_store.get(user_id, []))


def add(user_id: int, role: str, content: str) -> None:
    """Append a message and trim to the rolling window."""
    if user_id not in _store:
        _store[user_id] = deque(maxlen=_MAX_MESSAGES)
    _store[user_id].append({"role": role, "content": content})
