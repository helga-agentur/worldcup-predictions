from __future__ import annotations

import datetime as dt
import tempfile
import unittest
from pathlib import Path

try:
    import duckdb  # noqa: F401
except ModuleNotFoundError:  # pragma: no cover - local editable runs may not have deps installed.
    duckdb = None

from worldcup_predictions.core.contracts import Fixture, OutcomeProbabilities, Prediction, ScoreMatrixEntry, ScoreTip, Signal
from worldcup_predictions.core.datasets import (
    DATA_UPDATE_HOOKS,
    DIAGNOSTICS_COMPLETENESS_AUDIT,
    PLUGIN_EVENT_OUTPUTS,
    PLUGIN_RUN_DIAGNOSTICS,
    PREDICTION_BACKTEST,
    PREDICTION_SIGNAL_IMPACTS,
    TOURNAMENT_FIXTURES,
)
from worldcup_predictions.core.events import EventName, event_value
from worldcup_predictions.core.plugin import BasePlugin, PluginManager, PluginResult
from worldcup_predictions.core.signals import ML_HDA_PROBABILITIES, TOTAL_GOALS_FACTOR
from worldcup_predictions.core.workflow import PredictionWorkflow, WorkflowContext
from worldcup_predictions.evaluation.audit import build_prediction_audit_rows
from worldcup_predictions.evaluation.diagnostics_completeness import write_diagnostics_completeness_audit
from worldcup_predictions.evaluation.data_hooks import run_data_update_hooks
from worldcup_predictions.evaluation.provider_points import build_provider_points_rows
from worldcup_predictions.evaluation.reports import write_standard_reports
from worldcup_predictions.evaluation.scheduled_update import summarize_source_ledger_rows
from worldcup_predictions.plugins.diagnostics.debug_report import DebugReportPlugin
from worldcup_predictions.plugins.diagnostics.debug_report.plugin import signal_impact_rows
from worldcup_predictions.plugins.source_runtime import SourceRuntime
from worldcup_predictions.plugins.workflow.structured_output import StructuredOutputPlugin
from worldcup_predictions.plugins.providers import SrfChProviderOptimizerPlugin
from worldcup_predictions.storage import DuckDBStorage, SourceLedgerRecord, SourceRequest
from worldcup_predictions.tournament import FixtureRecord, ResultRecord, TeamResolver, TournamentState


class StaticPredictionPlugin(BasePlugin):
    id = "static_prediction"
    priority = 10
    subscribed_events = (EventName.PREDICTIONS_REQUESTED.value,)

    def __init__(self, prediction: Prediction) -> None:
        self.prediction = prediction

    def handle(self, event, context, payload):
        return PluginResult(
            plugin_id=self.id,
            event=event_value(event),
            predictions=[self.prediction],
        )


class StaticSignalPlugin(BasePlugin):
    id = "static_signal"
    priority = 5
    subscribed_events = (EventName.FEATURE_SIGNALS_REQUESTED.value,)

    def __init__(self, fixture_key: str) -> None:
        self.fixture_key = fixture_key

    def handle(self, event, context, payload):
        return PluginResult(
            plugin_id=self.id,
            event=event_value(event),
            signals=[
                Signal(
                    name=TOTAL_GOALS_FACTOR,
                    source="test_signal",
                    fixture_key=self.fixture_key,
                    value=0.95,
                    weight=0.25,
                    confidence=0.8,
                    rationale="test signal",
                )
            ],
        )


class WorkflowLimitTest(unittest.TestCase):
    def test_limit_zero_means_all_predictions(self) -> None:
        match_prediction = prediction("2026-07-10T18:00:00Z", "Brazil", "Japan")
        manager = PluginManager([StaticPredictionPlugin(match_prediction)])
        workflow = PredictionWorkflow(
            manager,
            context=WorkflowContext(project_root=Path("."), data_root=Path("data")),
        )

        run = workflow.next_predictions(limit=0)

        self.assertEqual(len(run.predictions), 1)


