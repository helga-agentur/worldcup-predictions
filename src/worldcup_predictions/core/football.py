"""Football-domain helpers shared by model and plugins."""

from __future__ import annotations

import datetime as dt
import math
import unicodedata
from typing import Any


TOURNAMENT_START_DATE = dt.date(2026, 6, 11)


POSITION_ALIASES = {
    "goalkeeper": "goalkeeper",
    "keeper": "goalkeeper",
    "torwart": "goalkeeper",
    "defender": "defense",
    "defence": "defense",
    "defense": "defense",
    "centre-back": "defense",
    "center-back": "defense",
    "full-back": "defense",
    "verteidiger": "defense",
    "midfielder": "midfield",
    "midfield": "midfield",
    "mittelfeld": "midfield",
    "forward": "attack",
    "striker": "attack",
    "attack": "attack",
    "attacker": "attack",
    "winger": "attack",
    "sturm": "attack",
}


def clamp(value: float, lower: float, upper: float) -> float:
    return max(lower, min(upper, value))


def clean_person_name(value: Any) -> str:
    text = unicodedata.normalize("NFKD", str(value or ""))
    text = "".join(char for char in text if not unicodedata.combining(char))
    return " ".join(text.lower().replace("-", " ").replace(".", " ").split())


def normalize_position(value: Any) -> str:
    text = clean_person_name(value)
    if text in POSITION_ALIASES:
        return POSITION_ALIASES[text]
    for token, normalized in POSITION_ALIASES.items():
        if token in text:
            return normalized
    return "unknown"


def optional_float(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def optional_int(value: Any) -> int | None:
    if value in (None, ""):
        return None
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return None


def age_at_tournament(date_of_birth: Any, *, reference: dt.date = TOURNAMENT_START_DATE) -> float | None:
    if not date_of_birth:
        return None
    try:
        born = dt.date.fromisoformat(str(date_of_birth)[:10])
    except ValueError:
        return None
    return (reference - born).days / 365.25


def scaled_log_adjustment(value: float, median_value: float, confidence: float, *, cap: float) -> float:
    if value <= 0 or median_value <= 0 or confidence <= 0:
        return 0.0
    return clamp(math.log(value / median_value) / 10, -cap, cap) * confidence
