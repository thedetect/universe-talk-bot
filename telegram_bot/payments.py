"""
Payment handling for the Telegram astrology bot.

This module encapsulates all functions related to Telegram payments.
It builds invoices for subscriptions, responds to pre‑checkout
queries, and processes successful payments by updating user
subscriptions in the database.  Integrating with Telegram payments
requires enabling a payment provider via BotFather and supplying
the `PAYMENT_PROVIDER_TOKEN` in the configuration.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from telegram import LabeledPrice, Update
from telegram.ext import CallbackContext

from . import config, database


def send_subscription_invoice(update: Update, context: CallbackContext) -> None:
    """Send an invoice to the user to purchase a premium subscription.

    The invoice includes the title, description, price and currency
    configured in `config.py`.  When the user completes the payment,
    Telegram will send a `successful_payment` update which is handled
    by `handle_successful_payment`.

    Parameters
    ----------
    update : telegram.Update
        Incoming update representing a user message.
    context : telegram.ext.CallbackContext
        Context passed by the dispatcher.
    """
    chat_id = update.effective_chat.id
    title = config.SUBSCRIPTION_TITLE
    description = config.SUBSCRIPTION_DESCRIPTION
    # Payload can be any string; we use a fixed identifier for
    # subscription purchases
    payload = "subscription-payload"
    provider_token = config.PAYMENT_PROVIDER_TOKEN
    currency = config.CURRENCY
    # Telegram expects prices as a list of LabeledPrice objects with
    # amounts specified in the smallest currency units (e.g. cents)
    prices = [LabeledPrice(label=title, amount=config.PRICE_AMOUNT)]
    photo_url = config.SUBSCRIPTION_IMAGE_URL
    photo_width = 512
    photo_height = 512
    need_name = False
    need_phone_number = False
    need_email = False
    need_shipping_address = False
    is_flexible = False
    context.bot.send_invoice(
        chat_id,
        title=title,
        description=description,
        payload=payload,
        provider_token=provider_token,
        currency=currency,
        prices=prices,
        photo_url=photo_url,
        photo_width=photo_width,
        photo_height=photo_height,
        need_name=need_name,
        need_phone_number=need_phone_number,
        need_email=need_email,
        need_shipping_address=need_shipping_address,
        is_flexible=is_flexible,
    )


def process_precheckout_query(update: Update, context: CallbackContext) -> None:
    """Answer the pre‑checkout query.

    Telegram sends a pre‑checkout query before processing a payment.  The
    bot must answer this query to confirm that it can proceed with
    collecting funds.  If any checks fail (e.g., the price has
    changed), you can refuse the payment by passing `ok=False` and a
    reason.
    """
    query = update.pre_checkout_query
    # For simplicity, always approve the payment.  In a real
    # application you might verify stock availability or user
    # eligibility here.
    query.answer(ok=True)


def handle_successful_payment(update: Update, context: CallbackContext) -> None:
    """Handle a successful payment from the user.

    When the user pays the invoice, Telegram sends an update with
    `successful_payment` populated.  This function updates the
    subscription status in the database and sends a thank you
    message.
    """
    user = update.effective_user
    telegram_id = user.id
    # Determine new expiration date.  If the user already has a
    # subscription, extend it; otherwise set it to now + duration.
    existing = database.get_user(telegram_id)
    now = datetime.utcnow().replace(tzinfo=timezone.utc)
    if existing and existing["subscription_active"] and existing["subscription_expiration"]:
        try:
            current_expiration = datetime.fromisoformat(existing["subscription_expiration"])
        except Exception:
            current_expiration = now
        if current_expiration > now:
            base_date = current_expiration
        else:
            base_date = now
    else:
        base_date = now
    new_expiration = base_date + timedelta(days=config.SUBSCRIPTION_DURATION_DAYS)
    database.set_subscription_status(
        telegram_id,
        active=True,
        expiration_date=new_expiration.isoformat(),
    )
    # Optionally reward additional loyalty points for the purchase
    database.update_points(telegram_id, config.REFERRAL_BONUS_POINTS)
    # Send confirmation message
    context.bot.send_message(
        chat_id=update.effective_chat.id,
        text=(
            "Спасибо за покупку подписки! Ваша подписка активна до "
            f"{new_expiration.date().isoformat()}. Приятного чтения астрологических сообщений!"
        ),
    )