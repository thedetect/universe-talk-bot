# -*- coding: utf-8 -*-
"""
astrology.py — персональный генератор ежедневного сообщения по транзитам Skyfield.

Точки персонализации:
- Тема дня: по сильнейшему аспекту транзита к наталу
- 3 тезиса: по топ-3 аспектам
- "Действуй" (2 пункта): по гармоничным аспектам
- "Категорически" (2 пункта): по напряжённым аспектам
- Утренний ритуал: по стихии солнечного знака натала
- Девиз дня: из data/mottos_ru.txt (детерминированно по дате и user_id)

Если эфемериды недоступны (нет сети/кэша), возвращается мягкий fallback-текст.
"""

from __future__ import annotations
from dataclasses import dataclass
from datetime import datetime
from typing import List, Dict, Tuple
import os
import math
import hashlib
import pytz

try:
    from skyfield.api import load
except Exception:  # на всякий случай, если пакет не установился
    load = None  # type: ignore

# ======== Конфигурация времени ========
TZ = os.getenv("TZ", "Europe/Berlin")
TZINFO = pytz.timezone(TZ)

# ======== Skyfield: timescale и эфемериды ========
_ts = None
_eph = None
if load:
    try:
        _ts = load.timescale()
        # de421.bsp скачивается и кэшируется автоматически Skyfield'ом
        _eph = load("de421.bsp")
    except Exception:
        _ts = None
        _eph = None

# ======== Аспекты и планеты ========
ASPECTS: List[Tuple[str, float, float]] = [
    ("conj", 0.0, 6.0),    # соединение
    ("sext", 60.0, 4.0),   # секстиль
    ("sq", 90.0, 4.0),     # квадрат
    ("tri", 120.0, 4.0),   # тригон
    ("opp", 180.0, 6.0),   # оппозиция
]

PLANETS = ["Sun", "Moon", "Mercury", "Venus", "Mars", "Jupiter", "Saturn"]

# ======== Правила текстов ========
RULES_THEME: Dict[Tuple[str, str], str] = {
    ("Sun", "conj"): "Фокус на самовыражении и ясности. Важно заявить о себе.",
    ("Moon", "conj"): "Чувствительность выше обычного — прислушайся к себе.",
    ("Mercury", "conj"): "День для слов, договорённостей и быстрых решений.",
    ("Venus", "conj"): "Тепло в отношениях и эстетике. Укрась свой день.",
    ("Mars", "conj"): "Энергия и напор, делай первый шаг.",
    ("Jupiter", "conj"): "Расширение горизонтов: учёба, планы, щедрость.",
    ("Saturn", "conj"): "Структура и дисциплина. Порядок приведёт к свободе.",
    # можно расширить при желании под другие аспекты
}

RULES_DO: Dict[str, List[str]] = {
    "Mercury_pos": ["Ответь на важное письмо/сообщение", "Запланируй короткий созвон"],
    "Venus_pos":   ["Сделай кому-то комплимент", "Добавь красоты в рабочее пространство"],
    "Mars_pos":    ["Сделай один смелый шаг к цели", "Разгреби то, что откладывал(а)"],
    "Jupiter_pos": ["Потрать 20 минут на обучение", "Расширь план на месяц"],
    "Saturn_pos":  ["Определи рамки и сроки дела", "Убери лишнее из планов"],
    "Moon_pos":    ["10 минут тишины для себя", "Запиши 3 чувства прямо сейчас"],
}

RULES_DONT: Dict[str, List[str]] = {
    "Mercury_neg": ["Не принимай решения «на эмоциях»", "Избегай инфошума"],
    "Venus_neg":   ["Не сравнивай себя с другими", "Не покупай импульсивно «лишь бы»"],
    "Mars_neg":    ["Не сжигай мосты в спешке", "Не спорь ради спора"],
    "Jupiter_neg": ["Не разбрасывайся обещаниями", "Не перерасходуй ресурс"],
    "Saturn_neg":  ["Не откладывай важное «на потом»", "Не перегружай график"],
    "Moon_neg":    ["Не игнорируй усталость", "Не заедай чувства — их лучше прожить"],
}

RITUALS_BY_ELEMENT: Dict[str, str] = {
    "fire":  "5 минут активного дыхания/движения — разогрей внутренний мотор.",
    "earth": "Тёплый чай и чек-лист из трёх простых дел — заземлись.",
    "air":   "3 минуты дыхания 4–7–8 — освежи голову.",
    "water": "Спокойная музыка и стакан воды — дай чувствам мягкую поддержку.",
}

