# telegram_bot/bot.py
# -*- coding: utf-8 -*-

import logging
import os
import re
from datetime import datetime
from typing import Optional

import pytz
from apscheduler.schedulers.background import BackgroundScheduler
from telegram import ParseMode, ReplyKeyboardMarkup, ReplyKeyboardRemove, Update
from telegram.ext import (
    Updater, CommandHandler, MessageHandler, Filters, CallbackContext
)

from telegram_bot import database
from telegram_bot.astrology import generate_daily_message, UserData

# ---------- ЛОГИ ----------
logging.basicConfig(
    format="%(asctime)s %(levelname)s %(name)s | %(message)s", level=logging.INFO
)
log = logging.getLogger("bot")

# ---------- ОКРУЖЕНИЕ ----------
BOT_TOKEN = os.getenv("BOT_TOKEN", "")
PUBLIC_URL = os.getenv("PUBLIC_URL", "").rstrip("/")
WEBHOOK_SECRET = os.getenv("WEBHOOK_SECRET", "secret")
PORT = int(os.getenv("PORT", "10000"))

REFERRAL_BONUS_DAYS = int(os.getenv("REFERRAL_BONUS_DAYS", "10"))
PROJECT_TZ = os.getenv("TZ", "Europe/Berlin")
ADMIN_IDS = {int(x) for x in os.getenv("ADMIN_IDS", "").replace(" ", "").split(",") if x}

TIME_RE = re.compile(r"^([01]?\d|2[0-3]):([0-5]\d)$")

scheduler = BackgroundScheduler(timezone=pytz.timezone(PROJECT_TZ))

# ---------- МЕНЮ ----------
def main_menu(is_admin: bool) -> ReplyKeyboardMarkup:
    rows = [
        ["🕒 Изменить время", "🌍 Часовой пояс"],
        ["🧾 Обновить данные", "ℹ️ Мой статус"],
        ["⏰ Мой запуск (/mytime)"],
    ]
    if is_admin:
        rows.append(["⚙️ Админка"])
    return ReplyKeyboardMarkup(rows, resize_keyboard=True)

UPDATE_MENU = ReplyKeyboardMarkup(
    [
        ["Имя", "Дата рождения"],
        ["Место рождения", "Время рождения"],
        ["⬅️ Оставить как есть"],
    ],
    resize_keyboard=True,
    one_time_keyboard=True,
)

ADMIN_MENU = ReplyKeyboardMarkup(
    [
        ["📣 Рассылка", "➕ Бонусы"],
        ["🚫 Блокировка", "👀 Статистика"],
        ["⬅️ В меню"],
    ],
    resize_keyboard=True,
    one_time_keyboard=False,
)


def reply_menu(update: Update, text: str) -> None:
    uid = update.effective_user.id
    update.effective_message.reply_text(text, reply_markup=main_menu(uid in ADMIN_IDS))


# ---------- ДОСТУПНОСТЬ РАССЫЛКИ ----------
def _parse_date(d: Optional[str]):
    if not d:
        return None
    for fmt in ("%Y-%m-%d", "%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S"):
        try:
            return datetime.strptime(d, fmt).date()
        except Exception:
            pass
    return None


def can_receive_today(u: dict):
    status = (u.get("subscription_status") or "").lower()
    trial_until_raw = u.get("trial_until") or u.get("sub_until") or u.get("subscription_until")
    bonus_days = int(u.get("bonus_days") or 0)

    tzname = u.get("tz") or PROJECT_TZ
    tz = pytz.timezone(tzname)
    today = datetime.now(tz).date()

    if status == "active":
        return True, "active"

    trial_until = _parse_date(trial_until_raw)
    if trial_until and today <= trial_until:
        return True, "trial"

    if bonus_days > 0:
        return True, "bonus"

    return False, "expired"


