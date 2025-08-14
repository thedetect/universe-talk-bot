# -*- coding: utf-8 -*-
"""
SQLite-слой с автомиграцией схемы для Telegram-бота.
- Создаёт таблицу users, если её нет.
- Добавляет недостающие колонки (без потери данных).
- Совместим со старыми БД, где была колонка telegram_id вместо user_id.
"""

import os
import sqlite3
from contextlib import closing

# Путь к БД: через переменную окружения DB_PATH, иначе рядом с проектом.
DB_PATH = os.getenv(
    "DB_PATH",
    os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "user_data.db")),
)

# Описание актуальной схемы (колонки -> тип)
SCHEMA_COLUMNS = {
    "user_id": "INTEGER",                     # уникальный ID пользователя Telegram
    "chat_id": "INTEGER",                     # текущий chat_id
    "name": "TEXT",
    "birth_date": "TEXT",                     # ДД.ММ.ГГГГ
    "birth_place": "TEXT",                    # "город, страна"
    "birth_time": "TEXT",                     # "HH:MM"
    "daily_time": "TEXT",                     # время рассылки "HH:MM"
    "tz": "TEXT",                             # таймзона пользователя
    "referral_code": "TEXT",
    "referred_by": "TEXT",
    "bonus_days": "INTEGER",
    "trial_start": "TEXT",                    # ISO-строка даты
    "trial_until": "TEXT",                    # ISO-строка даты
    "subscription_status": "TEXT",            # 'trial' | 'active' | 'expired'
}

DEFAULTS = {
    "bonus_days": 0,
    "tz": "Europe/Berlin",
    "subscription_status": "trial",
}

def _connect():
    # check_same_thread=False — чтобы можно было использовать в APScheduler
    return sqlite3.connect(DB_PATH, check_same_thread=False)

def init_db():
    """Создаёт таблицу users (если нет) и выполняет автомиграции."""
    with closing(_connect()) as con, closing(con.cursor()) as cur:
        # Базовая таблица (минимальный набор, остальное добавим АЛЬТЕРАМИ)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS users (
                user_id INTEGER UNIQUE,
                chat_id INTEGER,
                name TEXT,
                birth_date TEXT,
                birth_place TEXT,
                birth_time TEXT,
                daily_time TEXT,
                tz TEXT,
                referral_code TEXT,
                referred_by TEXT,
                bonus_days INTEGER DEFAULT 0,
                trial_start TEXT,
                trial_until TEXT,
                subscription_status TEXT DEFAULT 'trial'
            );
        """)
        # Текущие колонки в таблице
        cur.execute("PRAGMA table_info(users)")
        existing = {row[1] for row in cur.fetchall()}

        # Миграция: если была старая колонка telegram_id — переносим в user_id
        if "telegram_id" in existing and "user_id" not in existing:
            cur.execute("ALTER TABLE users ADD COLUMN user_id INTEGER;")
            cur.execute("UPDATE users SET user_id = telegram_id WHERE user_id IS NULL;")
            existing.add("user_id")

        # Добавляем недостающие колонки из актуальной схемы
        for col, typ in SCHEMA_COLUMNS.items():
            if col not in existing:
                if col in DEFAULTS:
                    default_val = DEFAULTS[col]
                    if isinstance(default_val, int):
                        cur.execute(f"ALTER TABLE users ADD COLUMN {col} {typ} DEFAULT {default_val};")
                    else:
                        cur.execute(f"ALTER TABLE users ADD COLUMN {col} {typ} DEFAULT '{default_val}';")
                else:
                    cur.execute(f"ALTER TABLE users ADD COLUMN {col} {typ};")

        # Индексы
        cur.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_users_user_id ON users(user_id);")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_users_chat_id ON users(chat_id);")

        con.commit()

def upsert_user(user_id: int, chat_id: int):
    """Создаёт пользователя или обновляет chat_id по user_id."""
    with closing(_connect()) as con, closing(con.cursor()) as cur:
        cur.execute("""
            INSERT INTO users (user_id, chat_id)
            VALUES (?, ?)
            ON CONFLICT(user_id) DO UPDATE SET chat_id=excluded.chat_id;
        """, (user_id, chat_id))
        con.commit()

def update_user_field(user_id: int, field: str, value):
    """Обновляет одно поле пользователя. Бросит ValueError для неизвестных полей."""
    if field not in SCHEMA_COLUMNS:
        raise ValueError(f"unknown field: {field}")

    with closing(_connect()) as con, closing(con.cursor()) as cur:
        cur.execute(f"UPDATE users SET {field}=? WHERE user_id=?", (value, user_id))
        # Если строки нет — создадим и сразу поставим поле
        if cur.rowcount == 0:
            # создаём пользователя и снова обновляем
            cur.execute("""
                INSERT INTO users (user_id, chat_id)
                VALUES (?, ?)
                ON CONFLICT(user_id) DO NOTHING;
            """, (user_id, None))
            cur.execute(f"UPDATE users SET {field}=? WHERE user_id=?", (value, user_id))
        con.commit()

def get_user(user_id: int) -> dict | None:
    """Возвращает словарь с данными пользователя или None."""
    with closing(_connect()) as con, closing(con.cursor()) as cur:
        cur.execute(f"SELECT {', '.join(SCHEMA_COLUMNS.keys())} FROM users WHERE user_id=?", (user_id,))
        row = cur.fetchone()
        if not row:
            return None
        return dict(zip(SCHEMA_COLUMNS.keys(), row))

def get_all_users():
    """Все пользователи (список словарей)."""
    with closing(_connect()) as con, closing(con.cursor()) as cur:
        cur.execute(f"SELECT {', '.join(SCHEMA_COLUMNS.keys())} FROM users")
        rows = cur.fetchall()
        return [dict(zip(SCHEMA_COLUMNS.keys(), r)) for r in rows]