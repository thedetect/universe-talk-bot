"""
Main entry point for the Telegram astrology bot.

This script wires together the different modules of the project and
exposes a fully functional Telegram bot.  It handles registration,
input of personal data, scheduling of daily messages, payment
processing for subscriptions, a loyalty program, and simple
administrative commands.  The code is written to be clear and
maintainable, with extensive comments explaining each step of the
process.
"""

from __future__ import annotations

import logging
import re
from datetime import datetime, time
from typing import Dict, Optional, Tuple

from telegram import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    ReplyKeyboardMarkup,
    ReplyKeyboardRemove,
    Update,
)
from telegram.ext import (
    Updater,
    CommandHandler,
    CallbackContext,
    ConversationHandler,
    MessageHandler,
    Filters,
    PreCheckoutQueryHandler,
    CallbackQueryHandler,
)

from . import astrology, config, database, payments, referral


# Enable logging to aid debugging and production monitoring
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
)
logger = logging.getLogger(__name__)


# Define conversation states for the registration process
(
    NAME,
    BIRTH_DATE,
    BIRTH_PLACE,
    BIRTH_TIME,
    NOTIFY_TIME,
    CHANGE_TIME,
    SELECT_FIELD,
    TYPING_VALUE,
) = range(8)


def parse_date(date_str: str) -> Optional[str]:
    """Parse a date string in DD.MM.YYYY format into ISO YYYY-MM-DD.

    Returns None on failure.
    """
    try:
        day, month, year = map(int, date_str.strip().split("."))
        dt = datetime(year, month, day)
        return dt.date().isoformat()
    except Exception:
        return None


def parse_time_str(time_str: str) -> Optional[str]:
    """Parse a time string in HH:MM format into canonical HH:MM.

    Returns None on failure or if hours/minutes are out of range.
    """
    match = re.fullmatch(r"(\d{1,2}):(\d{2})", time_str.strip())
    if not match:
        return None
    hour = int(match.group(1))
    minute = int(match.group(2))
    if hour < 0 or hour > 23 or minute < 0 or minute > 59:
        return None
    return f"{hour:02d}:{minute:02d}"


def schedule_daily_job(context: CallbackContext, telegram_id: int, schedule_str: str) -> None:
    """Schedule or reschedule the daily horoscope job for a user.

    This helper expects a CallbackContext instance (provided by
    telegram.ext during command handling) and uses its job queue.
    If a job already exists for the user, it is removed before
    scheduling a new one.
    """
    job_name = f"daily_{telegram_id}"
    current_jobs = context.job_queue.get_jobs_by_name(job_name)
    for job in current_jobs:
        job.schedule_removal()
    try:
        hour, minute = map(int, schedule_str.split(":"))
        target_time = time(hour=hour, minute=minute)
    except Exception:
        logger.warning(f"Invalid schedule format for user {telegram_id}: {schedule_str}")
        return
    context.job_queue.run_daily(
        send_daily_horoscope,
        time=target_time,
        days=(0, 1, 2, 3, 4, 5, 6),
        context=telegram_id,
        name=job_name,
    )
    logger.info(f"Scheduled daily message for user {telegram_id} at {schedule_str}")


def schedule_job_with_jobqueue(job_queue, telegram_id: int, schedule_str: str) -> None:
    """Schedule a daily job using a bare JobQueue instance.

    This function mirrors `schedule_daily_job` but accepts a JobQueue
    directly instead of a CallbackContext.  It is useful at startup
    when no context is available.
    """
    job_name = f"daily_{telegram_id}"
    for job in job_queue.get_jobs_by_name(job_name):
        job.schedule_removal()
    try:
        hour, minute = map(int, schedule_str.split(":"))
        target_time = time(hour=hour, minute=minute)
    except Exception:
        logger.warning(f"Invalid schedule format for user {telegram_id}: {schedule_str}")
        return
    job_queue.run_daily(
        send_daily_horoscope,
        time=target_time,
        days=(0, 1, 2, 3, 4, 5, 6),
        context=telegram_id,
        name=job_name,
    )
    logger.info(f"Scheduled daily message for user {telegram_id} at {schedule_str}")


