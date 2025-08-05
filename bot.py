import os
import random
import logging
from datetime import datetime, timedelta
from telegram import Update, ReplyKeyboardMarkup, ReplyKeyboardRemove, KeyboardButton, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    ApplicationBuilder, CommandHandler, MessageHandler, ContextTypes,
    ConversationHandler, CallbackQueryHandler, filters
)
import gspread
from google.oauth2.service_account import Credentials

# === Настройки ===
logging.basicConfig(level=logging.INFO)
TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
SPREADSHEET_ID = os.getenv("SPREADSHEET_ID")
SHEET_NAME = "Sheet1"
GSCOPE = ["https://www.googleapis.com/auth/spreadsheets", "https://www.googleapis.com/auth/drive"]

# === Состояния ===
ASK_NAME, ASK_BIRTHDATE, ASK_BIRTHTIME, ASK_BIRTHPLACE, ASK_TIME, CONFIRM = range(6)

# === Подключение к Google Sheets ===
def get_gsheets_client():
    creds = Credentials.from_service_account_info(eval(os.getenv("GOOGLE_CREDS")), scopes=GSCOPE)
    gc = gspread.authorize(creds)
    sh = gc.open_by_key(SPREADSHEET_ID)
    worksheet = sh.worksheet(SHEET_NAME)
    return worksheet

def save_user_to_gsheet(user_data):
    ws = get_gsheets_client()
    user_id = str(user_data.get("user_id"))
    existing = ws.col_values(1)
    new_row = [
        user_id, user_data.get("name"), user_data.get("birthdate"),
        user_data.get("birthtime"), user_data.get("birthplace"),
        user_data.get("reg_date"), "", "", "", "", "", "", "",
        user_data.get("send_time", "10:05")
    ]
    if user_id in existing:
        idx = existing.index(user_id) + 1
        ws.update(f"A{idx}:N{idx}", [new_row])
    else:
        ws.append_row(new_row, value_input_option="USER_ENTERED")

# === Цитаты и ритуалы ===
def get_random_quote():
    try:
        with open("quotes.txt", encoding="utf-8") as f:
            quotes = [q.strip() for q in f if q.strip()]
        return random.choice(quotes)
    except:
        return "Ты уже всё можешь. Вселенная внутри тебя."

def get_random_ritual():
    try:
        with open("morning_rituals.txt", encoding="utf-8") as f:
            rituals = [r.strip() for r in f if r.strip()]
        return random.choice(rituals)
    except:
        return "Закрой глаза и почувствуй: ты живёшь в потоке."

# === Генерация послания ===
def get_daily_message(user_data):
    name = user_data.get("name", "друг")
    themes = ["Осознанность и интуиция", "Доверие к себе", "Покой и принятие"]
    luna = ["Луна усиливает твою интуицию.", "Сегодня Луна особенно мудра."]
    mars = ["Марс придаёт решимости.", "Энергия дня помогает действовать."]
    venus = ["Венера усиливает любовь и притяжение.", "Забота о себе особенно важна."]

    do = list(set(random.sample([
        "Сделай паузу и прислушайся к себе.",
        "Запиши то, за что ты благодарен.",
        "Сделай шаг навстречу мечте."
    ], 2)))

    no = list(set(random.sample([
        "Не игнорируй тревогу — это сигнал.",
        "Не торопись принимать решения.",
        "Не критикуй себя."
    ], 2)))

    return (
        f"🌅 Доброе утро, {name}!

"
        f"*🔮 Тема дня:* {random.choice(themes)}

"
        f"🌙 {random.choice(luna)}
"
        f"🔥 {random.choice(mars)}
"
        f"🌸 {random.choice(venus)}

"
        f"*✅ Действуй:*
• {do[0]}
• {do[1]}

"
        f"*❌ Категорически:*
• {no[0]}
• {no[1]}

"
        f"*🕯 Утренний ритуал (5 минут):*
{get_random_ritual()}

"
        f"*✨ Девиз дня:*
«{get_random_quote()}»"
    )

# === Хендлеры ===
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.clear()
    await update.message.reply_text("Как тебя зовут?", reply_markup=ReplyKeyboardMarkup([[KeyboardButton("Отмена")]], resize_keyboard=True))
    return ASK_NAME

async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.clear()
    await update.message.reply_text("Ввод отменён. Чтобы начать заново, напиши /start.", reply_markup=ReplyKeyboardRemove())
    return ConversationHandler.END

async def ask_name(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["name"] = update.message.text
    await update.message.reply_text("Дата рождения (например, 27.11.1997):")
    return ASK_BIRTHDATE

async def ask_birthdate(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["birthdate"] = update.message.text
    await update.message.reply_text("Время рождения (например, 18:25):")
    return ASK_BIRTHTIME

async def ask_birthtime(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["birthtime"] = update.message.text
    await update.message.reply_text("Место рождения (город):")
    return ASK_BIRTHPLACE

async def ask_birthplace(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["birthplace"] = update.message.text
    await update.message.reply_text("🌅 Утро — это время тишины и ясности.
Во сколько Вселенной стоит заглянуть к тебе? (например, 10:05)")
    return ASK_TIME

async def ask_time(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["send_time"] = update.message.text
    context.user_data["reg_date"] = datetime.now().strftime("%d.%m.%Y %H:%M")
    context.user_data["user_id"] = update.effective_user.id
    await update.message.reply_text("✨ Вселенная уже готовит послание для тебя...")
    try:
        save_user_to_gsheet(context.user_data)
        msg = get_daily_message(context.user_data)
        await update.message.reply_text(msg, parse_mode='Markdown')
    except Exception as e:
        await update.message.reply_text("Произошла ошибка при сохранении данных.")
        logging.error(e)
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
        },
        fallbacks=[CommandHandler("cancel", cancel)]
    )
    app.add_handler(conv_handler)
    app.run_polling()

if __name__ == "__main__":
    main()