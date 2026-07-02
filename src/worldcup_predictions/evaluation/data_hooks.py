"""Versioned one-shot runtime data updates."""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any

from worldcup_predictions.core.datasets import DATA_UPDATE_HOOKS
from worldcup_predictions.storage.ledger import canonical_json, normalize_datetime, utc_now


HOOK_V1 = "hook_v1_normalize_fifa_slot_codes"
HOOK_V2 = "hook_v2_remove_duplicate_side_fixtures"
LEGACY_SLOT_REPLACEMENTS = {
    "L101": "RU101",
    "L102": "RU102",
}


def run_data_update_hooks(storage: Any, *, run_id: str | None = None) -> list[dict[str, Any]]:
    """Run pending runtime data hooks and record successful hook ids."""

    applied = {
        str(row.get("hook_id"))
        for row in storage.read_records(DATA_UPDATE_HOOKS, latest_only=True)
        if row.get("status") == "success" and row.get("hook_id")
    }
    results: list[dict[str, Any]] = []
    for hook_id, hook_func in (
        (HOOK_V1, normalize_legacy_fifa_slot_codes),
        (HOOK_V2, remove_duplicate_side_fixture_rows),
    ):
        if hook_id in applied:
            results.append({"hook_id": hook_id, "status": "skipped", "reason": "already_applied"})
            continue
        rows_changed = hook_func(storage)
        result = {
            "record_key": hook_id,
            "hook_id": hook_id,
            "status": "success",
            "rows_changed": rows_changed,
            "ran_at_utc": normalize_datetime(utc_now()),
        }
        storage.write_records(DATA_UPDATE_HOOKS, [result], source="data_update_hooks", run_id=run_id)
        results.append(result)
    return results


def normalize_legacy_fifa_slot_codes(storage: Any) -> int:
    """Replace old internal loser aliases with FIFA runner-up placeholder ids."""

    connect = getattr(storage, "_connect", None)
    export_dataset = getattr(storage, "_export_dataset", None)
    if connect is None or export_dataset is None:
        raise RuntimeError("data update hooks require DuckDBStorage")

    con = connect()
    try:
        rows = con.execute(
            """
            SELECT dataset, record_key, source, fixture_key, run_id, observed_at_utc, payload_json, metadata_json
            FROM structured_records
            ORDER BY observed_at_utc, source, record_key
            """
        ).fetchall()
        prepared = []
        affected_datasets: set[str] = set()
        rows_changed = 0
        for dataset, record_key, source, fixture_key, row_run_id, observed_at, payload_json, metadata_json in rows:
            transformed = (
                _replace_legacy_slot_text(dataset),
                _replace_legacy_slot_text(record_key),
                source,
                _replace_legacy_slot_text(fixture_key),
                row_run_id,
                observed_at,
                canonical_json(_replace_legacy_slots(_loads_json(payload_json))),
                canonical_json(_replace_legacy_slots(_loads_json(metadata_json))),
            )
            original = (dataset, record_key, source, fixture_key, row_run_id, observed_at, payload_json, metadata_json)
            if transformed != original:
                rows_changed += 1
                affected_datasets.add(transformed[0])
            prepared.append(transformed)

        if not rows_changed:
            return 0

        con.execute("BEGIN TRANSACTION")
        try:
            con.execute("DELETE FROM structured_records")
            con.executemany("INSERT INTO structured_records VALUES (?, ?, ?, ?, ?, ?, ?, ?)", prepared)
            for dataset in sorted(affected_datasets):
                export_dataset(con, dataset)
            con.execute("COMMIT")
        except Exception:
            con.execute("ROLLBACK")
            raise
        return rows_changed
    finally:
        con.close()


def remove_duplicate_side_fixture_rows(storage: Any) -> int:
    """Remove impossible persisted rows such as BEL-BEL."""

    connect = getattr(storage, "_connect", None)
    export_dataset = getattr(storage, "_export_dataset", None)
    if connect is None or export_dataset is None:
        raise RuntimeError("data update hooks require DuckDBStorage")

    con = connect()
    try:
        rows = con.execute(
            """
            SELECT dataset, record_key, source, fixture_key, run_id, observed_at_utc, payload_json, metadata_json
            FROM structured_records
            ORDER BY observed_at_utc, source, record_key
            """
        ).fetchall()
        kept = []
        affected_datasets: set[str] = set()
        removed = 0
        for row in rows:
            dataset, record_key, _source, fixture_key, _run_id, _observed_at, payload_json, _metadata_json = row
            payload = _loads_json(payload_json)
            fixture_candidates = (
                fixture_key,
                payload.get("fixture_key") if isinstance(payload, dict) else "",
                record_key,
            )
            if dataset != DATA_UPDATE_HOOKS and any(_has_duplicate_sides(value) for value in fixture_candidates):
                removed += 1
                affected_datasets.add(dataset)
                continue
            kept.append(row)

        if not removed:
            return 0

        con.execute("BEGIN TRANSACTION")
        try:
            con.execute("DELETE FROM structured_records")
            con.executemany("INSERT INTO structured_records VALUES (?, ?, ?, ?, ?, ?, ?, ?)", kept)
            for dataset in sorted(affected_datasets):
                export_dataset(con, dataset)
            con.execute("COMMIT")
        except Exception:
            con.execute("ROLLBACK")
            raise
        return removed
    finally:
        con.close()


def _replace_legacy_slots(value: Any) -> Any:
    if isinstance(value, str):
        return _replace_legacy_slot_text(value)
    if isinstance(value, list):
        return [_replace_legacy_slots(item) for item in value]
    if isinstance(value, Mapping):
        return {str(key): _replace_legacy_slots(item) for key, item in value.items()}
    return value


def _replace_legacy_slot_text(value: object) -> object:
    if value is None:
        return None
    if not isinstance(value, str):
        return value
    normalized = value
    for old, new in LEGACY_SLOT_REPLACEMENTS.items():
        normalized = normalized.replace(old, new)
    return normalized


def _loads_json(value: str | None) -> Any:
    if not value:
        return {}
    return json.loads(value)


def _has_duplicate_sides(value: object) -> bool:
    parts = str(value or "").split("|")
    if len(parts) != 3:
        return False
    home = parts[1].strip().upper()
    away = parts[2].strip().upper()
    return bool(home and away and home == away)
