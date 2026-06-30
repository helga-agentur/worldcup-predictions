"""Audit whether prediction-impacting workflow decisions are explainable."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any

from worldcup_predictions.core.datasets import (
    DIAGNOSTICS_COMPLETENESS_AUDIT,
    OPTIMIZED_TIPS,
    PLUGIN_RUN_DIAGNOSTICS,
    PREDICTION_DEBUG_REPORT,
    PREDICTION_SIGNAL_IMPACTS,
    PREDICTIONS,
)
from worldcup_predictions.core.metadata import PluginKind
from worldcup_predictions.core.plugin import Plugin, plugin_metadata
from worldcup_predictions.storage.ledger import stable_hash, utc_now


PREDICTION_REQUIRED_FIELDS = (
    "fixture_key",
    "event_date",
    "home_team",
    "away_team",
    "expected_home_goals",
    "expected_away_goals",
    "most_likely_home",
    "most_likely_away",
    "prob_home",
    "prob_draw",
    "prob_away",
    "confidence_label",
    "confidence_percent",
    "score_matrix",
    "metadata",
)

OPTIMIZED_TIP_REQUIRED_FIELDS = (
    "fixture_key",
    "provider",
    "expected_points",
    "optimizer_id",
    "selection_type",
    "rationale",
    "metadata",
)

DEBUG_REQUIRED_FIELDS = (
    "fixture_key",
    "most_likely_result",
    "expected_home_goals",
    "expected_away_goals",
    "prob_home",
    "prob_draw",
    "prob_away",
    "signal_count",
    "signal_sources",
    "missing_signal_sources",
    "signal_adjustments",
    "provider_tips",
)

SIGNAL_IMPACT_REQUIRED_FIELDS = (
    "fixture_key",
    "signal_name",
    "signal_source",
    "signal_value",
    "signal_weight",
    "signal_confidence",
    "rationale",
    "applied",
    "adjustments",
)

PLUGIN_DIAGNOSTIC_REQUIRED_FIELDS = (
    "run_id",
    "plugin_id",
    "event",
    "plugin_kind",
    "duration_ms",
    "payload",
    "output_counts",
    "fixture_keys",
    "artifact_names",
    "diagnostic_levels",
    "metadata",
)

DATASET_ROW_REQUIREMENTS = {
    PREDICTIONS: PREDICTION_REQUIRED_FIELDS,
    OPTIMIZED_TIPS: OPTIMIZED_TIP_REQUIRED_FIELDS,
    PREDICTION_DEBUG_REPORT: DEBUG_REQUIRED_FIELDS,
    PREDICTION_SIGNAL_IMPACTS: SIGNAL_IMPACT_REQUIRED_FIELDS,
    PLUGIN_RUN_DIAGNOSTICS: PLUGIN_DIAGNOSTIC_REQUIRED_FIELDS,
}

PREDICTION_IMPACTING_KINDS = {
    PluginKind.SOURCE,
    PluginKind.SIGNAL,
    PluginKind.MODEL,
    PluginKind.PROVIDER_OPTIMIZER,
    PluginKind.SIMULATOR,
    PluginKind.EVALUATOR,
}


def write_diagnostics_completeness_audit(
    storage,
    plugins: Iterable[Plugin],
    *,
    run_id: str | None = None,
) -> list[dict[str, Any]]:
    """Persist diagnostics-completeness findings for the current workflow state."""

    rows = build_diagnostics_completeness_rows(storage, plugins, run_id=run_id)
    writer = getattr(storage, "replace_records", storage.write_records)
    writer(
        DIAGNOSTICS_COMPLETENESS_AUDIT,
        rows,
        source="diagnostics_completeness",
        run_id=run_id,
    )
    return rows


def build_diagnostics_completeness_rows(
    storage,
    plugins: Iterable[Plugin],
    *,
    run_id: str | None = None,
) -> list[dict[str, Any]]:
    """Build non-blocking audit rows for plugin metadata and core diagnostic outputs."""

    audit_id = run_id or f"diagnostics_{utc_now().strftime('%Y%m%dT%H%M%SZ')}"
    rows: list[dict[str, Any]] = []
    rows.extend(_plugin_metadata_rows(audit_id, plugins))
    rows.extend(_plugin_run_presence_rows(audit_id, storage, plugins, run_id=run_id))
    rows.extend(_dataset_requirement_rows(audit_id, storage))
    return rows


def _plugin_metadata_rows(audit_id: str, plugins: Iterable[Plugin]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for plugin in sorted(plugins, key=lambda item: (getattr(item, "priority", 0), item.id)):
        metadata = plugin_metadata(plugin)
        missing: list[str] = []
        recommendations: list[str] = []
        if not metadata.description:
            missing.append("description")
        if not getattr(plugin, "subscribed_events", ()):
            missing.append("subscribed_events")
        if metadata.kind in {PluginKind.SIGNAL, PluginKind.MODEL, PluginKind.PROVIDER_OPTIMIZER} and not metadata.confidence_policy:
            missing.append("confidence_policy")
        if metadata.kind == PluginKind.SOURCE:
            if not metadata.datasets_written and not metadata.signals_emitted:
                missing.append("datasets_written_or_signals_emitted")
            if metadata.quota_policy.quota_limited and not metadata.quota_policy.ledger_required:
                missing.append("quota_policy.ledger_required")
            if metadata.env_vars and not metadata.quota_policy.quota_limited:
                missing.append("api_source_quota_policy")
            if metadata.env_vars and not metadata.quota_policy.ledger_required:
                missing.append("api_source_ledger_required")
            if metadata.env_vars and not metadata.quota_policy.description:
                missing.append("quota_policy.description")
        if metadata.kind in PREDICTION_IMPACTING_KINDS and not (
            metadata.datasets_read or metadata.datasets_written or metadata.signals_emitted
        ):
            recommendations.append("prediction-impacting plugin should declare read/write datasets or emitted signals")
        status = "ok" if not missing else "warning"
        rows.append(
            _row(
                audit_id,
                scope="plugin_metadata",
                subject=plugin.id,
                status=status,
                severity="warning" if missing else "info",
                reason="plugin metadata explains its role" if not missing else "plugin metadata is incomplete",
                missing_fields=missing,
                metadata={
                    "plugin_kind": metadata.kind.value,
                    "priority": getattr(plugin, "priority", None),
                    "events": list(getattr(plugin, "subscribed_events", ())),
                    "datasets_read": list(metadata.datasets_read),
                    "datasets_written": list(metadata.datasets_written),
                    "signals_emitted": list(metadata.signals_emitted),
                    "recommendations": recommendations,
                },
            )
        )
    return rows


def _plugin_run_presence_rows(
    audit_id: str,
    storage,
    plugins: Iterable[Plugin],
    *,
    run_id: str | None,
) -> list[dict[str, Any]]:
    rows = _read_dataset(storage, PLUGIN_RUN_DIAGNOSTICS)
    if run_id:
        rows = [row for row in rows if row.get("run_id") == run_id or row.get("_record", {}).get("run_id") == run_id]
    seen = {str(row.get("plugin_id") or "") for row in rows}
    observed_events = {str(row.get("event") or "") for row in rows}
    audit_rows: list[dict[str, Any]] = []
    for plugin in sorted(plugins, key=lambda item: item.id):
        subscribed_events = tuple(getattr(plugin, "subscribed_events", ()))
        if not subscribed_events:
            continue
        if plugin.id in seen:
            status = "ok"
            reason = "plugin emitted core run diagnostics"
            severity = "info"
            missing: list[str] = []
        elif observed_events.isdisjoint(set(subscribed_events)):
            status = "ok"
            reason = "no subscribed event was dispatched in this workflow"
            severity = "info"
            missing = []
        else:
            status = "warning"
            reason = "plugin did not run in this workflow or emitted no core diagnostics"
            severity = "warning"
            missing = ["plugin_run_diagnostics"]
        audit_rows.append(
            _row(
                audit_id,
                scope="plugin_run",
                subject=plugin.id,
                status=status,
                severity=severity,
                reason=reason,
                missing_fields=missing,
                metadata={
                    "run_id": run_id,
                    "events": list(subscribed_events),
                    "observed_events": sorted(observed_events),
                    "observed_plugin_diagnostic_rows": sum(1 for row in rows if row.get("plugin_id") == plugin.id),
                },
            )
        )
    return audit_rows


def _dataset_requirement_rows(audit_id: str, storage) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for dataset, required_fields in DATASET_ROW_REQUIREMENTS.items():
        records = _read_dataset(storage, dataset)
        if not records:
            rows.append(
                _row(
                    audit_id,
                    scope="dataset_presence",
                    subject=dataset,
                    status="warning",
                    severity="warning",
                    reason="dataset has no rows yet",
                    missing_fields=["rows"],
                    metadata={"required_fields": list(required_fields), "checked_rows": 0},
                )
            )
            continue
        missing_by_field: dict[str, int] = {}
        checked_rows = 0
        for record in records:
            checked_rows += 1
            missing = _missing_required_fields(record, required_fields)
            for field in missing:
                missing_by_field[field] = missing_by_field.get(field, 0) + 1
        if dataset == OPTIMIZED_TIPS:
            missing_tip_identity = sum(1 for record in records if not (record.get("tip") or record.get("selection")))
            if missing_tip_identity:
                missing_by_field["tip_or_selection"] = missing_tip_identity
        if missing_by_field:
            status = "warning"
            severity = "warning"
            reason = "some rows lack fields needed for later weight/model analysis"
        else:
            status = "ok"
            severity = "info"
            reason = "rows include required diagnostic fields"
        rows.append(
            _row(
                audit_id,
                scope="dataset_fields",
                subject=dataset,
                status=status,
                severity=severity,
                reason=reason,
                missing_fields=sorted(missing_by_field),
                metadata={
                    "checked_rows": checked_rows,
                    "missing_by_field": missing_by_field,
                    "required_fields": list(required_fields),
                },
            )
        )
    return rows


def _missing_required_fields(record: Mapping[str, Any], fields: tuple[str, ...]) -> list[str]:
    missing: list[str] = []
    for field in fields:
        value = record.get(field)
        if value is None or value == "":
            missing.append(field)
    return missing


def _read_dataset(storage, dataset: str) -> list[dict[str, Any]]:
    try:
        return storage.read_records(dataset, latest_only=True)
    except Exception:  # noqa: BLE001 - audits should expose gaps, not crash the workflow.
        return []


def _row(
    audit_id: str,
    *,
    scope: str,
    subject: str,
    status: str,
    severity: str,
    reason: str,
    missing_fields: list[str],
    metadata: dict[str, Any],
) -> dict[str, Any]:
    payload = {
        "audit_id": audit_id,
        "scope": scope,
        "subject": subject,
        "status": status,
        "severity": severity,
        "reason": reason,
        "missing_fields": missing_fields,
        "metadata": metadata,
    }
    return {
        "record_key": stable_hash({"scope": scope, "subject": subject}),
        **payload,
    }
