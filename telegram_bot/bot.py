# -*- coding: utf-8 -*-
"""
bot.py — Telegram-бот (PTB 13.15) с персональными ежедневными сообщениями.
- Webhook старт корректный (передаём webhook_url, без лишнего set_webhook)
- Fallback SQLite БД, если нет вашего database.py
- Пробный период 10 дней, подписка sub_until, бонус-дни bonus_days
- После любой правки данных — мгновенно возвращаем меню
"""

import os
import logging
import sqlite3
from contextlib import closing
from datetime import datetime, date, time as dtime

import pytz
from telegram import ReplyKeyboardMarkup, ReplyKeyboardRemove, Update, ParseMode
from telegram.ext import (
    Updater,
    CommandHandler,
    MessageHandler,
    Filters,
    CallbackContext,
)

# ==== безопасные импорты локальных модулей ====
try:
    # ваш config.py (если есть)
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

# персональный генератор текста (должен быть в telegram_bot/astrology.py)
from .astrology import generate_daily_message, UserData  # noqa: E402

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

TRIAL_DAYS = 10

def truthy(v) -> bool:
    return str(v).strip().lower() in {"1", "true", "yes", "y", "on"}

# ============================ БД (fallback, если нет вашего database.py) ============================
try:
    from . import database as db  # используем ваш модуль, если он есть
except Exception:
    db = None

def _fallback_init_db():
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    with closing(sqlite3.connect(DB_PATH, check_same_thread=False)) as con:
        con.execute("""
        CREATE TABLE IF NOT EXISTS users(
            user_id      INTEGER PRIMARY KEY,
            chat_id      INTEGER,
            name         TEXT,
            birth_date   TEXT,      -- "YYYY-MM-DD"
            birth_time   TEXT,      -- "HH:MM"
            birth_place  TEXT,
            daily_time   TEXT,      -- "HH:MM"
            trial_start  TEXT,      -- "YYYY-MM-DD"
            sub_until    TEXT,      -- "YYYY-MM-DD"
            bonus_days   INTEGER DEFAULT 0,
            referred_by  INTEGER,
            created_at   TEXT DEFAULT CURRENT_TIMESTAMP
        );
        """)
        con.commit()

def _fallback_get_user(uid: int):
    with closing(sqlite3.connect(DB_PATH, check_same_thread=False)) as con:
        con.row_factory = sqlite3.Row
        cur = con.execute("SELECT * FROM users WHERE user_id=?", (uid,))
        return cur.fetchone()

def _fallback_upsert_user(uid: int, chat_id: int):
    today = date.today().isoformat()
    with closing(sqlite3.connect(DB_PATH, check_same_thread=False)) as con:
        con.execute("""
            INSERT INTO users(user_id, chat_id, trial_start)
            VALUES(?, ?, ?)
            ON CONFLICT(user_id) DO UPDATE SET chat_id=excluded.chat_id
        """, (uid, chat_id, today))
        con.commit()

def _fallback_update_field(uid: int, field: str, value: str):
    allowed = {"name","birth_date","birth_time","birth_place","daily_time","sub_until","bonus_days","referred_by"}
    if field not in allowed:
        return
    with closing(sqlite3.connect(DB_PATH, check_same_thread=False)) as con:
        con.execute(f"UPDATE users SET {field}=? WHERE user_id=?", (value, uid))
        con.commit()

def _fallback_all_users():
    with closing(sqlite3.connect(DB_PATH, check_same_thread=False)) as con:
        con.row_factory = sqlite3.Row
        cur = con.execute("SELECT * FROM users WHERE chat_id IS NOT NULL")
        return cur.fetchall()

def _fallback_all_chat_ids():
    with closing(sqlite3.connect(DB_PATH, check_same_thread=False)) as con:
        cur = con.execute("SELECT chat_id FROM users WHERE chat_id IS NOT NULL")
        return [r[0] for r in cur.fetchall()]

def init_db():
    try:
        if db:
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
            u = db.get_user(uid)
            if not u:
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

def all_users():
    try:
        if db:
            return db.get_all_users()
        return _fallback_all_users()
    except Exception:
        return _fallback_all_users()

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

# ============================ ВСПОМОГАТЕЛЬНОЕ ============================
def parse_hhmm(s: str):
    try:
        hh, mm = s.split(":")
        return int(hh), int(mm)
    except Exception:
        return None

def to_utc_time(hh: int, mm: int) -> dtime:
    """Перевод личного времени (в TZ) в UTC-время для JobQueue.run_daily()."""
    local = TZINFO.localize(datetime.now().replace(hour=hh, minute=mm, second=0, microsecond=0))
    utc = local.astimezone(pytz.utc)
    return dtime(hour=utc.hour, minute=utc.minute, second=0)

