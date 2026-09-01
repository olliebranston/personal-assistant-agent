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


def get_distinct_exercise_names(conn: sqlite3.Connection) -> list[str]:
    """Return every distinct exercise name ever logged, exactly as stored.

    Used to build a name-matching vocabulary for exercise-name normalization
    (tools/gym.py) — real logged history takes priority over the static
    session-plan vocabulary since it's what a lookup actually needs to match.
    """
    rows = conn.execute("SELECT DISTINCT exercise FROM exercise_sets").fetchall()
    return [r["exercise"] for r in rows]


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


def delete_food_log(conn: sqlite3.Connection, log_id: int) -> bool:
    """Delete one food log entry by id. Returns True if a row was removed."""
    cur = conn.execute("DELETE FROM food_logs WHERE id = ?", (log_id,))
    conn.commit()
    return cur.rowcount > 0


def delete_food_logs_for_date(conn: sqlite3.Connection, date: str) -> int:
    """Delete every food log entry for a date. Returns the number of rows removed."""
    cur = conn.execute("DELETE FROM food_logs WHERE date = ?", (date,))
    conn.commit()
    return cur.rowcount


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


# ---------------------------------------------------------------------------
# FPL DDL — Phase 1 adherence layer (PHASE1-BRIEF.md §2)
# ---------------------------------------------------------------------------

GAMEWEEK_DDL = """
CREATE TABLE IF NOT EXISTS gameweeks (
    gw              INTEGER PRIMARY KEY,
    deadline_utc    TEXT NOT NULL,
    is_current      INTEGER DEFAULT 0,
    is_next         INTEGER DEFAULT 0,
    finished        INTEGER DEFAULT 0,
    data_checked    INTEGER DEFAULT 0
)
"""

MY_PICKS_DDL = """
CREATE TABLE IF NOT EXISTS my_picks (
    gw              INTEGER,
    element_id      INTEGER,
    position        INTEGER,
    is_captain      INTEGER,
    is_vice         INTEGER,
    multiplier      INTEGER,
    PRIMARY KEY (gw, element_id)
)
"""

MY_HISTORY_DDL = """
CREATE TABLE IF NOT EXISTS my_history (
    gw              INTEGER PRIMARY KEY,
    points          INTEGER,
    total_points    INTEGER,
    overall_rank    INTEGER,
    bank            INTEGER,
    team_value      INTEGER,
    transfers       INTEGER,
    transfer_cost   INTEGER,
    chip            TEXT,
    points_on_bench INTEGER
)
"""

RIVALS_DDL = """
CREATE TABLE IF NOT EXISTS rivals (
    entry_id        INTEGER PRIMARY KEY,
    entry_name      TEXT,
    player_name     TEXT,
    started_event   INTEGER,
    active          INTEGER NOT NULL DEFAULT 1
)
"""

RIVAL_PICKS_DDL = """
CREATE TABLE IF NOT EXISTS rival_picks (
    gw              INTEGER NOT NULL,
    entry_id        INTEGER NOT NULL,
    element_id      INTEGER NOT NULL,
    multiplier      INTEGER NOT NULL,
    PRIMARY KEY (gw, entry_id, element_id)
)
"""

RIVAL_HISTORY_DDL = """
CREATE TABLE IF NOT EXISTS rival_history (
    gw              INTEGER NOT NULL,
    entry_id        INTEGER NOT NULL,
    points          INTEGER,
    total_points    INTEGER,
    rank            INTEGER,
    chip            TEXT,
    points_on_bench INTEGER,
    PRIMARY KEY (gw, entry_id)
)
"""

# Existing assistant.db files predate points_on_bench on my_history (added
# for PHASE3-ADDENDUM.md §B's captain/bench/squad decomposition) — same
# idempotent-ALTER pattern as FOOD_LOG_MIGRATIONS above.
MY_HISTORY_MIGRATIONS = (
    "ALTER TABLE my_history ADD COLUMN points_on_bench INTEGER",
)


