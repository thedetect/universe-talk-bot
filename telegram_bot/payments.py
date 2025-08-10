
from __future__ import annotations
from telegram import Update
from telegram.ext import CallbackContext
from . import config, database

TARIFFS = [(30,"Подписка на 30 дней"),(60,"Подписка на 60 дней"),(90,"Подписка на 90 дней"),(120,"Подписка на 120 дней"),(180,"Подписка на 180 дней")]

def offer_subscriptions_text() -> str:
    return "Доступные варианты продления:\n" + "\n".join([f"• {t} — /extend_{d}" for d,t in TARIFFS])

def handle_extend(update: Update, context: CallbackContext, days: int):
    if not config.PAYMENT_PROVIDER_TOKEN:
        database.extend_subscription(update.effective_user.id, days)
        update.message.reply_text(f"Подписка продлена на {days} дней. Спасибо!")
        return
    update.message.reply_text("Оплата пока не настроена. Мы сохранили ваш выбор — включим как только подключим провайдера.")
