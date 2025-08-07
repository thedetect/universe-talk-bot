"""
Referral and loyalty program logic.

This module implements a simple referral system for the astrology bot.
Each user receives a unique referral code that can be shared with
friends.  When a new user signs up using a referral link, the
referring user is awarded points that count towards rewards.  Users
can query their referral status to see how many friends they have
invited and how many points they have accumulated.

The referral mechanism works by attaching a parameter to the `/start`
command.  For example, a referral link might look like
`t.me/YourBot?start=ABC123`.  When a user starts the bot with this
parameter, the code is passed into `handle_referral` which records
the relationship in the database.
"""

from __future__ import annotations

import random
import string
from typing import List, Optional, Tuple

from . import config, database


def generate_referral_code() -> str:
    """Generate a random uppercase referral code.

    The code consists of eight alphanumeric characters.  A
    cryptographically secure random generator is not required here
    because the codes do not protect sensitive data; they simply
    distinguish one user from another.
    """
    return ''.join(random.choices(string.ascii_uppercase + string.digits, k=8))


def assign_referral_code(user_id: int) -> str:
    """Assign a new unique referral code to a user.

    If the user already has a referral code, it is returned as is.
    Otherwise a new code is generated and stored in the database.

    Parameters
    ----------
    user_id : int
        Telegram ID of the user.

    Returns
    -------
    str
        The user's referral code.
    """
    user = database.get_user(user_id)
    if user and user["referral_code"]:
        return user["referral_code"]
    # Generate a unique code and ensure no collision
    code = generate_referral_code()
    while database.get_user_by_referral_code(code) is not None:
        code = generate_referral_code()
    # Store the code
    database.add_or_update_user(user_id, referral_code=code)
    return code


def handle_referral(ref_code: Optional[str], new_user_id: int) -> None:
    """Record a referral if a valid code is supplied.

    When a new user starts the bot via a referral link, the `ref_code`
    parameter is passed to this function.  If the code matches an
    existing user and the new user has not already been referred,
    we mark the `referred_by` field for the new user and award
    loyalty points to the referrer.  If the code is invalid or the
    user refers themselves, no action is taken.

    Parameters
    ----------
    ref_code : str or None
        The referral code extracted from the /start command.  If None
        or empty, nothing happens.
    new_user_id : int
        Telegram ID of the user who just started the bot.
    """
    if not ref_code:
        return
    # Prevent self referrals
    referring_user = database.get_user_by_referral_code(ref_code)
    if not referring_user or referring_user["telegram_id"] == new_user_id:
        return
    new_user = database.get_user(new_user_id)
    # Only record referral if this is the first time and user has no referrer
    if new_user and new_user["referred_by"]:
        return
    # Assign the referred_by field and credit points
    database.add_or_update_user(new_user_id, referred_by=ref_code)
    database.update_points(referring_user["telegram_id"], config.REFERRAL_BONUS_POINTS)


def get_referral_status(user_id: int) -> Tuple[int, int, List[str]]:
    """Return a summary of the user's referral program status.

    Parameters
    ----------
    user_id : int
        Telegram ID of the user.

    Returns
    -------
    tuple
        (total_points, number_of_referrals, list_of_referred_names)
    """
    user = database.get_user(user_id)
    if not user:
        return (0, 0, [])
    points = user["points"] or 0
    referrals = database.list_referrals(user_id)
    names = [row["name"] or str(row["telegram_id"]) for row in referrals]
    return (points, len(referrals), names)