def send_daily_horoscope(context: CallbackContext) -> None:
    """Callback function executed by the job queue to send daily messages.

    It checks the user's subscription status; if active, it calculates
    the horoscope and sends it.  Otherwise it sends a reminder to
    subscribe and a sample message.
    """
    telegram_id = context.job.context
    user = database.get_user(telegram_id)
    if not user:
        return
    chat_id = telegram_id
    # If user has an active subscription, send the full message
    if user["subscription_active"]:
        message = astrology.get_daily_message(
            user["birth_date"], user["birth_time"]
        )
        context.bot.send_message(chat_id=chat_id, text=message)
    else:
        # If not subscribed, send a limited preview and reminder
        preview = astrology.get_daily_message(
            user["birth_date"], user["birth_time"]
        ).split("\n")[0]  # First line as preview
        reminder = (
            f"{preview}\n\n"
            "Для доступа к полному ежедневному сообщению оформите подписку с помощью команды /subscribe."
        )
        context.bot.send_message(chat_id=chat_id, text=reminder)


def start(update: Update, context: CallbackContext) -> int:
    """Entry point for the /start command.

    If the user is new or missing data, initiate the registration
    conversation.  Otherwise greet the user and present the menu.
    Handles referral codes passed as arguments.
    """
    telegram_id = update.effective_user.id
    # Handle referral code in start parameter if present
    if context.args:
        ref_code = context.args[0]
        referral.handle_referral(ref_code, telegram_id)
    # Check if the user already exists
    user = database.get_user(telegram_id)
    if user and user["name"] and user["birth_date"] and user["birth_time"]:
        # Existing user: greet and show menu
        update.message.reply_text(
            f"Привет, {user['name']}! Рады снова тебя видеть. Используй /menu для доступа к функциям."
        )
        # Ensure a daily job is scheduled
        if user["schedule_time"]:
            schedule_daily_job(context, telegram_id, user["schedule_time"])
        return ConversationHandler.END
    else:
        # New user or incomplete profile: start registration
        update.message.reply_text(
            "Привет! Давай познакомимся. Как тебя зовут?",
            reply_markup=ReplyKeyboardRemove(),
        )
        return NAME


def ask_birth_date(update: Update, context: CallbackContext) -> int:
    """Store the user's name and ask for birth date."""
    context.user_data["name"] = update.message.text.strip()
    update.message.reply_text(
        "Отлично, {0}! Пожалуйста, введи дату своего рождения (ДД.ММ.ГГГГ).".format(
            context.user_data["name"]
        )
    )
    return BIRTH_DATE


def ask_birth_place(update: Update, context: CallbackContext) -> int:
    """Validate birth date and ask for birth place."""
    date_str = update.message.text.strip()
    iso_date = parse_date(date_str)
    if not iso_date:
        update.message.reply_text(
            "Не удалось распознать дату. Укажи её в формате ДД.ММ.ГГГГ, например, 05.06.1990."
        )
        return BIRTH_DATE
    context.user_data["birth_date"] = iso_date
    update.message.reply_text(
        "Спасибо! В каком городе и стране ты родился? (например, Москва, Россия)"
    )
    return BIRTH_PLACE


def ask_birth_time(update: Update, context: CallbackContext) -> int:
    """Store birth place and ask for birth time."""
    context.user_data["birth_place"] = update.message.text.strip()
    update.message.reply_text(
        "Укажи точное время рождения (часы и минуты в формате HH:MM, например, 18:25)."
    )
    return BIRTH_TIME


def ask_notify_time(update: Update, context: CallbackContext) -> int:
    """Validate birth time, store it, and ask for notification time."""
    time_input = update.message.text.strip()
    parsed_time = parse_time_str(time_input)
    if not parsed_time:
        update.message.reply_text(
            "Время должно быть в формате HH:MM (24‑часовой), например, 08:30. Попробуй ещё раз."
        )
        return BIRTH_TIME
    context.user_data["birth_time"] = parsed_time
    update.message.reply_text(
        "Когда тебе удобно получать ежедневное сообщение? Укажи время в формате HH:MM."
    )
    return NOTIFY_TIME


