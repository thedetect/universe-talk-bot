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
from datetime import datetime, time, timedelta
from typing import Dict, Optional, Tuple

from telegram import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    ReplyKeyboardMarkup,
    ReplyKeyboardRemove,
    KeyboardButton,
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

from telegram.error import TelegramError

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
    MENU_ACTION,
) = range(9)


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
    now = datetime.utcnow()
    # Parse subscription and trial expiration dates if available
    sub_expiration = None
    if user.get("subscription_expiration"):
        try:
            sub_expiration = datetime.fromisoformat(user["subscription_expiration"])
        except Exception:
            sub_expiration = None
    trial_expiration = None
    if user.get("trial_expiration"):
        try:
            trial_expiration = datetime.fromisoformat(user["trial_expiration"])
        except Exception:
            trial_expiration = None
    is_trial = bool(user.get("is_trial"))
    subscription_active = bool(user.get("subscription_active"))
    # Helper to send full horoscope message
    def send_full_message():
        msg = astrology.get_daily_message(user["birth_date"], user["birth_time"])
        context.bot.send_message(chat_id=chat_id, text=msg)
    def send_preview_and_offer():
        preview = astrology.get_daily_message(user["birth_date"], user["birth_time"]).split("\n")[0]
        # Offer to extend subscription or use bonus days
        text = (
            f"{preview}\n\n"
            "Ваш пробный период завершён. Вы можете продлить подписку или воспользоваться бонусными днями, "
            "накопленными за приглашения."
        )
        # Present choices for subscription extension
        keyboard = [
            [InlineKeyboardButton("30 дней", callback_data="extend_30")],
            [InlineKeyboardButton("60 дней", callback_data="extend_60")],
            [InlineKeyboardButton("90 дней", callback_data="extend_90")],
            [InlineKeyboardButton("120 дней", callback_data="extend_120")],
            [InlineKeyboardButton("180 дней", callback_data="extend_180")],
        ]
        context.bot.send_message(
            chat_id=chat_id,
            text=text,
            reply_markup=InlineKeyboardMarkup(keyboard),
        )
    # Case 1: Active paid subscription
    if subscription_active and not is_trial:
        if sub_expiration and sub_expiration >= now:
            send_full_message()
            return
        else:
            # Paid subscription expired
            database.set_subscription_status(telegram_id, active=False)
            subscription_active = False
            sub_expiration = None
    # Case 2: Active trial
    if subscription_active and is_trial:
        if sub_expiration and sub_expiration >= now:
            send_full_message()
            return
        else:
            # Trial expired; clear trial flags
            database.clear_trial(telegram_id)
            database.set_subscription_status(telegram_id, active=False)
            subscription_active = False
            is_trial = False
    # Case 3: Use bonus days if available
    bonus_days = user.get("points") or 0
    if bonus_days > 0:
        # Extend subscription with bonus days
        new_expiration = now + timedelta(days=bonus_days)
        # Reset points
        database.update_points(telegram_id, -bonus_days)
        database.set_subscription_status(
            telegram_id,
            active=True,
            expiration_date=new_expiration.isoformat(),
            is_trial=False,
        )
        send_full_message()
        return
    # Case 4: No active subscription or trial; send preview and offer subscription
    send_preview_and_offer()


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
    # Compute a 10-day trial period starting now
    now = datetime.utcnow()
    trial_end = now + timedelta(days=10)
    # Persist trial expiration and mark subscription as active (trial)
    database.set_trial_expiration(telegram_id, trial_end.isoformat())
    database.set_subscription_status(
        telegram_id,
        active=True,
        expiration_date=trial_end.isoformat(),
        is_trial=True,
    )
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


def menu(update: Update, context: CallbackContext) -> int:
    """Show the main menu using a reply keyboard.

    When the user invokes /menu, present a set of buttons at the bottom of
    the chat.  These buttons correspond to common actions such as
    changing the notification time, viewing referrals, managing the
    subscription and updating personal data.  When a button is pressed
    the text of that button will be sent to the bot and handled by
    `handle_menu_reply`.  We return the `MENU_ACTION` state so that
    further messages can be captured by the conversation handler.
    """
    telegram_id = update.effective_user.id
    user = database.get_user(telegram_id)
    if not user:
        update.message.reply_text(
            "Сначала зарегистрируйтесь с помощью команды /start."
        )
        return ConversationHandler.END
    # Build a reply keyboard with menu options.  Each button sends its
    # label as text when pressed.  Use one_time_keyboard so the
    # keyboard hides after a selection.
    keyboard = [
        [KeyboardButton("Изменить время"), KeyboardButton("Рефералы")],
        [KeyboardButton("Оформить подписку"), KeyboardButton("Статус подписки")],
        [KeyboardButton("Обновить данные")],
    ]
    # If the user is an admin, provide a broadcast option
    if telegram_id in config.ADMIN_IDS:
        keyboard.append([KeyboardButton("Рассылка")])
    reply_markup = ReplyKeyboardMarkup(
        keyboard,
        one_time_keyboard=True,
        resize_keyboard=True,
    )
    # Send a minimal message with the keyboard attached.  We use a
    # single space as the text to avoid displaying an instruction like
    # "Выберите действие", per user request.
    # Use a zero‑width space to provide a non‑empty message while keeping
    # the menu header invisible to the user.  Telegram requires the text
    # argument to be non‑empty.
    update.message.reply_text("\u200b", reply_markup=reply_markup)
    return MENU_ACTION


