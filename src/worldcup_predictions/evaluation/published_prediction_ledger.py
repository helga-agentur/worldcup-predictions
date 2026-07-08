"""Website-facing prediction ledger with locked historical rows."""

from __future__ import annotations

import datetime as dt
from typing import Any

from worldcup_predictions.core.constants import PUBLISHED_PREDICTION_LOCK_BUFFER_MINUTES
from worldcup_predictions.core.datasets import PREDICTION_LEDGER, PUBLISHED_PREDICTION_LEDGER
from worldcup_predictions.storage.ledger import normalize_datetime, parse_datetime, utc_now
from worldcup_predictions.tournament.repository import load_tournament_state


FROZEN_STATUSES = {"locked", "final"}

PREDICTION_FIELDS_TO_FREEZE = (
    "prediction_context",
    "predicted_home_goals",
    "predicted_away_goals",
    "most_likely_score",
    "most_likely_home",
    "most_likely_away",
    "prob_home",
    "prob_draw",
    "prob_away",
    "confidence_percent",
    "score_matrix",
    "provider_tips",
    "srf_tip",
    "srf_expected_points",
    "twenty_min_tip",
    "twenty_min_expected_points",
)

ACTUAL_FIELDS = (
    "actual_score",
    "actual_home",
    "actual_away",
)

PUBLISHED_REPLACEMENT_CONTEXTS = {
    "frozen_prediction_snapshot_before_kickoff",
    "archived_pre_refactor_prediction",
}

KNOWN_PUBLISHED_CONTEXTS = {
    "latest_live_prediction",
    "retrospective_current_model_before_kickoff",
    *PUBLISHED_REPLACEMENT_CONTEXTS,
}


def build_published_prediction_ledger_rows(storage, *, now: dt.datetime | None = None) -> list[dict[str, Any]]:
    """Build website-facing ledger rows from the latest model ledger.

    Future rows keep updating between scheduled runs. Rows that are inside the tip
    close buffer or already final are frozen so the website can keep an honest
    public archive while the model continues to learn.
    """

    now = now or utc_now()
    now_iso = normalize_datetime(now) or ""
    current_rows = storage.read_records(PREDICTION_LEDGER, latest_only=True)
    existing_rows = storage.read_records(PUBLISHED_PREDICTION_LEDGER, latest_only=True)
    active_fixture_keys = _active_fixture_keys(storage)
    existing_by_fixture = {
        str(row.get("fixture_key")): _strip_record(row)
        for row in existing_rows
        if row.get("fixture_key")
    }

    published_rows = []
    for row in sorted(current_rows, key=lambda item: str(item.get("event_date") or "")):
        fixture_key = str(row.get("fixture_key") or "")
        if not fixture_key:
            continue
        current = _as_published_row(_strip_record(row), now=now, now_iso=now_iso)
        if current.get("status") != "final" and active_fixture_keys and fixture_key not in active_fixture_keys:
            continue
        existing = existing_by_fixture.get(fixture_key)
        if existing is None:
            published_rows.append(current)
            continue
        if _should_reopen_future_final(existing, current, now=now):
            published_rows.append(_merge_live_row(existing, current, now_iso=now_iso))
            continue
        if _should_freeze(existing, current):
            published_rows.append(_merge_frozen_row(existing, current, now_iso=now_iso))
        else:
            published_rows.append(_merge_live_row(existing, current, now_iso=now_iso))
    return _dedupe_published_rows(published_rows)


def write_published_prediction_ledger(storage, *, run_id: str | None = None, now: dt.datetime | None = None) -> int:
    rows = build_published_prediction_ledger_rows(storage, now=now)
    return storage.replace_records(PUBLISHED_PREDICTION_LEDGER, rows, source="published_prediction_ledger", run_id=run_id)


