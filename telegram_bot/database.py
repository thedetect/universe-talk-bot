"""
Database access layer for the Telegram astrology bot.

This module encapsulates all interactions with the SQLite database.
By centralizing database logic here, the rest of the code base can
remain focused on business logic without worrying about SQL queries
and connection management.  The functions defined below create
tables if they do not already exist, insert and update records,
retrieve users by different criteria, and track subscription and
referral information.

The database schema is intentionally simple.  A single table,
`users`, stores all relevant information about each user.  Columns
include the Telegram ID, personal details (name, birth data),
preferences (notification time), loyalty program attributes (points
and referral codes), and subscription status.  When new features
are added it's preferable to extend this table rather than creating
many small tables, as SQLite performs well with a modest number of
columns.

Functions exported by this module raise exceptions on error.  Callers
should handle these appropriately, either by showing a friendly
message to the user or by logging the issue for later diagnosis.
"""

from __future__ import annotations

import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime
from typing import Any, Dict, Iterable, List, Optional, Tuple

from . import config

# A lock used to serialize access to the SQLite database.  SQLite
# connections are not inherently thread‑safe when used across
# multiple threads, so we guard each operation with a lock.
_db_lock = threading.Lock()


@contextmanager
def _get_connection() -> Iterable[sqlite3.Connection]:
    """Context manager that yields a SQLite connection.

    The connection uses `row_factory` to return `sqlite3.Row` objects,
    allowing access to columns by name.  The connection is opened
    with `check_same_thread=False` so it can be safely used across
    threads when protected by `_db_lock`.

    Yields
    ------
    sqlite3.Connection
        A connection to the database.
    """
    conn = sqlite3.connect(config.DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
    finally:
        conn.close()


def init_db() -> None:
    """Create database tables if they do not already exist.

    This function should be called once at startup.  It ensures that
    the `users` table exists with the proper schema.  Any future
    schema migrations can be handled here by executing `ALTER TABLE`
    statements guarded by checks.
    """
    with _db_lock:
        with _get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS users (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    telegram_id INTEGER UNIQUE NOT NULL,
                    name TEXT,
                    birth_date TEXT,
                    birth_place TEXT,
                    birth_time TEXT,
                    schedule_time TEXT,
                    referral_code TEXT UNIQUE,
                    referred_by TEXT,
                    points INTEGER DEFAULT 0,
                    subscription_active INTEGER DEFAULT 0,
                    subscription_expiration TEXT,
                    registration_date TEXT
                )
                """
            )
            conn.commit()


def add_or_update_user(
    telegram_id: int,
    name: Optional[str] = None,
    birth_date: Optional[str] = None,
    birth_place: Optional[str] = None,
    birth_time: Optional[str] = None,
    schedule_time: Optional[str] = None,
    referral_code: Optional[str] = None,
    referred_by: Optional[str] = None,
) -> None:
    """Insert a new user or update an existing record.

    Parameters
    ----------
    telegram_id : int
        Unique identifier of the user in Telegram.
    name : str, optional
        User's name.
    birth_date : str, optional
        Birth date in ISO format (YYYY‑MM‑DD).
    birth_place : str, optional
        Birth place as free‑form text.
    birth_time : str, optional
        Birth time in HH:MM 24h format.
    schedule_time : str, optional
        Preferred daily message time in HH:MM.
    referral_code : str, optional
        Unique referral code assigned to this user.
    referred_by : str, optional
        Referral code of the user who referred this user.
    """
    registration_date = datetime.utcnow().isoformat()
    with _db_lock:
        with _get_connection() as conn:
            cursor = conn.cursor()
            # Check if the user exists
            cursor.execute(
                "SELECT id FROM users WHERE telegram_id = ?", (telegram_id,)
            )
            row = cursor.fetchone()
            if row:
                # Prepare dynamic update statement
                fields: List[str] = []
                values: List[Any] = []
                if name is not None:
                    fields.append("name = ?")
                    values.append(name)
                if birth_date is not None:
                    fields.append("birth_date = ?")
                    values.append(birth_date)
                if birth_place is not None:
                    fields.append("birth_place = ?")
                    values.append(birth_place)
                if birth_time is not None:
                    fields.append("birth_time = ?")
                    values.append(birth_time)
                if schedule_time is not None:
                    fields.append("schedule_time = ?")
                    values.append(schedule_time)
                if referral_code is not None:
                    fields.append("referral_code = ?")
                    values.append(referral_code)
                if referred_by is not None:
                    fields.append("referred_by = ?")
                    values.append(referred_by)
                if fields:
                    values.append(telegram_id)
                    query = f"UPDATE users SET {', '.join(fields)} WHERE telegram_id = ?"
                    cursor.execute(query, values)
            else:
                cursor.execute(
                    """
                    INSERT INTO users (
                        telegram_id, name, birth_date, birth_place, birth_time,
                        schedule_time, referral_code, referred_by, points,
                        subscription_active, subscription_expiration, registration_date
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 0, 0, NULL, ?)
                    """,
                    (
                        telegram_id,
                        name,
                        birth_date,
                        birth_place,
                        birth_time,
                        schedule_time,
                        referral_code,
                        referred_by,
                        registration_date,
                    ),
                )
            conn.commit()


def get_user(telegram_id: int) -> Optional[sqlite3.Row]:
    """Retrieve a user by Telegram ID.

    Returns `None` if the user does not exist.
    """
    with _db_lock:
        with _get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT * FROM users WHERE telegram_id = ?", (telegram_id,)
            )
            return cursor.fetchone()


def get_user_by_referral_code(referral_code: str) -> Optional[sqlite3.Row]:
    """Retrieve a user by their referral code."""
    with _db_lock:
        with _get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT * FROM users WHERE referral_code = ?", (referral_code,)
            )
            return cursor.fetchone()


def set_schedule_time(telegram_id: int, schedule_time: str) -> None:
    """Update a user's preferred daily message time."""
    with _db_lock:
        with _get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "UPDATE users SET schedule_time = ? WHERE telegram_id = ?",
                (schedule_time, telegram_id),
            )
            conn.commit()


