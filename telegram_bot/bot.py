# -*- coding: utf-8 -*-
"""
ГЛАВНЫЙ ФАЙЛ БОТА.
Запуск:  python -m telegram_bot.bot

Требует ENV:
  BOT_TOKEN
  PUBLIC_URL = https://universe-talk-bot.onrender.com
  WEBHOOK_SECRET
  PORT = 10000
  USE_WEBHOOK = 1
  TZ = Europe/Berlin
  DB_PATH = /data/user_data_v2.db
  ADMIN_IDS = 12345,67890
  REFERRAL_BONUS_DAYS = 0 (или число)
  PYTHONUNBUFFERED = 1
"""

import os
import re
import json
import sqlite3
import logging
from datetime import datetime, time as dtime, timedelta, timezone
from typing import Optional, Dict, Any

import pytz
from apscheduler.triggers.cron import CronTrigger
from telegram import (
    Update, ReplyKeyboardMarkup, ReplyKeyboardRemove, ParseMode,
    InputFile,
)
from telegram.ext import (
    Updater, CallbackContext, CommandHandler, MessageHandler, Filters,
    ConversationHandler,
)

# Если используешь генератор сообщений из своего модуля:
try:
    from .astrology import generate_daily_message  # type: ignore
except Exception:
    # запасной генератор на случай отсутствия модуля
    def generate_daily_message(user_row: Dict[str, Any]) -> str:
        name = user_row.get("name") or "друг"
        return (
            f"Доброе утро, {name}!\n\n"
            "Сегодня — хороший день, чтобы сделать маленький шаг к важной цели. "
            "Один звонок, одно письмо, одна мысль — и вселенная подхватит тебя. 🌟\n\n"
            "Вопрос дня: какой шаг подарит мне ощущение движения прямо сейчас?"
        )

# -------------------- НАСТРОЙКИ/ENV --------------------

BOT_TOKEN = os.getenv("BOT_TOKEN", "")
PUBLIC_URL = os.getenv("PUBLIC_URL", "").rstrip("/")
WEBHOOK_SECRET = os.getenv("WEBHOOK_SECRET", "")
USE_WEBHOOK = os.getenv("USE_WEBHOOK", "1") in ("1", "true", "True", "yes", "YES")
PORT = int(os.getenv("PORT", "10000"))
DB_PATH = os.getenv("DB_PATH", "/data/user_data_v2.db")
SERVER_TZ = os.getenv("TZ", "Europe/Berlin")

ADMIN_IDS = {int(x) for x in re.split(r"[,\s]+", os.getenv("ADMIN_IDS", "").strip()) if x}
REFERRAL_BONUS_DAYS = int(os.getenv("REFERRAL_BONUS_DAYS", "0"))

# -------------------- ЛОГИ --------------------

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s | %(message)s"
)
log = logging.getLogger("bot")

# -------------------- DB --------------------

def db() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn

def init_db() -> None:
    conn = db()
    cur = conn.cursor()
    cur.executescript(
        """
        CREATE TABLE IF NOT EXISTS users (
            user_id        INTEGER PRIMARY KEY,
            chat_id        INTEGER,
            name           TEXT,
            birth_date     TEXT,
            birth_place    TEXT,
            birth_time     TEXT,
            tz             TEXT DEFAULT 'Europe/Berlin',
            send_time      TEXT DEFAULT '09:00', -- HH:MM в часовом поясе пользователя
            is_blocked     INTEGER DEFAULT 0,

            is_subscribed  INTEGER DEFAULT 0,
            sub_until      TEXT,               -- ISO timestamp в UTC
            bonus_days     INTEGER DEFAULT 0,

            referrer_id    INTEGER,
            ref_bonus_given INTEGER DEFAULT 0,

            created_at     TEXT
        );

        CREATE INDEX IF NOT EXISTS idx_users_sendtime ON users(send_time);
        """
    )
    conn.commit()
    conn.close()

def row_to_dict(row: sqlite3.Row) -> Dict[str, Any]:
    return dict(row) if row else {}

def get_user(user_id: int) -> Dict[str, Any]:
    conn = db()
    cur = conn.cursor()
    cur.execute("SELECT * FROM users WHERE user_id=?", (user_id,))
    r = cur.fetchone()
    conn.close()
    return row_to_dict(r)

