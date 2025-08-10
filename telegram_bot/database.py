
from __future__ import annotations
import sqlite3, threading
from datetime import datetime, timedelta
from typing import Optional, List, Dict, Any
from . import config

_DB_LOCK = threading.Lock()

SCHEMA = '''
CREATE TABLE IF NOT EXISTS users (
    telegram_id INTEGER PRIMARY KEY,
    name TEXT,
    birth_date TEXT,
    birth_place TEXT,
    birth_time TEXT,
    notify_time TEXT,
    created_at TEXT,
    referral_code TEXT,
    referred_by INTEGER,
    points INTEGER DEFAULT 0,
    is_trial INTEGER DEFAULT 1,
    trial_expiration TEXT,
    subscription_until TEXT
);
CREATE INDEX IF NOT EXISTS idx_referred_by ON users(referred_by);
'''

def _conn():
    return sqlite3.connect(config.DB_PATH, check_same_thread=False)

def init_db():
    with _DB_LOCK:
        with _conn() as con:
            con.executescript(SCHEMA)

def upsert_user(telegram_id: int, **fields):
    fields = dict(fields)
    with _DB_LOCK, _conn() as con:
        cur = con.cursor()
        placeholders = ", ".join([f"{k}=?" for k in fields.keys()])
        values = list(fields.values()) + [telegram_id]
        cur.execute(f"UPDATE users SET {placeholders} WHERE telegram_id = ?", values)
        if cur.rowcount == 0:
            cols = ["telegram_id"] + list(fields.keys())
            vals = [telegram_id] + list(fields.values())
            q = f"INSERT INTO users ({', '.join(cols)}) VALUES ({', '.join(['?']*len(vals))})"
            cur.execute(q, vals)
        con.commit()

def get_user(telegram_id: int) -> Optional[Dict[str, Any]]:
    with _DB_LOCK, _conn() as con:
        con.row_factory = sqlite3.Row
        r = con.execute("SELECT * FROM users WHERE telegram_id = ?", (telegram_id,)).fetchone()
        return dict(r) if r else None

def get_all_users() -> List[Dict[str, Any]]:
    with _DB_LOCK, _conn() as con:
        con.row_factory = sqlite3.Row
        return [dict(r) for r in con.execute("SELECT * FROM users").fetchall()]

def update_user_field(telegram_id: int, field: str, value):
    with _DB_LOCK, _conn() as con:
        con.execute(f"UPDATE users SET {field}=? WHERE telegram_id=?", (value, telegram_id))
        con.commit()

def list_referrals(telegram_id: int) -> List[Dict[str, Any]]:
    with _DB_LOCK, _conn() as con:
        con.row_factory = sqlite3.Row
        return [dict(r) for r in con.execute("SELECT * FROM users WHERE referred_by=?", (telegram_id,)).fetchall()]

def add_referral_points(telegram_id: int, days: int):
    u = get_user(telegram_id) or {}
    pts = int(u.get("points") or 0) + int(days)
    update_user_field(telegram_id, "points", pts)

def award_trial_if_needed(telegram_id: int):
    u = get_user(telegram_id)
    if not u:
        return
    if u.get("trial_expiration"):
        return
    until = (datetime.utcnow() + timedelta(days=config.TRIAL_DAYS)).strftime("%Y-%m-%d")
    upsert_user(telegram_id, is_trial=1, trial_expiration=until)

def use_bonus_days_if_needed(telegram_id: int):
    u = get_user(telegram_id) or {}
    today = datetime.utcnow().date()
    trial_exp = u.get("trial_expiration")
    sub_until = u.get("subscription_until")
    points = int(u.get("points") or 0)
    if sub_until:
        return
    if trial_exp and datetime.strptime(trial_exp, "%Y-%m-%d").date() >= today:
        return
    if points > 0:
        new_until = today + timedelta(days=points)
        upsert_user(telegram_id, subscription_until=new_until.strftime("%Y-%m-%d"), points=0, is_trial=0)

def has_access(telegram_id: int) -> bool:
    u = get_user(telegram_id) or {}
    today = datetime.utcnow().date()
    if u.get("subscription_until"):
        try:
            if datetime.strptime(u["subscription_until"], "%Y-%m-%d").date() >= today:
                return True
        except: pass
    if u.get("trial_expiration"):
        try:
            if datetime.strptime(u["trial_expiration"], "%Y-%m-%d").date() >= today:
                return True
        except: pass
    return False

def extend_subscription(telegram_id: int, days: int):
    u = get_user(telegram_id) or {}
    today = datetime.utcnow().date()
    base = today
    if u.get("subscription_until"):
        try:
            cur = datetime.strptime(u["subscription_until"], "%Y-%m-%d").date()
            if cur > base:
                base = cur
        except: pass
    new_until = base + timedelta(days=days)
    upsert_user(telegram_id, subscription_until=new_until.strftime("%Y-%m-%d"), is_trial=0)
