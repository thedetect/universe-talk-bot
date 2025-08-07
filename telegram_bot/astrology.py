"""
Astrology calculation utilities using the Skyfield library.

This module defines functions to compute the positions of major
planets at a user's birth time and at the current moment, then
compare those positions to determine astrological transits.  The
result is used to craft motivational and reflective messages for the
user.  The calculations here are simplified and should not be
considered accurate enough for professional astrological work.  They
demonstrate how one might use astronomical positions to inform a
creative daily horoscope.

Skyfield downloads ephemeris data on demand.  The first time the
functions in this module run, they may download the `de421.bsp`
file from the internet.  This file contains planetary positions
covering the years 1900–2050.  If your environment cannot access
the internet, you should download the file ahead of time and place
it in the working directory; Skyfield will then use the local copy.
"""

from __future__ import annotations

import math
import random
from datetime import datetime, timezone
from typing import Dict, Optional

from skyfield.api import load
from skyfield.framelib import ecliptic_frame


PLANETS = ["sun", "moon", "mercury", "venus", "mars", "jupiter", "saturn"]


def _get_ecliptic_longitudes(ts, eph, t) -> Dict[str, float]:
    """Compute ecliptic longitude of each planet at time `t`.

    Parameters
    ----------
    ts : skyfield.api.Timescale
        A Skyfield timescale object created via `load.timescale()`.
    eph : skyfield.jpllib.SpiceKernel
        Loaded ephemeris (e.g. via `load('de421.bsp')`).
    t : skyfield.api.Time
        The time at which positions should be calculated.

    Returns
    -------
    dict
        Mapping of planet name to ecliptic longitude in degrees.
    """
    longitudes: Dict[str, float] = {}
    earth = eph["earth"]
    for planet in PLANETS:
        try:
            body = eph[planet]
        except KeyError:
            # Skip unknown bodies
            continue
        # Geocentric observation of the planet
        astrometric = earth.at(t).observe(body).apparent()
        # Convert to ecliptic coordinates
        lat, lon, distance = astrometric.frame_latlon(ecliptic_frame)
        longitudes[planet] = lon.degrees % 360
    return longitudes


def compute_natal_positions(birth_datetime: datetime) -> Dict[str, float]:
    """Compute natal ecliptic longitudes for the seven classical planets.

    Parameters
    ----------
    birth_datetime : datetime.datetime
        The user's birth date and time in UTC.  If a naïve datetime is
        provided, it is assumed to be in UTC.

    Returns
    -------
    dict
        Planet names mapped to ecliptic longitudes in degrees at the
        moment of birth.
    """
    # Convert naive datetime to aware UTC
    if birth_datetime.tzinfo is None:
        birth_datetime = birth_datetime.replace(tzinfo=timezone.utc)
    ts = load.timescale()
    t = ts.from_datetime(birth_datetime)
    eph = load("de421.bsp")
    return _get_ecliptic_longitudes(ts, eph, t)


def compute_current_positions(current_datetime: Optional[datetime] = None) -> Dict[str, float]:
    """Compute current ecliptic longitudes of the planets.

    Parameters
    ----------
    current_datetime : datetime, optional
        The time for which to calculate positions.  Defaults to now
        in UTC.

    Returns
    -------
    dict
        Planet names mapped to current ecliptic longitudes.
    """
    if current_datetime is None:
        current_datetime = datetime.utcnow().replace(tzinfo=timezone.utc)
    elif current_datetime.tzinfo is None:
        current_datetime = current_datetime.replace(tzinfo=timezone.utc)
    ts = load.timescale()
    t = ts.from_datetime(current_datetime)
    eph = load("de421.bsp")
    return _get_ecliptic_longitudes(ts, eph, t)


def compute_transits(natal_positions: Dict[str, float], current_positions: Dict[str, float]) -> Dict[str, float]:
    """Compute angular difference between natal and current positions.

    The difference is normalized to the range [0, 360).  Smaller
    differences indicate conjunctions, while values near 180° indicate
    oppositions.

    Returns a mapping of planet to the absolute difference in
    degrees.
    """
    transits: Dict[str, float] = {}
    for planet, natal_lon in natal_positions.items():
        current_lon = current_positions.get(planet)
        if current_lon is None:
            continue
        diff = abs(current_lon - natal_lon) % 360
        # Normalize to <= 180
        if diff > 180:
            diff = 360 - diff
        transits[planet] = diff
    return transits