# ======== Модель входных данных ========
@dataclass
class UserData:
    user_id: int
    name: str
    birth_datetime_iso: str  # "YYYY-MM-DD HH:MM" (локальное время пользователя, TZ из env)
    daily_time: str          # "HH:MM"

# ======== Утилиты ========
def _safe_ts():
    if _ts is None:
        raise RuntimeError("Skyfield timescale not initialized")
    return _ts

def _safe_eph():
    if _eph is None:
        raise RuntimeError("Skyfield ephemeris not available")
    return _eph

def _ecl_long(planet: str, t) -> float:
    """Экл. долгота планеты в градусах [0..360)."""
    eph = _safe_eph()
    e = eph[planet].at(t).ecliptic_position().longitude.degrees % 360.0
    return float(e)

def _ang_diff(a: float, b: float) -> float:
    """Минимальная угловая разница 0..180."""
    return abs((a - b + 180.0) % 360.0 - 180.0)

def _find_aspects(natal: Dict[str, float], trans: Dict[str, float]) -> List[Tuple[str, str, str, float]]:
    """
    Возвращает список аспектов (tr_planet, nat_planet, aspect_code, weight)
    weight — простая метрика важности: скорость планеты + точность аспекта
    """
    hits: List[Tuple[str, str, str, float]] = []
    speed_rank = {"Moon": 6, "Mercury": 5, "Venus": 5, "Sun": 4, "Mars": 4, "Jupiter": 3, "Saturn": 2}
    for p_tr, lon_tr in trans.items():
        for p_nat, lon_nat in natal.items():
            d = _ang_diff(lon_tr, lon_nat)
            for code, exact, orb in ASPECTS:
                # считаем аспект "пойманным", если попали в орбис вокруг точного угла
                if abs(d - exact) <= orb:
                    tight = max(0.0, (orb - abs(d - exact)) / orb)  # 0..1
                    w = speed_rank.get(p_tr, 1) + tight
                    hits.append((p_tr, p_nat, code, w))
                    break
    hits.sort(key=lambda x: x[3], reverse=True)
    return hits