def can_receive_today(u_row) -> (bool, str):
    """Решение о доставке: (ok, why_not/cta_text)."""
    today = date.today()
    # извлекаем поля безопасно
    trial_start = (u_row["trial_start"] if isinstance(u_row, dict) else getattr(u_row, "trial_start", None)) or None
    sub_until  = (u_row["sub_until"]  if isinstance(u_row, dict) else getattr(u_row, "sub_until", None)) or None
    bonus_days = (u_row["bonus_days"] if isinstance(u_row, dict) else getattr(u_row, "bonus_days", 0)) or 0

    # активная подписка?
    if sub_until:
        try:
            if date.fromisoformat(sub_until) >= today:
                return True, ""
        except Exception:
            pass

    # пробный период 10 дней
    if trial_start:
        try:
            d0 = date.fromisoformat(trial_start)
            if (today - d0).days < TRIAL_DAYS:
                return True, ""
        except Exception:
            pass

    # бонусные дни
    if bonus_days and int(bonus_days) > 0:
        # уменьшим на 1 — отправим
        new_val = int(bonus_days) - 1
        update_field(u_row["user_id"], "bonus_days", str(new_val))
        return True, ""

    # иначе — нельзя
    cta = (
        "Пробный период закончился. Оформи подписку или используй бонус-дни, если они есть: /subscribe /referrals"
    )
    return False, cta

def schedule_for_user(job_queue, u_row):
    """Ставит/переставляет ежедневную задачу для пользователя."""
    uid = u_row["user_id"] if isinstance(u_row, dict) else u_row[0]
    chat_id = u_row["chat_id"] if isinstance(u_row, dict) else u_row[1]
    daily_time = (u_row["daily_time"] if isinstance(u_row, dict) else None) or "09:00"
    parsed = parse_hhmm(daily_time) or (9, 0)
    when_utc = to_utc_time(*parsed)
    name = f"user-{uid}"
    # убрать прежние
    for j in job_queue.get_jobs_by_name(name):
        j.schedule_removal()
    # добавить новые
    job_queue.run_daily(callback=send_daily_job, time=when_utc, context={"user_id": uid, "chat_id": chat_id}, name=name)
    log.info("Scheduled %s at %s (UTC)", name, when_utc.strftime("%H:%M"))

# ============================ ХЕНДЛЕРЫ ============================
def start(update: Update, context: CallbackContext):
    user = update.effective_user
    ensure_user(user.id, update.effective_chat.id)

    # если пришёл по реферальной ссылке ?start=<code>
    if update.message and update.message.text and " " in update.message.text:
        try:
            _, code = update.message.text.split(" ", 1)
            code = code.strip()
            if code.isdigit():
                # не трогаем, если уже установлен
                u = get_user(user.id)
                ref_now = u["referred_by"] if u else None
                if not ref_now:
                    update_field(user.id, "referred_by", code)
                    # начисление бонусов пригласившему (10 дней)
                    try:
                        inviter = int(code)
                        inviter_row = get_user(inviter)
                        if inviter_row:
                            current = int(inviter_row["bonus_days"] or 0)
                            update_field(inviter, "bonus_days", str(current + 10))
                    except Exception:
                        pass
        except Exception:
            pass

    txt = (
        f"Привет, {user.first_name or 'друг'}! 🌟\n\n"
        "Я твой персональный астро-ассистент. Жми /menu, чтобы настроить профиль, "
        "установить время рассылки и получить реферальную ссылку."
    )
    update.message.reply_text(txt, reply_markup=MAIN_KB)

def menu(update: Update, context: CallbackContext):
    update.message.reply_text("Выберите действие:", reply_markup=MAIN_KB)

def close_menu(update: Update, context: CallbackContext):
    update.message.reply_text("Меню закрыто.", reply_markup=ReplyKeyboardRemove())

def status(update: Update, context: CallbackContext):
    uid = update.effective_user.id
    u = get_user(uid)
    dt = u["daily_time"] if u and u["daily_time"] else "не задано"
    sub = u["sub_until"] if u and u["sub_until"] else "нет"
    trial = u["trial_start"] if u and u["trial_start"] else "—"
    bonus = int(u["bonus_days"] or 0) if u else 0
    txt = (
        f"Время ежедневной рассылки: {dt}\n"
        f"Подписка до: {sub}\n"
        f"Пробный старт: {trial} (+{TRIAL_DAYS} дней)\n"
        f"Бонус-дней: {bonus}"
    )
    update.message.reply_text(txt)

def referrals(update: Update, context: CallbackContext):
    uid = update.effective_user.id
    bot_username = context.bot.username or "bot"
    link = f"https://t.me/{bot_username}?start={uid}"
    u = get_user(uid)
    bonus = int(u["bonus_days"] or 0) if u else 0
    text = (
        f"Твоя реферальная ссылка:\n{link}\n\n"
        f"Бонус-дней на счету: {bonus}\n"
        "За каждого приглашённого — +10 дней."
    )
    update.message.reply_text(text, disable_web_page_preview=True)

def subscribe(update: Update, context: CallbackContext):
    update.message.reply_text(
        "Платёжка подключится позже. Пока можно продлить доступ бонус-днями из /referrals.\n"
        "Как будешь готов — напомни /subscribe.",
    )

def settime(update: Update, context: CallbackContext):
    update.message.reply_text(
        "Введи время в формате ЧЧ:ММ (например, 08:30):", reply_markup=ReplyKeyboardRemove()
    )
    context.user_data["await_time"] = True

