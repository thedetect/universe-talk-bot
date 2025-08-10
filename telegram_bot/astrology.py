
from __future__ import annotations
from datetime import datetime
from typing import Dict
from skyfield.api import load

def _sun_longitude(ts, eph, dt):
    t = ts.utc(dt.year, dt.month, dt.day, dt.hour, dt.minute)
    e = eph['sun'].at(t).ecliptic_position().longitude.radians
    return e

def daily_message(user: Dict) -> str:
    ts = load.timescale()
    eph = load('de421.bsp')
    try:
        bd = user.get("birth_date","")
        bt = (user.get("birth_time") or "12:00")
        d, m, y = [int(x) for x in bd.split(".")]
        h, mi = [int(x) for x in bt.split(":")]
        natal_dt = datetime(y,m,d,h,mi)
    except Exception:
        natal_dt = datetime(2000,1,1,12,0)
    today_dt = datetime.utcnow()
    try:
        lon_natal = _sun_longitude(ts, eph, natal_dt)
        lon_today = _sun_longitude(ts, eph, today_dt)
        delta = abs(lon_today - lon_natal)
    except Exception:
        delta = 0.0

    if delta < 0.1:
        tip = "Энергии дня максимально созвучны твоей натальной солнечной вибрации — действуй смело."
    elif delta < 0.5:
        tip = "Хорошо зайдут аккуратные шаги: бери курс на устойчивое продвижение, без рывков."
    else:
        tip = "Контрастный день: учиться и пробовать новое — да, спорные решения — лучше завтра."

    name = user.get("name") or "Друг"
    return f"✨ {name}, космос шепчет: {tip}\n\nСаморефлексия: как одним действием сегодня приблизишься к цели?"
