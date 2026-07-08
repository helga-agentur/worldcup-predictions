"""openfootball/worldcup fixture and result source plugin."""

from __future__ import annotations

import datetime as dt
import urllib.error

from worldcup_predictions.core.constants import (
    ENDPOINT_OPENFOOTBALL_WORLDCUP_BASE,
    OPENFOOTBALL_WORLD_CUP_FILES,
    SOURCE_OPENFOOTBALL,
)
from worldcup_predictions.core.contracts import Artifact, Diagnostic
from worldcup_predictions.core.datasets import TOURNAMENT_FIXTURES, TOURNAMENT_RESULTS
from worldcup_predictions.core.events import EventName, event_value
from worldcup_predictions.core.metadata import PluginKind, PluginMetadata, QuotaPolicy
from worldcup_predictions.core.plugin import BasePlugin, PluginResult
from worldcup_predictions.plugins.source_runtime import SourceRuntime
from worldcup_predictions.storage.ledger import SourceRequest
from worldcup_predictions.tournament import parse_openfootball_text
from worldcup_predictions.tournament.repository import load_tournament_state, write_derived_state, write_fixtures, write_results


class OpenFootballSourcePlugin(BasePlugin):
    """Fetch openfootball/worldcup Football.TXT files and store structured facts."""

    id = "openfootball_source"
    version = "0.1.0"
    priority = 115
    subscribed_events = (EventName.FIXTURES_REQUESTED.value,)
    metadata = PluginMetadata(
        plugin_id=id,
        kind=PluginKind.SOURCE,
        description="Fetch openfootball/worldcup fixture and result data into tournament state.",
        datasets_written=(TOURNAMENT_FIXTURES, TOURNAMENT_RESULTS),
        quota_policy=QuotaPolicy(
            quota_limited=False,
            ledger_required=True,
            description="Public GitHub raw files are refreshed through the source ledger so scheduled cron runs do not refetch unchanged inputs.",
        ),
        confidence_policy="openfootball fixtures/results are useful public fallbacks; result rows enter tournament state only after the central source-consensus policy confirms them.",
    )

    def handle(self, event, context, payload):
        runtime = SourceRuntime(self, event, context)
        if context.storage is None:
            return runtime.storage_unavailable_result("openfootball data")

        fixture_count = 0
        result_count = 0
        diagnostics: list[Diagnostic] = []
        for filename, path in OPENFOOTBALL_WORLD_CUP_FILES.items():
            result = self._fetch_file(runtime, filename=filename, path=path)
            diagnostics.extend(result.diagnostics)
            fixture_count += int(result.metadata.get("fixtures") or 0)
            result_count += int(result.metadata.get("results") or 0)

        state = load_tournament_state(runtime.storage)
        write_derived_state(runtime.storage, state, run_id=runtime.context.run_id)
        runtime.context.state["tournament_state"] = state
        return PluginResult(
            plugin_id=self.id,
            event=event_value(event),
            artifacts=[
                Artifact(TOURNAMENT_FIXTURES, "structured_dataset", self.id, data={"rows_written": fixture_count}),
                Artifact(TOURNAMENT_RESULTS, "structured_dataset", self.id, data={"rows_written": result_count}),
            ],
            diagnostics=diagnostics,
            metadata={"fixtures": fixture_count, "results": result_count, "files": len(OPENFOOTBALL_WORLD_CUP_FILES)},
        )

    def _fetch_file(self, runtime: SourceRuntime, *, filename: str, path: str) -> PluginResult:
        endpoint = f"{ENDPOINT_OPENFOOTBALL_WORLDCUP_BASE}/{path}"
        request = SourceRequest(
            source=SOURCE_OPENFOOTBALL,
            endpoint=endpoint,
            purpose="world_cup_football_txt",
            params={"file": filename},
            quota_cost=0,
            min_refresh_interval=dt.timedelta(minutes=runtime.context.config.source_defaults.default_refresh_minutes),
            quota_scope=SOURCE_OPENFOOTBALL,
            rate_limit_backoff=dt.timedelta(hours=6),
        )
        decision = runtime.should_fetch(request)
        if not decision.should_fetch:
            return runtime.skipped_fetch_result("openfootball", decision.reason, metadata={**decision.metadata, "file": filename})
        try:
            text, _headers = runtime.fetch_text(endpoint)
        except (OSError, TimeoutError, urllib.error.HTTPError) as exc:
            runtime.record_error(request, exc, metadata={"file": filename})
            return runtime.result(
                diagnostics=[
                    runtime.diagnostic(
                        "warning",
                        "openfootball fetch failed; stored tournament rows will be used.",
                        metadata={"file": filename, "error": str(exc)},
                    )
                ]
            )

        source_id = f"openfootball/worldcup:{filename}"
        fixtures, results = parse_openfootball_text(text, source_id=source_id)
        fixture_count = write_fixtures(runtime.storage, fixtures, source=source_id, run_id=runtime.context.run_id)
        result_count = write_results(runtime.storage, results, source=source_id, run_id=runtime.context.run_id)
        runtime.record_success(
            request,
            message="Fetched openfootball World Cup data.",
            metadata={"file": filename, "fixtures": fixture_count, "results": result_count},
        )
        return runtime.result(metadata={"fixtures": fixture_count, "results": result_count})