def migrate_my_history(conn: sqlite3.Connection) -> None:
    """Idempotent: add points_on_bench to an existing my_history table if missing."""
    for ddl in MY_HISTORY_MIGRATIONS:
        try:
            conn.execute(ddl)
        except sqlite3.OperationalError:
            pass  # column already exists
    conn.commit()

PLAYER_SNAPSHOT_DDL = """
CREATE TABLE IF NOT EXISTS player_snapshots (
    taken_at        TEXT,
    element_id      INTEGER,
    now_cost        INTEGER,
    status          TEXT,
    chance_next     INTEGER,
    news            TEXT,
    PRIMARY KEY (taken_at, element_id)
)
"""

NOTIFICATIONS_SENT_DDL = """
CREATE TABLE IF NOT EXISTS notifications_sent (
    gw              INTEGER,
    kind            TEXT,
    sent_at         TEXT,
    PRIMARY KEY (gw, kind)
)
"""

ACKNOWLEDGEMENTS_DDL = """
CREATE TABLE IF NOT EXISTS acknowledgements (
    gw              INTEGER PRIMARY KEY,
    acked_at        TEXT
)
"""


# ---------------------------------------------------------------------------
# FPL CRUD
# ---------------------------------------------------------------------------


def upsert_gameweeks(conn: sqlite3.Connection, gameweeks: list[dict]) -> None:
    """Replace the gameweeks table with fresh rows from bootstrap-static.

    Each dict: {gw, deadline_utc, is_current, is_next, finished, data_checked}.
    """
    conn.executemany(
        """INSERT INTO gameweeks (gw, deadline_utc, is_current, is_next, finished, data_checked)
           VALUES (:gw, :deadline_utc, :is_current, :is_next, :finished, :data_checked)
           ON CONFLICT(gw) DO UPDATE SET
               deadline_utc = excluded.deadline_utc,
               is_current   = excluded.is_current,
               is_next      = excluded.is_next,
               finished     = excluded.finished,
               data_checked = excluded.data_checked""",
        gameweeks,
    )
    conn.commit()


def get_gameweek(conn: sqlite3.Connection, gw: int) -> dict | None:
    row = conn.execute("SELECT * FROM gameweeks WHERE gw = ?", (gw,)).fetchone()
    return dict(row) if row else None


def get_all_gameweeks(conn: sqlite3.Connection) -> list[dict]:
    rows = conn.execute("SELECT * FROM gameweeks ORDER BY gw").fetchall()
    return [dict(r) for r in rows]


def get_next_gameweek(conn: sqlite3.Connection, now_utc_iso: str) -> dict | None:
    """Return the gameweek with the nearest upcoming deadline, or None if the season's over."""
    row = conn.execute(
        "SELECT * FROM gameweeks WHERE deadline_utc > ? ORDER BY deadline_utc ASC LIMIT 1",
        (now_utc_iso,),
    ).fetchone()
    return dict(row) if row else None


def get_current_gameweek(conn: sqlite3.Connection) -> dict | None:
    row = conn.execute("SELECT * FROM gameweeks WHERE is_current = 1").fetchone()
    return dict(row) if row else None


def get_unreviewed_finished_gameweeks(conn: sqlite3.Connection) -> list[dict]:
    """Gameweeks where data_checked is true but we haven't sent a 'review' notification yet."""
    rows = conn.execute(
        """SELECT g.* FROM gameweeks g
           WHERE g.data_checked = 1
             AND NOT EXISTS (
                 SELECT 1 FROM notifications_sent n
                  WHERE n.gw = g.gw AND n.kind = 'review'
             )
           ORDER BY g.gw"""
    ).fetchall()
    return [dict(r) for r in rows]


