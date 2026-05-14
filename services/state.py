"""Short-lived per-user state for pending confirmations (in-memory, not persisted)."""

from __future__ import annotations

_pending: dict[int, dict] = {}


def get(user_id: int) -> dict | None:
    return _pending.get(user_id)


def set_state(user_id: int, data: dict) -> None:
    _pending[user_id] = data


def clear(user_id: int) -> None:
    _pending.pop(user_id, None)


def has(user_id: int) -> bool:
    return user_id in _pending
