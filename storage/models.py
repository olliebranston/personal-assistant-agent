"""Dataclasses and CRUD for all SQLite tables.

Gym tables:  GymSession, ExerciseSet
Meal tables: FoodLog
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from typing import Optional

# ---------------------------------------------------------------------------
# DDL — imported by storage.db.init_db to create tables on first startup
# ---------------------------------------------------------------------------

GYM_SESSION_DDL = """
CREATE TABLE IF NOT EXISTS gym_sessions (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    date          TEXT    NOT NULL,   -- ISO format YYYY-MM-DD
    session_type  TEXT    NOT NULL,   -- push | pull | legs | short
    notes         TEXT    DEFAULT ''
)
"""

EXERCISE_SET_DDL = """
CREATE TABLE IF NOT EXISTS exercise_sets (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id  INTEGER NOT NULL REFERENCES gym_sessions(id),
    exercise    TEXT    NOT NULL,
    weight_kg   REAL,                 -- working weight; NULL for bodyweight exercises
    warmup_kg   REAL,                 -- s[X] starting weight from training log notation
    sets        INTEGER NOT NULL,
    reps        INTEGER NOT NULL,     -- target reps per set
    notes       TEXT    DEFAULT ''    -- form cues, drop sets, missed reps, etc.
)
"""

# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------

@dataclass
class GymSession:
    date: str           # YYYY-MM-DD
    session_type: str   # push | pull | legs | short
    notes: str = ""
    id: Optional[int] = field(default=None)


@dataclass
class ExerciseSet:
    session_id: int
    exercise: str
    sets: int
    reps: int
    weight_kg: Optional[float] = None   # None for bodyweight
    warmup_kg: Optional[float] = None   # None if no warm-up recorded
    notes: str = ""
    id: Optional[int] = field(default=None)

# ---------------------------------------------------------------------------
# Gym CRUD
# ---------------------------------------------------------------------------

def insert_session(conn: sqlite3.Connection, session: GymSession) -> int:
    """Insert a gym session row and return its new id.

    The returned id is required immediately: all ExerciseSets for this session
    must reference it via session_id.
    """
    cur = conn.execute(
        "INSERT INTO gym_sessions (date, session_type, notes) VALUES (?, ?, ?)",
        (session.date, session.session_type, session.notes),
    )
    conn.commit()
    return cur.lastrowid


def insert_set(conn: sqlite3.Connection, ex: ExerciseSet) -> int:
    """Insert one logged exercise set and return its new id."""
    cur = conn.execute(
        """INSERT INTO exercise_sets
               (session_id, exercise, weight_kg, warmup_kg, sets, reps, notes)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        (ex.session_id, ex.exercise, ex.weight_kg, ex.warmup_kg,
         ex.sets, ex.reps, ex.notes),
    )
    conn.commit()
    return cur.lastrowid


def get_last_sets_for_exercise(
    conn: sqlite3.Connection,
    exercise: str,
    limit: int = 5,
) -> list[dict]:
    """Return the most recent logged sets for a named exercise, newest first.

    Used by the gym agent for progressive overload lookups — e.g. "what did I
    do last time on bench?" The case-insensitive match handles "Bench Press"
    vs "bench press" from free-text input.
    """
    rows = conn.execute(
        """SELECT es.*, gs.date, gs.session_type
             FROM exercise_sets es
             JOIN gym_sessions gs ON es.session_id = gs.id
            WHERE lower(es.exercise) = lower(?)
            ORDER BY gs.date DESC, es.id DESC
            LIMIT ?""",
        (exercise, limit),
    ).fetchall()
    return [dict(r) for r in rows]


def get_recent_sessions(
    conn: sqlite3.Connection,
    limit: int = 10,
) -> list[dict]:
    """Return the last N gym sessions with their exercise sets nested inside.

    Each returned dict has a 'sets' key containing a list of exercise set dicts.
    Used for weekly summaries and to determine which session type is due next.
    """
    sessions = conn.execute(
        "SELECT * FROM gym_sessions ORDER BY date DESC, id DESC LIMIT ?",
        (limit,),
    ).fetchall()

    result = []
    for s in sessions:
        s_dict = dict(s)
        s_dict["sets"] = [
            dict(r) for r in conn.execute(
                "SELECT * FROM exercise_sets WHERE session_id = ?",
                (s_dict["id"],),
            ).fetchall()
        ]
        result.append(s_dict)
    return result


# ---------------------------------------------------------------------------
# Meal DDL
# ---------------------------------------------------------------------------

FOOD_LOG_DDL = """
CREATE TABLE IF NOT EXISTS food_logs (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    date        TEXT    NOT NULL,   -- YYYY-MM-DD
    meal_slot   TEXT    NOT NULL,   -- breakfast|snack|lunch|shake|dinner|alcohol|other
    description TEXT    NOT NULL,   -- what the user logged (display only — see grams/food_name)
    protein_g   REAL    NOT NULL,
    kcal        REAL    NOT NULL,
    source      TEXT    DEFAULT 'usda',  -- usda | reference | user_defined | estimated
    grams       REAL,                    -- structured quantity; NULL only on pre-migration rows
    food_name   TEXT                     -- structured food name; NULL only on pre-migration rows
)
"""

