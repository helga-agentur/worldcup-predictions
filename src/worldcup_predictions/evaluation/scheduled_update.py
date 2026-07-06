"""Cron-friendly prediction run summaries."""

from __future__ import annotations

from collections import Counter
from typing import Any

from worldcup_predictions.core.datasets import PREDICTION_RUN_SUMMARIES
from worldcup_predictions.core.workflow import WorkflowRun
from worldcup_predictions.storage.ledger import utc_now


def build_prediction_run_summary_row(
    run: WorkflowRun,
    *,
    snapshot_id: str,
    snapshot_rows: int,
    maintenance: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build a compact manifest for one scheduled prediction run."""

    signal_names = Counter()
    signal_sources = Counter()
    artifact_names = Counter()
    diagnostic_levels = Counter()
    plugin_events = []
    for result in run.context.event_results:
        for signal in result.signals:
            signal_names[signal.name] += 1
            signal_sources[signal.source] += 1
        for artifact in result.artifacts:
            artifact_names[artifact.name] += 1
        for diagnostic in result.diagnostics:
            diagnostic_levels[diagnostic.level] += 1
        plugin_events.append(
            {
                "plugin_id": result.plugin_id,
                "event": result.event,
                "signals": len(result.signals),
                "artifacts": len(result.artifacts),
                "predictions": len(result.predictions),
                "optimized_tips": len(result.optimized_tips),
                "diagnostics": len(result.diagnostics),
                "metadata": result.metadata,
            }
        )

    outcome_counts = Counter()
    confidence_total = 0.0
    expected_total_goals = 0.0
    for prediction in run.predictions:
        most_likely = prediction.most_likely
        if most_likely.home > most_likely.away:
            outcome_counts["home"] += 1
        elif most_likely.home < most_likely.away:
            outcome_counts["away"] += 1
        else:
            outcome_counts["draw"] += 1
        confidence_total += float(prediction.confidence_percent or 0.0)
        expected_total_goals += float((prediction.expected_home_goals or 0.0) + (prediction.expected_away_goals or 0.0))
    prediction_count = max(1, len(run.predictions))
    prediction_summary = {
        "most_likely_outcomes": dict(sorted(outcome_counts.items())),
        "average_confidence": confidence_total / prediction_count,
        "average_expected_total_goals": expected_total_goals / prediction_count,
    }

    return {
        "record_key": snapshot_id,
        "run_id": run.context.run_id,
        "snapshot_id": snapshot_id,
        "started_at_utc": run.context.started_at.isoformat(),
        "finished_at_utc": utc_now().isoformat(),
        "prediction_count": len(run.predictions),
        "optimized_tip_count": len(run.optimized_tips),
        "snapshot_rows": snapshot_rows,
        "prediction_summary": prediction_summary,
        "diagnostic_levels": dict(sorted(diagnostic_levels.items())),
        "signal_names": dict(sorted(signal_names.items())),
        "signal_sources": dict(sorted(signal_sources.items())),
        "artifact_names": dict(sorted(artifact_names.items())),
        "plugin_events": plugin_events,
        "maintenance": dict(maintenance or {}),
    }


def write_prediction_run_summary(
    storage,
    run: WorkflowRun,
    *,
    snapshot_id: str,
    snapshot_rows: int,
    maintenance: dict[str, Any] | None = None,
) -> int:
    row = build_prediction_run_summary_row(
        run,
        snapshot_id=snapshot_id,
        snapshot_rows=snapshot_rows,
        maintenance=maintenance,
    )
    return storage.write_records(PREDICTION_RUN_SUMMARIES, [row], source="scheduled_update", run_id=run.context.run_id)


def summarize_source_ledger_rows(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Build a compact per-run source health summary from source-ledger rows."""

    status_counts = Counter()
    source_counts = Counter()
    source_status_counts = Counter()
    quota_cost_by_status = Counter()
    quota_cost_by_source_status = Counter()
    item_count_by_source = Counter()
    cache_skipped_by_source = Counter()
    failures = []
    zero_row_successes = []
    skipped = []
    rate_limited = []
    not_modified = []
    for row in rows:
        source = str(row.get("source") or "unknown")
        status = str(row.get("status") or "unknown")
        quota_cost = int(row.get("quota_cost") or 0)
        metadata = row.get("metadata") if isinstance(row.get("metadata"), dict) else {}
        item_count_by_source[source] += _metadata_item_count(metadata)
        status_counts[status] += 1
        source_counts[source] += 1
        source_status_counts[f"{source}:{status}"] += 1
        quota_cost_by_status[status] += quota_cost
        quota_cost_by_source_status[f"{source}:{status}"] += quota_cost
        diagnostic_row = {
            "source": source,
            "endpoint": row.get("endpoint"),
            "purpose": row.get("purpose"),
            "fixture_key": row.get("fixture_key"),
            "quota_scope": row.get("quota_scope"),
            "status": status,
            "message": row.get("message"),
            "fetched_at_utc": row.get("fetched_at_utc"),
            "quota_cost": quota_cost,
            "quota_remaining": row.get("quota_remaining"),
            "next_safe_fetch_at": row.get("next_safe_fetch_at"),
        }
        decision_reason = str(metadata.get("decision_reason") or metadata.get("decision_metadata", {}).get("reason") or "")
        if status == "skipped":
            skipped.append(diagnostic_row)
            if decision_reason in {"fresh_enough", "next_safe_fetch_at_not_reached"}:
                cache_skipped_by_source[source] += 1
        elif status == "not_modified":
            not_modified.append(diagnostic_row)
            cache_skipped_by_source[source] += 1
        elif status == "rate_limited":
            rate_limited.append(diagnostic_row)
            failures.append(diagnostic_row)
        elif status != "success":
            failures.append(diagnostic_row)
        elif metadata.get("rows") == 0:
            zero_row_successes.append(
                {
                    "source": source,
                    "endpoint": row.get("endpoint"),
                    "purpose": row.get("purpose"),
                    "fixture_key": row.get("fixture_key"),
                    "fetched_at_utc": row.get("fetched_at_utc"),
                }
            )
    return {
        "requests": len(rows),
        "calls_made": sum(count for status, count in status_counts.items() if status != "skipped"),
        "calls_avoided": status_counts.get("skipped", 0),
        "cache_hits": status_counts.get("not_modified", 0),
        "cache_skips": sum(cache_skipped_by_source.values()),
        "quota_cost_made": sum(cost for status, cost in quota_cost_by_status.items() if status != "skipped"),
        "quota_cost_avoided": quota_cost_by_status.get("skipped", 0),
        "status_counts": dict(sorted(status_counts.items())),
        "source_counts": dict(sorted(source_counts.items())),
        "source_status_counts": dict(sorted(source_status_counts.items())),
        "items_by_source": dict(sorted(item_count_by_source.items())),
        "cache_skipped_by_source": dict(sorted(cache_skipped_by_source.items())),
        "quota_cost_by_status": dict(sorted(quota_cost_by_status.items())),
        "quota_cost_by_source_status": dict(sorted(quota_cost_by_source_status.items())),
        "failures": failures,
        "skipped": skipped,
        "not_modified": not_modified,
        "rate_limited": rate_limited,
        "zero_row_successes": zero_row_successes,
    }


def _metadata_item_count(metadata: dict[str, Any]) -> int:
    count = 0
    for key in ("rows", "rows_written", "fixtures", "results", "details", "matches", "events", "articles", "signals", "teams", "players", "sports"):
        value = metadata.get(key)
        if isinstance(value, bool):
            continue
        if isinstance(value, int | float):
            count += int(value)
    return count
