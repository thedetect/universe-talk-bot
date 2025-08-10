
from __future__ import annotations
import secrets
from typing import List
from . import database, config

ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"

def _all_users() -> List[dict]:
    try:
        return list(database.get_all_users())
    except Exception:
        return []

def generate_referral_code(length: int = 8) -> str:
    existing = {u.get("referral_code") for u in _all_users() if u.get("referral_code")}
    while True:
        code = "".join(secrets.choice(ALPHABET) for _ in range(length))
        if code not in existing:
            return code

def assign_referral_code(telegram_id: int) -> str:
    u = database.get_user(telegram_id)
    if not u:
        raise ValueError("User not found")
    if u.get("referral_code"):
        return u["referral_code"]
    code = generate_referral_code()
    database.update_user_field(telegram_id, "referral_code", code)
    return code

def handle_referral(new_user_id: int, ref_code: str) -> bool:
    if not ref_code:
        return False
    referrer = None
    for u in _all_users():
        if u.get("referral_code") == ref_code:
            referrer = u
            break
    if not referrer or referrer["telegram_id"] == new_user_id:
        return False
    nu = database.get_user(new_user_id) or {}
    if nu.get("referred_by") is None:
        database.update_user_field(new_user_id, "referred_by", referrer["telegram_id"])
        database.add_referral_points(referrer["telegram_id"], config.REF_BONUS_DAYS_PER_USER)
        return True
    return False

def get_referral_status(telegram_id: int) -> dict:
    u = database.get_user(telegram_id) or {}
    code = u.get("referral_code") or assign_referral_code(telegram_id)
    invited = [x for x in _all_users() if x.get("referred_by") == telegram_id]
    return {
        "code": code,
        "count": len(invited),
        "invited": [i.get("name") or str(i.get("telegram_id")) for i in invited],
        "bonus_days": int(u.get("points") or 0),
    }
