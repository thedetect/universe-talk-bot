# -*- coding: utf-8 -*-
"""
bot.py — универсальный запускатель бота (PTB 13.15) с корректным webhook-режимом.

Окружение (Render → Settings → Environment):
  TELEGRAM_BOT_TOKEN   — токен бота (обязательно, новый после revoke)
  USE_WEBHOOK          — "1" (включить вебхук) или "0" (polling)
  PUBLIC_URL           — https://<твой-сервис>.onrender.com  (только при USE_WEBHOOK=1)
  WEBHOOK_SECRET       — длинная случайная строка (только при USE_WEBHOOK=1)
  DB_PATH              — /opt/render/project/src/user_data.db  (если нет диска /data)
                          или /data/user_data.db (если подключён Persistent Disk)
  TZ                   — Europe/Berlin
  PYTHONUNBUFFERED     — 1
"""

import os
import logging
import sqlite3
from contextlib import closing
from datetime import datetime

import pytz
from telegram import ReplyKeyboardMarkup, ReplyKeyboardRemove, Update, ParseMode
from telegram.ext import (
    Updater,
    CommandHandler,
    MessageHandler,
    Filters,
    CallbackContext,
    ConversationHandler,
)

# ==== безопасные импорты локальных модулей (если их нет — бот всё равно поднимется) ====
try:
    # твой конфиг (если есть)
    from .config import (
        TELEGRAM_BOT_TOKEN as CFG_TOKEN,
        PUBLIC_URL as CFG_PUBLIC_URL,
        WEBHOOK_SECRET as CFG_WEBHOOK_SECRET,
        USE_WEBHOOK as CFG_USE_WEBHOOK,
        ADMIN_IDS as CFG_ADMIN_IDS,
        DB_PATH as CFG_DB_PATH,
    )
except Exception:
    CFG_TOKEN = CFG_PUBLIC_URL = CFG_WEBHOOK_SECRET = None
    CFG_USE_WEBHOOK = None
    CFG_ADMIN_IDS = []
    CFG_DB_PATH = None

try:
    # твой модуль БД, если есть
    from . import database as db
except Exception:
    db = None

# (необязательные модули; если есть — используем)
try:
    from . import referral
except Exception:
    referral = None

try:
    from . import payments
except Exception:
    payments = None

try:
    from . import astrology
except Exception:
    astrology = None

# ============================ ЛОГИ ============================
logging.basicConfig(
    format="%(asctime)s %(levelname)s | %(name)s: %(message)s",
    level=logging.INFO,
)
log = logging.getLogger("bot")

# ============================ КОНФИГ ============================
TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", CFG_TOKEN or "").strip()
USE_WEBHOOK = str(os.getenv("USE_WEBHOOK", CFG_USE_WEBHOOK if CFG_USE_WEBHOOK is not None else "1")).strip()
PUBLIC_URL = os.getenv("PUBLIC_URL", CFG_PUBLIC_URL or "").strip()
WEBHOOK_SECRET = os.getenv("WEBHOOK_SECRET", CFG_WEBHOOK_SECRET or "").strip()
DB_PATH = os.getenv("DB_PATH", CFG_DB_PATH or "/opt/render/project/src/user_data.db").strip()
TZ = os.getenv("TZ", "Europe/Berlin")
TZINFO = pytz.timezone(TZ)

ADMIN_IDS = set()
for raw in (os.getenv("ADMIN_IDS", "") or ",").split(","):
    raw = raw.strip()
    if raw.isdigit():
        ADMIN_IDS.add(int(raw))
for v in (CFG_ADMIN_IDS or []):
    try:
        ADMIN_IDS.add(int(v))
    except Exception:
        pass

def truthy(v) -> bool:
    return str(v).strip().lower() in {"1", "true", "yes", "y", "on"}