def upsert_user(user_id: int, chat_id: Optional[int] = None) -> None:
    conn = db()
    cur = conn.cursor()
    now_iso = datetime.now(timezone.utc).isoformat()
    cur.execute("SELECT user_id FROM users WHERE user_id=?", (user_id,))
    if cur.fetchone():
        if chat_id:
            cur.execute("UPDATE users SET chat_id=? WHERE user_id=?", (chat_id, user_id))
    else:
        cur.execute(
            "INSERT INTO users(user_id, chat_id, created_at) VALUES(?,?,?)",
            (user_id, chat_id, now_iso),
        )
    conn.commit()
    conn.close()

def update_user(user_id: int, **fields: Any) -> None:
    if not fields:
        return
    keys = ", ".join([f"{k}=?" for k in fields.keys()])
    values = list(fields.values()) + [user_id]
    conn = db()
    cur = conn.cursor()
    cur.execute(f"UPDATE users SET {keys} WHERE user_id=?", values)
    conn.commit()
    conn.close()

def all_users() -> list:
    conn = db()
    cur = conn.cursor()
    cur.execute("SELECT * FROM users WHERE is_blocked=0")
    rows = [row_to_dict(r) for r in cur.fetchall()]
    conn.close()
    return rows

# -------------------- УТИЛИТЫ --------------------

def _parse_iso(dt: str):
    try:
        return datetime.fromisoformat(dt.replace("Z", "+00:00"))
    except Exception:
        return None

def can_receive_today(u: Dict[str, Any]) -> bool:
    """
    Безопасная логика допуска к рассылке:
      - активная подписка, или
      - bonus_days > 0, или
      - триал 10 дней с момента регистрации, или
      - sub_until в будущем.
    НИКОГДА не кидает KeyError, даже если поля нет.
    """
    if not u or u.get("is_blocked"):
        return False

    is_sub = bool(u.get("is_subscribed"))
    if is_sub:
        return True

    bonus_days = int(u.get("bonus_days") or 0)
    if bonus_days > 0:
        return True

    now = datetime.now(timezone.utc)
    sub_until = u.get("sub_until")
    if sub_until:
        dt = _parse_iso(sub_until)
        if dt and dt > now:
            return True

    created_at = _parse_iso(u.get("created_at") or "")
    if created_at and (now - created_at).days < 10:
        return True

    return False

def parse_time_hhmm(s: str) -> Optional[dtime]:
    m = re.match(r"^([01]?\d|2[0-3]):([0-5]\d)$", s.strip())
    if not m:
        return None
    return dtime(int(m.group(1)), int(m.group(2)))

def user_tz(u: Dict[str, Any]) -> pytz.BaseTzInfo:
    tz_name = u.get("tz") or "Europe/Berlin"
    try:
        return pytz.timezone(tz_name)
    except Exception:
        return pytz.timezone("Europe/Berlin")

def send_main_menu(update: Update, context: CallbackContext, text: str = "Выберите действие:") -> None:
    uid = update.effective_user.id
    is_admin = uid in ADMIN_IDS
    kb = [
        ["🕒 Изменить время", "🗺 Часовой пояс"],
        ["📝 Обновить анкету", "📣 Рефералы"],
        ["🔔 Статус", "❌ Отмена"],
    ]
    if is_admin:
        kb.append(["👑 Админка"])
    update.message.reply_text(text, reply_markup=ReplyKeyboardMarkup(kb, resize_keyboard=True))

# -------------------- РАССЫЛКА --------------------

def build_daily_text(user_row: Dict[str, Any]) -> str:
    # Подставляем твой генератор (астрология/skyfield)
    msg = generate_daily_message(user_row)
    # Можно добавить «девиз дня» из базы/файла, если нужно
    return msg

def job_name(uid: int) -> str:
    return f"user-{uid}"

def schedule_user_job(context: CallbackContext, u: Dict[str, Any]) -> None:
    """Создаём/пересоздаём ежедневную джобу для пользователя по его TZ и времени."""
    uid = u["user_id"]
    st = u.get("send_time") or "09:00"
    t = parse_time_hhmm(st) or dtime(9, 0)
    tz = user_tz(u)

    # удаляем старую
    for j in context.job_queue.get_jobs_by_name(job_name(uid)):
        j.remove()

    trigger = CronTrigger(hour=t.hour, minute=t.minute, second=0, timezone=tz)
    context.job_queue.run_job(
        func=send_daily_job,
        name=job_name(uid),
        job_kwargs={"trigger": trigger},
        context={"user_id": uid},
    )
    log.info("Scheduled user %s at %s (%s)", uid, st, tz)