# New databases get grams/food_name from FOOD_LOG_DDL above. Existing
# assistant.db files predate those columns — there's no migration framework
# in this project, so this idempotent ALTER TABLE (called from
# storage.db.init_db) brings them up to date in place. Pre-migration rows
# keep grams/food_name as NULL; nothing backfills them since old entries are
# never recomputed in practice.
FOOD_LOG_MIGRATIONS = (
    "ALTER TABLE food_logs ADD COLUMN grams REAL",
    "ALTER TABLE food_logs ADD COLUMN food_name TEXT",
)


def migrate_food_logs(conn: sqlite3.Connection) -> None:
    """Idempotent: add grams/food_name to an existing food_logs table if missing."""
    for ddl in FOOD_LOG_MIGRATIONS:
        try:
            conn.execute(ddl)
        except sqlite3.OperationalError:
            pass  # column already exists
    conn.commit()

# ---------------------------------------------------------------------------
# Meal dataclasses
# ---------------------------------------------------------------------------

@dataclass
class FoodLog:
    date: str        # YYYY-MM-DD
    meal_slot: str   # breakfast|snack|lunch|shake|dinner|alcohol|other
    description: str
    protein_g: float
    kcal: float
    grams: float      # structured quantity — see migrate_food_logs for why this exists
    food_name: str    # structured food name — see migrate_food_logs for why this exists
    source: str = "usda"
    id: Optional[int] = field(default=None)

# ---------------------------------------------------------------------------
# Meal CRUD
# ---------------------------------------------------------------------------

