"""
Configuration file for the Telegram astrology bot.

This module centralizes all configuration constants used across the
application.  Storing values in a single place makes it easy to
maintain and update settings without hunting through the rest of
the code base.  Whenever possible avoid hard‑coding sensitive or
environment specific values directly into your logic.  Instead
reference them from here.  Some of these values, such as API
tokens, should be replaced with your own credentials before
running the bot in a production environment.

Attributes
----------
BOT_TOKEN : str
    The Telegram bot token obtained from BotFather.  This token
    authorizes the bot to connect to Telegram's Bot API.

PAYMENT_PROVIDER_TOKEN : str
    Token for the payment provider configured through BotFather.  When
    you connect a payment provider in BotFather, you will receive a
    provider token which must be supplied here.  See the Telegram
    documentation for details: https://core.telegram.org/bots/payments

DB_PATH : str
    Path to the SQLite database file used for storing user data,
    preferences and subscription status.  The bot will create this
    file if it does not already exist.

ADMIN_IDS : list[int]
    Telegram user IDs of administrators.  Only these users may
    execute privileged commands such as /broadcast.

CURRENCY : str
    ISO4217 currency code for the subscription price.  For example
    'EUR' for Euro or 'USD' for U.S. dollars.

PRICE_AMOUNT : int
    The cost of a subscription expressed in the smallest units of
    the currency (for example, cents).  Telegram accepts integer
    values only.  For example, a price of €4.99 should be entered
    as 499 if the currency is EUR.

SUBSCRIPTION_DURATION_DAYS : int
    The number of days a subscription remains valid after purchase.

REFERRAL_BONUS_POINTS : int
    The number of loyalty points awarded to a user for each
    successful referral.

TIMEZONE : str
    Default timezone used when scheduling daily messages.  Users
    may specify their own notification times, but if a timezone
    is not provided this value will be used.  See the `pytz` or
    `zoneinfo` documentation for valid names.
"""

import os

# Telegram bot token provided by BotFather.  Replace the placeholder
# below with your actual token before deploying the bot.  Never
# commit real tokens to public repositories.
BOT_TOKEN: str = os.environ.get("TELEGRAM_BOT_TOKEN", "8044113090:AAHj1t4b-9Jd-fCksbTrfOnzorrZcORL-UA")

# Payment provider token obtained from BotFather after configuring
# your payment provider (e.g. Stripe).  Replace with your own token.
PAYMENT_PROVIDER_TOKEN: str = os.environ.get("1744374395:TEST:bb3fb88b22f385da8b8e", "YOUR_PROVIDER_TOKEN_HERE")

# Location of the SQLite database file.  By default this file will
# reside in the same directory as this configuration module.
DB_PATH: str = os.environ.get("BOT_DB_PATH", os.path.join(os.path.dirname(__file__), "user_data.db"))

# IDs of users who should be treated as administrators.  Admins have
# access to commands that broadcast messages or view internal state.
ADMIN_IDS: list[int] = []  # e.g. [123456789]

# Currency code and pricing for subscriptions.  Adjust these
# according to your business model.  See the Telegram payment docs
# for supported currencies: https://core.telegram.org/bots/payments
CURRENCY: str = "EUR"
PRICE_AMOUNT: int = 499  # 4.99 EUR expressed in cents
SUBSCRIPTION_DURATION_DAYS: int = 30

# Loyalty program configuration
REFERRAL_BONUS_POINTS: int = 10

# Default timezone for scheduling daily messages.  Users can choose
# their own preferred times; however this timezone will be used
# when interpreting those times unless specified otherwise.
TIMEZONE: str = "Europe/Berlin"

# Name and description for the subscription product.  These values
# appear in the invoice presented to the user.
SUBSCRIPTION_TITLE: str = "Premium Astrology Subscription"
SUBSCRIPTION_DESCRIPTION: str = (
    "Получите доступ к ежедневным астрологическим сообщениям с глубокими мотивационными "
    "и духовными советами, а также дополнительные бонусы в нашей программе лояльности."
)

# URL to an image displayed in the invoice.  You can host an image
# externally or serve one from your own infrastructure.  If you
# choose not to provide an image just leave this blank.
SUBSCRIPTION_IMAGE_URL: str | None = None