import os
import random
import logging
from datetime import datetime, timedelta
import pytz

from telegram import Update, ReplyKeyboardMarkup, ReplyKeyboardRemove, KeyboardButton
from telegram.ext import (
    ApplicationBuilder, CommandHandler, MessageHandler, filters,
    ConversationHandler, ContextTypes
)

import gspread
from google.oauth2.service_account import Credentials

# === Настройки ===
TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
SPREADSHEET_ID = os.getenv("SPREADSHEET_ID")
SHEET_NAME = "Sheet1"

GSCOPE = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive"
]

logging.basicConfig(level=logging.INFO)

# === Состояния ===
ASK_NAME, ASK_BIRTHDATE, ASK_BIRTHTIME, ASK_BIRTHPLACE, ASK_TIME, CONFIRM = range(6)

# === GSheets ===
def get_gsheets_client():
    creds_json = os.getenv("GOOGLE_CREDS")
    creds = Credentials.from_service_account_info(eval(creds_json), scopes=GSCOPE)
    gc = gspread.authorize(creds)
    sh = gc.open_by_key(SPREADSHEET_ID)
    return sh.worksheet(SHEET_NAME)

def save_user_to_gsheet(user_data):
    ws = get_gsheets_client()
    row = [
        user_data.get("user_id"),
        user_data.get("name"),
        user_data.get("birthdate"),
        user_data.get("birthtime"),
        user_data.get("birthplace"),
        user_data.get("reg_date"),
        "", "", "", "", "", "", "",
        user_data.get("send_time")
    ]
    ws.append_row(row, value_input_option="USER_ENTERED")

# === Цитаты ===
def get_random_quote():
    try:
        with open("quotes.txt", encoding="utf-8") as f:
            quotes = [line.strip() for line in f if line.strip()]
        return random.choice(quotes) if quotes else ""
    except:
        return "Сегодня — лучший день для нового взгляда на себя!"

# === Прогноз ===
THEMES = ["Осознанность и интуиция", "Решительность и движение вперёд", "Гармония и принятие", "Энергия перемен", "Доверие к Вселенной"]
LUNA = ["Луна сегодня усиливает интуицию.", "Эмоциональный фон нестабилен.", "Важно прислушиваться к внутреннему голосу."]
MARS = ["Марс активирует желание действовать.", "Твоя энергия особенно сильна.", "Марс даёт тебе силу говорить «нет»."]
VENUS = ["Венера усиливает притяжение и романтику.", "Подходит день для заботы о себе.", "Венера способствует вдохновению."]
DO = ["Обрати внимание на желания.", "Сделай шаг к мечте.", "Проведи время наедине с собой."]
DONT = ["Не принимай поспешных решений.", "Не игнорируй тревогу.", "Избегай самоедства."]
RITUAL = [
    "Закрой глаза, сделай три вдоха и скажи: «Я доверяю Вселенной».",
    "Посмотри на небо и вспомни три своих мечты.",
    "Сделай утреннюю зарядку и представь, как энергия наполняет тело."
]

def get_daily(user_data):
    name = user_data.get("name", "друг")
    return (
        f"🌅 *Доброе утро, {name}!*\n\n"
        f"🔮 *Тема дня:* «{random.choice(THEMES)}»\n"
        f"{random.choice(['Прислушайся к себе.', 'Не бойся идти вперёд.'])}\n\n"
        f"🌙 {random.choice(LUNA)}\n"
        f"🔥 {random.choice(MARS)}\n"
        f"🌸 {random.choice(VENUS)}\n\n"
        f"*✅ Действуй:*\n• {random.choice(DO)}\n• {random.choice(DO)}\n\n"
        f"*❌ Категорически:*\n• {random.choice(DONT)}\n• {random.choice(DONT)}\n\n"
        f"*🕯️ Утренний ритуал (5 минут):*\n{random.choice(RITUAL)}\n\n"
        f"*💬 Девиз дня:*\n_{get_random_quote()}_"
    )

# === Команды ===
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.clear()
    await update.message.reply_text(
        "Добро пожаловать во Вселенную ✨\n\nКак тебя зовут?",
        reply_markup=ReplyKeyboardMarkup([[KeyboardButton("Отмена")]], resize_keyboard=True)
    )
    return ASK_NAME

async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Ввод отменён. Чтобы начать заново, введи /start.",
        reply_markup=ReplyKeyboardRemove()
    )
    return ConversationHandler.END

async def ask_name(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message.text.lower() == "отмена":
        return await cancel(update, context)
    context.user_data["name"] = update.message.text
    await update.message.reply_text("Дата рождения (например: 27.11.1997):")
    return ASK_BIRTHDATE

async def ask_birthdate(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message.text.lower() == "отмена":
        return await cancel(update, context)
    context.user_data["birthdate"] = update.message.text
    await update.message.reply_text("Время рождения (например: 18:25):")
    return ASK_BIRTHTIME

async def ask_birthtime(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message.text.lower() == "отмена":
        return await cancel(update, context)
    context.user_data["birthtime"] = update.message.text
    await update.message.reply_text("Место рождения (город, страна):")
    return ASK_BIRTHPLACE

async def ask_birthplace(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message.text.lower() == "отмена":
        return await cancel(update, context)
    context.user_data["birthplace"] = update.message.text
    await update.message.reply_text(
        "⏰ *Утро — это время тишины и ясности.*\n\n"
        "Во сколько Вселенной стоит заглянуть к тебе?\n\n_Пример: 10:05_",
        parse_mode='Markdown'
    )
    return ASK_TIME

async def ask_time(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    if text.lower() == "отмена":
        return await cancel(update, context)
    context.user_data["send_time"] = text
    context.user_data["user_id"] = update.effective_user.id
    context.user_data["reg_date"] = datetime.now().strftime("%d.%m.%Y %H:%M")
    await update.message.reply_text(
        "✨ Всё готово! Получи своё первое послание от Вселенной:",
        reply_markup=ReplyKeyboardMarkup([[KeyboardButton("Поговорить со Вселенной")]], resize_keyboard=True)
    )
    return CONFIRM

async def confirm(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message.text != "Поговорить со Вселенной":
        return await cancel(update, context)
    try:
        save_user_to_gsheet(context.user_data)
    except Exception as e:
        await update.message.reply_text(f"Ошибка при сохранении: {e}")
    daily = get_daily(context.user_data)
    await update.message.reply_text(
        daily,
        parse_mode='Markdown',
        reply_markup=ReplyKeyboardRemove()
    )
    return ConversationHandler.END

def main():
    app = ApplicationBuilder().token(TOKEN).build()

    conv_handler = ConversationHandler(
        entry_points=[CommandHandler("start", start)],
        states={
            ASK_NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, ask_name)],
            ASK_BIRTHDATE: [MessageHandler(filters.TEXT & ~filters.COMMAND, ask_birthdate)],
            ASK_BIRTHTIME: [MessageHandler(filters.TEXT & ~filters.COMMAND, ask_birthtime)],
            ASK_BIRTHPLACE: [MessageHandler(filters.TEXT & ~filters.COMMAND, ask_birthplace)],
            ASK_TIME: [MessageHandler(filters.TEXT & ~filters.COMMAND, ask_time)],
            CONFIRM: [MessageHandler(filters.TEXT & ~filters.COMMAND, confirm)],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
        allow_reentry=True
    )

    app.add_handler(conv_handler)
    app.run_polling()

if __name__ == "__main__":
    main()