def _sun_element(lon: float) -> str:
    """Стихия Солнца по знаку: огонь/земля/воздух/вода."""
    idx = int((lon % 360.0) // 30)  # 0..11
    # 0 Овен,1 Телец,2 Близнецы,3 Рак,4 Лев,5 Дева,6 Весы,7 Скорпион,8 Стрелец,9 Козерог,10 Водолей,11 Рыбы
    if idx in (0, 4, 8):
        return "fire"
    if idx in (1, 5, 9):
        return "earth"
    if idx in (2, 6, 10):
        return "air"
    return "water"

def _motto_for(dt_local: datetime, user_id: int) -> str:
    """
    Дет. выбор девиза по дате (локальной) и user_id.
    Берём data/mottos_ru.txt (по одной цитате в строку), если файла нет — fallback.
    """
    path = os.path.join(os.path.dirname(__file__), "..", "data", "mottos_ru.txt")
    quotes: List[str] = []
    try:
        if os.path.exists(path):
            with open(path, "r", encoding="utf-8") as f:
                quotes = [ln.strip() for ln in f if ln.strip()]
    except Exception:
        quotes = []
    if not quotes:
        quotes = [
            "Любовь увеличивает понимание.",
            "Там, где внимание — там энергия.",
            "Каждый день даёт шанс начать заново.",
            "Я выбираю двигаться шаг за шагом.",
        ]
    h = int(hashlib.sha1(f"{dt_local.date()}:{user_id}".encode("utf-8")).hexdigest(), 16)
    return quotes[h % len(quotes)]

# ======== Генерация персонального сообщения ========
def generate_daily_message(u: UserData) -> str:
    """
    Возвращает полностью собранный персональный текст для пользователя.
    """
    # Если Skyfield недоступен — мягкий fallback
    if _ts is None or _eph is None:
        name = u.name or "друг"
        motto = _motto_for(datetime.now(TZINFO), u.user_id)
        fallback = [
            f"🌅 Доброе утро, {name}!",
            "🔮 Тема дня: «Бережность и ясность»",
            "",
            "• Сегодня важно прислушиваться к внутреннему голосу.",
            "• Добавь немного красоты и порядка в дела.",
            "• Сделай один маленький шаг к цели.",
            "",
            "✅ Действуй:",
            "• Заверши то, что давно откладывал(а).",
            "• Запиши 3 коротких шага на сегодня.",
            "",
            "❌ Категорически:",
            "• Не торопи события — всё придёт вовремя.",
            "• Не сравнивай себя с другими.",
            "",
            "🕯 Утренний ритуал (5 минут):",
            "Тёплый напиток, три глубоких вдоха и короткая запись мыслей.",
            "",
            f"🔑 Девиз дня: «{motto}»",
        ]
        return "\n".join(fallback)

    # 1) Разбираем дату рождения: строка "YYYY-MM-DD HH:MM" в локальном TZ
    try:
        born_local = datetime.strptime(u.birth_datetime_iso.strip(), "%Y-%m-%d %H:%M")
    except Exception:
        born_local = datetime(2000, 1, 1, 12, 0)
    born_aware = TZINFO.localize(born_local)
    born_utc = born_aware.astimezone(pytz.utc)

    t_nat = _ts.from_datetime(born_utc)

    # 2) Натал: эклиптические долготы планет
    natal: Dict[str, float] = {}
    for p in PLANETS:
        try:
            natal[p] = _ecl_long(p, t_nat)
        except Exception:
            natal[p] = 0.0

    # 3) Транзиты: берём полдень локального дня, чтобы стабильно
    now_local = datetime.now(TZINFO)
    trans_local_noon = now_local.replace(hour=12, minute=0, second=0, microsecond=0)
    trans_utc = trans_local_noon.astimezone(pytz.utc)
    t_tr = _ts.from_datetime(trans_utc)

    trans: Dict[str, float] = {}
    for p in PLANETS:
        try:
            trans[p] = _ecl_long(p, t_tr)
        except Exception:
            trans[p] = 0.0

    # 4) Аспекты и ранжирование
    aspects = _find_aspects(natal, trans)  # [(tr, nat, code, weight), ...]
    top = aspects[:6]

    # 5) Тема дня
    theme = "День для бережности и внимания к себе."
    if top:
        tr, natp, code, _ = top[0]
        theme = RULES_THEME.get((tr, code), theme)

    # 6) Тезисы (до 3 коротких)
    theses: List[str] = []
    for tr, natp, code, _ in top[:3]:
        if tr == "Moon":
            theses.append("Сегодня важно прислушиваться к внутреннему голосу.")
        elif tr == "Mars" and code in ("conj", "tri", "sext"):
            theses.append("Марс даёт тебе силу говорить «нет» лишнему.")
        elif tr == "Venus":
            theses.append("День подходит для заботы о себе и близких.")
        elif tr == "Mercury":
            theses.append("Мысли становятся яснее — проговори важное.")
        elif tr == "Jupiter":
            theses.append("Идеи прорастают — смело расширяй горизонт.")
        elif tr == "Saturn":
            theses.append("Порядок и рамки сегодня — твои союзники.")
    # уникализируем и ограничим
    seen = set()
    theses = [t for t in theses if not (t in seen or seen.add(t))][:3]

    # 7) Действуй/Категорически
    do_list: List[str] = []
    dont_list: List[str] = []
    for tr, natp, code, _ in top:
        positive = code in ("conj", "tri", "sext")
        key = f"{tr}_{'pos' if positive else 'neg'}"
        if positive:
            do_list.extend(RULES_DO.get(key, []))
        else:
            dont_list.extend(RULES_DONT.get(key, []))
        if len(do_list) >= 4 and len(dont_list) >= 4:
            break
    # уникализируем и ограничим до 2
    def uniq2(items: List[str]) -> List[str]:
        s, out = set(), []
        for it in items:
            if it not in s:
                s.add(it); out.append(it)
            if len(out) >= 2:
                break
        return out

    do_final = uniq2(do_list) or ["Сделай один маленький шаг к мечте.", "Заверши то, что давно откладывал(а)."]
    dont_final = uniq2(dont_list) or ["Не торопи события — всё придёт вовремя.", "Избегай самоедства и лишней критики."]

    # 8) Ритуал по стихии Солнца натала
    sun_element = _sun_element(natal.get("Sun", 0.0))
    ritual = RITUALS_BY_ELEMENT.get(sun_element, RITUALS_BY_ELEMENT["earth"])

    # 9) Девиз дня
    motto = _motto_for(now_local, u.user_id)

    # 10) Сборка сообщения
    name = u.name or "друг"
    parts: List[str] = []
    parts.append(f"🌅 Доброе утро, {name}!")
    parts.append(f"\n🔮 Тема дня: «{theme}»")
    if theses:
        parts.append("")
        parts.extend(f"• {t}" for t in theses)
    parts.append("")
    parts.append("✅ Действуй:")
    parts.extend(f"• {x}" for x in do_final)
    parts.append("")
    parts.append("❌ Категорически:")
    parts.extend(f"• {x}" for x in dont_final)
    parts.append("")
    parts.append("🕯 Утренний ритуал (5 минут):")
    parts.append(ritual)
    parts.append("")
    parts.append(f"🔑 Девиз дня: «{motto}»")

    return "\n".join(parts)