def complete_registration(update: Update, context: CallbackContext) -> int:
    """Finalize registration: save data, assign referral code, schedule job."""
    notify_input = update.message.text.strip()
    notify_time = parse_time_str(notify_input)
    if not notify_time:
        update.message.reply_text(
            "Пожалуйста, укажи время в формате HH:MM, например, 09:00."
        )
        return NOTIFY_TIME
    # Save user to database
    telegram_id = update.effective_user.id
    database.add_or_update_user(
        telegram_id,
        name=context.user_data.get("name"),
        birth_date=context.user_data.get("birth_date"),
        birth_place=context.user_data.get("birth_place"),
        birth_time=context.user_data.get("birth_time"),
        schedule_time=notify_time,
    )
    # Assign a referral code if not already present
    code = referral.assign_referral_code(telegram_id)
    # Schedule the daily job
    schedule_daily_job(context, telegram_id, notify_time)
    # Greet the user and present the menu
    update.message.reply_text(
        "Регистрация завершена! Теперь ты будешь получать ежедневные сообщения в {time}. "
        "Твоя персональная реферальная ссылка: t.me/{bot_username}?start={code}. "
        "Используй /menu для дальнейших действий.".format(
            time=notify_time, bot_username=context.bot.username, code=code
        )
    )
    return ConversationHandler.END


def cancel(update: Update, context: CallbackContext) -> int:
    """Cancel the conversation and cleanup."""
    update.message.reply_text(
        "Регистрация отменена. Используй /start, чтобы начать заново.",
        reply_markup=ReplyKeyboardRemove(),
    )
    return ConversationHandler.END


def menu(update: Update, context: CallbackContext) -> None:
    """Display available commands and features to the user."""
    telegram_id = update.effective_user.id
    user = database.get_user(telegram_id)
    if not user:
        update.message.reply_text(
            "Сначала зарегистрируйтесь с помощью команды /start."
        )
        return
    # Present the user with an inline keyboard of available actions.  Each
    # button sends a callback query that we handle in `handle_menu_callback`.
    keyboard = [
        [InlineKeyboardButton("Изменить время", callback_data="menu_settime")],
        [InlineKeyboardButton("Рефералы", callback_data="menu_referrals")],
        [InlineKeyboardButton("Оформить подписку", callback_data="menu_subscribe")],
        [InlineKeyboardButton("Статус подписки", callback_data="menu_status")],
        [InlineKeyboardButton("Обновить данные", callback_data="menu_update")],
    ]
    # If the user is an admin, include a broadcast option
    if telegram_id in config.ADMIN_IDS:
        keyboard.append([InlineKeyboardButton("Рассылка", callback_data="menu_broadcast")])
    # Always include a cancel/close option so the user can dismiss the menu
    keyboard.append([InlineKeyboardButton("Закрыть меню", callback_data="menu_close")])
    reply_markup = InlineKeyboardMarkup(keyboard)
    update.message.reply_text(
        "Выберите действие:", reply_markup=reply_markup
    )


def set_time_command(update: Update, context: CallbackContext) -> None:
    """Ask the user for a new notification time."""
    telegram_id = update.effective_user.id
    user = database.get_user(telegram_id)
    if not user:
        update.message.reply_text(
            "Сначала зарегистрируйтесь с помощью /start."
        )
        return
    update.message.reply_text(
        "Введите новое время для получения сообщений (HH:MM)."
    )
    # Use conversation handler state to capture the next message
    update.message.reply_text(
        "Введите новое время в формате HH:MM, например, 21:30."
    )
    return CHANGE_TIME


def handle_time_change(update: Update, context: CallbackContext) -> int:
    """Handle the user's input for changing notification time."""
    telegram_id = update.effective_user.id
    new_time = parse_time_str(update.message.text.strip())
    if not new_time:
        update.message.reply_text(
            "Неверный формат. Укажи время в HH:MM, например, 20:15."
        )
        return CHANGE_TIME
    # Update in DB
    database.set_schedule_time(telegram_id, new_time)
    # Reschedule job
    schedule_daily_job(context, telegram_id, new_time)
    update.message.reply_text(
        f"Время ежедневной рассылки обновлено на {new_time}."
    )
    return ConversationHandler.END