def _as_published_row(row: dict[str, Any], *, now: dt.datetime, now_iso: str) -> dict[str, Any]:
    status = _published_status(row, now=now)
    event_date = str(row.get("event_date") or "")
    published = dict(row)
    published.update(
        {
            "record_key": str(row.get("fixture_key") or row.get("record_key") or ""),
            "fixture_key": row.get("fixture_key"),
            "event_date": event_date,
            "status": status,
            "prediction_ledger_status": row.get("status"),
            "first_published_at_utc": now_iso,
            "published_at_utc": now_iso,
            "locked_at_utc": now_iso if status in FROZEN_STATUSES else None,
            "finalized_at_utc": now_iso if status == "final" else None,
            "website_visibility": "public",
        }
    )
    published["metadata"] = {
        **_mapping(row.get("metadata")),
        "published_source_dataset": PREDICTION_LEDGER,
        "lock_buffer_minutes": PUBLISHED_PREDICTION_LOCK_BUFFER_MINUTES,
    }
    return published


def _published_status(row: dict[str, Any], *, now: dt.datetime) -> str:
    if row.get("status") == "past" or row.get("actual_score"):
        return "final"
    event_at = parse_datetime(str(row.get("event_date") or ""))
    if event_at is not None and event_at <= now + dt.timedelta(minutes=PUBLISHED_PREDICTION_LOCK_BUFFER_MINUTES):
        return "locked"
    return "future"


def _should_freeze(existing: dict[str, Any], current: dict[str, Any]) -> bool:
    if existing.get("status") in FROZEN_STATUSES:
        return True
    return current.get("status") == "final"


def _should_reopen_future_final(existing: dict[str, Any], current: dict[str, Any], *, now: dt.datetime) -> bool:
    if existing.get("status") != "final" or current.get("status") == "final":
        return False
    if current.get("actual_score"):
        return False
    return _event_is_future(current, now=now)


def _event_is_future(row: dict[str, Any], *, now: dt.datetime) -> bool:
    try:
        event_at = parse_datetime(str(row.get("event_date") or ""))
    except ValueError:
        return False
    if event_at is None:
        return False
    return event_at > now


def _merge_live_row(existing: dict[str, Any], current: dict[str, Any], *, now_iso: str) -> dict[str, Any]:
    merged = dict(current)
    merged["first_published_at_utc"] = existing.get("first_published_at_utc") or current.get("first_published_at_utc")
    merged["published_at_utc"] = now_iso
    return merged


def _merge_frozen_row(existing: dict[str, Any], current: dict[str, Any], *, now_iso: str) -> dict[str, Any]:
    if _should_replace_retrospective_row(existing, current):
        merged = dict(current)
        merged["first_published_at_utc"] = existing.get("first_published_at_utc") or current.get("first_published_at_utc")
        merged["published_at_utc"] = now_iso
        merged["locked_at_utc"] = existing.get("locked_at_utc") or now_iso
        merged["finalized_at_utc"] = existing.get("finalized_at_utc") or now_iso if current.get("status") == "final" else existing.get("finalized_at_utc")
        merged["metadata"] = {
            **_mapping(current.get("metadata")),
            "replaced_retrospective_prediction": True,
            "previous_prediction_context": existing.get("prediction_context"),
            "previous_metadata": existing.get("metadata") or {},
        }
        return merged
    merged = dict(existing)
    for field in PREDICTION_FIELDS_TO_FREEZE:
        merged[field] = existing.get(field)
    if _should_fill_reconstructed_archive_fields(existing, current):
        for field in PREDICTION_FIELDS_TO_FREEZE:
            current_value = current.get(field)
            if _is_missing_archived_value(merged.get(field)) and not _is_missing_archived_value(current_value):
                merged[field] = current_value
    for field in ACTUAL_FIELDS:
        merged[field] = current.get(field, existing.get(field))
    if current.get("status") == "final":
        merged["status"] = "final"
        merged["finalized_at_utc"] = existing.get("finalized_at_utc") or now_iso
    else:
        merged["status"] = existing.get("status") or current.get("status")
    merged["record_key"] = str(existing.get("fixture_key") or existing.get("record_key") or current.get("record_key") or "")
    merged["prediction_ledger_status"] = current.get("prediction_ledger_status") or current.get("status")
    merged["published_at_utc"] = now_iso
    merged["locked_at_utc"] = existing.get("locked_at_utc") or now_iso
    merged["metadata"] = {
        **_mapping(existing.get("metadata")),
        "current_prediction_ledger_status": current.get("prediction_ledger_status"),
        "current_prediction_ledger_metadata": current.get("metadata") or {},
    }
    if _should_fill_reconstructed_archive_fields(existing, current):
        merged["metadata"] = {
            **merged["metadata"],
            "reconstructed_prediction": True,
            "reconstruction_method": _mapping(current.get("metadata")).get("reconstruction_method"),
            "reconstruction_source": "prediction_ledger_missing_archive_field_fill",
        }
    return merged