# ============================ ПРОСТЕЙШАЯ БД (fallback, если нет твоего database.py) ============================
def _fallback_init_db():
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    with closing(sqlite3.connect(DB_PATH, check_same_thread=False)) as con:
        con.execute("""
        CREATE TABLE IF NOT EXISTS users(
            user_id     INTEGER PRIMARY KEY,
            chat_id     INTEGER,
            name        TEXT,
            birth_date  TEXT,
            birth_place TEXT,
            birth_time  TEXT,
            daily_time  TEXT,
            created_at  TEXT DEFAULT CURRENT_TIMESTAMP
        );
        """)
        con.execute("""
        CREATE TABLE IF NOT EXISTS referrals(
            user_id     INTEGER PRIMARY KEY,
            code        TEXT,
            invited_cnt INTEGER DEFAULT 0,
            bonus_days  INTEGER DEFAULT 0
        );
        """)
        con.commit()

def _fallback_get_user(uid: int):
    with closing(sqlite3.connect(DB_PATH, check_same_thread=False)) as con:
        cur = con.execute("SELECT user_id, chat_id, name, birth_date, birth_place, birth_time, daily_time FROM users WHERE user_id=?", (uid,))
        row = cur.fetchone()
        return row

def _fallback_upsert_user(uid: int, chat_id: int):
    with closing(sqlite3.connect(DB_PATH, check_same_thread=False)) as con:
        con.execute(
            "INSERT INTO users(user_id, chat_id) VALUES(?, ?) ON CONFLICT(user_id) DO UPDATE SET chat_id=excluded.chat_id",
            (uid, chat_id),
        )
        con.commit()

def _fallback_update_field(uid: int, field: str, value: str):
    if field not in {"name", "birth_date", "birth_place", "birth_time", "daily_time"}:
        return
    with closing(sqlite3.connect(DB_PATH, check_same_thread=False)) as con:
        con.execute(f"UPDATE users SET {field}=? WHERE user_id=?", (value, uid))
        con.commit()

def _fallback_all_chat_ids():
    with closing(sqlite3.connect(DB_PATH, check_same_thread=False)) as con:
        cur = con.execute("SELECT chat_id FROM users WHERE chat_id IS NOT NULL")
        return [r[0] for r in cur.fetchall()]

# Обёртки — используем твой database.py, если есть; иначе fallback
def init_db():
    try:
        if db:  # твой модуль
            db.init_db()
        else:
            _fallback_init_db()
        log.info("DB ready at %s", DB_PATH)
    except Exception as e:
        log.exception("DB init failed: %s", e)
        _fallback_init_db()

def ensure_user(uid: int, chat_id: int):
    try:
        if db:
            if not db.get_user(uid):
                db.create_or_update_user(uid, chat_id=chat_id)
            else:
                db.create_or_update_user(uid, chat_id=chat_id)
        else:
            _fallback_upsert_user(uid, chat_id)
    except Exception:
        _fallback_upsert_user(uid, chat_id)

def update_field(uid: int, field: str, value: str):
    try:
        if db:
            db.update_user_field(uid, field, value)
        else:
            _fallback_update_field(uid, field, value)
    except Exception:
        _fallback_update_field(uid, field, value)

def get_user(uid: int):
    try:
        if db:
            return db.get_user(uid)
        return _fallback_get_user(uid)
    except Exception:
        return _fallback_get_user(uid)

def all_chat_ids():
    try:
        if db:
            return db.get_all_chat_ids()
        return _fallback_all_chat_ids()
    except Exception:
        return _fallback_all_chat_ids()

# ============================ КЛАВИАТУРЫ ============================
MAIN_KB = ReplyKeyboardMarkup(
    [
        ["⚙️ Обновить данные", "🕒 Время рассылки"],
        ["💳 Подписка", "👥 Рефералы"],
        ["❌ Закрыть меню"],
    ],
    resize_keyboard=True,
)

UPDATE_KB = ReplyKeyboardMarkup(
    [
        ["Имя", "Дата рождения"],
        ["Место рождения", "Время рождения"],
        ["Оставить всё как есть"],
    ],
    resize_keyboard=True,
)