def reschedule_all(context: CallbackContext) -> None:
    users = all_users()
    for u in users:
        if u.get("send_time"):
            schedule_user_job(context, u)

def send_daily_job(context: CallbackContext) -> None:
    """Вызывается APScheduler-ом по расписанию."""
    try:
        uid = context.job.context["user_id"]
        u = get_user(uid)
        if not u:
            return
        if not can_receive_today(u):
            return
        chat_id = u.get("chat_id")
        if not chat_id:
            return
        text = build_daily_text(u)
        context.bot.send_message(chat_id=chat_id, text=text)
        # если расходуем бонусные дни — уменьшаем
        if not u.get("is_subscribed"):
            bd = int(u.get("bonus_days") or 0)
            if bd > 0:
                update_user(uid, bonus_days=bd - 1)
    except Exception:
        log.exception("send_daily_job error")

# -------------------- РЕФЕРАЛЫ --------------------

def handle_deeplink_ref(update: Update) -> Optional[int]:
    """Возвращает referrer_id, если стартовали с параметром."""
    args = update.message.text.strip().split(maxsplit=1)
    if len(args) == 2 and args[0].lower().startswith("/start"):
        ref = args[1].strip()
        if ref.isdigit():
            return int(ref)
    return None

def accrue_ref_bonus(new_user_id: int, referrer_id: int) -> None:
    """Начисляем бонусные дни рефереру, если это НЕ самореферал и ещё не начисляли."""
    if referrer_id == new_user_id:
        return
    r = get_user(referrer_id)
    if not r:
        return
    if int(r.get("is_blocked") or 0):
        return
    # проверка уже выдавали или нет
    u_new = get_user(new_user_id)
    if int(u_new.get("ref_bonus_given") or 0):
        return
    bonus = REFERRAL_BONUS_DAYS
    if bonus > 0:
        new_bonus = int(r.get("bonus_days") or 0) + bonus
        update_user(referrer_id, bonus_days=new_bonus)
    update_user(new_user_id, ref_bonus_given=1, referrer_id=referrer_id)

# -------------------- ДИАЛОГИ --------------------

ASK_NAME, ASK_BDATE, ASK_BPLACE, ASK_BTIME = range(4)

def start(update: Update, context: CallbackContext) -> int:
    user = update.effective_user
    upsert_user(user.id, update.effective_chat.id)

    # deeplink
    ref = handle_deeplink_ref(update)
    if ref:
        # фикс: не начисляем если реферал запускает свою же ссылку
        accrue_ref_bonus(user.id, ref)

    update.message.reply_text(
        "Привет! Я твой персональный астробот.\n"
        "Давай заполним анкету — это займёт 1–2 минуты.\n\n"
        "Как тебя зовут?",
        reply_markup=ReplyKeyboardRemove(),
    )
    return ASK_NAME

def ask_name(update: Update, context: CallbackContext) -> int:
    name = update.message.text.strip()
    update_user(update.effective_user.id, name=name)
    update.message.reply_text("Дата рождения (ДД.ММ.ГГГГ)?")
    return ASK_BDATE

def ask_bdate(update: Update, context: CallbackContext) -> int:
    s = update.message.text.strip()
    if not re.match(r"^\d{2}\.\d{2}\.\d{4}$", s):
        update.message.reply_text("Формат ДД.ММ.ГГГГ, попробуй ещё раз.")
        return ASK_BDATE
    update_user(update.effective_user.id, birth_date=s)
    update.message.reply_text("Место рождения (город, страна)?")
    return ASK_BPLACE

def ask_bplace(update: Update, context: CallbackContext) -> int:
    s = update.message.text.strip()
    update_user(update.effective_user.id, birth_place=s)
    update.message.reply_text("Время рождения (часы:минуты, например 18:25)?")
    return ASK_BTIME

def ask_btime(update: Update, context: CallbackContext) -> int:
    s = update.message.text.strip()
    if not re.match(r"^([01]?\d|2[0-3]):([0-5]\d)$", s):
        update.message.reply_text("Формат ЧЧ:ММ, попробуй ещё раз.")
        return ASK_BTIME
    update_user(update.effective_user.id, birth_time=s)
    send_main_menu(update, context, "Готово! Анкета сохранена.\n")
    return ConversationHandler.END

def menu(update: Update, context: CallbackContext) -> None:
    send_main_menu(update, context)

