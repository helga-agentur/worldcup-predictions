"""Automatic match notes from stored public evidence."""

from __future__ import annotations

from collections import defaultdict
from typing import Any

from worldcup_predictions.core.constants import SIGNAL_WEIGHT_AUTOMATIC_MATCH_NOTE, SOURCE_AUTOMATIC_MATCH_NOTES
from worldcup_predictions.core.contracts import Artifact, Diagnostic, Signal
from worldcup_predictions.core.datasets import AUTOMATIC_MATCH_NOTES, EXTRACTION_DIAGNOSTICS, LINEUP_AVAILABILITY, PUBLIC_MATCH_ANALYSIS
from worldcup_predictions.core.events import EventName, event_value
from worldcup_predictions.core.extraction import extraction_diagnostic_row
from worldcup_predictions.core.metadata import PluginKind, PluginMetadata
from worldcup_predictions.core.plugin import BasePlugin, PluginResult
from worldcup_predictions.core.signals import TEAM_EXPECTED_GOALS_FACTOR, TOTAL_GOALS_FACTOR
from worldcup_predictions.storage.ledger import stable_hash


class AutomaticMatchNotesPlugin(BasePlugin):
    """Build conservative, auditable notes from already-fetched public rows."""

    id = "automatic_match_notes"
    version = "0.1.0"
    priority = 310
    subscribed_events = (EventName.FEATURE_SIGNALS_REQUESTED.value,)
    metadata = PluginMetadata(
        plugin_id=id,
        kind=PluginKind.SIGNAL,
        description="Aggregate public-analysis and availability rows into capped automatic match-note signals.",
        datasets_read=(PUBLIC_MATCH_ANALYSIS, LINEUP_AVAILABILITY),
        datasets_written=(AUTOMATIC_MATCH_NOTES, EXTRACTION_DIAGNOSTICS),
        signals_emitted=(TOTAL_GOALS_FACTOR, TEAM_EXPECTED_GOALS_FACTOR),
        confidence_policy="Signals require at least two reliable supporting rows and are capped below direct source signals.",
    )

    def handle(self, event, context, payload):
        if context.storage is None:
            return PluginResult(
                plugin_id=self.id,
                event=event_value(event),
                diagnostics=[Diagnostic("warning", "Structured storage is unavailable; automatic match notes were skipped.", self.id)],
            )
        analysis_rows = context.storage.read_records(PUBLIC_MATCH_ANALYSIS, latest_only=True)
        availability_rows = context.storage.read_records(LINEUP_AVAILABILITY, latest_only=True)
        rows, extraction_diagnostics = automatic_note_rows_with_diagnostics(analysis_rows, availability_rows)
        count = context.storage.write_records(AUTOMATIC_MATCH_NOTES, rows, source=self.id, run_id=context.run_id)
        diagnostic_count = context.storage.write_records(EXTRACTION_DIAGNOSTICS, extraction_diagnostics, source=self.id, run_id=context.run_id)
        signals = automatic_note_signals(rows)
        diagnostics = []
        if not rows:
            diagnostics.append(
                Diagnostic(
                    level="info",
                    message="No automatic public match notes were generated from stored evidence.",
                    source=self.id,
                )
            )
        return PluginResult(
            plugin_id=self.id,
            event=event_value(event),
            signals=signals,
            artifacts=[
                Artifact(AUTOMATIC_MATCH_NOTES, "structured_dataset", self.id, data={"rows": count, "signals": len(signals)}),
                Artifact(EXTRACTION_DIAGNOSTICS, "structured_dataset", self.id, data={"rows": diagnostic_count}),
            ],
            diagnostics=diagnostics,
            metadata={"rows": count, "signals": len(signals), "extraction_diagnostics": diagnostic_count},
        )