class PredictionAuditStorageTest(unittest.TestCase):
    @unittest.skipIf(duckdb is None, "duckdb dependency is not installed")
    def test_missing_snapshot_audit_row_satisfies_dataset_contract(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            storage = DuckDBStorage.at_data_root(Path(tmp) / "data")
            resolver = TeamResolver.default()
            fixture = FixtureRecord(
                event_date="2026-07-10T18:00:00Z",
                home_team=resolver.resolve("Brazil"),
                away_team=resolver.resolve("Japan"),
                stage="Group Stage",
            )
            state = TournamentState(
                fixtures=[fixture],
                results=[
                    ResultRecord(
                        event_date=fixture.event_date,
                        home_team=fixture.home_team,
                        away_team=fixture.away_team,
                        score=ScoreTip(2, 0),
                    )
                ],
                standings={},
            )

            rows = build_prediction_audit_rows(storage, state, run_id="test_run")

            self.assertEqual(rows[0]["source"], "missing_snapshot")
            self.assertTrue(rows[0]["snapshot_id"].startswith("missing_snapshot:"))
            self.assertEqual(storage.read_records("prediction_audit")[0]["snapshot_id"], rows[0]["snapshot_id"])


def prediction(event_date: str, home: str, away: str) -> Prediction:
    probabilities = OutcomeProbabilities(home=0.49, draw=0.29, away=0.22)
    return Prediction(
        fixture=Fixture(event_date=event_date, home_team=home, away_team=away),
        most_likely=ScoreTip(1, 1),
        outcome_probabilities=probabilities,
        confidence_label="Medium-low",
        confidence_percent=probabilities.max_probability(),
        expected_home_goals=1.2,
        expected_away_goals=0.9,
        source="static_prediction",
        score_matrix=[
            ScoreMatrixEntry(1, 1, 0.30),
            ScoreMatrixEntry(1, 0, 0.24),
            ScoreMatrixEntry(2, 1, 0.20),
            ScoreMatrixEntry(0, 0, 0.16),
            ScoreMatrixEntry(0, 1, 0.10),
        ],
        metadata={"signal_adjustments": [{"signal": TOTAL_GOALS_FACTOR, "weight": 0.2, "factor": 0.95}]},
    )


@unittest.skipIf(duckdb is None, "duckdb dependency is not installed")
class StorageTest(unittest.TestCase):
    def test_data_update_hooks_normalize_legacy_fifa_slot_codes_once(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            storage = DuckDBStorage.at_data_root(Path(tmp) / "data")
            storage.write_records(
                "custom_runtime_dataset",
                [
                    {
                        "record_key": "2026-07-18T21:00:00Z|L101|L102",
                        "fixture_key": "2026-07-18T21:00:00Z|L101|L102",
                        "home_team": "L101",
                        "away_team": "L102",
                        "nested": {"slot": "L101"},
                    }
                ],
                source="test",
            )
            storage.write_records(
                "custom_prediction_dataset",
                [
                    {
                        "record_key": "2026-07-07T00:00:00Z|BEL|BEL",
                        "fixture_key": "2026-07-07T00:00:00Z|BEL|BEL",
                        "home_team": "Belgium",
                        "away_team": "Belgium",
                    }
                ],
                source="test",
            )

            first = run_data_update_hooks(storage, run_id="test_run")
            rows = storage.read_records("custom_runtime_dataset", latest_only=True)
            duplicate_rows = storage.read_records("custom_prediction_dataset", latest_only=True)
            second = run_data_update_hooks(storage, run_id="test_run_2")
            hook_rows = storage.read_records(DATA_UPDATE_HOOKS, latest_only=True)

            self.assertEqual([row["status"] for row in first], ["success", "success"])
            self.assertEqual(first[0]["rows_changed"], 1)
            self.assertEqual(first[1]["rows_changed"], 1)
            self.assertEqual(rows[0]["record_key"], "2026-07-18T21:00:00Z|RU101|RU102")
            self.assertEqual(rows[0]["fixture_key"], "2026-07-18T21:00:00Z|RU101|RU102")
            self.assertEqual(rows[0]["home_team"], "RU101")
            self.assertEqual(rows[0]["nested"]["slot"], "RU101")
            self.assertEqual(duplicate_rows, [])
            self.assertEqual([row["status"] for row in second], ["skipped", "skipped"])
            self.assertEqual(len(hook_rows), 2)

    def test_provider_points_fall_back_to_backtest_tips_for_finished_matches(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            storage = DuckDBStorage.at_data_root(Path(tmp) / "data")
            resolver = TeamResolver.default()
            fixture = FixtureRecord(
                event_date="2026-07-10T18:00:00Z",
                home_team=resolver.resolve("Brazil"),
                away_team=resolver.resolve("Japan"),
                stage="Group Stage",
                group="Group A",
            )
            state = TournamentState(
                fixtures=[fixture],
                results=[
                    ResultRecord(
                        event_date=fixture.event_date,
                        home_team=fixture.home_team,
                        away_team=fixture.away_team,
                        score=ScoreTip(2, 0),
                    )
                ],
                standings={},
            )
            storage.write_records(
                PREDICTION_BACKTEST,
                [
                    {
                        "record_key": fixture.key,
                        "fixture_key": fixture.key,
                        "points": 10,
                        "srf_tip": "2:0",
                        "srf_tip_home": 2,
                        "srf_tip_away": 0,
                        "twenty_min_selection": "Brazil",
                        "twenty_min_selection_type": "outcome",
                    }
                ],
                source="test",
            )

            srf_rows = build_provider_points_rows(storage, state, provider="srf.ch")
            twenty_rows = build_provider_points_rows(storage, state, provider="20min.ch")

            self.assertEqual(srf_rows[0]["points"], 10)
            self.assertEqual(srf_rows[0]["source"], PREDICTION_BACKTEST)
            self.assertEqual(twenty_rows[0]["points"], 5)
            self.assertEqual(twenty_rows[0]["source"], PREDICTION_BACKTEST)

    def test_signal_impact_rows_expose_probability_signal_value(self) -> None:
        match_prediction = prediction("2026-07-10T18:00:00Z", "Brazil", "Japan")
        rows = signal_impact_rows(
            [match_prediction],
            [
                Signal(
                    name=ML_HDA_PROBABILITIES,
                    source="ml_outcome",
                    fixture_key=match_prediction.fixture.key,
                    value=None,
                    weight=0.85,
                    confidence=0.78,
                    rationale="test probabilities",
                    metadata={"prob_home": 0.5, "prob_draw": 0.25, "prob_away": 0.25},
                )
            ],
        )

        self.assertEqual(rows[0]["signal_value"], {"prob_home": 0.5, "prob_draw": 0.25, "prob_away": 0.25})

    def test_source_ledger_blocks_fresh_successful_fetch(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            storage = DuckDBStorage.at_data_root(Path(tmp) / "data")
            request = SourceRequest(
                source="odds_api",
                endpoint="/v4/sports/soccer/odds",
                purpose="fixture_odds",
                params={"markets": "h2h"},
                min_refresh_interval=dt.timedelta(hours=2),
            )
            now = dt.datetime(2026, 6, 28, 12, tzinfo=dt.timezone.utc)

            self.assertTrue(storage.should_fetch(request, now=now).should_fetch)

            storage.record_fetch(
                SourceLedgerRecord(
                    request=request,
                    status="success",
                    fetched_at_utc="2026-06-28T11:30:00Z",
                    quota_remaining=10,
                )
            )
            decision = storage.should_fetch(request, now=now)

            self.assertFalse(decision.should_fetch)
            self.assertEqual(decision.reason, "fresh_enough")

    def test_source_runtime_records_cache_validators_and_not_modified(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            storage = DuckDBStorage.at_data_root(Path(tmp) / "data")
            context = WorkflowContext(project_root=Path(tmp), data_root=Path(tmp) / "data", storage=storage, run_id="run-cache")
            runtime = SourceRuntime(plugin=DebugReportPlugin(), event=EventName.FIXTURES_REQUESTED.value, context=context)
            request = SourceRequest(
                source="cache_source",
                endpoint="https://example.test/feed.json",
                purpose="cache_test",
                min_refresh_interval=dt.timedelta(minutes=30),
            )

            runtime._remember_response(
                request,
                {
                    "ETag": '"abc123"',
                    "Last-Modified": "Wed, 01 Jul 2026 10:00:00 GMT",
                    "Set-Cookie": "session=secret",
                },
                status_code=304,
            )
            runtime.record_success(request, metadata={"rows": 0})

            rows = storage.read_source_ledger(run_id="run-cache")
            summary = summarize_source_ledger_rows(rows)

            self.assertEqual(rows[0]["status"], "not_modified")
            self.assertEqual(rows[0]["metadata"]["cache_validators"]["etag"], '"abc123"')
            self.assertEqual(rows[0]["metadata"]["cache_validators"]["last_modified"], "Wed, 01 Jul 2026 10:00:00 GMT")
            self.assertEqual(rows[0]["metadata"]["response_headers"]["Set-Cookie"], "[redacted]")
            self.assertEqual(storage.cache_validators(request)["etag"], '"abc123"')
            self.assertEqual(summary["cache_hits"], 1)
            self.assertEqual(summary["cache_skips"], 1)
            self.assertEqual(summary["cache_skipped_by_source"], {"cache_source": 1})

    def test_source_ledger_respects_next_safe_fetch_time(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            storage = DuckDBStorage.at_data_root(Path(tmp) / "data")
            request = SourceRequest(
                source="news_api",
                endpoint="/v2/everything",
                purpose="lineup_news",
                params={"q": "Brazil Japan"},
            )
            storage.record_fetch(
                SourceLedgerRecord(
                    request=request,
                    status="rate_limited",
                    fetched_at_utc="2026-06-28T11:00:00Z",
                    next_safe_fetch_at="2026-06-28T13:00:00Z",
                    message="429",
                )
            )

            decision = storage.should_fetch(request, now=dt.datetime(2026, 6, 28, 12, tzinfo=dt.timezone.utc))

            self.assertFalse(decision.should_fetch)
            self.assertEqual(decision.reason, "next_safe_fetch_at_not_reached")

    def test_source_ledger_blocks_sibling_requests_in_quota_scope(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            storage = DuckDBStorage.at_data_root(Path(tmp) / "data")
            first = SourceRequest(
                source="news_api",
                endpoint="/v2/everything",
                purpose="lineup_news",
                params={"q": "Brazil Japan"},
                quota_scope="news_api",
            )
            second = SourceRequest(
                source="news_api",
                endpoint="/v2/everything",
                purpose="lineup_news",
                params={"q": "France Spain"},
                quota_scope="news_api",
            )
            storage.record_fetch(
                SourceLedgerRecord(
                    request=first,
                    status="rate_limited",
                    fetched_at_utc="2026-06-28T11:00:00Z",
                    next_safe_fetch_at="2026-06-28T17:00:00Z",
                    message="429",
                )
            )

            decision = storage.should_fetch(second, now=dt.datetime(2026, 6, 28, 12, tzinfo=dt.timezone.utc))

            self.assertFalse(decision.should_fetch)
            self.assertEqual(decision.reason, "quota_scope_next_safe_fetch_at_not_reached")
            self.assertEqual(decision.metadata["quota_scope"], "news_api")

    def test_quota_scope_ignores_exact_resource_freshness_skips(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            storage = DuckDBStorage.at_data_root(Path(tmp) / "data")
            first = SourceRequest(
                source="news_api",
                endpoint="/v2/everything",
                purpose="lineup_news",
                params={"q": "Brazil Japan"},
                quota_scope="news_api",
            )
            second = SourceRequest(
                source="news_api",
                endpoint="/v2/everything",
                purpose="lineup_news",
                params={"q": "France Spain"},
                quota_scope="news_api",
            )
            storage.record_fetch(
                SourceLedgerRecord(
                    request=first,
                    status="skipped",
                    fetched_at_utc="2026-06-28T11:00:00Z",
                    next_safe_fetch_at="2026-06-28T17:00:00Z",
                    message="fresh_enough",
                    metadata={"decision_reason": "fresh_enough"},
                )
            )

            decision = storage.should_fetch(second, now=dt.datetime(2026, 6, 28, 12, tzinfo=dt.timezone.utc))

            self.assertTrue(decision.should_fetch)

    def test_quota_scope_does_not_propagate_broken_resource_errors(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            storage = DuckDBStorage.at_data_root(Path(tmp) / "data")
            broken_resource = SourceRequest(
                source="football_data_org",
                endpoint="/v4/matches/invalid",
                purpose="world_cup_match_detail",
                params={"match_id": "invalid"},
                quota_scope="football_data_org",
            )
            sibling = SourceRequest(
                source="football_data_org",
                endpoint="/v4/competitions/WC/matches",
                purpose="world_cup_matches",
                quota_scope="football_data_org",
            )
            storage.record_fetch(
                SourceLedgerRecord(
                    request=broken_resource,
                    status="error",
                    fetched_at_utc="2026-06-28T11:00:00Z",
                    next_safe_fetch_at="2026-06-29T11:00:00Z",
                    message="HTTP 400",
                    metadata={"http_status": 400},
                )
            )

            decision = storage.should_fetch(sibling, now=dt.datetime(2026, 6, 28, 12, tzinfo=dt.timezone.utc))

            self.assertTrue(decision.should_fetch)

    def test_quota_scope_propagates_source_block_errors(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            storage = DuckDBStorage.at_data_root(Path(tmp) / "data")
            blocked_resource = SourceRequest(
                source="espn_scoreboard",
                endpoint="/soccer/scoreboard",
                purpose="espn_worldcup_scoreboard",
                quota_scope="espn_scoreboard",
            )
            sibling = SourceRequest(
                source="espn_scoreboard",
                endpoint="/soccer/fixtures",
                purpose="espn_worldcup_fixtures",
                quota_scope="espn_scoreboard",
            )
            storage.record_fetch(
                SourceLedgerRecord(
                    request=blocked_resource,
                    status="error",
                    fetched_at_utc="2026-06-28T11:00:00Z",
                    next_safe_fetch_at="2026-06-28T17:00:00Z",
                    message="HTTP 403",
                    metadata={"http_status": 403},
                )
            )

            decision = storage.should_fetch(sibling, now=dt.datetime(2026, 6, 28, 12, tzinfo=dt.timezone.utc))

            self.assertFalse(decision.should_fetch)
            self.assertEqual(decision.reason, "quota_scope_next_safe_fetch_at_not_reached")

    def test_source_ledger_is_filterable_by_run_id_and_summarizes_failures(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            storage = DuckDBStorage.at_data_root(Path(tmp) / "data")
            request = SourceRequest(
                source="weather",
                endpoint="https://api.open-meteo.com/v1/forecast",
                purpose="match_window_weather",
                params={"fixture": "fixture-a"},
                fixture_key="fixture-a",
            )
            storage.record_fetch(
                SourceLedgerRecord(
                    request=request,
                    status="error",
                    run_id="run-a",
                    fetched_at_utc="2026-06-28T12:00:00Z",
                    message="timeout",
                )
            )
            storage.record_fetch(
                SourceLedgerRecord(
                    request=request,
                    status="success",
                    run_id="run-b",
                    fetched_at_utc="2026-06-28T13:00:00Z",
                    metadata={"rows": 0},
                )
            )

            rows = storage.read_source_ledger(run_id="run-a")
            summary = summarize_source_ledger_rows(rows)

            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]["run_id"], "run-a")
            self.assertEqual(summary["status_counts"], {"error": 1})
            self.assertEqual(summary["source_status_counts"], {"weather:error": 1})
            self.assertEqual(summary["calls_made"], 1)
            self.assertEqual(summary["calls_avoided"], 0)
            self.assertEqual(summary["failures"][0]["message"], "timeout")

    def test_source_runtime_records_skipped_fetches_as_avoided_calls(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            storage = DuckDBStorage.at_data_root(Path(tmp) / "data")
            request = SourceRequest(
                source="odds_api",
                endpoint="/v4/sports/soccer/odds",
                purpose="fixture_odds",
                params={"markets": "h2h"},
                min_refresh_interval=dt.timedelta(hours=2),
            )
            storage.record_fetch(
                SourceLedgerRecord(
                    request=request,
                    status="success",
                    run_id="run-a",
                    fetched_at_utc=dt.datetime.now(dt.timezone.utc).isoformat().replace("+00:00", "Z"),
                    quota_remaining=10,
                )
            )
            context = WorkflowContext(
                project_root=Path(tmp),
                data_root=Path(tmp) / "data",
                storage=storage,
                run_id="run-b",
            )
            runtime = SourceRuntime(BasePlugin(), EventName.FIXTURES_REQUESTED, context)

            decision = runtime.should_fetch(request)
            rows = storage.read_source_ledger(run_id="run-b")
            summary = summarize_source_ledger_rows(rows)

            self.assertFalse(decision.should_fetch)
            self.assertEqual(rows[0]["status"], "skipped")
            self.assertEqual(rows[0]["message"], "fresh_enough")
            self.assertEqual(summary["calls_made"], 0)
            self.assertEqual(summary["calls_avoided"], 1)
            self.assertEqual(summary["quota_cost_avoided"], 1)

    def test_rate_limited_error_without_retry_after_backs_off_and_opens_run_circuit(self) -> None:
        import urllib.error

        with tempfile.TemporaryDirectory() as tmp:
            storage = DuckDBStorage.at_data_root(Path(tmp) / "data")
            context = WorkflowContext(project_root=Path(tmp), data_root=Path(tmp) / "data", storage=storage, run_id="run-a")
            runtime = SourceRuntime(BasePlugin(), EventName.FIXTURES_REQUESTED, context)
            request = SourceRequest(source="news_api", endpoint="/v2/everything", purpose="pregame", params={"q": "a"})

            error = urllib.error.HTTPError("/v2/everything", 429, "Too Many Requests", None, None)
            runtime.record_error(request, error)

            rows = storage.read_source_ledger(run_id="run-a")
            self.assertEqual(rows[0]["status"], "rate_limited")
            next_safe = dt.datetime.fromisoformat(str(rows[0]["next_safe_fetch_at"]).replace("Z", "+00:00"))
            hours_out = (next_safe - dt.datetime.now(dt.timezone.utc)).total_seconds() / 3600
            self.assertGreater(hours_out, 0.5)
            self.assertLess(hours_out, 1.5)
            self.assertFalse(storage.should_fetch(request).should_fetch)

            other_key_same_source = SourceRequest(source="news_api", endpoint="/v2/everything", purpose="pregame", params={"q": "b"})
            decision = runtime.should_fetch(other_key_same_source)
            self.assertFalse(decision.should_fetch)
            self.assertEqual(decision.reason, "rate_limited_this_run")

            other_source = SourceRequest(source="open_meteo", endpoint="/v1/forecast", purpose="weather", params={"q": "b"})
            self.assertTrue(runtime.should_fetch(other_source).should_fetch)

    def test_quota_exhaustion_error_body_backs_off_quota_scope(self) -> None:
        import io
        import urllib.error

        with tempfile.TemporaryDirectory() as tmp:
            storage = DuckDBStorage.at_data_root(Path(tmp) / "data")
            context = WorkflowContext(project_root=Path(tmp), data_root=Path(tmp) / "data", storage=storage, run_id="run-a")
            runtime = SourceRuntime(BasePlugin(), EventName.FIXTURES_REQUESTED, context)
            request = SourceRequest(
                source="the_odds_api",
                endpoint="/v4/sports/soccer/odds",
                purpose="fixture_odds",
                params={"markets": "h2h"},
                quota_scope="the_odds_api",
            )

            error = urllib.error.HTTPError(
                "/v4/sports/soccer/odds",
                401,
                "Unauthorized",
                None,
                io.BytesIO(b'{"error_code":"OUT_OF_USAGE_CREDITS","message":"Usage quota has been reached."}'),
            )
            runtime.record_error(request, error)
            error.close()

            rows = storage.read_source_ledger(run_id="run-a")
            self.assertEqual(rows[0]["status"], "rate_limited")
            self.assertIn("OUT_OF_USAGE_CREDITS", rows[0]["metadata"]["response_body"])
            # Monthly credits cannot heal within the daily probe horizon:
            # the whole scope goes quiet until the subscription cycle resets.
            self.assertEqual(rows[0]["metadata"]["backoff_reason"], "monthly_quota_exhausted")
            next_safe = dt.datetime.fromisoformat(str(rows[0]["next_safe_fetch_at"]).replace("Z", "+00:00"))
            days_out = (next_safe - dt.datetime.now(dt.timezone.utc)).total_seconds() / 86400
            self.assertGreater(days_out, 29.5)
            sibling = SourceRequest(
                source="the_odds_api",
                endpoint="/v4/sports/soccer/events",
                purpose="event_discovery",
                quota_scope="the_odds_api",
            )
            decision = runtime.should_fetch(sibling)
            self.assertFalse(decision.should_fetch)
            self.assertEqual(decision.reason, "rate_limited_quota_scope_this_run")
            # Later runs (fresh in-run state) must stay blocked by the ledger
            # itself -- no daily probe for an exhausted monthly quota.
            ledger_decision = storage.should_fetch(sibling)
            self.assertFalse(ledger_decision.should_fetch)
            self.assertEqual(ledger_decision.reason, "quota_scope_next_safe_fetch_at_not_reached")

    def test_client_error_codes_back_off_broken_request_keys(self) -> None:
        import urllib.error

        expectations = {400: (20.0, 28.0), 403: (0.5, 1.5), 404: (20.0, 28.0)}
        for code, (low, high) in expectations.items():
            with tempfile.TemporaryDirectory() as tmp:
                storage = DuckDBStorage.at_data_root(Path(tmp) / "data")
                context = WorkflowContext(project_root=Path(tmp), data_root=Path(tmp) / "data", storage=storage, run_id="run-a")
                runtime = SourceRuntime(BasePlugin(), EventName.FIXTURES_REQUESTED, context)
                request = SourceRequest(source="espn_scoreboard", endpoint="/scoreboard", purpose="scores", params={"code": code})

                runtime.record_error(request, urllib.error.HTTPError("/scoreboard", code, "blocked", None, None))

                rows = storage.read_source_ledger(run_id="run-a")
                self.assertEqual(rows[0]["status"], "error")
                next_safe = dt.datetime.fromisoformat(str(rows[0]["next_safe_fetch_at"]).replace("Z", "+00:00"))
                hours_out = (next_safe - dt.datetime.now(dt.timezone.utc)).total_seconds() / 3600
                self.assertGreater(hours_out, low, f"code {code}")
                self.assertLess(hours_out, high, f"code {code}")
                self.assertFalse(storage.should_fetch(request).should_fetch)

                sibling = SourceRequest(
                    source="espn_scoreboard", endpoint="/scoreboard", purpose="scores", params={"other": True}
                )
                decision = runtime.should_fetch(sibling)
                if code in (400, 404):
                    self.assertTrue(decision.should_fetch, f"code {code} must stay request-specific")
                else:
                    self.assertFalse(decision.should_fetch, f"code {code} must open the run circuit")
                    self.assertEqual(decision.reason, "source_failed_this_run")

    def test_model_calibration_skips_recompute_when_inputs_unchanged(self) -> None:
        import unittest.mock
        from worldcup_predictions.core.contracts import ScoreTip
        from worldcup_predictions.evaluation import model_calibration as mc
        from worldcup_predictions.model import HistoricalResult
        from worldcup_predictions.tournament import TeamResolver

        with tempfile.TemporaryDirectory() as tmp:
            storage = DuckDBStorage.at_data_root(Path(tmp) / "data")
            resolver = TeamResolver.default()
            history = [
                HistoricalResult(
                    date="2022-12-18",
                    home_team=resolver.resolve("Argentina"),
                    away_team=resolver.resolve("France"),
                    score=ScoreTip(3, 3),
                    tournament="FIFA World Cup",
                )
            ]
            fingerprint = mc.calibration_inputs_fingerprint(history)
            storage.write_records(
                "model_calibration",
                [{"record_key": "cal-1", "calibration_id": "cal-1", "inputs_fingerprint": fingerprint}],
                source="model_calibration",
                run_id="run-a",
            )

            with unittest.mock.patch.object(mc, "calibrate_baseline_model", side_effect=AssertionError("must not recompute")):
                written = mc.write_model_calibration(storage, history, run_id="run-b")
            self.assertEqual(written, 0)

            changed = history + [
                HistoricalResult(
                    date="2022-12-14",
                    home_team=resolver.resolve("France"),
                    away_team=resolver.resolve("Morocco"),
                    score=ScoreTip(2, 0),
                    tournament="FIFA World Cup",
                )
            ]
            with unittest.mock.patch.object(mc, "calibrate_baseline_model", return_value=[{"record_key": "cal-2", "calibration_id": "cal-2"}]) as compute:
                written = mc.write_model_calibration(storage, changed, run_id="run-c")
            self.assertEqual(compute.call_count, 1, "changed history must recompute")
            self.assertEqual(written, 1)
            rows = storage.read_records("model_calibration", latest_only=True)
            new_row = next(row for row in rows if row["calibration_id"] == "cal-2")
            self.assertEqual(new_row["inputs_fingerprint"], mc.calibration_inputs_fingerprint(changed))

    def test_repeated_extraction_diagnostics_are_not_reappended(self) -> None:
        from worldcup_predictions.core.extraction import extraction_diagnostic_row, unstored_extraction_diagnostics

        with tempfile.TemporaryDirectory() as tmp:
            storage = DuckDBStorage.at_data_root(Path(tmp) / "data")
            row = extraction_diagnostic_row(
                source="postmatch_stats",
                extractor="postmatch_v1",
                status="rejected",
                reason="no_parseable_xg_or_stat_fields",
                fixture_key="fixture-a",
            )

            first = unstored_extraction_diagnostics(storage, [row])
            self.assertEqual(len(first), 1)
            storage.write_records("extraction_diagnostics", first, source="postmatch_stats", run_id="run-a")

            second = unstored_extraction_diagnostics(storage, [dict(row)])
            self.assertEqual(second, [], "identical diagnostic must not be re-appended")

            changed = extraction_diagnostic_row(
                source="postmatch_stats",
                extractor="postmatch_v1",
                status="accepted",
                reason="stats_extracted",
                fixture_key="fixture-a",
            )
            fresh = unstored_extraction_diagnostics(storage, [changed])
            self.assertEqual(len(fresh), 1, "changed diagnostic must still be written")

            plain_storage = object()
            self.assertEqual(unstored_extraction_diagnostics(plain_storage, [dict(row)]), [dict(row)])

    def test_quota_floor_blocks_expire_after_a_day_so_reset_quotas_are_probed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            storage = DuckDBStorage.at_data_root(Path(tmp) / "data")
            now = dt.datetime.now(dt.timezone.utc)

            def request(day: int) -> SourceRequest:
                return SourceRequest(
                    source="the_odds_api",
                    endpoint="/v4/sports/soccer/odds",
                    purpose="fixture_odds",
                    params={"day": day},
                    quota_scope="the_odds_api",
                    quota_remaining_floor=5,
                )

            fresh_at = (now - dt.timedelta(hours=2)).isoformat().replace("+00:00", "Z")
            storage.record_fetch(
                SourceLedgerRecord(request=request(1), status="success", run_id="run-a", fetched_at_utc=fresh_at, quota_remaining=2)
            )
            same_key = storage.should_fetch(request(1))
            sibling = storage.should_fetch(request(2))
            self.assertFalse(same_key.should_fetch)
            self.assertFalse(sibling.should_fetch)
            self.assertEqual(sibling.reason, "quota_scope_quota_floor_reached")

            stale_at = (now - dt.timedelta(hours=30)).isoformat().replace("+00:00", "Z")
            storage2 = DuckDBStorage.at_data_root(Path(tmp) / "data2")
            storage2.record_fetch(
                SourceLedgerRecord(request=request(1), status="success", run_id="run-a", fetched_at_utc=stale_at, quota_remaining=2)
            )
            # Live regression 2026-07-10: every half-hourly run records its own
            # floor-skip rows, which echo the stale quota_remaining under a
            # fresh timestamp. Those decisions must not count as observations,
            # or the daily probe horizon is never reached.
            for hours_ago in (2, 1):
                skip_at = (now - dt.timedelta(hours=hours_ago)).isoformat().replace("+00:00", "Z")
                for day in (1, 2):
                    storage2.record_fetch(
                        SourceLedgerRecord(
                            request=request(day),
                            status="skipped",
                            run_id="run-b",
                            fetched_at_utc=skip_at,
                            quota_remaining=2,
                            message="quota_scope_quota_floor_reached",
                        )
                    )
            self.assertTrue(storage2.should_fetch(request(1)).should_fetch, "stale quota floor must allow a daily probe")
            self.assertTrue(storage2.should_fetch(request(2)).should_fetch, "stale scope floor must allow a daily probe")

    def test_football_data_ignores_per_minute_counter_as_quota(self) -> None:
        from worldcup_predictions.plugins.sources.fixtures.football_data.plugin import quota_remaining

        self.assertIsNone(quota_remaining({"X-Requests-Available-Minute": "2"}))
        self.assertEqual(quota_remaining({"X-RequestsAvailable": "40"}), 40)

    def test_repeated_failures_escalate_backoff_and_success_resets_the_ladder(self) -> None:
        import urllib.error

        with tempfile.TemporaryDirectory() as tmp:
            storage = DuckDBStorage.at_data_root(Path(tmp) / "data")
            context = WorkflowContext(project_root=Path(tmp), data_root=Path(tmp) / "data", storage=storage, run_id="run-a")
            runtime = SourceRuntime(BasePlugin(), EventName.FIXTURES_REQUESTED, context)
            request = SourceRequest(source="espn_scoreboard", endpoint="/scoreboard", purpose="scores", params={"day": 1})

            expected_hours = [1, 4, 12, 24, 24]
            for attempt, expected in enumerate(expected_hours, start=1):
                runtime.record_error(request, urllib.error.HTTPError("/scoreboard", 503, "unavailable", None, None))
                errors = [row for row in storage.read_source_ledger(run_id="run-a") if row["status"] == "error"]
                row = next(r for r in errors if r["metadata"].get("consecutive_failures") == attempt)
                next_safe = dt.datetime.fromisoformat(str(row["next_safe_fetch_at"]).replace("Z", "+00:00"))
                hours_out = (next_safe - dt.datetime.now(dt.timezone.utc)).total_seconds() / 3600
                self.assertGreater(hours_out, expected - 0.5, f"attempt {attempt}")
                self.assertLess(hours_out, expected + 0.5, f"attempt {attempt}")
                self.assertEqual(row["metadata"]["backoff_reason"], f"failure_ladder_step_{min(attempt, 4)}")

            storage.record_fetch(SourceLedgerRecord(request=request, status="success", run_id="run-a"))
            runtime.record_error(request, urllib.error.HTTPError("/scoreboard", 503, "unavailable", None, None))
            errors = [row for row in storage.read_source_ledger(run_id="run-a") if row["status"] == "error"]
            first_step_rows = [r for r in errors if r["metadata"].get("consecutive_failures") == 1]
            self.assertEqual(len(first_step_rows), 2, "success must reset the ladder to step 1")

    def test_transport_error_without_http_code_backs_off_and_skips_remaining_source_requests(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            storage = DuckDBStorage.at_data_root(Path(tmp) / "data")
            context = WorkflowContext(project_root=Path(tmp), data_root=Path(tmp) / "data", storage=storage, run_id="run-a")
            runtime = SourceRuntime(BasePlugin(), EventName.FIXTURES_REQUESTED, context)
            request = SourceRequest(source="open_meteo", endpoint="/v1/forecast", purpose="weather", params={"city": "a"})

            runtime.record_error(request, TimeoutError("timed out"))

            rows = storage.read_source_ledger(run_id="run-a")
            self.assertEqual(rows[0]["status"], "error")
            self.assertIsNotNone(rows[0]["next_safe_fetch_at"], "timeouts must back off too")
            self.assertEqual(rows[0]["metadata"]["backoff_reason"], "failure_ladder_step_1")

            sibling = SourceRequest(source="open_meteo", endpoint="/v1/forecast", purpose="weather", params={"city": "b"})
            decision = runtime.should_fetch(sibling)
            self.assertFalse(decision.should_fetch)
            self.assertEqual(decision.reason, "source_failed_this_run")
            skipped = [row for row in storage.read_source_ledger(run_id="run-a") if row["status"] == "skipped"]
            self.assertEqual(len(skipped), 1)
            self.assertEqual(skipped[0]["message"], "source_failed_this_run")

            other_source = SourceRequest(source="srf_public", endpoint="/results", purpose="scores")
            self.assertTrue(runtime.should_fetch(other_source).should_fetch)

    def test_structured_records_are_append_only_and_latest_reads_reuse_last_available_row(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            storage = DuckDBStorage.at_data_root(Path(tmp) / "data")
            dataset = "test_hourly_source"

            first_count = storage.write_records(
                dataset,
                [{"record_key": "fixture-a:weather", "fixture_key": "fixture-a", "value": 0.91}],
                source="weather",
                run_id="run-a",
            )
            latest_after_empty_run = storage.read_records(dataset, latest_only=True)
            second_count = storage.write_records(
                dataset,
                [{"record_key": "fixture-a:weather", "fixture_key": "fixture-a", "value": 0.84}],
                source="weather",
                run_id="run-c",
            )

            all_rows = storage.read_records(dataset)
            latest_rows = storage.read_records(dataset, latest_only=True)

            self.assertEqual(first_count, 1)
            self.assertEqual(second_count, 1)
            self.assertEqual(len(all_rows), 2)
            self.assertEqual(latest_after_empty_run[0]["value"], 0.91)
            self.assertEqual(latest_rows[0]["value"], 0.84)
            self.assertEqual(latest_rows[0]["_record"]["run_id"], "run-c")

    def test_timed_phase_records_duration_and_memory(self) -> None:
        from worldcup_predictions.core.runtime_metrics import peak_rss_mb, timed_phase

        sink: list[dict] = []
        with timed_phase("unit_test_phase", sink):
            pass

        self.assertEqual(len(sink), 1)
        entry = sink[0]
        self.assertEqual(entry["phase"], "unit_test_phase")
        self.assertGreaterEqual(entry["duration_seconds"], 0)
        self.assertIn("rss_mb_after", entry)
        self.assertGreater(peak_rss_mb(), 0)

    def test_deferred_dataset_exports_batch_parquet_writes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            storage = DuckDBStorage.at_data_root(Path(tmp) / "data")
            dataset = "test_hourly_source"
            parquet_path = Path(tmp) / "data" / "structured" / f"{dataset}.parquet"

            with storage.deferred_dataset_exports():
                storage.write_records(
                    dataset,
                    [{"record_key": "fixture-a:weather", "fixture_key": "fixture-a", "value": 0.91}],
                    source="weather",
                    run_id="run-a",
                )
                self.assertFalse(parquet_path.exists())
                storage.write_records(
                    dataset,
                    [{"record_key": "fixture-a:weather", "fixture_key": "fixture-a", "value": 0.84}],
                    source="weather",
                    run_id="run-b",
                )
                self.assertFalse(parquet_path.exists())

            self.assertTrue(parquet_path.exists())
            self.assertEqual(len(storage.read_records(dataset)), 2)
            # Writes after the deferred block export immediately again.
            parquet_path.unlink()
            storage.write_records(
                dataset,
                [{"record_key": "fixture-a:weather", "fixture_key": "fixture-a", "value": 0.7}],
                source="weather",
                run_id="run-c",
            )
            self.assertTrue(parquet_path.exists())

    def test_structured_output_plugin_persists_predictions(self) -> None:
        now = dt.datetime.now(dt.timezone.utc)
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            match_prediction = prediction(
                (now + dt.timedelta(hours=2)).isoformat().replace("+00:00", "Z"),
                "Brazil",
                "Japan",
            )
            manager = PluginManager(
                [
                    StaticPredictionPlugin(match_prediction),
                    SrfChProviderOptimizerPlugin(),
                    StructuredOutputPlugin(),
                ]
            )
            workflow = PredictionWorkflow.from_project_root(root, manager)

            run = workflow.next_predictions(limit=1)

            self.assertEqual(len(run.predictions), 1)
            self.assertEqual(len(run.optimized_tips), 1)
            self.assertTrue((root / "data" / "structured" / "predictions.parquet").exists())
            self.assertTrue((root / "data" / "structured" / "optimized_tips.parquet").exists())
            storage = workflow.context.storage
            self.assertIsNotNone(storage)

    def test_core_plugin_diagnostics_and_signal_impacts_are_persisted(self) -> None:
        now = dt.datetime.now(dt.timezone.utc)
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            match_prediction = prediction(
                (now + dt.timedelta(hours=2)).isoformat().replace("+00:00", "Z"),
                "Brazil",
                "Japan",
            )
            manager = PluginManager(
                [
                    StaticSignalPlugin(match_prediction.fixture.key),
                    StaticPredictionPlugin(match_prediction),
                    DebugReportPlugin(),
                ]
            )
            workflow = PredictionWorkflow.from_project_root(root, manager)

            workflow.next_predictions(limit=1)

            storage = workflow.context.storage
            self.assertIsNotNone(storage)
            plugin_rows = storage.read_records(PLUGIN_RUN_DIAGNOSTICS, latest_only=True)
            event_output_rows = storage.read_records(PLUGIN_EVENT_OUTPUTS, latest_only=True)
            impact_rows = storage.read_records(PREDICTION_SIGNAL_IMPACTS, latest_only=True)
            self.assertTrue(any(row["plugin_id"] == "static_prediction" for row in plugin_rows))
            self.assertTrue(any(row["plugin_id"] == "debug_report" for row in plugin_rows))
            self.assertIn("duration_ms", plugin_rows[0])
            self.assertIn("rss_mb_delta", plugin_rows[0])
            self.assertIn("rss_mb_after", plugin_rows[0])
            self.assertTrue(any(row["plugin_id"] == "static_signal" and row["output_type"] == "signal" for row in event_output_rows))
            self.assertTrue(any(row["plugin_id"] == "static_prediction" and row["output_type"] == "prediction" for row in event_output_rows))
            self.assertEqual(impact_rows[0]["signal_name"], TOTAL_GOALS_FACTOR)
            self.assertTrue(impact_rows[0]["applied"])

    def test_diagnostics_completeness_audit_is_persisted_and_reported(self) -> None:
        now = dt.datetime.now(dt.timezone.utc)
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            match_prediction = prediction(
                (now + dt.timedelta(hours=2)).isoformat().replace("+00:00", "Z"),
                "Brazil",
                "Japan",
            )
            manager = PluginManager(
                [
                    StaticSignalPlugin(match_prediction.fixture.key),
                    StaticPredictionPlugin(match_prediction),
                    StructuredOutputPlugin(),
                    SrfChProviderOptimizerPlugin(),
                    DebugReportPlugin(),
                ]
            )
            workflow = PredictionWorkflow.from_project_root(root, manager)

            workflow.next_predictions(limit=1)
            storage = workflow.context.storage
            self.assertIsNotNone(storage)
            rows = write_diagnostics_completeness_audit(storage, workflow.manager.plugins, run_id=workflow.context.run_id)
            reports = write_standard_reports(storage, root, run_id=workflow.context.run_id)

            persisted_rows = storage.read_records(DIAGNOSTICS_COMPLETENESS_AUDIT, latest_only=True)
            self.assertGreater(len(rows), 0)
            self.assertTrue(any(row["scope"] == "dataset_fields" for row in persisted_rows))
            self.assertTrue(any(row["scope"] == "plugin_run" for row in persisted_rows))
            self.assertTrue(any(report["report_key"] == "diagnostics-completeness" for report in reports))
            self.assertTrue((root / "reports" / "diagnostics-completeness.md").exists())

    def test_structured_records_validate_registered_dataset_contracts(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            storage = DuckDBStorage.at_data_root(Path(tmp) / "data")

            with self.assertRaisesRegex(ValueError, "missing required fields"):
                storage.write_records(
                    TOURNAMENT_FIXTURES,
                    [{"fixture_key": "2026-06-29_BRA_JPN"}],
                    source="test",
                )


if __name__ == "__main__":
    unittest.main()