# ============================ ХЕНДЛЕРЫ ============================
CHOOSING, TYPING_VALUE = range(2)

def start(update: Update, context: CallbackContext):
    user = update.effective_user
    ensure_user(user.id, update.effective_chat.id)
    msg = (
        f"Привет, {user.first_name or 'друг'}! 🌟\n\n"
        "Я твой персональный астро-ассистент. Жми /menu, чтобы настроить профиль, "
        "установить время ежедневной рассылки и посмотреть рефералов."
    )
    update.message.reply_text(msg)

def menu(update: Update, context: CallbackContext):
    update.message.reply_text("Выберите действие:", reply_markup=MAIN_KB)

def close_menu(update: Update, context: CallbackContext):
    update.message.reply_text("Меню закрыто.", reply_markup=ReplyKeyboardRemove())

def status(update: Update, context: CallbackContext):
    uid = update.effective_user.id
    u = get_user(uid)
    # очень простой статус
    when = None
    if u:
        # индексы в fallback-е: (user_id, chat_id, name, birth_date, birth_place, birth_time, daily_time)
        when = u[6] if len(u) >= 7 else None
    txt = ["Статус подписки: демо (без платежей пока)"]
    if when:
        txt.append(f"Время ежедневной рассылки: {when}")
    update.message.reply_text("\n".join(txt))

def referrals(update: Update, context: CallbackContext):
    uid = update.effective_user.id
    bot_username = context.bot.username or "bot"
    code = str(uid)
    link = f"https://t.me/{bot_username}?start={code}"
    invited = 0
    bonus_days = 0
    if referral:
        try:
            s = referral.get_stats(uid)
            invited = s.get("invited", 0)
            bonus_days = s.get("bonus_days", 0)
        except Exception:
            pass
    text = (
        f"Твоя реферальная ссылка:\n{link}\n\n"
        f"Приглашённых: {invited}\n"
        f"Бонусные дни: {bonus_days}"
    )
    update.message.reply_text(text, disable_web_page_preview=True)

def subscribe(update: Update, context: CallbackContext):
    update.message.reply_text(
        "Платёжка будет подключена позже. Пока доступен демо-режим.\n"
        "Когда будешь готов — напомни командой /subscribe.",
    )

def settime(update: Update, context: CallbackContext):
    update.message.reply_text(
        "Введи время в формате ЧЧ:ММ (например, 08:30):", reply_markup=ReplyKeyboardRemove()
    )
    context.user_data["await_time"] = True

def text_router(update: Update, context: CallbackContext):
    text = (update.message.text or "").strip()

    # обработка времени рассылки
    if context.user_data.pop("await_time", False):
        try:
            hh, mm = text.split(":")
            hh, mm = int(hh), int(mm)
            assert 0 <= hh < 24 and 0 <= mm < 60
        except Exception:
            update.message.reply_text("Неверный формат. Пример: 08:30. Попробуй ещё раз /settime.")
            return
        update_field(update.effective_user.id, "daily_time", f"{hh:02d}:{mm:02d}")
        update.message.reply_text("Готово! Время сохранено.", reply_markup=MAIN_KB)
        return

    # выбор поля для обновления
    if text in {"Имя", "Дата рождения", "Место рождения", "Время рождения"}:
        context.user_data["update_field"] = text
        update.message.reply_text("Введи новое значение:", reply_markup=ReplyKeyboardRemove())
        context.user_data["await_value"] = True
        return

    if text == "Оставить всё как есть":
        update.message.reply_text("Ок, без изменений.", reply_markup=MAIN_KB)
        context.user_data.pop("update_field", None)
        context.user_data.pop("await_value", None)
        return

    # ввод значения для выбранного поля
    if context.user_data.pop("await_value", False):
        fld_map = {
            "Имя": "name",
            "Дата рождения": "birth_date",
            "Место рождения": "birth_place",
            "Время рождения": "birth_time",
        }
        fld = fld_map.get(context.user_data.get("update_field"))
        if fld:
            update_field(update.effective_user.id, fld, text)
            update.message.reply_text("Обновлено ✅", reply_markup=MAIN_KB)
        else:
            update.message.reply_text("Что-то пошло не так. Попробуй /update ещё раз.", reply_markup=MAIN_KB)
        context.user_data.pop("update_field", None)
        return

    # всплывающее меню
    if text in {"⚙️ Обновить данные"}:
        return update_cmd(update, context)
    if text in {"🕒 Время рассылки"}:
        return settime(update, context)
    if text in {"💳 Подписка"}:
        return subscribe(update, context)
    if text in {"👥 Рефералы"}:
        return referrals(update, context)
    if text in {"❌ Закрыть меню"}:
        return close_menu(update, context)