def automatic_note_rows(analysis_rows: list[dict[str, Any]], availability_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows, _diagnostics = automatic_note_rows_with_diagnostics(analysis_rows, availability_rows)
    return rows


def automatic_note_rows_with_diagnostics(
    analysis_rows: list[dict[str, Any]],
    availability_rows: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    evidence: dict[str, list[dict[str, Any]]] = defaultdict(list)
    diagnostics: list[dict[str, Any]] = []
    for row in analysis_rows:
        fixture_key = str(row.get("fixture_key") or "")
        if row.get("phase") != "pregame":
            diagnostics.append(_source_row_diagnostic(row, status="rejected", reason="not_pregame_analysis"))
            continue
        if float(row.get("reliability") or 0.0) < 0.70:
            diagnostics.append(_source_row_diagnostic(row, status="rejected", reason="low_reliability_analysis"))
            continue
        metadata = row.get("metadata") or {}
        note = metadata.get("note") or {}
        categories = note.get("categories") or []
        if not fixture_key:
            diagnostics.append(_source_row_diagnostic(row, status="rejected", reason="missing_fixture_key"))
            continue
        if not categories:
            diagnostics.append(_source_row_diagnostic(row, status="rejected", reason="no_note_categories"))
            continue
        if categories:
            evidence[fixture_key].append(
                {
                    "kind": "analysis_note",
                    "categories": categories,
                    "source_url": row.get("source_url"),
                    "reliability": row.get("reliability"),
                    "total_goals_factor": _factor_from_categories(categories),
                }
            )
            diagnostics.append(_source_row_diagnostic(row, status="accepted", reason="accepted_analysis_note", metadata={"categories": categories}))
    for row in availability_rows:
        fixture_key = str(row.get("fixture_key") or "")
        if not fixture_key:
            diagnostics.append(_source_row_diagnostic(row, status="rejected", reason="missing_fixture_key"))
            continue
        if float(row.get("reliability") or 0.0) < 0.70:
            diagnostics.append(_source_row_diagnostic(row, status="rejected", reason="low_reliability_availability"))
            continue
        evidence[fixture_key].append(
            {
                "kind": "availability_note",
                "categories": [row.get("signal_type")],
                "side": row.get("affected_side"),
                "affected_team": row.get("affected_team"),
                "source_url": row.get("source_url"),
                "reliability": row.get("reliability"),
                "team_expected_goals_factor": row.get("expected_goals_factor"),
            }
        )
        diagnostics.append(
            _source_row_diagnostic(
                row,
                status="accepted",
                reason="accepted_availability_note",
                metadata={"signal_type": row.get("signal_type"), "side": row.get("affected_side")},
            )
        )
    rows = []
    for fixture_key, fixture_evidence in sorted(evidence.items()):
        if not fixture_key:
            continue
        total_goal_factors = [float(item["total_goals_factor"]) for item in fixture_evidence if item.get("total_goals_factor")]
        side_factors: dict[str, list[float]] = defaultdict(list)
        for item in fixture_evidence:
            side = str(item.get("side") or "")
            if side in {"home", "away"} and item.get("team_expected_goals_factor") is not None:
                side_factors[side].append(float(item["team_expected_goals_factor"]))
        if len(total_goal_factors) < 2 and all(len(values) < 2 for values in side_factors.values()):
            diagnostics.append(
                extraction_diagnostic_row(
                    source=SOURCE_AUTOMATIC_MATCH_NOTES,
                    extractor="automatic_match_notes_v1",
                    status="rejected",
                    reason="insufficient_support",
                    fixture_key=fixture_key,
                    metadata={
                        "evidence_count": len(fixture_evidence),
                        "total_goal_factor_count": len(total_goal_factors),
                        "side_factor_counts": {side: len(values) for side, values in side_factors.items()},
                    },
                )
            )
            continue
        rows.append(
            {
                "record_key": stable_hash({"fixture_key": fixture_key, "evidence": fixture_evidence}),
                "fixture_key": fixture_key,
                "evidence_count": len(fixture_evidence),
                "total_goals_factor": sum(total_goal_factors) / len(total_goal_factors) if len(total_goal_factors) >= 2 else None,
                "side_factors": {
                    side: sum(values) / len(values)
                    for side, values in side_factors.items()
                    if len(values) >= 2
                },
                "source_urls": [item.get("source_url") for item in fixture_evidence if item.get("source_url")][:8],
                "categories": sorted({str(category) for item in fixture_evidence for category in (item.get("categories") or []) if category}),
                "metadata": {"evidence": fixture_evidence},
            }
        )
        diagnostics.append(
            extraction_diagnostic_row(
                source=SOURCE_AUTOMATIC_MATCH_NOTES,
                extractor="automatic_match_notes_v1",
                status="accepted",
                reason="created_match_note",
                fixture_key=fixture_key,
                metadata={"evidence_count": len(fixture_evidence), "categories": rows[-1]["categories"]},
            )
        )
    return rows, diagnostics


def automatic_note_signals(rows: list[dict[str, Any]]) -> list[Signal]:
    signals: list[Signal] = []
    for row in rows:
        fixture_key = str(row.get("fixture_key") or "")
        evidence_count = int(row.get("evidence_count") or 0)
        confidence = min(0.75, 0.30 + 0.10 * evidence_count)
        if row.get("total_goals_factor") is not None:
            signals.append(
                Signal(
                    name=TOTAL_GOALS_FACTOR,
                    source=SOURCE_AUTOMATIC_MATCH_NOTES,
                    fixture_key=fixture_key,
                    value=float(row["total_goals_factor"]),
                    weight=SIGNAL_WEIGHT_AUTOMATIC_MATCH_NOTE,
                    confidence=confidence,
                    rationale=f"Automatic public-note consensus from {evidence_count} evidence row(s).",
                    metadata={"categories": row.get("categories"), "source_urls": row.get("source_urls")},
                )
            )
        for side, factor in dict(row.get("side_factors") or {}).items():
            signals.append(
                Signal(
                    name=TEAM_EXPECTED_GOALS_FACTOR,
                    source=SOURCE_AUTOMATIC_MATCH_NOTES,
                    fixture_key=fixture_key,
                    value=float(factor),
                    weight=SIGNAL_WEIGHT_AUTOMATIC_MATCH_NOTE,
                    confidence=confidence,
                    rationale=f"Automatic availability-note consensus for {side}.",
                    metadata={"side": side, "categories": row.get("categories"), "source_urls": row.get("source_urls")},
                )
            )
    return signals


def _factor_from_categories(categories: list[str]) -> float | None:
    lowered = {str(category).casefold() for category in categories}
    if "weather_context" in lowered:
        return 0.95
    if "injury_context" in lowered or "lineup_context" in lowered:
        return 0.98
    if "finishing_context" in lowered or "set_piece_context" in lowered:
        return 1.02
    return None


def _source_row_diagnostic(
    row: dict[str, Any],
    *,
    status: str,
    reason: str,
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return extraction_diagnostic_row(
        source=SOURCE_AUTOMATIC_MATCH_NOTES,
        extractor="automatic_match_notes_v1",
        status=status,
        reason=reason,
        fixture_key=row.get("fixture_key"),
        phase=row.get("phase"),
        source_name=row.get("source_name"),
        source_url=row.get("source_url"),
        title=row.get("title"),
        metadata={
            "reliability": row.get("reliability"),
            "signal_type": row.get("signal_type"),
            **dict(metadata or {}),
        },
    )
