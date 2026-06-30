"""Persist source and plugin diagnostics as structured rows."""

from __future__ import annotations

from worldcup_predictions.core.contracts import Artifact, Diagnostic
from worldcup_predictions.core.datasets import SOURCE_DIAGNOSTICS
from worldcup_predictions.core.events import EventName, event_value
from worldcup_predictions.core.metadata import PluginKind, PluginMetadata
from worldcup_predictions.core.plugin import BasePlugin, PluginResult
from worldcup_predictions.storage.ledger import stable_hash


class SourceDiagnosticsPlugin(BasePlugin):
    """Store run diagnostics so source failures and skipped layers are auditable."""

    id = "source_diagnostics"
    version = "0.1.0"
    priority = 940
    subscribed_events = (EventName.DEBUG_REPORT_REQUESTED.value,)
    metadata = PluginMetadata(
        plugin_id=id,
        kind=PluginKind.OUTPUT,
        description="Persist every plugin diagnostic with plugin/event metadata.",
        datasets_written=(SOURCE_DIAGNOSTICS,),
        confidence_policy="Diagnostics are audit artifacts and do not affect predictions.",
    )

    def handle(self, event, context, payload):
        if context.storage is None:
            return PluginResult(
                plugin_id=self.id,
                event=event_value(event),
                diagnostics=[Diagnostic("warning", "Structured storage is unavailable; source diagnostics were skipped.", self.id)],
            )
        rows = []
        for sequence, result in enumerate(context.event_results):
            for index, diagnostic in enumerate(result.diagnostics):
                rows.append(
                    {
                        "record_key": stable_hash({"run_id": context.run_id, "sequence": sequence, "index": index}),
                        "run_id": context.run_id,
                        "plugin_id": result.plugin_id,
                        "event": result.event,
                        "source": diagnostic.source,
                        "level": diagnostic.level,
                        "message": diagnostic.message,
                        "fixture_key": diagnostic.fixture_key,
                        "metadata": diagnostic.metadata,
                    }
                )
        count = context.storage.write_records(SOURCE_DIAGNOSTICS, rows, source=self.id, run_id=context.run_id)
        return PluginResult(
            plugin_id=self.id,
            event=event_value(event),
            artifacts=[Artifact(SOURCE_DIAGNOSTICS, "structured_dataset", self.id, data={"rows": count})],
            metadata={"rows": count},
        )
