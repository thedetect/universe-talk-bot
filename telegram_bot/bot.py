
from __future__ import annotations
import os, logging
from datetime import datetime
from functools import wraps

from telegram import ReplyKeyboardMarkup, ReplyKeyboardRemove, Update
from telegram.ext import Updater, CommandHandler, MessageHandler, Filters, CallbackContext, ConversationHandler

from . import config, database, referral, astrology, payments

logging.basicConfig(format="%(asctime)s %(levelname)s: %(message)s", level=logging.INFO)
logger = logging.getLogger(__name__)

NAME, BIRTH_DATE, BIRTH_PLACE, BIRTH_TIME, NOTIFY_TIME, MENU = range(6)

def admin_only(func):
    @wraps(func)
    def wrapper(update: Update, context: CallbackContext, *a, **kw):
        uid = update.effective_user.id
        if uid not in config.ADMIN_IDS:
            update.message.reply_text("Эта команда доступна только админу.")
            return
        return func(update, context, *a, **kw)
    return wrapper

def _main_keyboard():
    return ReplyKeyboardMarkup([
        ["Изменить время", "Рефералы"],
        ["Подписка", "Статус"],
        ["Обновить данные"],
        ["Закрыть меню"]
    ], resize_keyboard=True, one_time_keyboard=True)

def _valid_time(s: str) -> bool:
    try:
        datetime.strptime(s, "%H:%M"); return True
    except: return False

def _schedule_daily_jobs(updater: Updater):
    jq = updater.job_queue
    jq.stop(); jq.start()
    for u in database.get_all_users():
        t = (u.get("notify_time") or "09:00")
        try: hh, mm = [int(x) for x in t.split(":")]
        except: hh, mm = 9, 0
        def send_daily(ctx: CallbackContext, uid=u["telegram_id"], user=u):
            database.use_bonus_days_if_needed(uid)
            if database.has_access(uid):
                try: msg = astrology.daily_message(user)
                except Exception: msg = "Сегодня космос немного занят, но ты — нет. Сделай маленький шаг к мечте! ✨"
                ctx.bot.send_message(chat_id=uid, text=msg)
            else:
                ctx.bot.send_message(chat_id=uid,
                    text=("Пробный период закончился. "
                          "Продлить подписку: /subscribe или использовать бонусные дни от рефералов: /referrals"))
        from datetime import time as _time
        jq.run_daily(send_daily, time=_time(hour=hh, minute=mm), name=f"daily_{u['telegram_id']}")

def start(update: Update, context: CallbackContext):
    database.init_db()
    user = update.effective_user
    if context.args:
        code = context.args[0]
        try: referral.handle_referral(user.id, code)
        except Exception as e: logger.warning("Referral error: %s", e)
    database.upsert_user(user.id, created_at=datetime.utcnow().isoformat())
    database.award_trial_if_needed(user.id)
    update.message.reply_text("Привет! Давай познакомимся. Как тебя зовут?")
    return NAME

def name_step(update: Update, context: CallbackContext):
    name = update.message.text.strip()
    database.upsert_user(update.effective_user.id, name=name)
    update.message.reply_text("Дата рождения (ДД.ММ.ГГГГ)?")
    return BIRTH_DATE

def bdate_step(update: Update, context: CallbackContext):
    s = update.message.text.strip()
    try:
        d, m, y = [int(x) for x in s.split(".")]
        datetime(y,m,d)
    except:
        update.message.reply_text("Формат должен быть ДД.ММ.ГГГГ. Попробуем ещё раз:"); return BIRTH_DATE
    database.upsert_user(update.effective_user.id, birth_date=s)
    update.message.reply_text("Место рождения (город, страна)?"); return BIRTH_PLACE

def bplace_step(update: Update, context: CallbackContext):
    place = update.message.text.strip()
    database.upsert_user(update.effective_user.id, birth_place=place)
    update.message.reply_text("Время рождения (ЧЧ:ММ)? Если не знаешь — напиши 12:00"); return BIRTH_TIME

def btime_step(update: Update, context: CallbackContext):
    t = update.message.text.strip()
    if not _valid_time(t):
        update.message.reply_text("Формат должен быть ЧЧ:ММ, например 18:25. Введи ещё раз:"); return BIRTH_TIME
    database.upsert_user(update.effective_user.id, birth_time=t)
    update.message.reply_text("Во сколько каждый день присылать сообщение? (ЧЧ:ММ)"); return NOTIFY_TIME

def ntime_step(update: Update, context: CallbackContext):
    t = update.message.text.strip()
    if not _valid_time(t):
        update.message.reply_text("Формат должен быть ЧЧ:ММ. Введи ещё раз:"); return NOTIFY_TIME
    database.upsert_user(update.effective_user.id, notify_time=t)
    code = referral.assign_referral_code(update.effective_user.id)
    update.message.reply_text(
        "Отлично! Настройка завершена.\n"
        f"Твоя реферальная ссылка: https://t.me/{context.bot.username}?start={code}\n\n"
        "Открой /menu, чтобы управлять ботом.", reply_markup=_main_keyboard())
    return ConversationHandler.END

def menu_cmd(update: Update, context: CallbackContext):
    update.message.reply_text("\u200b", reply_markup=_main_keyboard()); return MENU

