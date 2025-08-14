# telegram_bot/bot.py
# -*- coding: utf-8 -*-

import logging
import os
import re
from datetime import datetime
from typing import Optional

import pytz
from apscheduler.schedulers.background import BackgroundScheduler
from telegram import (
    ReplyKeyboardMarkup,
    ReplyKeyboardRemove,
    Update,
    ParseMode,
)
from telegram.ext import (
    Updater,
    CommandHandler,
    MessageHandler,
    Filters,
    CallbackContext,
)

# Внутренние модули
from telegram_bot import database
from telegram_bot.astrology import generate_daily_message, UserData

# ---------------------- Логирование ----------------------
logging.basicConfig(
    format="%(asctime)s %(levelname)s %(name)s | %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger("bot")

# ---------------------- Конфиг/окружение ----------------------
BOT_TOKEN = os.getenv("BOT_TOKEN", "")
PUBLIC_URL = os.getenv("PUBLIC_URL", "").rstrip("/")
WEBHOOK_SECRET = os.getenv("WEBHOOK_SECRET", "secret")
PORT = int(os.getenv("PORT", "10000"))

LOCAL_TZ_NAME = os.getenv("TZ", "Europe/Berlin")
LOCAL_TZ = pytz.timezone(LOCAL_TZ_NAME)

# ---------------------- Планировщик ----------------------
scheduler = BackgroundScheduler(timezone=LOCAL_TZ)

# ---------------------- Вспомогательные ----------------------
MAIN_MENU = ReplyKeyboardMarkup(
    [
        ["🕒 Изменить время", "🧾 Обновить данные"],
        ["ℹ️ Мой статус", "⏰ Мой запуск (/mytime)"],
    ],
    resize_keyboard=True,
    one_time_keyboard=False,
)

UPDATE_MENU = ReplyKeyboardMarkup(
    [
        ["Имя", "Дата рождения"],
        ["Место рождения", "Время рождения"],
        ["⬅️ Оставить как есть"],
    ],
    resize_keyboard=True,
    one_time_keyboard=True,
)

TIME_RE = re.compile(r"^([01]?\d|2[0-3]):([0-5]\d)$")


def reply_menu(update: Update, text: str) -> None:
    update.effective_message.reply_text(text, reply_markup=MAIN_MENU)


def get_user(context: CallbackContext, update: Update) -> dict:
    tg_user = update.effective_user
    chat_id = update.effective_chat.id
    database.init_db()

    u = database.get_user(tg_user.id)
    if not u:
        database.upsert_user(
            user_id=tg_user.id,
            chat_id=chat_id,
            first_name=tg_user.first_name or "",
            tz=LOCAL_TZ_NAME,
        )
        u = database.get_user(tg_user.id)
    else:
        # держим chat_id актуальным
        if not u.get("chat_id") or int(u.get("chat_id")) != int(chat_id):
            database.update_user_field(tg_user.id, "chat_id", chat_id)
            u["chat_id"] = chat_id
    return u


# ---------------------- Проверка права на рассылку ----------------------
def _parse_date_iso(s: Optional[str]):
    if not s:
        return None
    for fmt in ("%Y-%m-%d", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S"):
        try:
            return datetime.strptime(s, fmt).date()
        except Exception:
            continue
    return None


def can_receive_today(u: dict):
    """
    Возвращает (ok, reason) где reason ∈ {'active','trial','bonus','expired'}.
    Поддерживает разные схемы БД (trial_until/sub_until/subscription_until).
    """
    status = (u.get("subscription_status") or "").lower()
    trial_until_raw = (
        u.get("trial_until") or u.get("sub_until") or u.get("subscription_until")
    )
    bonus_days = int(u.get("bonus_days") or 0)

    tzname = u.get("tz") or LOCAL_TZ_NAME
    tz = pytz.timezone(tzname)
    today = datetime.now(tz).date()

    if status == "active":
        return True, "active"

    trial_until = _parse_date_iso(trial_until_raw)
    if trial_until and today <= trial_until:
        return True, "trial"

    if bonus_days > 0:
        return True, "bonus"

    return False, "expired"


# ---------------------- Планирование ежедневной задачи ----------------------
def schedule_or_update_daily_job(user_id: int, daily_time: str, user_tz: Optional[str]):
    tz = pytz.timezone(user_tz or LOCAL_TZ_NAME)
    job_id = f"user-{user_id}"

    # Удалим прежнюю
    for j in scheduler.get_jobs():
        if j.id == job_id:
            j.remove()

    hour, minute = map(int, daily_time.split(":"))
    scheduler.add_job(
        send_daily_job,
        trigger="cron",
        id=job_id,
        replace_existing=True,
        hour=hour,
        minute=minute,
        second=0,
        timezone=tz,
        args=[user_id],
    )
    logger.info("Scheduled %s at %02d:%02d (%s)", job_id, hour, minute, tz)


# ---------------------- Отправка прогноза ----------------------
def send_daily_job(user_id: int):
    """Функция, которую вызывает APScheduler по расписанию."""
    try:
        u = database.get_user(user_id)
        if not u:
            logger.warning("User %s not found in DB", user_id)
            return

        ok, reason = can_receive_today(u)
        if not ok:
            logger.info("Skip %s: reason=%s", user_id, reason)
            return

        tzname = u.get("tz") or LOCAL_TZ_NAME
        tz = pytz.timezone(tzname)

        userdata = UserData(
            name=u.get("first_name") or "",
            birth_date=u.get("birth_date") or "",
            birth_place=u.get("birth_place") or "",
            birth_time=u.get("birth_time") or "",
            tz=tzname,
        )
        text = generate_daily_message(userdata)

        chat_id = int(u["chat_id"])
        updater.bot.send_message(
            chat_id=chat_id,
            text=text,
            parse_mode=ParseMode.HTML,
            disable_web_page_preview=True,
        )

        # если расходуем бонусный день
        if reason == "bonus":
            bd = int(u.get("bonus_days") or 0)
            if bd > 0:
                database.update_user_field(user_id, "bonus_days", bd - 1)

    except Exception:
        logger.exception("send_daily_job failed for user=%s", user_id)


# ---------------------- Хендлеры ----------------------
def start(update: Update, context: CallbackContext):
    u = get_user(context, update)
    reply_menu(update, "Привет! Я готов присылать твой персональный дайджест.\nВыбери действие ниже.")


def menu(update: Update, context: CallbackContext):
    reply_menu(update, "Выбери действие:")


def mytime(update: Update, context: CallbackContext):
    u = get_user(context, update)
    tzname = u.get("tz") or LOCAL_TZ_NAME
    tz = pytz.timezone(tzname)
    daily = u.get("daily_time") or "не задано"
    job_id = f"user-{u['user_id']}"
    jobs = [j for j in scheduler.get_jobs() if j.id == job_id]
    if jobs and jobs[0].next_run_time:
        nxt = jobs[0].next_run_time.astimezone(tz).strftime("%d.%m %H:%M %Z")
        msg = f"⏰ Ваше время: {daily} ({tzname})\n➡️ Следующий запуск: {nxt}"
    else:
        msg = f"⏰ Ваше время: {daily} ({tzname}). Задача пока не поставлена."
    update.effective_message.reply_text(msg)


# --- /settime ---
def settime(update: Update, context: CallbackContext):
    update.effective_message.reply_text(
        "Отправь время в формате ЧЧ:ММ (напр. 08:30).",
        reply_markup=ReplyKeyboardRemove(),
    )
    context.user_data["await_time"] = True


def text_router(update: Update, context: CallbackContext):
    msg = (update.effective_message.text or "").strip()

    # обработка ожидания времени
    if context.user_data.get("await_time"):
        context.user_data["await_time"] = False
        if not TIME_RE.match(msg):
            reply_menu(update, "Пожалуйста, укажи время в формате ЧЧ:ММ, например 09:15.")
            return

        u = get_user(context, update)
        database.update_user_field(u["user_id"], "daily_time", msg)

        # Перепланируем
        schedule_or_update_daily_job(u["user_id"], msg, u.get("tz"))
        reply_menu(update, "Сохранено ✅")
        return

    # меню обновления профиля
    if msg == "🧾 Обновить данные":
        update.effective_message.reply_text("Что вы хотите изменить?", reply_markup=UPDATE_MENU)
        context.user_data["await_update"] = True
        return

    if context.user_data.get("await_update"):
        if msg == "⬅️ Оставить как есть":
            context.user_data["await_update"] = False
            reply_menu(update, "Меню открыто.")
            return
        elif msg == "Имя":
            update.effective_message.reply_text("Отправьте новое имя.", reply_markup=ReplyKeyboardRemove())
            context.user_data["await_name"] = True
            return
        elif msg == "Дата рождения":
            update.effective_message.reply_text("Отправьте дату рождения в формате ДД.ММ.ГГГГ.",
                                                reply_markup=ReplyKeyboardRemove())
            context.user_data["await_birth_date"] = True
            return
        elif msg == "Место рождения":
            update.effective_message.reply_text("Отправьте «Город, страна».", reply_markup=ReplyKeyboardRemove())
            context.user_data["await_birth_place"] = True
            return
        elif msg == "Время рождения":
            update.effective_message.reply_text("Отправьте время рождения в формате ЧЧ:ММ.",
                                                reply_markup=ReplyKeyboardRemove())
            context.user_data["await_birth_time"] = True
            return

    # конкретные поля профиля
    if context.user_data.pop("await_name", False):
        u = get_user(context, update)
        database.update_user_field(u["user_id"], "first_name", msg)
        reply_menu(update, "Имя обновлено ✅")
        return

    if context.user_data.pop("await_birth_date", False):
        u = get_user(context, update)
        database.update_user_field(u["user_id"], "birth_date", msg)
        reply_menu(update, "Дата рождения обновлена ✅")
        return

    if context.user_data.pop("await_birth_place", False):
        u = get_user(context, update)
        database.update_user_field(u["user_id"], "birth_place", msg)
        reply_menu(update, "Место рождения обновлено ✅")
        return

    if context.user_data.pop("await_birth_time", False):
        if not TIME_RE.match(msg):
            reply_menu(update, "Время рождения — формат ЧЧ:ММ.")
            return
        u = get_user(context, update)
        database.update_user_field(u["user_id"], "birth_time", msg)
        reply_menu(update, "Время рождения обновлено ✅")
        return

    # кнопки главного меню
    if msg == "🕒 Изменить время":
        return settime(update, context)
    if msg in ("ℹ️ Мой статус",):
        u = get_user(context, update)
        ok, reason = can_receive_today(u)
        update.effective_message.reply_text(f"Статус: {reason} (ok={ok})", reply_markup=MAIN_MENU)
        return
    if msg in ("⏰ Мой запуск (/mytime)",):
        return mytime(update, context)

    # иначе просто покажем меню
    reply_menu(update, "Выбери действие:")


# ---------------------- Ошибки ----------------------
def error_handler(update: object, context: CallbackContext) -> None:
    logger.exception("Unhandled exception", exc_info=context.error)


# ---------------------- Главный запуск ----------------------
def main():
    database.init_db()

    global updater
    updater = Updater(BOT_TOKEN, use_context=True)
    dp = updater.dispatcher

    # Команды
    dp.add_handler(CommandHandler("start", start))
    dp.add_handler(CommandHandler("menu", menu))
    dp.add_handler(CommandHandler("settime", settime))
    dp.add_handler(CommandHandler("mytime", mytime))

    # Текст/кнопки
    dp.add_handler(MessageHandler(Filters.text & ~Filters.command, text_router))

    # Ошибки
    dp.add_error_handler(error_handler)

    # Планировщик
    if not scheduler.running:
        scheduler.start()

    # --- WEBHOOK на Render ---
    # Внутри слушаем порт 10000, снаружи Telegram ходит на https://PUBLIC_URL/WEBHOOK_SECRET
    updater.start_webhook(listen="0.0.0.0", port=PORT, url_path=WEBHOOK_SECRET)
    if PUBLIC_URL:
        updater.bot.set_webhook(f"{PUBLIC_URL}/{WEBHOOK_SECRET}")
        logger.info("Webhook started: %s/%s", PUBLIC_URL, WEBHOOK_SECRET)
    else:
        logger.warning("PUBLIC_URL is empty, falling back to polling")
        updater.start_polling()

    logger.info("Scheduler started")
    logger.info("Your service is live 💫")

    updater.idle()


if __name__ == "__main__":
    main()