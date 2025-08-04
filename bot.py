import os
import json
import logging
import gspread
from google.oauth2.service_account import Credentials
from telegram import Update, ReplyKeyboardMarkup, ReplyKeyboardRemove
from telegram.ext import (
    ApplicationBuilder, CommandHandler, MessageHandler, ConversationHandler,
    ContextTypes, filters
)
from dotenv import load_dotenv

# Load .env if present (local runs)
load_dotenv()

# --- Env vars ---
TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
SPREADSHEET_ID = os.getenv("SPREADSHEET_ID")           # only the Sheet ID, not full URL
SHEET_NAME = os.getenv("SHEET_NAME", "Sheet1")
GOOGLE_CREDS_RAW = os.getenv("GOOGLE_CREDS")           # full JSON of Google Service Account

if not TOKEN:
    raise RuntimeError("TELEGRAM_BOT_TOKEN is not set.")
if not SPREADSHEET_ID:
    raise RuntimeError("SPREADSHEET_ID is not set.")
if not GOOGLE_CREDS_RAW:
    raise RuntimeError("GOOGLE_CREDS is not set. Paste the full JSON of the Service Account key.")

GSCOPE = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
]

logging.basicConfig(level=logging.INFO)

NAME, BIRTHDATE, BIRTHTIME, BIRTHPLACE, CONFIRM = range(5)

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Привет! Давай начнём.\n\nКак тебя зовут?",
        reply_markup=ReplyKeyboardRemove()
    )
    return NAME

async def get_name(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data['name'] = update.message.text.strip()
    await update.message.reply_text("Введи дату рождения (в формате ДД.ММ.ГГГГ):")
    return BIRTHDATE

async def get_birthdate(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data['birthdate'] = update.message.text.strip()
    await update.message.reply_text("Введи время рождения (например, 18:25):")
    return BIRTHTIME

async def get_birthtime(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data['birthtime'] = update.message.text.strip()
    await update.message.reply_text("Введи город рождения:")
    return BIRTHPLACE

async def get_birthplace(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data['birthplace'] = update.message.text.strip()
    name = context.user_data['name']
    birthdate = context.user_data['birthdate']
    birthtime = context.user_data['birthtime']
    birthplace = context.user_data['birthplace']
    text = (
        f"Проверь введённые данные:\n"
        f"Имя: {name}\n"
        f"Дата рождения: {birthdate}\n"
        f"Время: {birthtime}\n"
        f"Город: {birthplace}\n\n"
        f"Всё верно?"
    )
    buttons = [["Да, всё верно"], ["Изменить"]]
    await update.message.reply_text(
        text,
        reply_markup=ReplyKeyboardMarkup(buttons, one_time_keyboard=True, resize_keyboard=True)
    )
    return CONFIRM

def _gsheet_worksheet() -> gspread.Worksheet:
    creds = Credentials.from_service_account_info(json.loads(GOOGLE_CREDS_RAW), scopes=GSCOPE)
    client = gspread.authorize(creds)
    return client.open_by_key(SPREADSHEET_ID).worksheet(SHEET_NAME)

async def confirm(update: Update, context: ContextTypes.DEFAULT_TYPE):
    answer = (update.message.text or "").lower()
    if "да" in answer:
        try:
            sheet = _gsheet_worksheet()
            row = [
                update.effective_user.id,
                context.user_data.get('name', ''),
                context.user_data.get('birthdate', ''),
                context.user_data.get('birthtime', ''),
                context.user_data.get('birthplace', ''),
            ]
            sheet.append_row(row)
            await update.message.reply_text(
                "Спасибо! Данные сохранены.\n\n✨ Жди свой персональный прогноз!",
                reply_markup=ReplyKeyboardRemove()
            )
        except Exception as e:
            logging.error("Google Sheets error: %s", e)
            await update.message.reply_text(
                "Произошла ошибка при сохранении данных. Попробуй позже.",
                reply_markup=ReplyKeyboardRemove()
            )
        return ConversationHandler.END
    else:
        await update.message.reply_text(
            "Давай начнём сначала. Как тебя зовут?",
            reply_markup=ReplyKeyboardRemove()
        )
        return NAME

async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Ввод отменён.", reply_markup=ReplyKeyboardRemove())
    return ConversationHandler.END

def main():
    app = ApplicationBuilder().token(TOKEN).build()
    conv_handler = ConversationHandler(
        entry_points=[CommandHandler('start', start)],
        states={
            NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_name)],
            BIRTHDATE: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_birthdate)],
            BIRTHTIME: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_birthtime)],
            BIRTHPLACE: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_birthplace)],
            CONFIRM: [MessageHandler(filters.TEXT & ~filters.COMMAND, confirm)],
        },
        fallbacks=[CommandHandler('cancel', cancel)]
    )
    app.add_handler(conv_handler)
    app.run_polling()

if __name__ == '__main__':
    main()