def replace_my_picks(conn: sqlite3.Connection, gw: int, rows: list[dict]) -> None:
    """Overwrite this GW's picks — read fresh from the API after each deadline (§4.5 of FPL-CONTEXT.md)."""
    conn.execute("DELETE FROM my_picks WHERE gw = ?", (gw,))
    conn.executemany(
        """INSERT INTO my_picks (gw, element_id, position, is_captain, is_vice, multiplier)
           VALUES (:gw, :element_id, :position, :is_captain, :is_vice, :multiplier)""",
        [{**r, "gw": gw} for r in rows],
    )
    conn.commit()


def get_my_picks(conn: sqlite3.Connection, gw: int) -> list[dict]:
    rows = conn.execute(
        "SELECT * FROM my_picks WHERE gw = ? ORDER BY position", (gw,)
    ).fetchall()
    return [dict(r) for r in rows]


def get_latest_my_picks_gw(conn: sqlite3.Connection) -> int | None:
    row = conn.execute("SELECT MAX(gw) AS gw FROM my_picks").fetchone()
    return row["gw"] if row and row["gw"] is not None else None


def upsert_my_history(conn: sqlite3.Connection, row: dict) -> None:
    """Insert or replace one GW's row. Keys: gw, points, total_points, overall_rank,
    bank, team_value, transfers, transfer_cost, chip, points_on_bench."""
    row = {**row, "points_on_bench": row.get("points_on_bench")}
    conn.execute(
        """INSERT INTO my_history (gw, points, total_points, overall_rank, bank, team_value, transfers, transfer_cost, chip, points_on_bench)
           VALUES (:gw, :points, :total_points, :overall_rank, :bank, :team_value, :transfers, :transfer_cost, :chip, :points_on_bench)
           ON CONFLICT(gw) DO UPDATE SET
               points          = excluded.points,
               total_points    = excluded.total_points,
               overall_rank    = excluded.overall_rank,
               bank            = excluded.bank,
               team_value      = excluded.team_value,
               transfers       = excluded.transfers,
               transfer_cost   = excluded.transfer_cost,
               chip            = excluded.chip,
               points_on_bench = excluded.points_on_bench""",
        row,
    )
    conn.commit()


def get_my_history(conn: sqlite3.Connection, gw: int) -> dict | None:
    row = conn.execute("SELECT * FROM my_history WHERE gw = ?", (gw,)).fetchone()
    return dict(row) if row else None


def get_all_my_history(conn: sqlite3.Connection) -> list[dict]:
    rows = conn.execute("SELECT * FROM my_history ORDER BY gw").fetchall()
    return [dict(r) for r in rows]


# ---------------------------------------------------------------------------
# Mini-league rivals — PHASE3-BRIEF.md Step 4 / PHASE3-ADDENDUM.md §0.
#
# Rival squad data is never a fixture in production — it's pulled live from
# the API every gameweek by bot/fpl_jobs.py, exactly like Ollie's own picks.
# ---------------------------------------------------------------------------


def sync_rivals_from_standings(conn: sqlite3.Connection, rows: list[dict]) -> list[int]:
    """Full membership refresh from live leagues-classic/{id}/standings/.

    Each row: {entry_id, entry_name, player_name, started_event}. Anyone in
    `rows` is (re)activated with fresh name/started_event; anyone currently
    active but absent from `rows` is marked inactive, never deleted — a
    departed manager's historical picks stay meaningful. Membership is
    re-read every week, not cached, since managers join and leave mid-season.

    Returns the entry_ids that are newly active this call (i.e. either brand
    new or reactivated) so the caller knows who might need a backfill.
    """
    existing_active = {
        r["entry_id"] for r in conn.execute("SELECT entry_id FROM rivals WHERE active = 1").fetchall()
    }
    now_ids = {r["entry_id"] for r in rows}
    for r in rows:
        conn.execute(
            """INSERT INTO rivals (entry_id, entry_name, player_name, started_event, active)
               VALUES (:entry_id, :entry_name, :player_name, :started_event, 1)
               ON CONFLICT(entry_id) DO UPDATE SET
                   entry_name    = excluded.entry_name,
                   player_name   = excluded.player_name,
                   started_event = excluded.started_event,
                   active        = 1""",
            r,
        )
    if now_ids:
        placeholders = ",".join("?" * len(now_ids))
        conn.execute(f"UPDATE rivals SET active = 0 WHERE entry_id NOT IN ({placeholders})", tuple(now_ids))
    else:
        conn.execute("UPDATE rivals SET active = 0")
    conn.commit()
    return sorted(now_ids - existing_active)