def interpret_transits(transits: Dict[str, float]) -> str:
    """Generate a human‑readable interpretation of planetary transits.

    For each planet the angular distance to its natal position is
    classified into one of several aspect bins (conjunction, square,
    trine, opposition, sextile).  Each aspect has a set of
    motivational phrases which are randomly chosen to keep the
    messages fresh.  The final output is a paragraph assembled from
    all individual planet interpretations, plus a reflective
    question at the end.

    Returns
    -------
    str
        A multi‑sentence motivational and humorous horoscope.
    """
    messages: list[str] = []

    # Define aspect thresholds in degrees
    aspects = [
        (0, 10, "соединение"),          # conjunction
        (60 - 5, 60 + 5, "секстиль"),   # sextile
        (90 - 5, 90 + 5, "квадрат"),     # square
        (120 - 5, 120 + 5, "трин"),      # trine
        (180 - 10, 180 + 10, "оппозиция"), # opposition
    ]
    # Generic interpretations per planet and aspect
    planet_phrases = {
        "sun": {
            "соединение": [
                "Солнце возвращается в ту же точку, что и при вашем рождении. Это шанс переосмыслить своё "
                "чувство предназначения и сиять ярче."],
            "квадрат": [
                "Солнце образует квадрат к вашей натальной позиции, может казаться, что мир бросает вызов. "
                "Используйте эти искры напряжения, чтобы зажечь новые начинания."],
            "трин": [
                "Солнце в гармоничном тригоне с вашим наталом. Сегодня энергия течет легко — сияйте, как "
                "никогда раньше!"],
            "секстиль": [
                "Солнце делает секстиль. Это мягкий пинок судьбы: время для небольших, но важных шагов."],
            "оппозиция": [
                "Солнце напротив вашей натальной позиции. Отражения других людей покажут, кем вы стали."],
        },
        "moon": {
            "соединение": [
                "Луна возвращается в положение рождения, вызывая знакомые эмоции. Дайте себе право чувствовать."],
            "квадрат": [
                "Лунный квадрат может обострить чувства, но это отличный повод посмотреть на свои привычки по-новому."],
            "трин": [
                "Лунный трин приносит чувство гармонии и спокойствия. Побалуйте себя заботой."],
            "секстиль": [
                "Луна в секстиле поддерживает нежные перемены. Добавьте щепотку юмора в рутину."],
            "оппозиция": [
                "Луна напротив натала заставляет балансировать между чужими потребностями и своими. Найдите золотую середину."],
        },
        "mercury": {
            "соединение": [
                "Меркурий соединяется с вашим наталом, открывая ум для свежих идей. Запишите неожиданные мысли!"],
            "квадрат": [
                "Меркурий в квадрате предупреждает о недопониманиях. Проверяйте факты и не забывайте шутить."],
            "трин": [
                "Меркурий в тригоне обостряет ум. Идеи сегодня словно искры — лови их!"],
            "секстиль": [
                "Меркурий делает секстиль и помогает обучению. Начните читать ту книгу, что давно ждет вас."],
            "оппозиция": [
                "Меркурий напротив натала — время послушать других. Даже ваш кот может дать мудрый совет."],
        },
        "venus": {
            "соединение": [
                "Венера соединяется, принося волну любви и красоты. Улыбнитесь прохожему — вдруг это судьба?"],
            "квадрат": [
                "Венера в квадрате вызывает непостоянство. Тратьте деньги с умом и шутками."],
            "трин": [
                "Венера в тригоне наполняет мир гармонией. Позвольте себе маленькие удовольствия."],
            "секстиль": [
                "Венера делает секстиль, напоминая радоваться мелочам. Цветы у дороги — тоже праздник."],
            "оппозиция": [
                "Венера напротив натала заставляет увидеть отношения под новым углом. Любовь — это когда оба смеются."],
        },
        "mars": {
            "соединение": [
                "Марс в соединении разжигает вашу смелость. Сегодня вы герой своего романа."],
            "квадрат": [
                "Марс в квадрате может принести раздражение. Сбросьте лишнюю энергию на прогулке или в спортзале."],
            "трин": [
                "Марс в тригоне наполняет вас энтузиазмом. Время двигаться к цели быстрыми шагами."],
            "секстиль": [
                "Марс делает секстиль, предлагая мягкий импульс. Действуйте, но без спешки."],
            "оппозиция": [
                "Марс напротив натала может вызывать борьбу. Выберите битвы мудро — не спорьте с бабушкой."],
        },
        "jupiter": {
            "соединение": [
                "Юпитер в соединении расширяет горизонты. Учитесь, путешествуйте, мечтайте!"],
            "квадрат": [
                "Юпитер в квадрате предупреждает о чрезмерности. Следите за аппетитом к приключениям и пирожным."],
            "трин": [
                "Юпитер в тригоне приносит удачу. Попросите вселенную о помощи — она слушает."],
            "секстиль": [
                "Юпитер делает секстиль, открывая новые возможности. Действуйте с юмором."],
            "оппозиция": [
                "Юпитер напротив натала напоминает: равновесие — ключ. Делитесь тем, что у вас есть, и получите больше."],
        },
        "saturn": {
            "соединение": [
                "Сатурн в соединении учит дисциплине. Задачи, которые вы давно откладывали, ждут вашего внимания."],
            "квадрат": [
                "Сатурн в квадрате накладывает ограничения. Терпение — ваше супероружие."],
            "трин": [
                "Сатурн в тригоне награждает за упорство. Сегодня фундамент ваших усилий укрепляется."],
            "секстиль": [
                "Сатурн делает секстиль, поддерживая устойчивый рост. Медленные шаги — тоже прогресс."],
            "оппозиция": [
                "Сатурн напротив натала проверяет ваши границы. Учитесь говорить 'нет' с улыбкой."],
        },
    }

    reflective_questions = [
        "Какой урок можно вынести из сегодняшних событий?",
        "Чему я научился за этот день?",
        "Какие моменты заставили меня улыбнуться?",
        "Что новое я узнал о себе?",
        "Что я могу отпустить сегодня?",
    ]

    for planet, diff in transits.items():
        # Determine aspect
        aspect_name: Optional[str] = None
        for low, high, name in aspects:
            if low <= diff <= high:
                aspect_name = name
                break
        if aspect_name is None:
            # Differences that don't fall into any major aspect are less
            # intense; skip them to avoid cluttering the message.
            continue
        phrases = planet_phrases.get(planet, {}).get(aspect_name, None)
        if not phrases:
            continue
        message = random.choice(phrases)
        messages.append(message)
    if not messages:
        messages.append(
            "Сегодня планеты отдыхают от ярких аспектов, так что вы можете творить свою "
            "собственную судьбу. Не забудьте посмеяться над чем‑нибудь." )
    # Append a reflective question
    question = random.choice(reflective_questions)
    messages.append(question)
    # Combine into a single string
    return '\n'.join(messages)


def get_daily_message(birth_date: str, birth_time: str) -> str:
    """High‑level function to build the daily horoscope message.

    This helper parses the stored birth date and time strings, computes
    natal and current positions and their differences, and returns a
    composed motivational message.  It expects the date in the format
    YYYY‑MM‑DD and the time in HH:MM (24‑hour) format.

    Parameters
    ----------
    birth_date : str
        Birth date in ISO format.
    birth_time : str
        Birth time in HH:MM.

    Returns
    -------
    str
        Personalized daily horoscope text.
    """
    try:
        year, month, day = map(int, birth_date.split("-"))
        hour, minute = map(int, birth_time.split(":"))
        birth_dt = datetime(year, month, day, hour, minute, tzinfo=timezone.utc)
    except Exception:
        # Fallback if parsing fails
        birth_dt = datetime.utcnow().replace(tzinfo=timezone.utc)
    natal_positions = compute_natal_positions(birth_dt)
    current_positions = compute_current_positions()
    transits = compute_transits(natal_positions, current_positions)
    return interpret_transits(transits)