def set_time_command(update: Update, context: CallbackContext) -> None:
    """Ask the user for a new notification time."""
    telegram_id = update.effective_user.id
    user = database.get_user(telegram_id)
    if not user:
        update.message.reply_text(
            "Сначала зарегистрируйтесь с помощью /start."
        )
        return
    # Ask the user to provide a new time.  We return the CHANGE_TIME state
    # so that the next message from the user is captured by the
    # corresponding handler.
    update.message.reply_text(
        "Введите новое время в формате HH:MM (24‑часовой), например, 21:30."
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
        f"Бонусные дни: {points}\n"
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
    try:
        payments.send_subscription_invoice(update, context)
    except TelegramError as err:
        # Gracefully handle cases where the payment provider token is invalid or other errors occur
        logger.warning(f"Failed to send subscription invoice: {err}")
        update.message.reply_text(
            "К сожалению, сейчас невозможно оформить подписку через Telegram Payments. "
            "Попробуйте позже или свяжитесь с администратором."
        )


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
    # Ask which field to update using a reply keyboard.  When the user selects
    # an option, it will be sent as a regular text message.  The keyboard
    # disappears after one use.
    keyboard = [
        [KeyboardButton("Имя"), KeyboardButton("Дата рождения")],
        [KeyboardButton("Место рождения"), KeyboardButton("Время рождения")],
        [KeyboardButton("Оставить всё как есть")],
    ]
    reply_markup = ReplyKeyboardMarkup(keyboard, one_time_keyboard=True, resize_keyboard=True)
    update.message.reply_text(
        "Что вы хотите изменить?",
        reply_markup=reply_markup,
    )
    return SELECT_FIELD


def handle_update_selection(update: Update, context: CallbackContext) -> int:
    """Handle the user's selection of which personal field to update.

    The user chooses an option from a reply keyboard.  Based on the text,
    we set the `update_field` in `user_data` and prompt for the new value.
    If the user chooses to leave everything unchanged, we exit the
    conversation without further prompts.
    """
    choice = update.message.text.strip()
    # Map the Russian labels to internal field names
    mapping = {
        "Имя": "name",
        "Дата рождения": "birth_date",
        "Место рождения": "birth_place",
        "Время рождения": "birth_time",
        "Оставить всё как есть": "cancel",
    }
    field = mapping.get(choice)
    if field is None:
        update.message.reply_text("Неизвестная опция. Попробуйте снова.", reply_markup=ReplyKeyboardRemove())
        return ConversationHandler.END
    if field == "cancel":
        # User chose to cancel updating any data; simply remove the keyboard
        update.message.reply_text("Изменения отменены.", reply_markup=ReplyKeyboardRemove())
        return ConversationHandler.END
    # Store which field we are updating
    context.user_data["update_field"] = field
    # Prompt the user for the new value
    if field == "name":
        update.message.reply_text("Введите новое имя:", reply_markup=ReplyKeyboardRemove())
    elif field == "birth_date":
        update.message.reply_text("Введите новую дату рождения (ДД.ММ.ГГГГ):", reply_markup=ReplyKeyboardRemove())
    elif field == "birth_place":
        update.message.reply_text("Введите новое место рождения:", reply_markup=ReplyKeyboardRemove())
    elif field == "birth_time":
        update.message.reply_text("Введите новое время рождения (HH:MM):", reply_markup=ReplyKeyboardRemove())
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


def handle_menu_reply(update: Update, context: CallbackContext) -> int:
    """Handle user selections from the main menu reply keyboard.

    The user's choice is matched against the available menu options.  Depending
    on the selection, the bot either instructs the user to run a
    command (for actions that require multi‑step input), immediately
    executes a helper such as showing referral status or subscription
    status, or starts a secondary conversation (for updating data).

    After handling, the reply keyboard is removed.  The function
    returns `ConversationHandler.END` unless it forwards the user into
    another conversation state (e.g., updating personal data).
    """
    telegram_id = update.effective_user.id
    choice = update.message.text.strip()
    # Route based on user choice
    if choice == "Изменить время":
        update.message.reply_text(
            "Чтобы изменить время ежедневной рассылки, используйте команду /settime.",
            reply_markup=ReplyKeyboardRemove(),
        )
        return ConversationHandler.END
    elif choice == "Рефералы":
        # Show referral status and then remove keyboard
        show_referrals(update, context)
        # Remove the keyboard using a zero‑width space message
        update.message.reply_text("\u200b", reply_markup=ReplyKeyboardRemove())
        return ConversationHandler.END
    elif choice == "Оформить подписку":
        # Initiate subscription purchase
        subscribe(update, context)
        # Remove keyboard after invoking subscribe
        update.message.reply_text("\u200b", reply_markup=ReplyKeyboardRemove())
        return ConversationHandler.END
    elif choice == "Статус подписки":
        show_status(update, context)
        update.message.reply_text("\u200b", reply_markup=ReplyKeyboardRemove())
        return ConversationHandler.END
    elif choice == "Обновить данные":
        # Start the personal data update conversation.  update_personal_data
        # returns a state indicating the next step in that conversation.
        return update_personal_data(update, context)
    elif choice == "Рассылка":
        # Broadcast is an admin‑only command
        if telegram_id not in config.ADMIN_IDS:
            update.message.reply_text(
                "У вас нет прав для этой команды.",
                reply_markup=ReplyKeyboardRemove(),
            )
        else:
            update.message.reply_text(
                "Введите текст после команды /broadcast, чтобы отправить его всем пользователям.",
                reply_markup=ReplyKeyboardRemove(),
            )
        return ConversationHandler.END
    else:
        # Unknown selection or user dismissed the keyboard
        update.message.reply_text(
            "\u200b",
            reply_markup=ReplyKeyboardRemove(),
        )
        return ConversationHandler.END


# The inline menu callback handler is no longer used now that the main menu
# employs a reply keyboard.  Its logic has been replaced by
# `handle_menu_reply`, which dispatches based on the selected text.


def handle_extension_callback(update: Update, context: CallbackContext) -> None:
    """Handle callback queries for subscription extension.

    The callback data encodes the number of days to extend the subscription.
    After extending, the message is edited to confirm the new expiration date.
    """
    query = update.callback_query
    query.answer()
    data = query.data
    telegram_id = query.from_user.id
    # Extract number of days from callback_data, e.g., 'extend_30'
    try:
        days = int(data.split("_")[1])
    except Exception:
        context.bot.send_message(
            chat_id=telegram_id,
            text="Некорректный выбор срока. Попробуйте ещё раз."
        )
        return
    now = datetime.utcnow()
    new_expiration = now + timedelta(days=days)
    database.set_subscription_status(
        telegram_id,
        active=True,
        expiration_date=new_expiration.isoformat(),
        is_trial=False,
    )
    # Clear trial flags if still present
    database.clear_trial(telegram_id)
    # Edit original message to confirm
    query.edit_message_text(
        f"Подписка продлена на {days} дней. Дата окончания: {new_expiration.date().isoformat()}"
    )


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
            SELECT_FIELD: [MessageHandler(Filters.text & ~Filters.command, handle_update_selection)],
            TYPING_VALUE: [MessageHandler(Filters.text & ~Filters.command, handle_update_value)],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
    )
    dispatcher.add_handler(update_handler)
    # Menu conversation handler.  Presents a reply keyboard and captures
    # the user's selection via `handle_menu_reply`.  The menu can be
    # invoked multiple times and automatically hides after an option is
    # chosen.
    menu_handler = ConversationHandler(
        entry_points=[CommandHandler("menu", menu)],
        states={
            MENU_ACTION: [MessageHandler(Filters.text & ~Filters.command, handle_menu_reply)],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
    )
    dispatcher.add_handler(menu_handler)
    # Other commands that can be called directly
    dispatcher.add_handler(CommandHandler("referrals", show_referrals))
    dispatcher.add_handler(CommandHandler("subscribe", subscribe))
    dispatcher.add_handler(CommandHandler("status", show_status))
    dispatcher.add_handler(CommandHandler("broadcast", broadcast, pass_args=True))
    # The inline menu callback handler has been removed; the main menu now
    # uses a reply keyboard handled by the conversation above.
    # Callback handler for subscription extension choices
    dispatcher.add_handler(CallbackQueryHandler(handle_extension_callback, pattern=r"^extend_"))
    # Payment handlers
    dispatcher.add_handler(PreCheckoutQueryHandler(payments.process_precheckout_query))
    dispatcher.add_handler(MessageHandler(Filters.successful_payment, payments.handle_successful_payment))
    # Start the Bot
    updater.start_polling()
    logger.info("Bot started and polling for updates...")
    updater.idle()


if __name__ == "__main__":
    main()