def get_active_rivals(conn: sqlite3.Connection) -> list[dict]:
    rows = conn.execute("SELECT * FROM rivals WHERE active = 1 ORDER BY entry_id").fetchall()
    return [dict(r) for r in rows]


def get_all_rivals(conn: sqlite3.Connection) -> list[dict]:
    rows = conn.execute("SELECT * FROM rivals ORDER BY entry_id").fetchall()
    return [dict(r) for r in rows]


def replace_rival_picks(conn: sqlite3.Connection, gw: int, entry_id: int, rows: list[dict]) -> None:
    """Each row: {element_id, multiplier}. A gameweek's picks are public and
    immutable once its deadline passes — this is only ever called once per
    (gw, entry_id) in practice, but overwrites rather than assumes that."""
    conn.execute("DELETE FROM rival_picks WHERE gw = ? AND entry_id = ?", (gw, entry_id))
    conn.executemany(
        """INSERT INTO rival_picks (gw, entry_id, element_id, multiplier)
           VALUES (:gw, :entry_id, :element_id, :multiplier)""",
        [{**r, "gw": gw, "entry_id": entry_id} for r in rows],
    )
    conn.commit()


def has_rival_picks(conn: sqlite3.Connection, gw: int, entry_id: int) -> bool:
    row = conn.execute(
        "SELECT 1 FROM rival_picks WHERE gw = ? AND entry_id = ? LIMIT 1", (gw, entry_id)
    ).fetchone()
    return row is not None


def get_rival_picks(conn: sqlite3.Connection, gw: int, entry_id: int | None = None) -> list[dict]:
    if entry_id is not None:
        rows = conn.execute(
            "SELECT * FROM rival_picks WHERE gw = ? AND entry_id = ?", (gw, entry_id)
        ).fetchall()
    else:
        rows = conn.execute("SELECT * FROM rival_picks WHERE gw = ?", (gw,)).fetchall()
    return [dict(r) for r in rows]


def get_latest_rival_picks_gw(conn: sqlite3.Connection) -> int | None:
    """The most recent gameweek any rival picks have synced for — the
    reference point for template holes / differentials / captain-above
    queries, since a given gameweek's rival picks only exist once that
    gameweek's own deadline has passed."""
    row = conn.execute("SELECT MAX(gw) AS gw FROM rival_picks").fetchone()
    return row["gw"] if row and row["gw"] is not None else None


def get_latest_rival_picks_gw_before(conn: sqlite3.Connection, entry_id: int, gw: int) -> int | None:
    """The most recent gameweek strictly before `gw` this rival has stored
    picks for — used to diff two consecutive squads for rival_transfers."""
    row = conn.execute(
        "SELECT MAX(gw) AS gw FROM rival_picks WHERE entry_id = ? AND gw < ?", (entry_id, gw)
    ).fetchone()
    return row["gw"] if row and row["gw"] is not None else None


def upsert_rival_history(conn: sqlite3.Connection, row: dict) -> None:
    """Keys: gw, entry_id, points, total_points, rank, chip, points_on_bench."""
    conn.execute(
        """INSERT INTO rival_history (gw, entry_id, points, total_points, rank, chip, points_on_bench)
           VALUES (:gw, :entry_id, :points, :total_points, :rank, :chip, :points_on_bench)
           ON CONFLICT(gw, entry_id) DO UPDATE SET
               points          = excluded.points,
               total_points    = excluded.total_points,
               rank            = excluded.rank,
               chip            = excluded.chip,
               points_on_bench = excluded.points_on_bench""",
        row,
    )
    conn.commit()


