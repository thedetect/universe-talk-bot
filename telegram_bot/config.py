
import os
BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
PAYMENT_PROVIDER_TOKEN = os.getenv("TELEGRAM_PAYMENT_PROVIDER_TOKEN", "").strip()
CURRENCY = os.getenv("CURRENCY", "EUR")
DB_PATH = os.getenv("DB_PATH", "user_data.db")
TIMEZONE = os.getenv("TZ", "Europe/Berlin")
TRIAL_DAYS = int(os.getenv("TRIAL_DAYS", "10"))
REF_BONUS_DAYS_PER_USER = int(os.getenv("REF_BONUS_DAYS_PER_USER", "10"))
ADMIN_IDS = {int(x) for x in os.getenv("ADMIN_IDS", "").split(",") if x.strip().isdigit()}
WEBHOOK_SECRET = os.getenv("WEBHOOK_SECRET", "").strip()