def update_cmd(update: Update, context: CallbackContext):
    update.message.reply_text("Что вы хотите изменить?", reply_markup=UPDATE_KB)

def text_router(update: Update, context: CallbackContext):
    text = (update.message.text or "").strip()

    # установка времени
    if context.user_data.pop("await_time", False):
        parsed = parse_hhmm(text)
        if not parsed:
            update.message.reply_text("Неверный формат. Пример: 08:30. Попробуй ещё раз /settime.")
            return
        update_field(update.effective_user.id, "daily_time", f"{parsed[0]:02d}:{parsed[1]:02d}")
        # пересоздаём задачу
        u = get_user(update.effective_user.id)
        schedule_for_user(context.job_queue, u)
        update.message.reply_text("Готово! Время сохранено.")
        return menu(update, context)

    # выбор поля
    if text in {"Имя", "Дата рождения", "Место рождения", "Время рождения"}:
        context.user_data["update_field"] = text
        update.message.reply_text("Введи новое значение:", reply_markup=ReplyKeyboardRemove())
        context.user_data["await_value"] = True
        return

    if text == "Оставить всё как есть":
        update.message.reply_text("Ок, без изменений.")
        return menu(update, context)

    # ввод значения
    if context.user_data.pop("await_value", False):
        fld_map = {
            "Имя": "name",
            "Дата рождения": "birth_date",    # ожидаем YYYY-MM-DD
            "Место рождения": "birth_place",
            "Время рождения": "birth_time",   # ожидаем HH:MM
        }
        fld = fld_map.get(context.user_data.get("update_field"))
        if fld:
            update_field(update.effective_user.id, fld, text)
            update.message.reply_text("Обновлено ✅")
        else:
            update.message.reply_text("Что-то пошло не так. Попробуй /update ещё раз.")
        context.user_data.pop("update_field", None)
        return menu(update, context)

    # кнопки главного меню
    if text == "⚙️ Обновить данные":
        return update_cmd(update, context)
    if text == "🕒 Время рассылки":
        return settime(update, context)
    if text == "💳 Подписка":
        return subscribe(update, context)
    if text == "👥 Рефералы":
        return referrals(update, context)
    if text == "❌ Закрыть меню":
        return close_menu(update, context)

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

# ============================ ДЕЙЛИ-ДОСТАВКА ============================
def send_daily_job(context: CallbackContext):
    uid = context.job.context["user_id"]
    chat_id = context.job.context["chat_id"]
    u = get_user(uid)
    if not u:
        return
    ok, reason = can_receive_today(u)
    if not ok:
        try:
            context.bot.send_message(chat_id=chat_id, text=reason)
        except Exception:
            pass
        return

    # готовим структуру для генератора
    name = u["name"] or ""
    bdate = (u["birth_date"] or "1970-01-01").strip()
    btime = (u["birth_time"] or "12:00").strip()
    ud = UserData(
        user_id=uid,
        name=name,
        birth_datetime_iso=f"{bdate} {btime}",
        daily_time=u["daily_time"] or "09:00",
    )
    text = generate_daily_message(ud)
    try:
        context.bot.send_message(chat_id=chat_id, text=text)
    except Exception as e:
        log.warning("send_daily failed for %s: %s", uid, e)

# ============================ РЕГИСТРАЦИЯ ХЕНДЛЕРОВ ============================
def register_handlers(dp, job_queue):
    dp.add_handler(CommandHandler("start", start))
    dp.add_handler(CommandHandler("menu", menu))
    dp.add_handler(CommandHandler("status", status))
    dp.add_handler(CommandHandler("referrals", referrals))
    dp.add_handler(CommandHandler("subscribe", subscribe))
    dp.add_handler(CommandHandler("settime", settime))
    dp.add_handler(CommandHandler("update", update_cmd))
    dp.add_handler(CommandHandler("broadcast", broadcast, pass_args=True))

    dp.add_handler(MessageHandler(Filters.text & ~Filters.command, text_router))
    dp.add_handler(MessageHandler(Filters.command, unknown))

    # при старте — поставить задачи для всех пользователей
    for u in all_users():
        try:
            schedule_for_user(job_queue, u)
        except Exception as e:
            log.warning("schedule user failed: %s", e)

# ============================ ЗАПУСК (WEBHOOK/POLLING) ============================
def run_bot() -> None:
    if not TOKEN:
        raise RuntimeError("TELEGRAM_BOT_TOKEN is not set")

    init_db()

    updater = Updater(token=TOKEN, use_context=True)
    dp = updater.dispatcher
    register_handlers(dp, updater.job_queue)

    if truthy(USE_WEBHOOK):
        public_url = PUBLIC_URL.rstrip("/")
        secret = WEBHOOK_SECRET
        if not public_url or not secret:
            raise RuntimeError("PUBLIC_URL/WEBHOOK_SECRET must be set for webhook mode")

        port = int(os.environ.get("PORT", "10000"))
        # ГЛАВНЫЙ ФИКС: передаём внешний webhook_url напрямую
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