def insert_food_log(conn: sqlite3.Connection, log: FoodLog) -> int:
    """Insert a food log entry and return its new id."""
    cur = conn.execute(
        """INSERT INTO food_logs (date, meal_slot, description, protein_g, kcal, source, grams, food_name)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        (log.date, log.meal_slot, log.description, log.protein_g, log.kcal,
         log.source, log.grams, log.food_name),
    )
    conn.commit()
    return cur.lastrowid


def get_food_logs_for_date(conn: sqlite3.Connection, date: str) -> list[dict]:
    """Return all food log entries for a given date, ordered by insertion time."""
    rows = conn.execute(
        "SELECT * FROM food_logs WHERE date = ? ORDER BY id",
        (date,),
    ).fetchall()
    return [dict(r) for r in rows]


def get_daily_totals(conn: sqlite3.Connection, date: str) -> dict:
    """Return summed protein_g and kcal for a given date.

    Returns {"protein_g": 0.0, "kcal": 0.0} if nothing logged yet — safe to
    call at any point in the day.
    """
    row = conn.execute(
        """SELECT COALESCE(SUM(protein_g), 0.0) AS protein_g,
                  COALESCE(SUM(kcal), 0.0)      AS kcal
             FROM food_logs
            WHERE date = ?""",
        (date,),
    ).fetchone()
    return dict(row) if row else {"protein_g": 0.0, "kcal": 0.0}


def update_food_log(
    conn: sqlite3.Connection,
    log_id: int,
    protein_g: float,
    kcal: float,
    description: str | None = None,
    grams: float | None = None,
) -> None:
    """Update protein/kcal (and optionally description/grams) on an existing food log entry."""
    fields = ["protein_g = ?", "kcal = ?"]
    params: list = [protein_g, kcal]
    if description is not None:
        fields.append("description = ?")
        params.append(description)
    if grams is not None:
        fields.append("grams = ?")
        params.append(grams)
    params.append(log_id)
    conn.execute(f"UPDATE food_logs SET {', '.join(fields)} WHERE id = ?", params)
    conn.commit()


# ---------------------------------------------------------------------------
# User-calibrated food DDL + CRUD
# ---------------------------------------------------------------------------
#
# Checked before USDA on every lookup (tools/meal.py:_lookup_with_user_override).
# Populated only via set_user_food_macros, when Ollie answers "couldn't find
# reliable data for X — what's the protein/kcal per 100g?" — once calibrated,
# a food stays correct forever instead of repeatedly hitting USDA or a stale
# hardcoded table.

USER_FOOD_DDL = """
CREATE TABLE IF NOT EXISTS user_foods (
    food_key         TEXT PRIMARY KEY,  -- normalised (lower/stripped) food description
    protein_per_100g REAL NOT NULL,
    kcal_per_100g    REAL NOT NULL,
    created_at       TEXT NOT NULL
)
"""


def _normalize_food_key(query: str) -> str:
    return query.lower().strip()


def upsert_user_food(
    conn: sqlite3.Connection,
    food_key: str,
    protein_per_100g: float,
    kcal_per_100g: float,
    created_at: str,
) -> None:
    """Insert or replace Ollie's calibrated macros for a food."""
    conn.execute(
        """INSERT INTO user_foods (food_key, protein_per_100g, kcal_per_100g, created_at)
           VALUES (?, ?, ?, ?)
           ON CONFLICT(food_key) DO UPDATE SET
               protein_per_100g = excluded.protein_per_100g,
               kcal_per_100g = excluded.kcal_per_100g,
               created_at = excluded.created_at""",
        (_normalize_food_key(food_key), protein_per_100g, kcal_per_100g, created_at),
    )
    conn.commit()


def get_user_food(conn: sqlite3.Connection, query: str) -> tuple[float, float] | None:
    """Return (protein_per_100g, kcal_per_100g) for a substring match against
    Ollie's calibrated foods, or None. Same substring-match style as
    services.nutrition._fallback_lookup."""
    q = _normalize_food_key(query)
    rows = conn.execute("SELECT food_key, protein_per_100g, kcal_per_100g FROM user_foods").fetchall()
    for row in rows:
        key = row["food_key"]
        if key in q or q in key:
            return row["protein_per_100g"], row["kcal_per_100g"]
    return None


# ---------------------------------------------------------------------------
# Weight tracking DDL + CRUD
# ---------------------------------------------------------------------------

WEIGHT_LOG_DDL = """
CREATE TABLE IF NOT EXISTS weight_logs (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    date        TEXT    NOT NULL,   -- YYYY-MM-DD
    weight_kg   REAL    NOT NULL
)
"""

# ---------------------------------------------------------------------------
# Meal plan DDL + CRUD
# ---------------------------------------------------------------------------

MEAL_PLAN_DDL = """
CREATE TABLE IF NOT EXISTS meal_plans (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    week_start    TEXT    NOT NULL,   -- YYYY-MM-DD (Monday of the week)
    slot          TEXT    NOT NULL,   -- e.g. 'mon_lunch', 'fri_dinner', 'sat_dinner'
    recipe_slug   TEXT    NOT NULL,
    confirmed     INTEGER DEFAULT 0
)
"""


def log_weight(conn: sqlite3.Connection, date: str, weight_kg: float) -> int:
    """Insert or replace today's weight log. Returns new row id."""
    conn.execute("DELETE FROM weight_logs WHERE date = ?", (date,))
    cur = conn.execute(
        "INSERT INTO weight_logs (date, weight_kg) VALUES (?, ?)",
        (date, weight_kg),
    )
    conn.commit()
    return cur.lastrowid


def get_weight_history(conn: sqlite3.Connection, limit: int = 10) -> list[dict]:
    """Return the last N weight log entries, newest first."""
    rows = conn.execute(
        "SELECT * FROM weight_logs ORDER BY date DESC LIMIT ?",
        (limit,),
    ).fetchall()
    return [dict(r) for r in rows]


def get_latest_weight(conn: sqlite3.Connection) -> dict | None:
    """Return the most recent weight log entry, or None if none exist."""
    row = conn.execute(
        "SELECT * FROM weight_logs ORDER BY date DESC LIMIT 1",
    ).fetchone()
    return dict(row) if row else None


def insert_meal_plan(conn: sqlite3.Connection, week_start: str, slot: str, recipe_slug: str) -> int:
    """Insert one slot of the meal plan. Returns new row id."""
    cur = conn.execute(
        "INSERT INTO meal_plans (week_start, slot, recipe_slug) VALUES (?, ?, ?)",
        (week_start, slot, recipe_slug),
    )
    conn.commit()
    return cur.lastrowid


def get_meal_plan(conn: sqlite3.Connection, week_start: str) -> list[dict]:
    """Return all slots for a given week, ordered by slot name."""
    rows = conn.execute(
        "SELECT * FROM meal_plans WHERE week_start = ? ORDER BY slot",
        (week_start,),
    ).fetchall()
    return [dict(r) for r in rows]


def get_recent_recipe_slugs(conn: sqlite3.Connection, weeks: int = 2) -> list[str]:
    """Return recipe slugs used in the last N weeks — used to avoid repetition."""
    rows = conn.execute(
        """SELECT DISTINCT recipe_slug FROM meal_plans
           ORDER BY week_start DESC LIMIT ?""",
        (weeks * 10,),
    ).fetchall()
    return [r["recipe_slug"] for r in rows]


def get_week_logs(conn: sqlite3.Connection, start_date: str, end_date: str) -> list[dict]:
    """Return daily totals for each day in the range [start_date, end_date].

    Used by the Friday week summary. Days with no entries are omitted.
    """
    rows = conn.execute(
        """SELECT date,
                  SUM(protein_g) AS protein_g,
                  SUM(kcal)      AS kcal,
                  COUNT(*)       AS entries
             FROM food_logs
            WHERE date BETWEEN ? AND ?
            GROUP BY date
            ORDER BY date""",
        (start_date, end_date),
    ).fetchall()
    return [dict(r) for r in rows]