def show_referrals(update: Update, context: CallbackContext) -> None:
    """Display the referral link, number of referrals, and points."""
    telegram_id = update.effective_user.id
    user = database.get_user(telegram_id)
    if not user:
        update.message.reply_text(
            "Сначала зарегистрируйтесь через /start."
        )
        return
    code = referral.assign_referral_code(telegram_id)
    points, count, names = referral.get_referral_status(telegram_id)
    link = f"t.me/{context.bot.username}?start={code}"
    if names:
        ref_list = ", ".join(names)
    else:
        ref_list = "пока нет приглашённых пользователей"
    update.message.reply_text(
        f"Твоя реферальная ссылка: {link}\n"
        f"Кол-во приглашённых: {count}\n"
        f"Баллы: {points}\n"
        f"Приглашённые: {ref_list}"
    )


def subscribe(update: Update, context: CallbackContext) -> None:
    """Initiate the payment process for a subscription."""
    telegram_id = update.effective_user.id
    user = database.get_user(telegram_id)
    if not user:
        update.message.reply_text(
            "Сначала зарегистрируйтесь через /start."
        )
        return
    # If user already has an active subscription, inform them
    if user["subscription_active"]:
        update.message.reply_text(
            f"У вас уже есть активная подписка до {user['subscription_expiration']}."
        )
        return
    payments.send_subscription_invoice(update, context)


def show_status(update: Update, context: CallbackContext) -> None:
    """Show the user's subscription status."""
    telegram_id = update.effective_user.id
    user = database.get_user(telegram_id)
    if not user:
        update.message.reply_text(
            "Сначала зарегистрируйтесь через /start."
        )
        return
    if user["subscription_active"]:
        update.message.reply_text(
            f"Ваша подписка активна до {user['subscription_expiration']}"
        )
    else:
        update.message.reply_text("У вас нет активной подписки. Используйте /subscribe, чтобы её оформить.")