def handle_menu_buttons(update: Update, context: CallbackContext) -> None:
    t = (update.message.text or "").strip()
    uid = update.effective_user.id

    if t == "🕒 Изменить время":
        update.message.reply_text("Укажи местное время для ежедневного сообщения (ЧЧ:ММ):",
                                  reply_markup=ReplyKeyboardRemove())
        context.user_data["waiting"] = "send_time"
        return

    if t == "🗺 Часовой пояс":
        update.message.reply_text("Введи название часового пояса (например, Asia/Yekaterinburg):",
                                  reply_markup=ReplyKeyboardRemove())
        context.user_data["waiting"] = "tz"
        return

    if t == "📝 Обновить анкету":
        return start(update, context)

    if t == "📣 Рефералы":
        me = get_user(uid)
        link = f"https://t.me/{context.bot.username}?start={uid}"
        invited = []  # можно хранить отдельно; тут просто ссылка и баланс
        reply = (
            f"Твоя реферальная ссылка:\n{link}\n\n"
            f"Бонусные дни: {int(me.get('bonus_days') or 0)}\n"
            f"Поделись ссылкой — и получай бонусные дни."
        )
        update.message.reply_text(reply)
        return send_main_menu(update, context)

    if t == "🔔 Статус":
        me = get_user(uid)
        is_sub = "да" if int(me.get("is_subscribed") or 0) else "нет"
        sub_until = me.get("sub_until") or "—"
        bonus = int(me.get("bonus_days") or 0)
        trial_info = ""
        cr = _parse_iso(me.get("created_at") or "")
        if cr:
            days = (datetime.now(timezone.utc) - cr).days
            if days < 10:
                trial_info = f"\nПробный период: осталось {max(0, 9 - days)} дн."
        update.message.reply_text(
            f"Подписка: {is_sub}\nДействует до: {sub_until}\nБонусные дни: {bonus}{trial_info}"
        )
        return send_main_menu(update, context)

    if t == "❌ Отмена":
        return send_main_menu(update, context, "Меню закрыто.")

    if t == "👑 Админка" and uid in ADMIN_IDS:
        kb = [
            ["📤 Broadcast", "🔧 Начислить бонус"],
            ["🚫 Блок", "✅ Разблок"],
            ["ℹ️ Пользователь", "⬅️ Назад"],
        ]
        update.message.reply_text("Админ-меню:", reply_markup=ReplyKeyboardMarkup(kb, resize_keyboard=True))
        context.user_data["admin"] = True
        return

    # обработка admin-меню
    if context.user_data.get("admin"):
        if t == "⬅️ Назад":
            context.user_data["admin"] = False
            return send_main_menu(update, context)

        if t == "📤 Broadcast":
            update.message.reply_text("Отправь текст рассылки:",
                                      reply_markup=ReplyKeyboardRemove())
            context.user_data["admin_wait"] = "broadcast"
            return
        if t == "🔧 Начислить бонус":
            update.message.reply_text("Формат: user_id пробел дни. Пример: 123456 5",
                                      reply_markup=ReplyKeyboardRemove())
            context.user_data["admin_wait"] = "bonus"
            return
        if t == "🚫 Блок":
            update.message.reply_text("Укажи user_id для блокировки:",
                                      reply_markup=ReplyKeyboardRemove())
            context.user_data["admin_wait"] = "block"
            return
        if t == "✅ Разблок":
            update.message.reply_text("Укажи user_id для разблокировки:",
                                      reply_markup=ReplyKeyboardRemove())
            context.user_data["admin_wait"] = "unblock"
            return
        if t == "ℹ️ Пользователь":
            update.message.reply_text("Укажи user_id:",
                                      reply_markup=ReplyKeyboardRemove())
            context.user_data["admin_wait"] = "info"
            return

    # если ждём конкретный ввод
    waiting = context.user_data.pop("waiting", None)
    if waiting == "send_time":
        tm = parse_time_hhmm(t)
        if not tm:
            update.message.reply_text("Формат ЧЧ:ММ. Попробуй ещё раз.")
            context.user_data["waiting"] = "send_time"
            return
        update_user(uid, send_time=f"{tm.hour:02d}:{tm.minute:02d}")
        schedule_user_job(context, get_user(uid))
        send_main_menu(update, context, f"Сохранил время {tm.hour:02d}:{tm.minute:02d}.")
        return

    if waiting == "tz":
        try:
            _ = pytz.timezone(t)
        except Exception:
            update.message.reply_text("Неверный TZ. Примеры: Europe/Berlin, Asia/Yekaterinburg")
            context.user_data["waiting"] = "tz"
            return
        update_user(uid, tz=t)
        schedule_user_job(context, get_user(uid))
        send_main_menu(update, context, f"Часовой пояс сохранён: {t}")
        return

    # ожидание админских вводов
    aw = context.user_data.pop("admin_wait", None)
    if aw and uid in ADMIN_IDS:
        if aw == "broadcast":
            text = update.message.text
            cnt = 0
            for u in all_users():
                try:
                    if u.get("chat_id") and not u.get("is_blocked"):
                        context.bot.send_message(chat_id=u["chat_id"], text=text)
                        cnt += 1
                except Exception:
                    pass
            update.message.reply_text(f"Отправлено {cnt} пользователям.")
            return send_main_menu(update, context)
        if aw == "bonus":
            m = re.match(r"^\s*(\d+)\s+(\-?\d+)\s*$", update.message.text or "")
            if not m:
                update.message.reply_text("Нужно: user_id и дни (число).")
                context.user_data["admin_wait"] = "bonus"
                return
            uid2 = int(m.group(1))
            days = int(m.group(2))
            u2 = get_user(uid2)
            if not u2:
                update.message.reply_text("Нет такого пользователя.")
                return send_main_menu(update, context)
            new_b = int(u2.get("bonus_days") or 0) + days
            update_user(uid2, bonus_days=max(0, new_b))
            update.message.reply_text("Готово.")
            return send_main_menu(update, context)
        if aw == "block":
            uid2 = int(update.message.text.strip())
            update_user(uid2, is_blocked=1)
            update.message.reply_text("Заблокирован.")
            return send_main_menu(update, context)
        if aw == "unblock":
            uid2 = int(update.message.text.strip())
            update_user(uid2, is_blocked=0)
            update.message.reply_text("Разблокирован.")
            return send_main_menu(update, context)
        if aw == "info":
            uid2 = int(update.message.text.strip())
            u2 = get_user(uid2)
            if not u2:
                update.message.reply_text("Нет такого пользователя.")
            else:
                update.message.reply_text("```\n" + json.dumps(u2, ensure_ascii=False, indent=2) + "\n```",
                                          parse_mode=ParseMode.MARKDOWN)
            return send_main_menu(update, context)

