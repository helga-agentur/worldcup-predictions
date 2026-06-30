"""One-file prediction export helpers."""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path
from typing import Any

from worldcup_predictions.core.datasets import (
    EXTRACTION_DIAGNOSTICS,
    OPTIMIZED_TIPS,
    PLUGIN_RUN_DIAGNOSTICS,
    PREDICTION_DEBUG_REPORT,
    PREDICTION_EXPORTS,
    PREDICTION_LEDGER,
    PUBLISHED_PREDICTION_LEDGER,
    PREDICTION_RUN_SUMMARIES,
    PREDICTION_SIGNAL_IMPACTS,
    PREDICTIONS,
    SOURCE_DIAGNOSTICS,
)
from worldcup_predictions.storage.ledger import canonical_json, stable_hash, utc_now


def build_prediction_export_payload(storage, *, export_id: str) -> dict[str, Any]:
    """Join prediction/debug artifacts into one comparison-friendly payload."""

    predictions = storage.read_records(PREDICTIONS, latest_only=True)
    optimized_tips = storage.read_records(OPTIMIZED_TIPS, latest_only=True)
    debug_rows = storage.read_records(PREDICTION_DEBUG_REPORT, latest_only=True)
    signal_impacts = storage.read_records(PREDICTION_SIGNAL_IMPACTS, latest_only=True)
    source_diagnostics = storage.read_records(SOURCE_DIAGNOSTICS, latest_only=True)
    extraction_diagnostics = storage.read_records(EXTRACTION_DIAGNOSTICS, latest_only=True)
    plugin_diagnostics = storage.read_records(PLUGIN_RUN_DIAGNOSTICS, latest_only=True)
    run_summaries = storage.read_records(PREDICTION_RUN_SUMMARIES, latest_only=True)
    ledger_rows = storage.read_records(PREDICTION_LEDGER, latest_only=True)
    published_ledger_rows = storage.read_records(PUBLISHED_PREDICTION_LEDGER, latest_only=True)

    tips_by_fixture = _group_by(optimized_tips, "fixture_key")
    debug_by_fixture = {str(row.get("fixture_key")): _strip_record(row) for row in debug_rows}
    impacts_by_fixture = _group_by(signal_impacts, "fixture_key")
    source_diagnostics_by_fixture = _group_by(source_diagnostics, "fixture_key")
    extraction_diagnostics_by_fixture = _group_by(extraction_diagnostics, "fixture_key")

    matches = []
    for row in sorted(predictions, key=lambda item: str(item.get("event_date") or "")):
        fixture_key = str(row.get("fixture_key") or "")
        matches.append(
            {
                "fixture_key": fixture_key,
                "event_date": row.get("event_date"),
                "home_team": row.get("home_team"),
                "away_team": row.get("away_team"),
                "stage": row.get("stage"),
                "group": row.get("group"),
                "matchday": row.get("matchday"),
                "most_likely": f"{row.get('most_likely_home')}:{row.get('most_likely_away')}",
                "expected_goals": {
                    "home": row.get("expected_home_goals"),
                    "away": row.get("expected_away_goals"),
                },
                "hda": {
                    "home": row.get("prob_home"),
                    "draw": row.get("prob_draw"),
                    "away": row.get("prob_away"),
                },
                "confidence": {
                    "label": row.get("confidence_label"),
                    "percent": row.get("confidence_percent"),
                },
                "score_matrix": row.get("score_matrix") or [],
                "optimized_tips": [_strip_record(item) for item in tips_by_fixture.get(fixture_key, [])],
                "debug": debug_by_fixture.get(fixture_key, {}),
                "signal_impacts": [_strip_record(item) for item in impacts_by_fixture.get(fixture_key, [])],
                "source_diagnostics": [_strip_record(item) for item in source_diagnostics_by_fixture.get(fixture_key, [])],
                "extraction_diagnostics": [_strip_record(item) for item in extraction_diagnostics_by_fixture.get(fixture_key, [])],
                "metadata": row.get("metadata") or {},
            }
        )

    diagnostic_counts = Counter(str(row.get("level") or row.get("severity") or "unknown") for row in source_diagnostics)
    extraction_counts = Counter(str(row.get("reason") or "unknown") for row in extraction_diagnostics if row.get("status") == "rejected")
    return {
        "export_id": export_id,
        "generated_at_utc": utc_now().isoformat(),
        "summary": {
            "matches": len(matches),
            "optimized_tips": len(optimized_tips),
            "source_diagnostics": len(source_diagnostics),
            "extraction_diagnostics": len(extraction_diagnostics),
            "source_diagnostic_levels": dict(sorted(diagnostic_counts.items())),
            "extraction_rejection_reasons": dict(sorted(extraction_counts.items())),
            "prediction_ledger_rows": len(ledger_rows),
            "past_prediction_ledger_rows": sum(1 for row in ledger_rows if row.get("status") == "past"),
            "future_prediction_ledger_rows": sum(1 for row in ledger_rows if row.get("status") == "future"),
            "published_prediction_ledger_rows": len(published_ledger_rows),
            "published_future_rows": sum(1 for row in published_ledger_rows if row.get("status") == "future"),
            "published_locked_rows": sum(1 for row in published_ledger_rows if row.get("status") == "locked"),
            "published_final_rows": sum(1 for row in published_ledger_rows if row.get("status") == "final"),
            "latest_run_summaries": [_strip_record(row) for row in run_summaries[-5:]],
        },
        "matches": matches,
        "prediction_ledger": [_strip_record(row) for row in sorted(ledger_rows, key=lambda item: str(item.get("event_date") or ""))],
        "published_prediction_ledger": [
            _strip_record(row)
            for row in sorted(published_ledger_rows, key=lambda item: str(item.get("event_date") or ""))
        ],
        "plugin_diagnostics": [_strip_record(row) for row in plugin_diagnostics],
    }


def write_prediction_export(storage, output_path: Path, *, export_id: str, run_id: str | None = None) -> dict[str, Any]:
    """Write a one-file JSON export and persist its manifest."""

    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    payload = build_prediction_export_payload(storage, export_id=export_id)
    output_path.write_text(json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False, default=str) + "\n", encoding="utf-8")
    manifest = {
        "record_key": stable_hash({"export_id": export_id, "path": str(output_path)}),
        "export_id": export_id,
        "path": str(output_path),
        "generated_at_utc": payload["generated_at_utc"],
        "prediction_count": payload["summary"]["matches"],
        "optimized_tip_count": payload["summary"]["optimized_tips"],
        "bytes": len(canonical_json(payload).encode("utf-8")),
    }
    storage.write_records(PREDICTION_EXPORTS, [manifest], source="prediction_export", run_id=run_id)
    return manifest


def _group_by(rows: list[dict[str, Any]], key: str) -> dict[str, list[dict[str, Any]]]:
    grouped: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        grouped.setdefault(str(row.get(key) or ""), []).append(row)
    return grouped


def _strip_record(row: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in row.items() if key != "_record"}
