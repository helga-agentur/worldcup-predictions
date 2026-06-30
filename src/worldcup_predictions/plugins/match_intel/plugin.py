"""Persist prematch review-intelligence rows."""

from __future__ import annotations

from typing import Any

from worldcup_predictions.core.contracts import Artifact, Diagnostic
from worldcup_predictions.core.datasets import MATCH_INTEL
from worldcup_predictions.core.events import EventName, event_value
from worldcup_predictions.core.metadata import PluginKind, PluginMetadata
from worldcup_predictions.core.plugin import BasePlugin, PluginResult
from worldcup_predictions.core.payloads import DebugReportRequestedPayload
from worldcup_predictions.evaluation.match_intel import build_match_intel_rows


class MatchIntelPlugin(BasePlugin):
    """Build review-priority rows from the final prediction output."""

    id = "match_intel"
    version = "0.1.0"
    priority = 930
    subscribed_events = (EventName.DEBUG_REPORT_REQUESTED.value,)
    metadata = PluginMetadata(
        plugin_id=id,
        kind=PluginKind.OUTPUT,
        description="Persist prematch review-priority rows from predictions, provider tips, and active model signals.",
        datasets_written=(MATCH_INTEL,),
        confidence_policy="Output-only plugin; helps humans inspect fragile fixtures without changing probabilities.",
    )

    def handle(self, event, context: Any, payload: dict[str, Any] | DebugReportRequestedPayload) -> PluginResult:
        if context.storage is None:
            return PluginResult(
                plugin_id=self.id,
                event=event_value(event),
                diagnostics=[Diagnostic("warning", "Structured storage is unavailable; match intel was not persisted.", self.id)],
            )
        predictions = payload.get("predictions", []) if isinstance(payload, dict) else payload.predictions
        optimized_tips = payload.get("optimized_tips", []) if isinstance(payload, dict) else payload.optimized_tips
        rows = build_match_intel_rows(list(predictions), list(optimized_tips))
        count = context.storage.write_records(MATCH_INTEL, rows, source=self.id, run_id=context.run_id)
        return PluginResult(
            plugin_id=self.id,
            event=event_value(event),
            artifacts=[Artifact(MATCH_INTEL, "structured_dataset", self.id, data={"rows": count})],
            metadata={"rows": count},
        )