# ---------- ПЛАНИРОВАНИЕ ----------
def schedule_job(user_id: int, daily_time: str, tzname: Optional[str]):
    tz = pytz.timezone(tzname or PROJECT_TZ)
    job_id = f"user-{user_id}"

    # удалить старую
    for j in scheduler.get_jobs():
        if j.id == job_id:
            j.remove()

    hh, mm = map(int, daily_time.split(":"))
    scheduler.add_job(
        send_daily_job, "cron",
        id=job_id, replace_existing=True,
        hour=hh, minute=mm, second=0, timezone=tz,
        args=[user_id]
    )
    log.info("Scheduled %s at %02d:%02d (%s)", job_id, hh, mm, tz.zone)


def send_daily_job(user_id: int):
    try:
        u = database.get_user(user_id)
        if not u or int(u.get("is_blocked") or 0) == 1:
            return

        ok, reason = can_receive_today(u)
        if not ok:
            log.info("Skip user %s: reason=%s", user_id, reason)
            return

        tzname = u.get("tz") or PROJECT_TZ
        userdata = UserData(
            name=u.get("first_name") or "",
            birth_date=u.get("birth_date") or "",
            birth_place=u.get("birth_place") or "",
            birth_time=u.get("birth_time") or "",
            tz=tzname,
        )
        text = generate_daily_message(userdata)

        chat_id = int(u["chat_id"])
        updater.bot.send_message(chat_id, text, parse_mode=ParseMode.HTML, disable_web_page_preview=True)

        if reason == "bonus":
            cur = int(u.get("bonus_days") or 0)
            if cur > 0:
                database.update_user_field(user_id, "bonus_days", cur - 1)
    except Exception:
        log.exception("send_daily_job failed for %s", user_id)


# ---------- КОМАНДЫ/ХЕНДЛЕРЫ ----------
def start(update: Update, context: CallbackContext):
    database.init_db()
    m = update.effective_message
    tgu = update.effective_user
    chat = update.effective_chat

    # создать/обновить пользователя
    database.upsert_user(
        tgu.id, chat.id, tgu.first_name or "", tz=PROJECT_TZ, username=tgu.username or ""
    )

    # deep-link referral: /start <code>
    if context.args:
        code = context.args[0]
        credited, referrer = database.handle_referral(tgu.id, code, REFERRAL_BONUS_DAYS)
        if credited:
            m.reply_text("Спасибо! Бонусные дни начислены вашему пригласившему 🌟")
        elif referrer == tgu.id:
            m.reply_text("Это ваша собственная ссылка — бонусы себе не начисляются 🙂")
        # если код левый — молча игнорируем

    # показать меню
    reply_menu(update, "Привет! Я готов присылать твой персональный дайджест.\nВыбери действие ниже.")

    # планировать, если время уже есть
    u = database.get_user(tgu.id)
    if u and u.get("daily_time"):
        schedule_job(tgu.id, u["daily_time"], u.get("tz"))


def menu(update: Update, context: CallbackContext):
    reply_menu(update, "Выбери действие:")


def mytime(update: Update, context: CallbackContext):
    u = database.get_user(update.effective_user.id)
    tzname = u.get("tz") or PROJECT_TZ
    tz = pytz.timezone(tzname)
    daily = u.get("daily_time") or "не задано"
    job_id = f"user-{u['user_id']}"
    jobs = [j for j in scheduler.get_jobs() if j.id == job_id]
    if jobs and jobs[0].next_run_time:
        nxt = jobs[0].next_run_time.astimezone(tz).strftime("%d.%m %H:%M %Z")
        update.effective_message.reply_text(f"⏰ Время: {daily} ({tzname})\n➡️ Следующий запуск: {nxt}",
                                            reply_markup=main_menu(update.effective_user.id in ADMIN_IDS))
    else:
        reply_menu(update, f"⏰ Время: {daily} ({tzname}). Задача ещё не поставлена.")


def settime(update: Update, context: CallbackContext):
    update.effective_message.reply_text(
        "Отправь время в формате ЧЧ:ММ (напр. 08:30).",
        reply_markup=ReplyKeyboardRemove(),
    )
    context.user_data["await_time"] = True