def menu_router(update: Update, context: CallbackContext):
    txt = (update.message.text or "").strip().lower()
    if "изменить время" in txt:
        update.message.reply_text("Введи новое время (ЧЧ:ММ):", reply_markup=ReplyKeyboardRemove()); return NOTIFY_TIME
    if "реферал" in txt:
        st = referral.get_referral_status(update.effective_user.id)
        update.message.reply_text(
            f"Твоя ссылка: https://t.me/{context.bot.username}?start={st['code']}\n"
            f"Приглашённых: {st['count']}\n"
            f"Бонусные дни: {st['bonus_days']}\n"
            f"Приглашённые: {', '.join(st['invited']) or '—'}", reply_markup=ReplyKeyboardRemove())
        return ConversationHandler.END
    if "подписка" in txt:
        update.message.reply_text(payments.offer_subscriptions_text(), reply_markup=ReplyKeyboardRemove()); return ConversationHandler.END
    if "статус" in txt:
        u = database.get_user(update.effective_user.id) or {}
        update.message.reply_text(
            f"Триал до: {u.get('trial_expiration') or '—'}\n"
            f"Подписка до: {u.get('subscription_until') or '—'}\n"
            f"Бонусные дни: {u.get('points') or 0}", reply_markup=ReplyKeyboardRemove()); return ConversationHandler.END
    if "обновить данные" in txt:
        update.message.reply_text("Введи имя:", reply_markup=ReplyKeyboardRemove()); return NAME
    if "закрыть меню" in txt:
        update.message.reply_text("Меню закрыто.", reply_markup=ReplyKeyboardRemove()); return ConversationHandler.END
    update.message.reply_text("Выбери пункт меню.", reply_markup=_main_keyboard()); return MENU

def settime_direct(update: Update, context: CallbackContext):
    t = update.message.text.strip()
    if not _valid_time(t):
        update.message.reply_text("Формат должен быть ЧЧ:ММ. Попробуй ещё раз:"); return NOTIFY_TIME
    database.upsert_user(update.effective_user.id, notify_time=t)
    update.message.reply_text(f"Готово. Новое время: {t}"); return ConversationHandler.END

def subscribe(update: Update, context: CallbackContext):
    update.message.reply_text(payments.offer_subscriptions_text())

def extend_30(update, ctx): payments.handle_extend(update, ctx, 30)
def extend_60(update, ctx): payments.handle_extend(update, ctx, 60)
def extend_90(update, ctx): payments.handle_extend(update, ctx, 90)
def extend_120(update, ctx): payments.handle_extend(update, ctx, 120)
def extend_180(update, ctx): payments.handle_extend(update, ctx, 180)

def referrals_cmd(update: Update, context: CallbackContext):
    st = referral.get_referral_status(update.effective_user.id)
    update.message.reply_text(
        f"Твоя ссылка: https://t.me/{context.bot.username}?start={st['code']}\n"
        f"Приглашённых: {st['count']}\nБонусные дни: {st['bonus_days']}")

def error_handler(update: object, context: CallbackContext):
    logger.exception("Bot error: %s", context.error)

def main():
    database.init_db()

    up = Updater(token=config.BOT_TOKEN, use_context=True)
    dp = up.dispatcher

    conv = ConversationHandler(
        entry_points=[CommandHandler("start", start), CommandHandler("menu", menu_cmd)],
        states={
            NAME: [MessageHandler(Filters.text & ~Filters.command, name_step)],
            BIRTH_DATE: [MessageHandler(Filters.text & ~Filters.command, bdate_step)],
            BIRTH_PLACE: [MessageHandler(Filters.text & ~Filters.command, bplace_step)],
            BIRTH_TIME: [MessageHandler(Filters.text & ~Filters.command, btime_step)],
            NOTIFY_TIME: [MessageHandler(Filters.text & ~Filters.command, ntime_step)],
            MENU: [MessageHandler(Filters.text & ~Filters.command, menu_router)],
        },
        fallbacks=[CommandHandler("menu", menu_cmd)],
    )
    dp.add_handler(conv)
    dp.add_handler(CommandHandler("subscribe", subscribe))
    dp.add_handler(CommandHandler("referrals", referrals_cmd))
    dp.add_handler(CommandHandler("extend_30", extend_30))
    dp.add_handler(CommandHandler("extend_60", extend_60))
    dp.add_handler(CommandHandler("extend_90", extend_90))
    dp.add_handler(CommandHandler("extend_120", extend_120))
    dp.add_handler(CommandHandler("extend_180", extend_180))
    dp.add_error_handler(error_handler)

    _schedule_daily_jobs(up)

    use_webhook = os.getenv("USE_WEBHOOK", "0") == "1"
    if use_webhook:
        public_url = os.getenv("PUBLIC_URL")
        secret = os.getenv("WEBHOOK_SECRET") or config.WEBHOOK_SECRET
        if not public_url or not secret:
            raise RuntimeError("PUBLIC_URL and WEBHOOK_SECRET must be set for webhook mode")
        port = int(os.environ.get("PORT", "10000"))
        up.start_webhook(listen="0.0.0.0", port=port, url_path=secret)
        up.bot.set_webhook(f"{public_url.rstrip('/')}/{secret}")
        up.idle()
    else:
        up.start_polling(); up.idle()

if __name__ == "__main__":
    main()