def update_points(telegram_id: int, points_delta: int) -> None:
    """Increment a user's loyalty points.

    Points may be positive or negative.  Passing a negative value will
    decrease the points accordingly but not below zero.
    """
    with _db_lock:
        with _get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT points FROM users WHERE telegram_id = ?", (telegram_id,)
            )
            row = cursor.fetchone()
            if not row:
                return
            current_points = row["points"] or 0
            new_points = max(0, current_points + points_delta)
            cursor.execute(
                "UPDATE users SET points = ? WHERE telegram_id = ?",
                (new_points, telegram_id),
            )
            conn.commit()


def set_subscription_status(
    telegram_id: int, active: bool, expiration_date: Optional[str] = None
) -> None:
    """Update a user's subscription status.

    If `active` is False, `expiration_date` will be cleared.
    """
    with _db_lock:
        with _get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "UPDATE users SET subscription_active = ?, subscription_expiration = ? WHERE telegram_id = ?",
                (1 if active else 0, expiration_date if active else None, telegram_id),
            )
            conn.commit()


def get_all_users() -> List[sqlite3.Row]:
    """Return a list of all user rows."""
    with _db_lock:
        with _get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM users")
            return cursor.fetchall()


def get_subscribed_users() -> List[sqlite3.Row]:
    """Return a list of users with an active subscription.

    Users whose subscription expiration date has passed are considered
    inactive.  This function automatically deactivates subscriptions
    that have expired.
    """
    now_iso = datetime.utcnow().isoformat()
    with _db_lock:
        with _get_connection() as conn:
            cursor = conn.cursor()
            # Deactivate expired subscriptions
            cursor.execute(
                """
                UPDATE users
                SET subscription_active = 0, subscription_expiration = NULL
                WHERE subscription_active = 1
                  AND subscription_expiration IS NOT NULL
                  AND subscription_expiration < ?
                """,
                (now_iso,),
            )
            conn.commit()
            # Retrieve active subscribers
            cursor.execute(
                "SELECT * FROM users WHERE subscription_active = 1"
            )
            return cursor.fetchall()


def count_referrals(telegram_id: int) -> int:
    """Return the number of users referred by the given user."""
    user = get_user(telegram_id)
    if not user or not user["referral_code"]:
        return 0
    code = user["referral_code"]
    with _db_lock:
        with _get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT COUNT(*) FROM users WHERE referred_by = ?",
                (code,),
            )
            result = cursor.fetchone()
            return result[0] if result else 0


def list_referrals(telegram_id: int) -> List[sqlite3.Row]:
    """Return a list of rows representing users referred by a user."""
    user = get_user(telegram_id)
    if not user or not user["referral_code"]:
        return []
    code = user["referral_code"]
    with _db_lock:
        with _get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT * FROM users WHERE referred_by = ?",
                (code,),
            )
            return cursor.fetchall()