def settz(update: Update, context: CallbackContext):
    update.effective_message.reply_text(
        "Отправь IANA-таймзону, напр.: Europe/Berlin, Asia/Yekaterinburg.",
        reply_markup=ReplyKeyboardRemove(),
    )
    context.user_data["await_tz"] = True


def text_router(update: Update, context: CallbackContext):
    text = (update.effective_message.text or "").strip()
    uid = update.effective_user.id

    # ожидание времени
    if context.user_data.pop("await_time", False):
        if not TIME_RE.match(text):
            reply_menu(update, "Формат времени: ЧЧ:ММ, напр. 09:15.")
            return
        database.update_user_field(uid, "daily_time", text)
        u = database.get_user(uid)
        schedule_job(uid, text, u.get("tz"))
        reply_menu(update, "Сохранено ✅")
        return

    # ожидание TZ
    if context.user_data.pop("await_tz", False):
        try:
            pytz.timezone(text)  # проверка
        except Exception:
            reply_menu(update, "Некорректная таймзона. Пример: Asia/Yekaterinburg")
            return
        database.update_user_field(uid, "tz", text)
        u = database.get_user(uid)
        if u.get("daily_time"):
            schedule_job(uid, u["daily_time"], text)
        reply_menu(update, "Часовой пояс обновлён ✅")
        return

    # меню обновления профиля
    if text == "🧾 Обновить данные":
        update.effective_message.reply_text("Что вы хотите изменить?", reply_markup=UPDATE_MENU)
        context.user_data["await_update"] = True
        return

    if context.user_data.get("await_update"):
        if text == "⬅️ Оставить как есть":
            context.user_data["await_update"] = False
            reply_menu(update, "Меню открыто.")
            return
        elif text == "Имя":
            update.effective_message.reply_text("Отправьте новое имя.", reply_markup=ReplyKeyboardRemove())
            context.user_data["await_name"] = True
            return
        elif text == "Дата рождения":
            update.effective_message.reply_text("ДД.ММ.ГГГГ", reply_markup=ReplyKeyboardRemove())
            context.user_data["await_birth_date"] = True
            return
        elif text == "Место рождения":
            update.effective_message.reply_text("Город, страна", reply_markup=ReplyKeyboardRemove())
            context.user_data["await_birth_place"] = True
            return
        elif text == "Время рождения":
            update.effective_message.reply_text("ЧЧ:ММ", reply_markup=ReplyKeyboardRemove())
            context.user_data["await_birth_time"] = True
            return

    # поля
    if context.user_data.pop("await_name", False):
        database.update_user_field(uid, "first_name", text)
        reply_menu(update, "Имя обновлено ✅")
        return
    if context.user_data.pop("await_birth_date", False):
        database.update_user_field(uid, "birth_date", text)
        reply_menu(update, "Дата рождения обновлена ✅")
        return
    if context.user_data.pop("await_birth_place", False):
        database.update_user_field(uid, "birth_place", text)
        reply_menu(update, "Место рождения обновлено ✅")
        return
    if context.user_data.pop("await_birth_time", False):
        if not TIME_RE.match(text):
            reply_menu(update, "Формат ЧЧ:ММ, напр. 07:30.")
            return
        database.update_user_field(uid, "birth_time", text)
        reply_menu(update, "Время рождения обновлено ✅")
        return

    # кнопки главного меню
    if text == "🕒 Изменить время":
        return settime(update, context)
    if text == "🌍 Часовой пояс":
        return settz(update, context)
    if text == "ℹ️ Мой статус":
        u = database.get_user(uid)
        ok, reason = can_receive_today(u)
        reply_menu(update, f"Статус: {reason} (ok={ok})")
        return
    if text == "⏰ Мой запуск (/mytime)":
        return mytime(update, context)

    # админка
    if text == "⚙️ Админка" and uid in ADMIN_IDS:
        update.effective_message.reply_text("Админ-меню:", reply_markup=ADMIN_MENU)
        return

    if uid in ADMIN_IDS:
        if text == "⬅️ В меню":
            reply_menu(update, "Ок")
            return
        if text == "📣 Рассылка":
            update.effective_message.reply_text("Пришли текст рассылки. Он уйдёт всем (не заблокированным).",
                                                reply_markup=ReplyKeyboardRemove())
            context.user_data["await_broadcast"] = True
            return
        if text == "➕ Бонусы":
            update.effective_message.reply_text("Формат: <telegram_id> <дней> (например: 123456789 5)",
                                                reply_markup=ReplyKeyboardRemove())
            context.user_data["await_bonus"] = True
            return
        if text == "🚫 Блокировка":
            update.effective_message.reply_text("Команды: /ban <id> или /unban <id>", reply_markup=ADMIN_MENU)
            return
        if text == "👀 Статистика":
            s = database.stats()
            update.effective_message.reply_text(
                f"Всего: {s['total']}\nАктивных: {s['active']}\nОплативших: {s['paid']}\nРефералов: {s['refs']}",
                reply_markup=ADMIN_MENU,
            )
            return

    if context.user_data.pop("await_broadcast", False) and uid in ADMIN_IDS:
        count = 0
        for chat_id in database.all_active_chats():
            try:
                updater.bot.send_message(chat_id, text, parse_mode=ParseMode.HTML, disable_web_page_preview=True)
                count += 1
            except Exception:
                continue
        reply_menu(update, f"✅ Отправлено: {count}")
        return

    if context.user_data.pop("await_bonus", False) and uid in ADMIN_IDS:
        try:
            tg_id_s, days_s = text.split()
            database.add_bonus_days(int(tg_id_s), int(days_s))
            reply_menu(update, "Готово ✅")
        except Exception:
            reply_menu(update, "Неверный формат. Пример: 123456789 5")
        return

    # про бан/разбан
    if uid in ADMIN_IDS and text.startswith("/ban"):
        try:
            ban_id = int(text.split()[1])
            database.set_block(ban_id, True)
            reply_menu(update, f"Пользователь {ban_id} заблокирован ✅")
        except Exception:
            reply_menu(update, "Формат: /ban 123456")
        return
    if uid in ADMIN_IDS and text.startswith("/unban"):
        try:
            ban_id = int(text.split()[1])
            database.set_block(ban_id, False)
            reply_menu(update, f"Пользователь {ban_id} разблокирован ✅")
        except Exception:
            reply_menu(update, "Формат: /unban 123456")
        return

    # по умолчанию
    reply_menu(update, "Выбери действие:")