def get_rival_history_row(conn: sqlite3.Connection, gw: int, entry_id: int) -> dict | None:
    row = conn.execute(
        "SELECT * FROM rival_history WHERE gw = ? AND entry_id = ?", (gw, entry_id)
    ).fetchone()
    return dict(row) if row else None


def get_rival_history_for_entry(conn: sqlite3.Connection, entry_id: int) -> list[dict]:
    rows = conn.execute(
        "SELECT * FROM rival_history WHERE entry_id = ? ORDER BY gw", (entry_id,)
    ).fetchall()
    return [dict(r) for r in rows]


def get_rival_history_for_gw(conn: sqlite3.Connection, gw: int) -> list[dict]:
    rows = conn.execute("SELECT * FROM rival_history WHERE gw = ?", (gw,)).fetchall()
    return [dict(r) for r in rows]


def insert_player_snapshots(conn: sqlite3.Connection, taken_at: str, rows: list[dict]) -> None:
    """Bulk snapshot of owned players' status. Each dict: {element_id, now_cost, status, chance_next, news}."""
    conn.executemany(
        """INSERT OR REPLACE INTO player_snapshots (taken_at, element_id, now_cost, status, chance_next, news)
           VALUES (:taken_at, :element_id, :now_cost, :status, :chance_next, :news)""",
        [{**r, "taken_at": taken_at} for r in rows],
    )
    conn.commit()


def get_latest_snapshot(conn: sqlite3.Connection, element_id: int) -> dict | None:
    row = conn.execute(
        "SELECT * FROM player_snapshots WHERE element_id = ? ORDER BY taken_at DESC LIMIT 1",
        (element_id,),
    ).fetchone()
    return dict(row) if row else None


def mark_notification_sent(conn: sqlite3.Connection, gw: int, kind: str, sent_at: str) -> None:
    """Idempotent — a cron that fires twice must not send twice (PHASE1-BRIEF.md §3)."""
    conn.execute(
        "INSERT OR IGNORE INTO notifications_sent (gw, kind, sent_at) VALUES (?, ?, ?)",
        (gw, kind, sent_at),
    )
    conn.commit()


def was_notification_sent(conn: sqlite3.Connection, gw: int, kind: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM notifications_sent WHERE gw = ? AND kind = ?", (gw, kind)
    ).fetchone()
    return row is not None


def set_acknowledged(conn: sqlite3.Connection, gw: int, acked_at: str) -> None:
    conn.execute(
        "INSERT INTO acknowledgements (gw, acked_at) VALUES (?, ?) "
        "ON CONFLICT(gw) DO UPDATE SET acked_at = excluded.acked_at",
        (gw, acked_at),
    )
    conn.commit()


def is_acknowledged(conn: sqlite3.Connection, gw: int) -> bool:
    row = conn.execute("SELECT 1 FROM acknowledgements WHERE gw = ?", (gw,)).fetchone()
    return row is not None


# ---------------------------------------------------------------------------
# FPL DDL — Phase 2 recommendation engine (PHASE2-BRIEF.md)
# ---------------------------------------------------------------------------

XP_PREDICTION_DDL = """
CREATE TABLE IF NOT EXISTS xp_predictions (
    gw            INTEGER,
    element_id    INTEGER,
    xp            REAL,
    model_version TEXT,
    computed_at   TEXT,
    PRIMARY KEY (gw, element_id, model_version)
)
"""

PREFERENCE_DDL = """
CREATE TABLE IF NOT EXISTS preferences (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    kind         TEXT NOT NULL,   -- 'force_in' | 'force_out' | 'min_club'
    value        TEXT NOT NULL,   -- element_id, or 'club_id=n' for min_club
    created_at   TEXT NOT NULL,
    expires_at   TEXT             -- NULL = no expiry
)
"""

