"""SQLite connection management and schema initialisation. Call init_db() once at startup."""

import sqlite3
from pathlib import Path

from storage.models import (
    EXERCISE_SET_DDL,
    FOOD_LOG_DDL,
    GYM_SESSION_DDL,
    MEAL_PLAN_DDL,
    WEIGHT_LOG_DDL,
)

DB_PATH = Path("assistant.db")


def get_connection() -> sqlite3.Connection:
    """Open (or reuse) the SQLite file and return a connection.

    row_factory=sqlite3.Row means every fetched row supports both dict-style
    (row["column"]) and index-style access. PRAGMA foreign_keys enforces the
    session_id FK in exercise_sets at the database level.
    """
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db() -> None:
    """Create all tables if they don't already exist. Safe to call every startup."""
    conn = get_connection()
    conn.execute(GYM_SESSION_DDL)
    conn.execute(EXERCISE_SET_DDL)
    conn.execute(FOOD_LOG_DDL)
    conn.execute(WEIGHT_LOG_DDL)
    conn.execute(MEAL_PLAN_DDL)
    conn.commit()
    conn.close()