def error_handler(update: object, context: CallbackContext) -> None:
    log.exception("Unhandled error", exc_info=context.error)


def main():
    database.init_db()

    global updater
    updater = Updater(BOT_TOKEN, use_context=True)

    dp = updater.dispatcher
    dp.add_handler(CommandHandler("start", start, pass_args=True))
    dp.add_handler(CommandHandler("menu", menu))
    dp.add_handler(CommandHandler("settime", settime))
    dp.add_handler(CommandHandler("settz", settz))
    dp.add_handler(CommandHandler("mytime", mytime))
    dp.add_handler(MessageHandler(Filters.text & ~Filters.command, text_router))
    dp.add_error_handler(error_handler)

    if not scheduler.running:
        scheduler.start()

    # webhook на Render (порт 10000 внутри контейнера)
    updater.start_webhook(listen="0.0.0.0", port=PORT, url_path=WEBHOOK_SECRET)
    if PUBLIC_URL:
        updater.bot.set_webhook(f"{PUBLIC_URL}/{WEBHOOK_SECRET}")
        log.info("Webhook started: %s/%s", PUBLIC_URL, WEBHOOK_SECRET)
    else:
        log.warning("PUBLIC_URL is empty, falling back to polling")
        updater.start_polling()

    log.info("Service is live")
    updater.idle()


if __name__ == "__main__":
    main()