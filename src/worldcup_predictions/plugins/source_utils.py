"""Shared helpers for source plugins."""

from __future__ import annotations

import datetime as dt
from pathlib import Path
from typing import Any

from worldcup_predictions.core.config import DEFAULT_CONFIG
from worldcup_predictions.core.constants import PROJECT_USER_AGENT
from worldcup_predictions.core.contracts import parse_utc_datetime
from worldcup_predictions.core.env import env_value
from worldcup_predictions.core.http import HttpClient


DEFAULT_TIMEOUT_SECONDS = DEFAULT_CONFIG.source_defaults.timeout_seconds
DEFAULT_USER_AGENT = PROJECT_USER_AGENT


def load_env_value(project_root: Path, name: str) -> str | None:
    """Load an environment variable from `os.environ` after project `.env` load."""

    return env_value(project_root, name)


def fetch_json(
    endpoint: str,
    params: dict[str, Any],
    *,
    timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS,
    headers: dict[str, str] | None = None,
) -> tuple[Any, dict[str, str]]:
    return HttpClient(timeout_seconds=timeout_seconds, user_agent=DEFAULT_USER_AGENT).get_json(
        endpoint,
        params,
        headers=headers,
    )


def fetch_text(
    endpoint: str,
    params: dict[str, Any] | None = None,
    *,
    timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS,
    headers: dict[str, str] | None = None,
) -> tuple[str, dict[str, str]]:
    response = HttpClient(timeout_seconds=timeout_seconds, user_agent=DEFAULT_USER_AGENT).get_text(
        endpoint,
        params,
        headers=headers,
    )
    return response.body, response.headers


def match_window_hours(event_date: str, *, before_minutes: int = 15, after_minutes: int = 135) -> tuple[dt.datetime, dt.datetime]:
    kickoff = parse_utc_datetime(event_date)
    if kickoff is None:
        raise ValueError(f"Invalid fixture event date: {event_date}")
    return kickoff - dt.timedelta(minutes=before_minutes), kickoff + dt.timedelta(minutes=after_minutes)


def date_range_for_window(start: dt.datetime, end: dt.datetime) -> tuple[str, str]:
    return start.date().isoformat(), end.date().isoformat()


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
        return int(value)
    except (TypeError, ValueError):
        return None
