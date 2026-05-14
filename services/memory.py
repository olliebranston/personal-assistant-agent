"""Rolling 6-message conversation memory per user — in-memory, not persisted across restarts."""

from __future__ import annotations

from collections import deque

_MAX_MESSAGES = 6  # 3 full exchanges (user + assistant)
_store: dict[int, deque[dict]] = {}


def get(user_id: int) -> list[dict]:
    """Return conversation history as a list of {role, content} dicts, oldest first."""
    return list(_store.get(user_id, []))


def add(user_id: int, role: str, content: str) -> None:
    """Append a message and trim to the rolling window."""
    if user_id not in _store:
        _store[user_id] = deque(maxlen=_MAX_MESSAGES)
    _store[user_id].append({"role": role, "content": content})


def clear(user_id: int) -> None:
    _store.pop(user_id, None)