def update_cmd(update: Update, context: CallbackContext):
    update.message.reply_text("Что вы хотите изменить?", reply_markup=UPDATE_KB)

def unknown(update: Update, context: CallbackContext):
    update.message.reply_text("Не понял команду. Попробуй /menu.")

def broadcast(update: Update, context: CallbackContext):
    uid = update.effective_user.id
    if uid not in ADMIN_IDS:
        update.message.reply_text("Команда только для администратора.")
        return
    text = " ".join(context.args).strip()
    if not text:
        update.message.reply_text("Пример: /broadcast Текст рассылки")
        return
    ok = 0
    for chat_id in all_chat_ids():
        try:
            context.bot.send_message(chat_id=chat_id, text=text)
            ok += 1
        except Exception:
            pass
    update.message.reply_text(f"Готово. Разослано: {ok}")

# ============================ РЕГИСТРАЦИЯ ХЕНДЛЕРОВ ============================
def register_handlers(dp):
    dp.add_handler(CommandHandler("start", start))
    dp.add_handler(CommandHandler("menu", menu))
    dp.add_handler(CommandHandler("status", status))
    dp.add_handler(CommandHandler("referrals", referrals))
    dp.add_handler(CommandHandler("subscribe", subscribe))
    dp.add_handler(CommandHandler("settime", settime))
    dp.add_handler(CommandHandler("update", update_cmd))
    dp.add_handler(CommandHandler("broadcast", broadcast, pass_args=True))

    # общий текстовый роутер (кнопки и ввод)
    dp.add_handler(MessageHandler(Filters.text & ~Filters.command, text_router))
    dp.add_handler(MessageHandler(Filters.command, unknown))

# ============================ ЗАПУСК (WEBHOOK/POLLING) ============================
def run_bot() -> None:
    if not TOKEN:
        raise RuntimeError("TELEGRAM_BOT_TOKEN is not set")

    # Готовим БД
    init_db()

    # Updater + handlers
    updater = Updater(token=TOKEN, use_context=True)
    dp = updater.dispatcher
    register_handlers(dp)

    if truthy(USE_WEBHOOK):
        public_url = PUBLIC_URL.rstrip("/")
        secret = WEBHOOK_SECRET
        if not public_url or not secret:
            raise RuntimeError("PUBLIC_URL/WEBHOOK_SECRET must be set for webhook mode")

        port = int(os.environ.get("PORT", "10000"))

        # ВАЖНО: передаём внешний webhook_url, чтобы Telegram видел порт 80/443 на Render,
        # а не внутренний $PORT. Дополнительно НИЧЕГО не вызываем (никакого bot.set_webhook).
        updater.start_webhook(
            listen="0.0.0.0",
            port=port,
            url_path=secret,
            webhook_url=f"{public_url}/{secret}",
        )
        log.info("Webhook started: %s/%s (listen 0.0.0.0:%s)", public_url, secret, port)
        updater.idle()
    else:
        updater.start_polling(clean=True)
        log.info("Polling started")
        updater.idle()

if __name__ == "__main__":
    run_bot()