GAMEWEEK_SHAPE_DDL = """
CREATE TABLE IF NOT EXISTS gameweek_shapes (
    gw            INTEGER PRIMARY KEY,
    fixture_count INTEGER NOT NULL,
    blanks_json   TEXT NOT NULL,
    doubles_json  TEXT NOT NULL,
    updated_at    TEXT NOT NULL
)
"""


# ---------------------------------------------------------------------------
# FPL CRUD — Phase 2
# ---------------------------------------------------------------------------


def log_xp_predictions(conn: sqlite3.Connection, gw: int, model_version: str, computed_at: str, predictions: dict[int, float]) -> None:
    """Bulk-log one model's per-player xP predictions for a gameweek — the history
    Phase 4 needs to answer 'are we beating just points_per_game' (PHASE2-BRIEF.md §2)."""
    conn.executemany(
        """INSERT OR REPLACE INTO xp_predictions (gw, element_id, xp, model_version, computed_at)
           VALUES (:gw, :element_id, :xp, :model_version, :computed_at)""",
        [
            {"gw": gw, "element_id": eid, "xp": xp, "model_version": model_version, "computed_at": computed_at}
            for eid, xp in predictions.items()
        ],
    )
    conn.commit()


def get_xp_predictions(conn: sqlite3.Connection, gw: int, model_version: str) -> dict[int, float]:
    rows = conn.execute(
        "SELECT element_id, xp FROM xp_predictions WHERE gw = ? AND model_version = ?",
        (gw, model_version),
    ).fetchall()
    return {r["element_id"]: r["xp"] for r in rows}


def add_preference(conn: sqlite3.Connection, kind: str, value: str, created_at: str, expires_at: str | None) -> int:
    cur = conn.execute(
        "INSERT INTO preferences (kind, value, created_at, expires_at) VALUES (?, ?, ?, ?)",
        (kind, value, created_at, expires_at),
    )
    conn.commit()
    return cur.lastrowid


def get_active_preferences(conn: sqlite3.Connection, now_iso: str) -> list[dict]:
    """Preferences not yet expired — expires_at IS NULL means no expiry."""
    rows = conn.execute(
        "SELECT * FROM preferences WHERE expires_at IS NULL OR expires_at > ? ORDER BY created_at",
        (now_iso,),
    ).fetchall()
    return [dict(r) for r in rows]


def prune_expired_preferences(conn: sqlite3.Connection, now_iso: str) -> None:
    conn.execute("DELETE FROM preferences WHERE expires_at IS NOT NULL AND expires_at <= ?", (now_iso,))
    conn.commit()


def upsert_gameweek_shape(conn: sqlite3.Connection, gw: int, fixture_count: int, blanks_json: str, doubles_json: str, updated_at: str) -> None:
    conn.execute(
        """INSERT INTO gameweek_shapes (gw, fixture_count, blanks_json, doubles_json, updated_at)
           VALUES (?, ?, ?, ?, ?)
           ON CONFLICT(gw) DO UPDATE SET
               fixture_count = excluded.fixture_count,
               blanks_json   = excluded.blanks_json,
               doubles_json  = excluded.doubles_json,
               updated_at    = excluded.updated_at""",
        (gw, fixture_count, blanks_json, doubles_json, updated_at),
    )
    conn.commit()


def get_gameweek_shape(conn: sqlite3.Connection, gw: int) -> dict | None:
    row = conn.execute("SELECT * FROM gameweek_shapes WHERE gw = ?", (gw,)).fetchone()
    return dict(row) if row else None


def get_all_gameweek_shapes(conn: sqlite3.Connection) -> dict[int, dict]:
    rows = conn.execute("SELECT * FROM gameweek_shapes ORDER BY gw").fetchall()
    return {r["gw"]: dict(r) for r in rows}