def _should_replace_retrospective_row(existing: dict[str, Any], current: dict[str, Any]) -> bool:
    existing_context = str(existing.get("prediction_context") or "")
    current_context = str(current.get("prediction_context") or "")
    if current_context not in PUBLISHED_REPLACEMENT_CONTEXTS:
        return False
    if existing_context == "retrospective_current_model_before_kickoff":
        return True
    if existing_context == current_context == "archived_pre_refactor_prediction":
        return _mapping(existing.get("metadata")).get("twenty_min_source") != _mapping(current.get("metadata")).get("twenty_min_source")
    return bool(existing_context and existing_context not in KNOWN_PUBLISHED_CONTEXTS)


def _should_fill_reconstructed_archive_fields(existing: dict[str, Any], current: dict[str, Any]) -> bool:
    if str(existing.get("prediction_context") or "") != "archived_pre_refactor_prediction":
        return False
    if not _mapping(current.get("metadata")).get("reconstructed_prediction"):
        return False
    return any(_is_missing_archived_value(existing.get(field)) for field in PREDICTION_FIELDS_TO_FREEZE)


def _is_missing_archived_value(value: Any) -> bool:
    if value in (None, ""):
        return True
    if isinstance(value, list):
        return len(value) == 0
    if isinstance(value, dict):
        return len(value) == 0
    return False


def _dedupe_published_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Keep one website row per logical match page.

    Upstream fixture sources can correct kickoff times after a row has already
    been locked. Since website slugs intentionally use date + teams, a shifted
    fixture key such as 01:00 MEX-ECU and 02:00 MEX-ECU would otherwise render
    twice and point at the same detail page.
    """

    best_by_match: dict[str, dict[str, Any]] = {}
    for row in rows:
        key = _website_match_key(row)
        if not key:
            key = str(row.get("fixture_key") or row.get("record_key") or "")
        current = best_by_match.get(key)
        if current is None or _published_row_rank(row) >= _published_row_rank(current):
            best_by_match[key] = row
    return sorted(best_by_match.values(), key=lambda item: (str(item.get("event_date") or ""), str(item.get("fixture_key") or "")))


def _website_match_key(row: dict[str, Any]) -> str:
    fixture_key = str(row.get("fixture_key") or "")
    parts = fixture_key.split("|")
    if len(parts) == 3:
        date = parts[0][:10]
        home = parts[1].strip().upper()
        away = parts[2].strip().upper()
    else:
        date = str(row.get("event_date") or "")[:10]
        home = str(row.get("home_fifa_code") or row.get("home_team") or "").strip().upper()
        away = str(row.get("away_fifa_code") or row.get("away_team") or "").strip().upper()
    if not (date and home and away):
        return ""
    return f"{date}|{home}|{away}"


def _published_row_rank(row: dict[str, Any]) -> tuple[int, str, str]:
    status_rank = {"future": 0, "locked": 1, "final": 2}.get(str(row.get("status") or ""), 0)
    actual_rank = 1 if row.get("actual_score") else 0
    return (
        status_rank + actual_rank,
        str(row.get("published_at_utc") or row.get("first_published_at_utc") or ""),
        str(row.get("event_date") or ""),
    )


def _active_fixture_keys(storage) -> set[str]:
    try:
        return {fixture.key for fixture in load_tournament_state(storage).fixtures}
    except Exception:
        return set()


def _mapping(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _strip_record(row: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in row.items() if key != "_record"}
