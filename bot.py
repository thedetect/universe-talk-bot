import os
import logging
from telegram import Update, ReplyKeyboardMarkup, KeyboardButton
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    ConversationHandler,
    ContextTypes,
    filters,
)
from datetime import datetime
from dotenv import load_dotenv
import gspread
from google.oauth2.service_account import Credentials

# === Load .env ===
load_dotenv()
TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")

# === Logging ===
logging.basicConfig(level=logging.INFO)

# === Google Sheets Setup ===
SCOPES = ['https://www.googleapis.com/auth/spreadsheets']
creds = Credentials.from_service_account_file('credentials.json', scopes=SCOPES)
client = gspread.authorize(creds)
sheet = client.open_by_key(os.getenv("SPREADSHEET_ID")).sheet1

# === States ===
NAME, DATE, TIME, CITY = range(4)

# === Handlers ===
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = str(update.effective_user.id)
    context.user_data.clear()
    await update.message.reply_text("🌅 Утро — это время силы и ясности\n\nКак тебя зовут?")
    return NAME

async def ask_date(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["name"] = update.message.text.strip()
    await update.message.reply_text("Когда ты родился? (например, 27.11.1997)")
    return DATE

async def ask_time(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["birth_date"] = update.message.text.strip()
    await update.message.reply_text("Во сколько ты родился? (например, 18:25)")
    return TIME

async def ask_city(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["birth_time"] = update.message.text.strip()
    await update.message.reply_text("Где ты родился?")
    return CITY

async def save_user(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["birth_city"] = update.message.text.strip()
    data = context.user_data
    user_id = update.effective_user.id

    row = [str(user_id), data["name"], data["birth_date"], data["birth_time"], data["birth_city"], datetime.now().isoformat()]
    sheet.append_row(row)
    await update.message.reply_text("✨ Всё готово! Получи своё первое послание от Вселенной.")
    await send_message(update, context)
    return ConversationHandler.END

# === Основное сообщение дня ===
async def send_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = str(update.effective_user.id)
    user_rows = sheet.get_all_records()
    user_info = next((r for r in user_rows if str(r['id']) == user_id), None)

    if not user_info:
        await update.message.reply_text("Ты ещё не настроил анкету. Напиши /start.")
        return

    name = user_info['name']
    theme = "Гармония и принятие"
    astro = [
        "🌙 Эмоциональный фон нестабилен.",
        "🔥 Марс даёт тебе силу говорить «нет».",
        "🌸 Венера способствует вдохновению."
    ]
    actions = [
        "✅ Обрати внимание на желания.",
        "✅ Проведи время наедине с собой."
    ]
    donts = ["❌ Не игнорируй тревогу."]

    ritual = get_random_ritual()

    message = (
        f"✨ Доброе утро, {name}!\n\n"
        f"🔮 *Тема дня*: «{theme}»\n"
        f"Прислушайся к себе.\n\n"
        + "\n".join(astro) + "\n\n"
        + "*Действуй:*\n" + "\n".join(actions) + "\n\n"
        + "*Категорически:*\n" + "\n".join(set(donts)) + "\n\n"
        + f"🕯️ *Утренний ритуал (5 минут)*:\n{ritual}\n\n"
        + "💬 *Девиз дня*: «Ты магнит для всего прекрасного.»"
    )

    await update.message.reply_markdown(message)

# === Утренние ритуалы ===
def get_random_ritual():
    try:
        with open("morning_rituals.txt", "r", encoding="utf-8") as file:
            import random
            rituals = [r.strip() for r in file if r.strip()]
            return random.choice(rituals)
    except:
        return "Поблагодари этот день и сделай глубокий вдох."

# === Меню ===
async def menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = str(update.effective_user.id)
    rows = sheet.get_all_records()
    row = next((r for r in rows if str(r['id']) == user_id), None)

    if row:
        text = (
            f"👤 *Твои данные*\n"
            f"Имя — {row['name']}\n"
            f"Дата рождения — {row['birth_date']}\n"
            f"Время рождения — {row['birth_time']}\n"
            f"Город — {row['birth_city']}\n\n"
            f"Чтобы изменить данные, напиши /start"
        )
    else:
        text = "Ты ещё не настроил анкету. Напиши /start."
    await update.message.reply_markdown(text)

# === Главная функция ===
def main():
    app = ApplicationBuilder().token(TOKEN).build()

    conv = ConversationHandler(
        entry_points=[CommandHandler("start", start)],
        states={
            NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, ask_date)],
            DATE: [MessageHandler(filters.TEXT & ~filters.COMMAND, ask_time)],
            TIME: [MessageHandler(filters.TEXT & ~filters.COMMAND, ask_city)],
            CITY: [MessageHandler(filters.TEXT & ~filters.COMMAND, save_user)],
        },
        fallbacks=[]
    )

    app.add_handler(conv)
    app.add_handler(CommandHandler("menu", menu))
    app.run_polling()

if __name__ == "__main__":
    main()