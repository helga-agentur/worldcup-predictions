"""Fetch martj42 historical international results into structured storage."""

from __future__ import annotations

import datetime as dt
import urllib.error

from worldcup_predictions.core.constants import (
    ENDPOINT_MARTJ42_RESULTS,
    ENDPOINT_MARTJ42_SHOOTOUTS,
    SOURCE_MARTJ42_RESULTS,
)
from worldcup_predictions.core.contracts import Diagnostic
from worldcup_predictions.core.datasets import HISTORICAL_RESULTS, SHOOTOUTS
from worldcup_predictions.core.events import EventName, event_value
from worldcup_predictions.core.metadata import PluginKind, PluginMetadata, QuotaPolicy
from worldcup_predictions.core.plugin import BasePlugin, PluginResult
from worldcup_predictions.model import parse_historical_results_text, parse_shootouts_text
from worldcup_predictions.plugins.source_runtime import SourceRuntime
from worldcup_predictions.storage.ledger import SourceRequest


class HistoricalResultsSourcePlugin(BasePlugin):
    """Keep historical international results available for model training."""

    id = "historical_results_source"
    version = "0.1.0"
    priority = 135
    subscribed_events = (EventName.FEATURE_SIGNALS_REQUESTED.value,)
    metadata = PluginMetadata(
        plugin_id=id,
        kind=PluginKind.SOURCE,
        description="Fetch martj42 international_results CSVs and write normalized historical results and shootouts.",
        datasets_written=(HISTORICAL_RESULTS, SHOOTOUTS),
        quota_policy=QuotaPolicy(
            quota_limited=False,
            ledger_required=True,
            description="GitHub raw CSVs are not quota-priced but are still ledgered and refreshed at most daily.",
        ),
    )

    def handle(self, event, context, payload):
        runtime = SourceRuntime(self, event, context)
        if context.storage is None:
            return runtime.storage_unavailable_result("historical results source")

        result_rows_written, result_diagnostics = self._fetch_results(runtime)
        shootout_rows_written, shootout_diagnostics = self._fetch_shootouts(runtime)
        return PluginResult(
            plugin_id=self.id,
            event=event_value(event),
            artifacts=[
                runtime.structured_artifact(HISTORICAL_RESULTS, rows_written=result_rows_written),
                runtime.structured_artifact(SHOOTOUTS, rows_written=shootout_rows_written),
            ],
            metadata={
                "historical_results_written": result_rows_written,
                "shootouts_written": shootout_rows_written,
            },
            diagnostics=[*result_diagnostics, *shootout_diagnostics],
        )

    def _fetch_results(self, runtime: SourceRuntime) -> tuple[int, list[Diagnostic]]:
        request = SourceRequest(
            source=SOURCE_MARTJ42_RESULTS,
            endpoint=ENDPOINT_MARTJ42_RESULTS,
            purpose="historical_results_csv",
            min_refresh_interval=dt.timedelta(days=1),
            quota_scope=SOURCE_MARTJ42_RESULTS,
            rate_limit_backoff=dt.timedelta(hours=6),
        )
        decision = runtime.should_fetch(request)
        if not decision.should_fetch:
            return 0, runtime.skipped_fetch_result("Historical results", decision.reason, metadata=decision.metadata).diagnostics
        try:
            text, _headers = runtime.fetch_text(ENDPOINT_MARTJ42_RESULTS)
            results = parse_historical_results_text(text, source="martj42/international_results")
        except (OSError, TimeoutError, urllib.error.HTTPError, ValueError) as exc:
            runtime.record_error(request, exc)
            return 0, [
                runtime.diagnostic(
                    "warning",
                    "Historical results fetch failed; stored rows will be used.",
                    metadata={"error": str(exc)},
                )
            ]
        runtime.record_success(request, metadata={"rows": len(results)})
        return runtime.storage.write_records(
            HISTORICAL_RESULTS,
            [result.to_record() for result in results],
            source=self.id,
            run_id=runtime.context.run_id,
        ), []

    def _fetch_shootouts(self, runtime: SourceRuntime) -> tuple[int, list[Diagnostic]]:
        request = SourceRequest(
            source=SOURCE_MARTJ42_RESULTS,
            endpoint=ENDPOINT_MARTJ42_SHOOTOUTS,
            purpose="historical_shootouts_csv",
            min_refresh_interval=dt.timedelta(days=1),
            quota_scope=SOURCE_MARTJ42_RESULTS,
            rate_limit_backoff=dt.timedelta(hours=6),
        )
        decision = runtime.should_fetch(request)
        if not decision.should_fetch:
            return 0, runtime.skipped_fetch_result("Historical shootouts", decision.reason, metadata=decision.metadata).diagnostics
        try:
            text, _headers = runtime.fetch_text(ENDPOINT_MARTJ42_SHOOTOUTS)
            rows = parse_shootouts_text(text, source="martj42/international_results")
        except (OSError, TimeoutError, urllib.error.HTTPError, ValueError) as exc:
            runtime.record_error(request, exc)
            return 0, [
                runtime.diagnostic(
                    "warning",
                    "Historical shootouts fetch failed; stored rows will be used.",
                    metadata={"error": str(exc)},
                )
            ]
        runtime.record_success(request, metadata={"rows": len(rows)})
        return runtime.storage.write_records(SHOOTOUTS, rows, source=self.id, run_id=runtime.context.run_id), []
