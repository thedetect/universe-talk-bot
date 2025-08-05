from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import CallbackContext, CallbackQueryHandler

def get_menu_keyboard():
    keyboard = [
        [InlineKeyboardButton("👤 Мой профиль", callback_data='view_profile')],
        [InlineKeyboardButton("✏️ Редактировать данные", callback_data='edit_profile')],
        [InlineKeyboardButton("🌅 Изменить время рассылки", callback_data='edit_time')],
        [InlineKeyboardButton("🤝 Пригласить друга", callback_data='invite_friend')]
    ]
    return InlineKeyboardMarkup(keyboard)

async def menu_handler(update: Update, context: CallbackContext):
    query = update.callback_query
    await query.answer()
    await query.edit_message_text(
        text="✨ Меню управления:
Выбери действие:",
        reply_markup=get_menu_keyboard()
    )

# Ниже будут функции view_profile, edit_profile и т.п., которые подключаются в основном bot.py