# -------------------- КОМАНДЫ --------------------

def cmd_menu(update: Update, context: CallbackContext):
    return send_main_menu(update, context)

def cmd_stop(update: Update, context: CallbackContext):
    update.message.reply_text("Диалог завершён.", reply_markup=ReplyKeyboardRemove())
    return ConversationHandler.END

# -------------------- MAIN --------------------

def main() -> None:
    init_db()

    updater = Updater(BOT_TOKEN, use_context=True)
    dp = updater.dispatcher

    # Диалог анкеты
    conv = ConversationHandler(
        entry_points=[CommandHandler("start", start)],
        states={
            ASK_NAME:   [MessageHandler(Filters.text & ~Filters.command, ask_name)],
            ASK_BDATE:  [MessageHandler(Filters.text & ~Filters.command, ask_bdate)],
            ASK_BPLACE: [MessageHandler(Filters.text & ~Filters.command, ask_bplace)],
            ASK_BTIME:  [MessageHandler(Filters.text & ~Filters.command, ask_btime)],
        },
        fallbacks=[CommandHandler("stop", cmd_stop)],
        allow_reentry=True,
    )
    dp.add_handler(conv)

    # Меню
    dp.add_handler(CommandHandler("menu", cmd_menu))
    dp.add_handler(MessageHandler(Filters.text & ~Filters.command, handle_menu_buttons))

    # Пересоздаём джобы при старте
    updater.job_queue.run_once(lambda c: reschedule_all(c), 1)

    # ---------- Старт через webhook c авто-фолбэком на polling ----------
    webhook_url = f"{PUBLIC_URL}/{WEBHOOK_SECRET}" if (USE_WEBHOOK and PUBLIC_URL and WEBHOOK_SECRET) else None
    try:
        if webhook_url:
            updater.start_webhook(
                listen="0.0.0.0",
                port=PORT,
                url_path=WEBHOOK_SECRET,
                webhook_url=webhook_url,
            )
            log.info("Webhook started at %s", webhook_url)
        else:
            updater.start_polling()
            log.info("Polling started")
    except Exception:
        log.exception("Webhook failed, fallback to polling()")
        updater.start_polling()

    updater.idle()


if __name__ == "__main__":
    main()
