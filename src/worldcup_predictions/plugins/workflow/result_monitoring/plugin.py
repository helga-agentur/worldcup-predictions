"""Persist auditable rows when final-score data changes."""

from __future__ import annotations

from typing import Any

from worldcup_predictions.core.contracts import Artifact, Diagnostic
from worldcup_predictions.core.datasets import RESULT_UPDATE_AUDIT
from worldcup_predictions.core.events import EventName, event_value
from worldcup_predictions.core.metadata import PluginKind, PluginMetadata
from worldcup_predictions.core.plugin import BasePlugin, PluginResult
from worldcup_predictions.storage.ledger import stable_hash, utc_now


class ResultMonitoringPlugin(BasePlugin):
    """Store newly discovered or changed result rows for later workflow audits."""

    id = "result_monitoring"
    version = "0.1.0"
    priority = 145
    subscribed_events = (EventName.RESULTS_UPDATED.value,)
    metadata = PluginMetadata(
        plugin_id=id,
        kind=PluginKind.OUTPUT,
        description="Persist result-update audit rows whenever final-score rows are newly discovered or changed.",
        datasets_written=(RESULT_UPDATE_AUDIT,),
        confidence_policy="Monitoring rows do not affect predictions; they explain when downstream audits and calibration should react.",
    )

    def handle(self, event, context, payload):
        if context.storage is None:
            return PluginResult(
                plugin_id=self.id,
                event=event_value(event),
                diagnostics=[Diagnostic("warning", "Structured storage is unavailable; result monitoring was skipped.", self.id)],
            )
        rows = result_update_rows_from_payload(payload, run_id=context.run_id)
        count = context.storage.write_records(RESULT_UPDATE_AUDIT, rows, source=self.id, run_id=context.run_id)
        diagnostics = []
        if not rows:
            diagnostics.append(Diagnostic("info", "RESULTS_UPDATED carried no result changes.", self.id))
        return PluginResult(
            plugin_id=self.id,
            event=event_value(event),
            artifacts=[Artifact(RESULT_UPDATE_AUDIT, "structured_dataset", self.id, data={"rows": count})],
            diagnostics=diagnostics,
            metadata={"rows": count, "source_event": payload.get("source_event", "")},
        )


def result_update_rows_from_payload(payload, *, run_id: str) -> list[dict[str, Any]]:
    """Build one audit row per newly seen or changed source result."""

    detected_at = utc_now().isoformat()
    rows = []
    for entry in list(payload.get("new_results", []) or []) + list(payload.get("changed_results", []) or []):
        current = dict(entry.get("current") or {})
        previous = dict(entry.get("previous") or {})
        update_type = str(entry.get("update_type") or "unknown")
        fixture_key = str(current.get("fixture_key") or "")
        source = _result_source(current)
        current_score = _score_text(current)
        previous_score = _score_text(previous) if previous else ""
        rows.append(
            {
                "record_key": stable_hash(
                    {
                        "run_id": run_id,
                        "fixture_key": fixture_key,
                        "source": source,
                        "update_type": update_type,
                        "current_score": current_score,
                        "previous_score": previous_score,
                    }
                ),
                "run_id": run_id,
                "detected_at_utc": detected_at,
                "source_event": payload.get("source_event", ""),
                "update_type": update_type,
                "fixture_key": fixture_key,
                "event_date": current.get("event_date"),
                "home_team": current.get("home_team"),
                "away_team": current.get("away_team"),
                "home_fifa_code": current.get("home_fifa_code"),
                "away_fifa_code": current.get("away_fifa_code"),
                "source": source,
                "previous_score": previous_score,
                "current_score": current_score,
                "previous_status": previous.get("status"),
                "current_status": current.get("status"),
                "previous_record": _compact_result(previous),
                "current_record": _compact_result(current),
                "reason": _reason(update_type, previous_score, current_score, source),
            }
        )
    return rows


def _score_text(row: dict[str, Any]) -> str:
    if row.get("home_score") is None or row.get("away_score") is None:
        return ""
    return f"{row.get('home_score')}:{row.get('away_score')}"


def _result_source(row: dict[str, Any]) -> str:
    return str(row.get("source") or row.get("_record", {}).get("source") or "unknown")


def _compact_result(row: dict[str, Any]) -> dict[str, Any]:
    if not row:
        return {}
    return {
        "record_key": row.get("record_key") or row.get("_record", {}).get("record_key"),
        "fixture_key": row.get("fixture_key"),
        "event_date": row.get("event_date"),
        "home_team": row.get("home_team"),
        "away_team": row.get("away_team"),
        "home_fifa_code": row.get("home_fifa_code"),
        "away_fifa_code": row.get("away_fifa_code"),
        "home_score": row.get("home_score"),
        "away_score": row.get("away_score"),
        "status": row.get("status"),
        "source": _result_source(row),
        "observed_at_utc": row.get("_record", {}).get("observed_at_utc"),
    }


def _reason(update_type: str, previous_score: str, current_score: str, source: str) -> str:
    if update_type == "new":
        return f"New final-score row discovered from {source}."
    if update_type == "changed":
        return f"Final-score row changed from {previous_score or 'unknown'} to {current_score or 'unknown'} from {source}."
    return f"Final-score update observed from {source}."