def update_personal_data(update: Update, context: CallbackContext) -> int:
    """Allow the user to update their personal data via conversation."""
    telegram_id = update.effective_user.id
    user = database.get_user(telegram_id)
    if not user:
        update.message.reply_text(
            "Сначала зарегистрируйтесь через /start."
        )
        return
    # Ask which field to update
    # Include an extra button that allows the user to cancel out of the update
    # process and return to the main menu without making changes.  We use
    # an inline keyboard so the user can simply tap to choose an action.
    keyboard = [
        [InlineKeyboardButton("Имя", callback_data="update_name")],
        [InlineKeyboardButton("Дата рождения", callback_data="update_birth_date")],
        [InlineKeyboardButton("Место рождения", callback_data="update_birth_place")],
        [InlineKeyboardButton("Время рождения", callback_data="update_birth_time")],
        [InlineKeyboardButton("Оставить всё как есть", callback_data="update_cancel")],
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    update.message.reply_text(
        "Что вы хотите изменить?", reply_markup=reply_markup
    )
    return SELECT_FIELD


def handle_update_callback(update: Update, context: CallbackContext) -> int:
    """Handle the inline button selection during update."""
    query = update.callback_query
    query.answer()
    data = query.data
    if data == "update_cancel":
        # User chose to leave everything unchanged.  Inform them and return to the
        # main menu.  Ending the conversation here prevents further prompts.
        query.message.reply_text(
            "Изменения отменены. Используйте /menu для доступа к функциям."
        )
        return ConversationHandler.END
    if data == "update_name":
        query.message.reply_text("Введите новое имя:")
        context.user_data["update_field"] = "name"
    elif data == "update_birth_date":
        query.message.reply_text("Введите новую дату рождения (ДД.ММ.ГГГГ):")
        context.user_data["update_field"] = "birth_date"
    elif data == "update_birth_place":
        query.message.reply_text("Введите новое место рождения:")
        context.user_data["update_field"] = "birth_place"
    elif data == "update_birth_time":
        query.message.reply_text("Введите новое время рождения (HH:MM):")
        context.user_data["update_field"] = "birth_time"
    else:
        query.message.reply_text("Неизвестная опция.")
        return
    # Move to the next conversation state
    return TYPING_VALUE


def handle_update_value(update: Update, context: CallbackContext) -> int:
    """Receive the new value for a personal data field and save it."""
    telegram_id = update.effective_user.id
    field = context.user_data.get("update_field")
    if not field:
        update.message.reply_text("Команда обновления не была выбрана. Используйте /update ещё раз.")
        return ConversationHandler.END
    new_value = update.message.text.strip()
    # Validate input for date and time
    if field == "birth_date":
        iso_date = parse_date(new_value)
        if not iso_date:
            update.message.reply_text(
                "Неверный формат. Введите дату в виде ДД.ММ.ГГГГ."
            )
            return ConversationHandler.END
        new_value = iso_date
    elif field == "birth_time":
        parsed_time = parse_time_str(new_value)
        if not parsed_time:
            update.message.reply_text(
                "Неверный формат. Введите время в виде HH:MM."
            )
            return ConversationHandler.END
        new_value = parsed_time
    # Update database
    kwargs = {field: new_value}
    database.add_or_update_user(telegram_id, **kwargs)
    update.message.reply_text("Информация обновлена. Спасибо!")
    return ConversationHandler.END


def broadcast(update: Update, context: CallbackContext) -> None:
    """Admin command to broadcast a message to all users."""
    telegram_id = update.effective_user.id
    if telegram_id not in config.ADMIN_IDS:
        update.message.reply_text("У вас нет прав для этой команды.")
        return
    args = context.args
    if not args:
        update.message.reply_text("Использование: /broadcast <сообщение>")
        return
    text = " ".join(args)
    users = database.get_all_users()
    count = 0
    for user in users:
        try:
            context.bot.send_message(user["telegram_id"], text)
            count += 1
        except Exception as exc:
            logger.warning(f"Не удалось отправить сообщение пользователю {user['telegram_id']}: {exc}")
    update.message.reply_text(f"Отправлено {count} сообщений.")


def handle_menu_callback(update: Update, context: CallbackContext) -> int:
    """Handle callback queries from the main menu inline keyboard.

    Depending on which button the user pressed, this function either
    sends a helpful prompt directing them to the appropriate command
    or performs a small action such as showing their referral status or
    subscription status.  The conversation is not advanced here;
    commands that require additional input should still be invoked
    manually by the user (e.g., /settime).
    """
    query = update.callback_query
    query.answer()
    data = query.data
    telegram_id = query.from_user.id
    # Route based on callback data
    if data == "menu_settime":
        # Inform the user how to change the time
        context.bot.send_message(
            chat_id=telegram_id,
            text=(
                "Чтобы изменить время ежедневной рассылки, отправьте команду /settime "
                "и следуйте инструкции."
            ),
        )
    elif data == "menu_referrals":
        # Show the referral status directly (reuse logic from show_referrals)
        user = database.get_user(telegram_id)
        if not user:
            context.bot.send_message(
                chat_id=telegram_id,
                text="Сначала зарегистрируйтесь через /start."
            )
        else:
            code = referral.assign_referral_code(telegram_id)
            points, count, names = referral.get_referral_status(telegram_id)
            link = f"t.me/{context.bot.username}?start={code}"
            ref_list = ", ".join(names) if names else "пока нет приглашённых пользователей"
            context.bot.send_message(
                chat_id=telegram_id,
                text=(
                    f"Твоя реферальная ссылка: {link}\n"
                    f"Кол-во приглашённых: {count}\n"
                    f"Баллы: {points}\n"
                    f"Приглашённые: {ref_list}"
                ),
            )
    elif data == "menu_subscribe":
        # Initiate subscription purchase.  If the user already has a subscription,
        # the helper will inform them.
        user = database.get_user(telegram_id)
        if not user:
            context.bot.send_message(
                chat_id=telegram_id,
                text="Сначала зарегистрируйтесь через /start."
            )
        elif user["subscription_active"]:
            context.bot.send_message(
                chat_id=telegram_id,
                text=f"У вас уже есть активная подписка до {user['subscription_expiration']}."
            )
        else:
            # Use the existing payment helper; it reads chat_id from the update.
            payments.send_subscription_invoice(update, context)
    elif data == "menu_status":
        # Show subscription status
        user = database.get_user(telegram_id)
        if not user:
            context.bot.send_message(
                chat_id=telegram_id,
                text="Сначала зарегистрируйтесь через /start."
            )
        elif user["subscription_active"]:
            context.bot.send_message(
                chat_id=telegram_id,
                text=f"Ваша подписка активна до {user['subscription_expiration']}"
            )
        else:
            context.bot.send_message(
                chat_id=telegram_id,
                text="У вас нет активной подписки. Используйте /subscribe, чтобы её оформить."
            )
    elif data == "menu_update":
        # Prompt the user to invoke the update command
        context.bot.send_message(
            chat_id=telegram_id,
            text=(
                "Чтобы обновить персональные данные, используйте команду /update. "
                "Там вы сможете выбрать, что изменить."
            ),
        )
    elif data == "menu_broadcast":
        # Admin broadcast command instructions
        if telegram_id not in config.ADMIN_IDS:
            context.bot.send_message(
                chat_id=telegram_id,
                text="У вас нет прав для этой команды."
            )
        else:
            context.bot.send_message(
                chat_id=telegram_id,
                text=(
                    "Введите текст после команды /broadcast, чтобы отправить его всем пользователям."
                ),
            )
    elif data == "menu_close":
        # Simply remove the inline keyboard by editing the message
        query.edit_message_reply_markup(reply_markup=None)
        context.bot.send_message(
            chat_id=telegram_id,
            text="Меню закрыто."
        )
    else:
        # Unrecognized callback data
        context.bot.send_message(
            chat_id=telegram_id,
            text="Неизвестная команда меню."
        )
    return ConversationHandler.END


def main() -> None:
    """Start the bot."""
    # Initialize database and ensure tables exist
    database.init_db()
    # Create the Updater and pass it your bot's token
    updater = Updater(token=config.BOT_TOKEN, use_context=True)
    dispatcher = updater.dispatcher
    # Create job queue
    job_queue = updater.job_queue
    # Load existing users and schedule daily jobs
    for user in database.get_all_users():
        if user["schedule_time"]:
            schedule_job_with_jobqueue(
                updater.job_queue,
                user["telegram_id"],
                user["schedule_time"],
            )
    # Registration conversation handler
    registration_handler = ConversationHandler(
        entry_points=[CommandHandler("start", start, pass_args=True)],
        states={
            NAME: [MessageHandler(Filters.text & ~Filters.command, ask_birth_date)],
            BIRTH_DATE: [MessageHandler(Filters.text & ~Filters.command, ask_birth_place)],
            BIRTH_PLACE: [MessageHandler(Filters.text & ~Filters.command, ask_birth_time)],
            BIRTH_TIME: [MessageHandler(Filters.text & ~Filters.command, ask_notify_time)],
            NOTIFY_TIME: [MessageHandler(Filters.text & ~Filters.command, complete_registration)],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
    )
    dispatcher.add_handler(registration_handler)
    # Set time conversation handler
    settime_handler = ConversationHandler(
        entry_points=[CommandHandler("settime", set_time_command)],
        states={
            CHANGE_TIME: [MessageHandler(Filters.text & ~Filters.command, handle_time_change)],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
    )
    dispatcher.add_handler(settime_handler)
    # Personal data update conversation handler
    update_handler = ConversationHandler(
        entry_points=[CommandHandler("update", update_personal_data)],
        states={
            SELECT_FIELD: [CallbackQueryHandler(handle_update_callback)],
            TYPING_VALUE: [MessageHandler(Filters.text & ~Filters.command, handle_update_value)],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
    )
    dispatcher.add_handler(update_handler)
    # Menu and other commands
    dispatcher.add_handler(CommandHandler("menu", menu))
    dispatcher.add_handler(CommandHandler("referrals", show_referrals))
    dispatcher.add_handler(CommandHandler("subscribe", subscribe))
    dispatcher.add_handler(CommandHandler("status", show_status))
    dispatcher.add_handler(CommandHandler("broadcast", broadcast, pass_args=True))
    # Callback handler for main menu inline buttons.  Pattern ensures we only
    # capture callbacks starting with 'menu_'.
    dispatcher.add_handler(CallbackQueryHandler(handle_menu_callback, pattern=r"^menu_"))
    # Payment handlers
    dispatcher.add_handler(PreCheckoutQueryHandler(payments.process_precheckout_query))
    dispatcher.add_handler(MessageHandler(Filters.successful_payment, payments.handle_successful_payment))
    # Start the Bot
    updater.start_polling()
    logger.info("Bot started and polling for updates...")
    updater.idle()


if __name__ == "__main__":
    main()