from __future__ import annotations

import datetime as dt
import json
import tempfile
import unittest
from pathlib import Path
from zoneinfo import ZoneInfo

try:
    import duckdb  # noqa: F401
except ModuleNotFoundError:  # pragma: no cover
    duckdb = None

from worldcup_predictions.core.contracts import Fixture, OutcomeProbabilities, Prediction, ScoreMatrixEntry, ScoreTip
from worldcup_predictions.core.datasets import (
    BASELINE_BUNDLES,
    EXTRACTION_DIAGNOSTICS,
    OPTIMIZED_TIPS,
    PREDICTION_BACKTEST,
    PREDICTION_EXPORTS,
    PREDICTION_LEDGER,
    PREDICTION_SNAPSHOTS,
    PUBLISHED_PREDICTION_SEED,
    PROVIDER_POINTS,
    PUBLISHED_PREDICTION_LEDGER,
    PREDICTIONS,
    TOURNAMENT_FIXTURES,
)
from worldcup_predictions.core.events import EventName, event_value
from worldcup_predictions.core.plugin import BasePlugin, PluginManager, PluginResult
from worldcup_predictions.core.workflow import PredictionWorkflow
from worldcup_predictions.evaluation.baseline_bundle import create_baseline_bundle
from worldcup_predictions.evaluation.prediction_export import write_prediction_export
from worldcup_predictions.evaluation.prediction_ledger import write_prediction_ledger
from worldcup_predictions.evaluation.published_prediction_ledger import write_published_prediction_ledger
from worldcup_predictions.plugins.diagnostics.debug_report import DebugReportPlugin
from worldcup_predictions.plugins.providers import SrfChProviderOptimizerPlugin
from worldcup_predictions.plugins.sources.enrichment.public_analysis.plugin import public_analysis_rows_with_diagnostics
from worldcup_predictions.plugins.workflow.structured_output import StructuredOutputPlugin
from worldcup_predictions.site import build_site
from worldcup_predictions.site.generator import normalized_base_url
from worldcup_predictions.storage import DuckDBStorage
from worldcup_predictions.tournament import FixtureRecord, TeamResolver


class StaticPredictionPlugin(BasePlugin):
    id = "static_prediction"
    priority = 10
    subscribed_events = (EventName.PREDICTIONS_REQUESTED.value,)

    def __init__(self, prediction: Prediction) -> None:
        self.prediction = prediction

    def handle(self, event, context, payload):
        return PluginResult(plugin_id=self.id, event=event_value(event), predictions=[self.prediction])


def prediction() -> Prediction:
    fixture = Fixture(
        event_date=(dt.datetime.now(dt.timezone.utc) + dt.timedelta(days=1)).isoformat().replace("+00:00", "Z"),
        home_team="Brazil",
        away_team="Japan",
        stage="Group Stage",
        group="Group A",
        metadata={"home_fifa_code": "BRA", "away_fifa_code": "JPN"},
    )
    return Prediction(
        fixture=fixture,
        most_likely=ScoreTip(1, 0),
        outcome_probabilities=OutcomeProbabilities(0.52, 0.27, 0.21),
        confidence_label="Medium-low",
        confidence_percent=0.52,
        expected_home_goals=1.4,
        expected_away_goals=0.8,
        source="static_prediction",
        score_matrix=[ScoreMatrixEntry(1, 0, 0.30), ScoreMatrixEntry(1, 1, 0.20)],
    )


def fixture_record() -> FixtureRecord:
    resolver = TeamResolver.default()
    return FixtureRecord(
        event_date="2026-07-10T18:00:00Z",
        home_team=resolver.resolve("Brazil"),
        away_team=resolver.resolve("Japan"),
        stage="Group Stage",
        group="Group A",
    )


class ExtractionDiagnosticsTest(unittest.TestCase):
    def test_public_analysis_reports_article_rejection_reasons(self) -> None:
        fixture = fixture_record()
        articles = [
            {
                "title": "Brazil Japan preview: tight game expected",
                "description": "Brazil and Japan could be compact and low scoring.",
                "publishedAt": "2026-07-10T08:00:00Z",
                "url": "https://example.test/accepted",
                "source": {"name": "Example"},
            },
            {
                "title": "Other match preview",
                "description": "No relevant teams here.",
                "publishedAt": "2026-07-10T08:00:00Z",
                "url": "https://example.test/rejected",
                "source": {"name": "Example"},
            },
        ]

        rows, diagnostics = public_analysis_rows_with_diagnostics(articles, fixture, phase="pregame")

        self.assertEqual(len(rows), 1)
        reasons = {row["reason"] for row in diagnostics}
        self.assertIn("accepted", reasons)
        self.assertIn("fixture_not_mentioned", reasons)


@unittest.skipIf(duckdb is None, "duckdb dependency is not installed")
class ExportAndBaselineTest(unittest.TestCase):
    def test_site_base_url_normalization_accepts_trailing_slash(self) -> None:
        self.assertEqual(normalized_base_url("http://127.0.0.1:8000/"), "http://127.0.0.1:8000")
        self.assertEqual(normalized_base_url("https://tippspiel.helga.ch"), "https://tippspiel.helga.ch")

    def test_published_ledger_replaces_stale_shifted_fixture_keys(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            storage = DuckDBStorage.at_data_root(root / "data")
            stale_key = "2026-07-01T01:00:00Z|MEX|ECU"
            corrected_key = "2026-07-01T02:00:00Z|MEX|ECU"
            storage.write_records(
                PREDICTION_LEDGER,
                [
                    {
                        "record_key": stale_key,
                        "fixture_key": stale_key,
                        "event_date": "2026-07-01T01:00:00Z",
                        "home_team": "Mexico",
                        "away_team": "Ecuador",
                        "status": "future",
                        "prediction_context": "latest_live_prediction",
                        "most_likely_home": 1,
                        "most_likely_away": 0,
                        "srf_tip": "1:0",
                        "twenty_min_tip": "Mexico",
                    }
                ],
                source="test",
            )
            write_published_prediction_ledger(
                storage,
                run_id="stale",
                now=dt.datetime(2026, 7, 1, 1, 40, tzinfo=dt.timezone.utc),
            )
            self.assertEqual(
                [row["fixture_key"] for row in storage.read_records(PUBLISHED_PREDICTION_LEDGER, latest_only=True)],
                [stale_key],
            )

            storage.replace_records(
                PREDICTION_LEDGER,
                [
                    {
                        "record_key": corrected_key,
                        "fixture_key": corrected_key,
                        "event_date": "2026-07-01T02:00:00Z",
                        "home_team": "Mexico",
                        "away_team": "Ecuador",
                        "status": "past",
                        "prediction_context": "retrospective_current_model_before_kickoff",
                        "actual_score": "2:1",
                        "actual_home": 2,
                        "actual_away": 1,
                        "most_likely_home": 2,
                        "most_likely_away": 1,
                        "srf_tip": "2:1",
                        "twenty_min_tip": "Mexico",
                    }
                ],
                source="test",
            )
            write_published_prediction_ledger(
                storage,
                run_id="corrected",
                now=dt.datetime(2026, 7, 1, 4, 40, tzinfo=dt.timezone.utc),
            )

            rows = storage.read_records(PUBLISHED_PREDICTION_LEDGER, latest_only=True)
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]["fixture_key"], corrected_key)
            self.assertEqual(rows[0]["status"], "final")

    def test_published_ledger_ignores_future_rows_outside_active_fixtures(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            storage = DuckDBStorage.at_data_root(Path(tmp) / "data")
            canonical_key = "2026-07-07T00:00:00Z|USA|BEL"
            fake_key = "2026-07-07T00:00:00Z|BEL|BEL"
            storage.write_records(
                TOURNAMENT_FIXTURES,
                [
                    {
                        "record_key": canonical_key,
                        "fixture_key": canonical_key,
                        "event_date": "2026-07-07T00:00:00Z",
                        "home_team": "United States",
                        "away_team": "Belgium",
                        "home_fifa_code": "USA",
                        "away_fifa_code": "BEL",
                        "stage": "Round of 16",
                        "status": "scheduled",
                        "metadata": {"source": "fifa_match_centre"},
                    }
                ],
                source="fifa_match_centre",
            )
            storage.write_records(
                PREDICTION_LEDGER,
                [
                    {
                        "record_key": fake_key,
                        "fixture_key": fake_key,
                        "event_date": "2026-07-07T00:00:00Z",
                        "home_team": "Belgium",
                        "away_team": "Belgium",
                        "status": "future",
                        "prediction_context": "latest_live_prediction",
                    },
                    {
                        "record_key": canonical_key,
                        "fixture_key": canonical_key,
                        "event_date": "2026-07-07T00:00:00Z",
                        "home_team": "United States",
                        "away_team": "Belgium",
                        "status": "future",
                        "prediction_context": "latest_live_prediction",
                    },
                ],
                source="test",
            )

            write_published_prediction_ledger(
                storage,
                run_id="test",
                now=dt.datetime(2026, 7, 2, 12, tzinfo=dt.timezone.utc),
            )

            rows = storage.read_records(PUBLISHED_PREDICTION_LEDGER, latest_only=True)
            self.assertEqual([row["fixture_key"] for row in rows], [canonical_key])

    def test_prediction_export_writes_one_file_with_score_matrix_and_diagnostics(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            workflow = _workflow(root)
            run = workflow.next_predictions(limit=1)
            storage = workflow.context.storage
            assert storage is not None
            storage.write_records(
                EXTRACTION_DIAGNOSTICS,
                [
                    {
                        "record_key": "diag-1",
                        "source": "test",
                        "extractor": "test",
                        "status": "rejected",
                        "reason": "fixture_not_mentioned",
                        "fixture_key": run.predictions[0].fixture.key,
                    }
                ],
                source="test",
                run_id=workflow.context.run_id,
            )

            manifest = write_prediction_export(storage, root / "prediction-export.json", export_id="test-export", run_id=workflow.context.run_id)
            payload = json.loads((root / "prediction-export.json").read_text(encoding="utf-8"))

            self.assertEqual(manifest["prediction_count"], 1)
            self.assertEqual(payload["matches"][0]["score_matrix"][0]["home"], 1)
            self.assertEqual(payload["matches"][0]["extraction_diagnostics"][0]["reason"], "fixture_not_mentioned")
            self.assertEqual(len(storage.read_records(PREDICTION_EXPORTS, latest_only=True)), 1)

    def test_prediction_ledger_combines_past_and_future_provider_tips(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            storage = DuckDBStorage.at_data_root(Path(tmp) / "data")
            past_key = "2026-06-13T18:00:00Z|BRA|JPN"
            future_key = "2026-07-10T18:00:00Z|BRA|JPN"
            storage.write_records(
                PREDICTION_BACKTEST,
                [
                    {
                        "record_key": past_key,
                        "fixture_key": past_key,
                        "event_date": "2026-06-13T18:00:00Z",
                        "home_team": "Brazil",
                        "away_team": "Japan",
                        "actual": "2:0",
                        "actual_home": 2,
                        "actual_away": 0,
                        "most_likely": "1:0",
                        "points": 6.0,
                        "prob_home": 0.52,
                        "prob_draw": 0.27,
                        "prob_away": 0.21,
                        "expected_home_goals": 1.23456789012345,
                        "expected_away_goals": 0.87654321098765,
                        "score_matrix": [{"home": 1, "away": 0, "probability": 0.123456789012345}],
                        "optimized_tips": [
                            {"provider": "srf.ch", "fixture_key": past_key, "tip": "1:0", "tip_home": 1, "tip_away": 0, "expected_points": 6.5},
                            {"provider": "20min.ch", "fixture_key": past_key, "tip": "Brazil", "selection": "Brazil", "selection_type": "outcome", "expected_points": 2.6},
                        ],
                    }
                ],
                source="test",
            )
            storage.write_records(
                PREDICTIONS,
                [
                    {
                        "record_key": future_key,
                        "fixture_key": future_key,
                        "event_date": "2026-07-10T18:00:00Z",
                        "home_team": "Brazil",
                        "away_team": "Japan",
                        "most_likely_home": 2,
                        "most_likely_away": 1,
                        "expected_home_goals": 1.77777777777777,
                        "expected_away_goals": 1.11111111111111,
                        "prob_home": 0.55,
                        "prob_draw": 0.25,
                        "prob_away": 0.20,
                        "confidence_percent": 0.55,
                        "score_matrix": [{"home": 2, "away": 1, "probability": 0.098765432109876}],
                    }
                ],
                source="test",
            )
            storage.write_records(
                OPTIMIZED_TIPS,
                [
                    {"record_key": "srf", "provider": "srf.ch", "fixture_key": future_key, "tip": "2:1", "expected_points": 7.0},
                    {"record_key": "20min", "provider": "20min.ch", "fixture_key": future_key, "tip": "Brazil", "selection": "Brazil", "selection_type": "outcome", "expected_points": 2.75},
                ],
                source="test",
            )

            count = write_prediction_ledger(storage, run_id="test-run")
            manifest = write_prediction_export(storage, Path(tmp) / "predictions.json", export_id="test-export", run_id="test-run")
            payload = json.loads((Path(tmp) / "predictions.json").read_text(encoding="utf-8"))
            rows = sorted(storage.read_records(PREDICTION_LEDGER, latest_only=True), key=lambda row: row["status"])

            self.assertEqual(count, 2)
            self.assertEqual(manifest["prediction_count"], 1)
            self.assertEqual(payload["summary"]["prediction_ledger_rows"], 2)
            self.assertEqual({row["status"] for row in rows}, {"past", "future"})
            past = next(row for row in rows if row["status"] == "past")
            future = next(row for row in rows if row["status"] == "future")
            self.assertEqual(past["score_matrix"][0]["probability"], 0.123456789012345)
            self.assertEqual(past["provider_tips"]["20min.ch"]["selection"], "Brazil")
            self.assertEqual(future["srf_tip"], "2:1")
            self.assertEqual(future["twenty_min_tip"], "Brazil")

    def test_prediction_ledger_prefers_published_seed_over_retrospective_backtest(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            storage = DuckDBStorage.at_data_root(Path(tmp) / "data")
            fixture_key = "2026-06-13T18:00:00Z|BRA|JPN"
            storage.write_records(
                PREDICTION_BACKTEST,
                [
                    {
                        "record_key": fixture_key,
                        "fixture_key": fixture_key,
                        "event_date": "2026-06-13T18:00:00Z",
                        "home_team": "Brazil",
                        "away_team": "Japan",
                        "actual": "2:0",
                        "actual_home": 2,
                        "actual_away": 0,
                        "most_likely": "1:0",
                        "points": 0.0,
                        "prob_home": 0.30,
                        "prob_draw": 0.25,
                        "prob_away": 0.45,
                        "expected_home_goals": 0.8,
                        "expected_away_goals": 1.2,
                        "score_matrix": [{"home": 0, "away": 1, "probability": 0.12}],
                        "optimized_tips": [
                            {"provider": "srf.ch", "fixture_key": fixture_key, "tip": "0:1", "tip_home": 0, "tip_away": 1},
                            {
                                "provider": "20min.ch",
                                "fixture_key": fixture_key,
                                "tip": "Japan",
                                "selection": "Japan",
                                "selection_type": "outcome",
                            },
                        ],
                    }
                ],
                source="test",
            )
            storage.write_records(
                PUBLISHED_PREDICTION_SEED,
                [
                    {
                        "record_key": fixture_key,
                        "fixture_key": fixture_key,
                        "event_date": "2026-06-13T18:00:00Z",
                        "home_team": "Brazil",
                        "away_team": "Japan",
                        "actual_score": "2:0",
                        "actual_home": 2,
                        "actual_away": 0,
                        "srf_tip": "1:0",
                        "srf_tip_home": 1,
                        "srf_tip_away": 0,
                        "srf_points": 6.0,
                        "prob_home": 0.55,
                        "prob_draw": 0.25,
                        "prob_away": 0.20,
                        "score_matrix": {
                            "home_goals_axis": [0, 1],
                            "away_goals_axis": [0, 1],
                            "probabilities": [[0.1, 0.2], [0.3, 0.4]],
                        },
                    }
                ],
                source="seed-test",
            )

            write_prediction_ledger(storage, run_id="test-run")
            rows = storage.read_records(PREDICTION_LEDGER, latest_only=True)

            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]["prediction_context"], "archived_pre_refactor_prediction")
            self.assertEqual(rows[0]["srf_tip"], "1:0")
            self.assertEqual(rows[0]["twenty_min_tip"], "Brazil")
            self.assertEqual(rows[0]["provider_tips"]["20min.ch"]["source"], PUBLISHED_PREDICTION_SEED)
            self.assertEqual(rows[0]["metadata"]["twenty_min_source"], "srf_tip_outcome_from_published_prediction_seed")
            self.assertEqual(rows[0]["metadata"]["srf_points"], 6.0)
            self.assertEqual(rows[0]["score_matrix"][3]["home"], 1)

    def test_prediction_ledger_prefers_current_frozen_snapshot_over_published_seed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            storage = DuckDBStorage.at_data_root(Path(tmp) / "data")
            fixture_key = "2026-06-13T18:00:00Z|BRA|JPN"
            storage.write_records(
                PREDICTION_BACKTEST,
                [
                    {
                        "record_key": fixture_key,
                        "fixture_key": fixture_key,
                        "event_date": "2026-06-13T18:00:00Z",
                        "home_team": "Brazil",
                        "away_team": "Japan",
                        "actual": "2:0",
                        "actual_home": 2,
                        "actual_away": 0,
                        "most_likely": "1:0",
                        "points": 0.0,
                        "prob_home": 0.30,
                        "prob_draw": 0.25,
                        "prob_away": 0.45,
                        "optimized_tips": [{"provider": "srf.ch", "fixture_key": fixture_key, "tip": "0:1", "tip_home": 0, "tip_away": 1}],
                    }
                ],
                source="test",
            )
            storage.write_records(
                PUBLISHED_PREDICTION_SEED,
                [{"record_key": fixture_key, "fixture_key": fixture_key, "srf_tip": "1:0", "srf_tip_home": 1, "srf_tip_away": 0}],
                source="seed-test",
            )
            storage.write_records(
                PREDICTION_SNAPSHOTS,
                [
                    {
                        "record_key": f"scheduled_20260613T120000Z:{fixture_key}",
                        "snapshot_id": "scheduled_20260613T120000Z",
                        "snapshot_time_utc": "2026-06-13T12:00:00Z",
                        "fixture_key": fixture_key,
                        "event_date": "2026-06-13T18:00:00Z",
                        "home_team": "Brazil",
                        "away_team": "Japan",
                        "most_likely": "2:0",
                        "prob_home": 0.70,
                        "prob_draw": 0.20,
                        "prob_away": 0.10,
                        "expected_home_goals": 2.0,
                        "expected_away_goals": 0.5,
                        "score_matrix": [{"home": 2, "away": 0, "probability": 0.20}],
                        "optimized_tips": [{"provider": "srf.ch", "fixture_key": fixture_key, "tip": "2:0", "tip_home": 2, "tip_away": 0}],
                    }
                ],
                source="snapshot-test",
            )

            write_prediction_ledger(storage, run_id="test-run")
            rows = storage.read_records(PREDICTION_LEDGER, latest_only=True)

            self.assertEqual(rows[0]["prediction_context"], "frozen_prediction_snapshot_before_kickoff")
            self.assertEqual(rows[0]["srf_tip"], "2:0")

    def test_published_ledger_freezes_prediction_values_and_builds_static_site(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            storage = DuckDBStorage.at_data_root(root / "data")
            fixture_key = "2026-07-10T18:00:00Z|BRA|JPN"
            storage.write_records(
                PREDICTION_LEDGER,
                [
                    {
                        "record_key": fixture_key,
                        "fixture_key": fixture_key,
                        "event_date": "2026-07-10T18:00:00Z",
                        "home_team": "Brazil",
                        "away_team": "Japan",
                        "status": "future",
                        "prediction_context": "latest_live_prediction",
                        "predicted_home_goals": 1.23456789012345,
                        "predicted_away_goals": 0.87654321098765,
                        "most_likely_score": "1:0",
                        "most_likely_home": 1,
                        "most_likely_away": 0,
                        "prob_home": 0.52,
                        "prob_draw": 0.27,
                        "prob_away": 0.21,
                        "confidence_label": "Medium",
                        "confidence_percent": 0.52,
                        "score_matrix": [{"home": 1, "away": 0, "probability": 0.123456789012345}],
                        "provider_tips": {"srf.ch": {"tip": "1:0"}},
                        "srf_tip": "1:0",
                        "twenty_min_tip": "Brazil",
                    }
                ],
                source="test",
            )
            write_published_prediction_ledger(storage, run_id="first")
            storage.write_records(
                PREDICTION_LEDGER,
                [
                    {
                        "record_key": fixture_key,
                        "fixture_key": fixture_key,
                        "event_date": "2026-07-10T18:00:00Z",
                        "home_team": "Brazil",
                        "away_team": "Japan",
                        "status": "past",
                        "prediction_context": "retrospective_current_model_before_kickoff",
                        "actual_score": "2:0",
                        "actual_home": 2,
                        "actual_away": 0,
                        "predicted_home_goals": 4.0,
                        "predicted_away_goals": 4.0,
                        "most_likely_score": "4:4",
                        "most_likely_home": 4,
                        "most_likely_away": 4,
                        "prob_home": 0.10,
                        "prob_draw": 0.80,
                        "prob_away": 0.10,
                        "confidence_label": "High",
                        "confidence_percent": 0.80,
                        "score_matrix": [{"home": 4, "away": 4, "probability": 0.99}],
                        "srf_tip": "4:4",
                        "twenty_min_tip": "Draw",
                    }
                ],
                source="test",
            )

            write_published_prediction_ledger(storage, run_id="final")
            storage.write_records(
                PROVIDER_POINTS,
                [
                    {"record_key": "srf:1", "provider": "srf.ch", "fixture_key": fixture_key, "points": 10.0, "cumulative_points": 10.0},
                    {"record_key": "20min:1", "provider": "20min.ch", "fixture_key": fixture_key, "points": 3.0, "cumulative_points": 3.0},
                ],
                source="test",
            )
            rows = storage.read_records(PUBLISHED_PREDICTION_LEDGER, latest_only=True)
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]["status"], "final")
            self.assertEqual(rows[0]["actual_score"], "2:0")
            self.assertEqual(rows[0]["most_likely_score"], "1:0")
            self.assertEqual(rows[0]["score_matrix"][0]["probability"], 0.123456789012345)

            result = build_site(project_root=root, storage=storage, gtm_container_id="", base_url="http://127.0.0.1:8000/")
            self.assertEqual(result.row_count, 1)
            self.assertTrue((result.output_dir / "index.html").exists())
            self.assertTrue((result.output_dir / "de" / "index.html").exists())
            self.assertTrue((result.output_dir / "en" / "index.html").exists())
            self.assertTrue((result.output_dir / "de" / "spiele" / "kommende" / "index.html").exists())
            self.assertTrue((result.output_dir / "de" / "spiele" / "vergangene" / "index.html").exists())
            self.assertTrue((result.output_dir / "en" / "matches" / "future" / "index.html").exists())
            self.assertTrue((result.output_dir / "en" / "matches" / "past" / "index.html").exists())
            self.assertTrue((result.output_dir / "api" / "predictions").exists())
            self.assertEqual(len(list((result.output_dir / "assets").glob("site.*.css"))), 1)
            self.assertEqual(len(list((result.output_dir / "assets").glob("theme.*.js"))), 1)
            self.assertTrue((result.output_dir / "assets" / "fonts" / "CadizWeb-Regular.woff2").exists())
            self.assertTrue((result.output_dir / "assets" / "fonts" / "Degular-Regular.woff2").exists())
            self.assertIn("assets/fonts/CadizWeb-Regular.woff2", result.asset_files)
            self.assertIn("assets/fonts/Degular-Regular.woff2", result.asset_files)
            redirect_html = (result.output_dir / "index.html").read_text(encoding="utf-8")
            html = (result.output_dir / "de" / "index.html").read_text(encoding="utf-8")
            en_html = (result.output_dir / "en" / "index.html").read_text(encoding="utf-8")
            de_future_html = (result.output_dir / "de" / "spiele" / "kommende" / "index.html").read_text(encoding="utf-8")
            de_past_html = (result.output_dir / "de" / "spiele" / "vergangene" / "index.html").read_text(encoding="utf-8")
            en_future_html = (result.output_dir / "en" / "matches" / "future" / "index.html").read_text(encoding="utf-8")
            en_past_html = (result.output_dir / "en" / "matches" / "past" / "index.html").read_text(encoding="utf-8")
            css = next((result.output_dir / "assets").glob("site.*.css")).read_text(encoding="utf-8")
            js = next((result.output_dir / "assets").glob("theme.*.js")).read_text(encoding="utf-8")
            detail = (result.output_dir / "de" / "spiele" / "2026-07-10-bra-jpn" / "index.html").read_text(encoding="utf-8")
            en_detail = (result.output_dir / "en" / "matches" / "2026-07-10-bra-jpn" / "index.html").read_text(encoding="utf-8")
            payload = json.loads((result.output_dir / "api" / "predictions").read_text(encoding="utf-8"))
            self.assertIn('meta http-equiv="refresh" content="0; url=/en/"', redirect_html)
            self.assertIn('helga_language', redirect_html)
            self.assertIn('navigator.languages', redirect_html)
            self.assertIn('<html lang="de">', html)
            self.assertIn('<html lang="en">', en_html)
            self.assertIn('<link rel="canonical" href="http://127.0.0.1:8000/de/">', html)
            self.assertIn('<link rel="alternate" hreflang="en" href="http://127.0.0.1:8000/en/">', html)
            self.assertIn('<link rel="alternate" hreflang="x-default" href="http://127.0.0.1:8000/en/">', html)
            self.assertIn('<meta property="og:site_name" content="WM 2026 Prognosen">', html)
            self.assertIn('<meta property="og:url" content="http://127.0.0.1:8000/de/">', html)
            self.assertIn('<meta property="og:locale" content="de_CH">', html)
            self.assertIn('<meta name="twitter:card" content="summary">', html)
            self.assertIn("World Cup 2026 Predictions", en_html)
            self.assertIn("Upcoming Matches", en_html)
            self.assertIn("Past matches", en_html)
            self.assertIn('<link rel="canonical" href="http://127.0.0.1:8000/de/spiele/kommende">', de_future_html)
            self.assertIn('<link rel="alternate" hreflang="en" href="http://127.0.0.1:8000/en/matches/future">', de_future_html)
            self.assertIn('<link rel="alternate" hreflang="x-default" href="http://127.0.0.1:8000/en/matches/future">', de_future_html)
            self.assertIn('<a class="language-switch__link" href="/en/matches/future" lang="en"', de_future_html)
            self.assertIn('<title>Kommende Spiele — WM 2026 Prognosen</title>', de_future_html)
            self.assertIn('<link rel="canonical" href="http://127.0.0.1:8000/de/spiele/vergangene">', de_past_html)
            self.assertIn('<link rel="alternate" hreflang="en" href="http://127.0.0.1:8000/en/matches/past">', de_past_html)
            self.assertIn('<link rel="canonical" href="http://127.0.0.1:8000/en/matches/future">', en_future_html)
            self.assertIn('<link rel="canonical" href="http://127.0.0.1:8000/en/matches/past">', en_past_html)
            self.assertIn("helga_theme", html)
            self.assertIn("helga_language=de", html)
            self.assertIn('document.documentElement.dataset.js = "true";', html)
            self.assertIn('data-menu-toggle', html)
            self.assertIn('<nav id="site-menu" class="site-menu" data-site-menu data-state="closed" aria-label="Hauptnavigation">', html)
            self.assertIn('<div class="site-menu__surface" data-site-menu-surface>', html)
            self.assertIn('<a class="site-menu__link" href="/de/" aria-current="page">', html)
            self.assertIn('<a class="site-menu__link" href="/de/spiele/kommende">', html)
            self.assertIn('<a class="site-menu__link" href="/de/spiele/vergangene">', html)
            self.assertIn('<a class="site-menu__link" href="/de/turnier">', html)
            self.assertIn('<a class="site-menu__link" href="/de/spiele/kommende" aria-current="page">', de_future_html)
            self.assertIn("data-theme-toggle", html)
            self.assertIn("Dark Mode", html)
            generated_at_zurich = dt.datetime.fromisoformat(result.generated_at_utc.replace("Z", "+00:00")).astimezone(
                ZoneInfo("Europe/Zurich")
            )
            self.assertIn(
                f'Letztes Update: <time datetime="{result.generated_at_utc}">{generated_at_zurich:%d.%m.%Y, %H:%M:%S}</time>',
                html,
            )
            self.assertNotIn("<span>Tippspiel Prognosen</span>", html)
            self.assertIn(
                '<nav class="breadcrumb" aria-label="Breadcrumb">\n'
                '  <a class="breadcrumb__link" href="/de/" aria-current="page">Prognosen</a>\n'
                "</nav>",
                html,
            )
            self.assertIn('<header class="page-intro" aria-labelledby="page-title">', html)
            self.assertIn(
                '<p class="page-intro__lead">Provider-neutrale Score-Prognosen, SRF-Optimierung und 20min-Optimierung aus dem aktuellen Datenlauf.</p>',
                html,
            )
            self.assertNotIn("Der gesamte Code ist öffentlich", html)
            self.assertNotIn("The full code is public", en_html)
            self.assertIn('<section class="section" aria-labelledby="summary-title">', html)
            self.assertIn('<h2 id="summary-title" class="visually-hidden">Prognosen Übersicht</h2>', html)
            self.assertIn('<dl class="summary">', html)
            self.assertIn('<dt class="summary__label">SRF Punkte</dt>', html)
            self.assertIn('<dd class="summary__value">6</dd>', html)
            self.assertIn('<dt class="summary__label">20min Punkte</dt>', html)
            self.assertIn('<dd class="summary__value">5</dd>', html)
            self.assertIn('<dt class="summary__label">Trefferquote</dt>', html)
            self.assertIn('<dd class="summary__value">100%</dd>', html)
            self.assertIn("0 exakt · 1 richtig · 0 falsch", html)
            self.assertIn('<dd class="summary__hitbar" aria-hidden="true">', html)
            self.assertIn('<span class="summary__hitbar-fill" data-result="exact" style="width: 0.00%"></span>', html)
            self.assertIn('<span class="summary__hitbar-fill" data-result="trend" style="width: 100.00%"></span>', html)
            self.assertIn('<span class="summary__hitbar-fill" data-result="miss" style="width: 0.00%"></span>', html)
            self.assertIn('<dt class="summary__label">Offene Tipps</dt>', html)
            self.assertIn("Ø 6.0/Spiel", html)
            self.assertIn("Hit rate", en_html)
            self.assertIn("0 exact scores · 1 correct outcomes · 0 wrong outcomes", en_html)
            self.assertIn("prefers-color-scheme", js)
            self.assertIn("Max-Age=31536000", js)
            self.assertIn("/assets/fonts/CadizWeb-Regular.woff2", css)
            self.assertIn("/assets/fonts/Degular-Regular.woff2", css)
            self.assertNotIn("helga.ch/themes/custom/customer/dist/webfonts", css)
            self.assertNotIn("Aktion", html)
            self.assertNotIn(">Details</a>", html)
            self.assertIn("Die Vorhersagen und Tipps können sich bis zum jeweiligen Spielstart noch verändern.", html)
            self.assertIn("JSON API", html)
            self.assertIn('href="/api/predictions"', html)
            rendered_header = html.split("</header>", 1)[0]
            self.assertNotIn('href="/api/predictions"', rendered_header)
            self.assertNotIn("language-switch", rendered_header)
            self.assertNotIn("data-theme-toggle", rendered_header)
            self.assertIn('<nav class="resources" aria-label="Ressourcen">', html)
            self.assertIn('<a class="resources__link" href="/de/turnier">Turnierprognose</a>', html)
            self.assertIn(
                '<a class="resources__link" href="/api/predictions" target="_blank" rel="noopener" data-analytics-event="helga_api_click">JSON API</a>',
                html,
            )
            future_section = html.split(
                '<section class="section" aria-labelledby="future-title">', 1
            )[1].split(
                '<section class="section" aria-labelledby="past-title">',
                1,
            )[0]
            past_section = html.split('<section class="section" aria-labelledby="past-title">', 1)[1]
            self.assertIn('<p class="section__actions">', future_section)
            self.assertIn('<a class="content-link" href="/de/spiele/kommende">Alle kommenden Spiele</a>', future_section)
            self.assertIn('<p class="section__actions">', past_section)
            self.assertIn('<a class="content-link" href="/de/spiele/vergangene">Alle vergangenen Spiele</a>', past_section)
            self.assertIn('<a class="match-row" href="/de/spiele/2026-07-10-bra-jpn/" data-analytics-event="helga_match_open">', past_section)
            self.assertIn("SRF Tipp 1:0", past_section)
            self.assertIn("<strong>2:0</strong>", past_section)
            self.assertIn("SRF +6 · 20min +5", past_section)
            self.assertIn('<span class="hit-chip" data-result="trend">Richtig</span>', past_section)
            self.assertIn("🇧🇷", past_section)
            self.assertNotIn('href="/">Prognosen</a>', html)
            self.assertIn('href="/de/" lang="de"', html)
            self.assertIn('aria-current="true">DE</a>', html)
            self.assertIn('href="/en/" lang="en"', html)
            self.assertIn('href="/de/spiele/2026-07-10-bra-jpn/"', html)
            self.assertIn('href="/en/matches/2026-07-10-bra-jpn/"', en_html)
            tournament_html = (result.output_dir / "de" / "turnier" / "index.html").read_text(encoding="utf-8")
            en_tournament_html = (result.output_dir / "en" / "tournament" / "index.html").read_text(encoding="utf-8")
            self.assertIn("Wer wird Weltmeister?", tournament_html)
            self.assertIn("Who wins the World Cup?", en_tournament_html)
            self.assertIn('<link rel="canonical" href="http://127.0.0.1:8000/de/turnier">', tournament_html)
            self.assertIn('<link rel="alternate" hreflang="en" href="http://127.0.0.1:8000/en/tournament">', tournament_html)
            self.assertIn("Zurzeit keine Turnier-Wahrscheinlichkeiten verfügbar.", tournament_html)
            self.assertIn("/de/turnier", (result.output_dir / "sitemap.xml").read_text(encoding="utf-8"))
            self.assertIn('<title>Brasilien - Japan — WM 2026 Prognosen</title>', detail)
            self.assertIn('"@type": "SportsEvent"', detail)
            self.assertIn('"startDate": "2026-07-10T18:00:00Z"', detail)
            self.assertIn('<h1 id="match-title" class="detail-hero__title">', detail)
            h1_match_name = detail.split('<h1 id="match-title" class="detail-hero__title">', 1)[1].split("</h1>", 1)[0]
            self.assertIn("Brasilien – Japan", h1_match_name)
            self.assertIn('<span class="detail-hero__flag" aria-hidden="true">🇧🇷</span>', h1_match_name)
            self.assertIn(
                "Brazil – Japan",
                en_detail.split('<h1 id="match-title" class="detail-hero__title">', 1)[1].split("</h1>", 1)[0],
            )
            self.assertIn('<div class="prob__bar" role="img"', detail)
            self.assertIn("Sieg Brasilien 🇧🇷 52%", detail)
            self.assertIn("Unentschieden 27%", detail)
            self.assertIn("Brazil win 🇧🇷 52%", en_detail)
            self.assertIn("Der SRF-Tipp entspricht dem wahrscheinlichsten Resultat 1:0 (12%).", detail)
            self.assertIn("Fr, 10.07.2026, 20:00", detail)
            self.assertIn("Fri, 10.07.2026, 20:00", en_detail)
            self.assertIn('xG 1.23:0.88', detail)
            self.assertIn('Sicherheit: <span class="numeric">Mittel (52.0%)</span>', detail)
            self.assertIn('<span class="verdict__score numeric">2:0</span>', detail)
            self.assertIn('<span class="hit-chip" data-result="trend">Richtig</span>', detail)
            self.assertIn("+6 P.", detail)
            self.assertIn("Resultat-Matrix", detail)
            self.assertIn('<table class="heatmap__table">', detail)
            self.assertIn('class="heatmap__cell" data-hot="true" data-most-likely="true"', detail)
            self.assertIn('title="1:0 — 12.3%"', detail)
            self.assertIn('data-actual="true"', detail)
            self.assertIn("Zeilen: Tore Brasilien · Spalten: Tore Japan", detail)
            self.assertIn("Rows: Brazil goals · Columns: Japan goals", en_detail)
            self.assertIn("Grün markiert: tatsächliches Resultat.", detail)
            self.assertIn("Zurück zur Übersicht", detail)
            self.assertIn('<a class="content-link" href="/de/">Zurück zur Übersicht</a>', detail)
            self.assertIn('<a class="breadcrumb__link" href="/de/">Prognosen</a>', detail)
            self.assertIn('<a class="breadcrumb__link" href="/de/spiele/2026-07-10-bra-jpn/" aria-current="page"><span class="match-name">', detail)
            self.assertIn('<link rel="canonical" href="http://127.0.0.1:8000/de/spiele/2026-07-10-bra-jpn/">', detail)
            self.assertIn('<link rel="alternate" hreflang="en" href="http://127.0.0.1:8000/en/matches/2026-07-10-bra-jpn/">', detail)
            self.assertIn("--space-lg: 24px;", css)
            self.assertIn("--space-4: 4px;", css)
            self.assertIn("--motion-nav-surface: 420ms;", css)
            self.assertIn("--motion-nav-content-opacity: 220ms;", css)
            self.assertIn("--motion-nav-content-transform: 260ms;", css)
            self.assertIn("--summary-max: calc(var(--max) - (var(--space-lg) * 8));", css)
            self.assertIn('html[data-theme="dark"]', css)
            self.assertIn("--helga-blue: #8fa2ff;", css)
            self.assertIn("--accent: #399918;", css)
            self.assertIn("--accent-soft: #b4e50d;", css)
            self.assertIn("--danger: #fb4141;", css)
            self.assertIn("--danger-soft: #ff9b2f;", css)
            self.assertIn(".section", css)
            self.assertIn(".page-intro", css)
            self.assertIn("font-size: clamp(2rem, 5.4vw, 5rem);", css)
            self.assertIn("overflow-y: scroll;", css)
            self.assertIn("scrollbar-gutter: stable;", css)
            self.assertIn("transform: translate3d(0, -100%, 0);", css)
            self.assertIn("transform var(--motion-nav-surface) cubic-bezier(0.22, 1, 0.36, 1)", css)
            self.assertIn("visibility: hidden;", css)
            self.assertIn('body[data-menu-open="true"] .site-header', css)
            self.assertIn("padding-top: var(--header-height);", css)
            self.assertIn("transform: translateX(-50%);", css)
            self.assertIn('html[data-js="true"] .site-menu[data-state="closing"]', css)
            self.assertIn('html[data-js="true"] .site-menu__surface {\n  width: min(100% - 32px, var(--max));', css)
            self.assertIn(".site-menu__inner {\n  width: 100%;", css)
            self.assertIn("contain: paint;", css)
            self.assertIn(".site-menu__link .icon", css)
            self.assertIn("color: currentColor;", css)
            self.assertIn('.site-menu__link[aria-current="page"]', css)
            self.assertIn(".summary", css)
            self.assertIn("max-width: var(--summary-max);", css)
            self.assertIn("margin: 0 auto;", css)
            self.assertIn(".summary__card", css)
            self.assertIn(".summary__hitbar", css)
            self.assertIn('.summary__hitbar-fill[data-result="miss"]', css)
            self.assertIn(".match-card", css)
            self.assertIn(".match-card__teams", css)
            self.assertIn(".tip-chip", css)
            self.assertIn(".prob__bar", css)
            self.assertIn('.prob__segment[data-kind="home"]', css)
            self.assertIn(".match-rows", css)
            self.assertIn(".hit-chip", css)
            self.assertIn(".detail-hero", css)
            self.assertIn(".verdict", css)
            self.assertIn(".heatmap__table", css)
            self.assertIn("color-mix(in srgb, var(--helga-blue) var(--heat, 0%), var(--mist))", css)
            self.assertIn(".odds__row", css)
            self.assertIn(".breadcrumb__link", css)
            self.assertIn(".content-link", css)
            self.assertIn(".language-switch", css)
            self.assertIn('.language-switch__link[aria-current="true"]', css)
            self.assertIn("@media (max-width: 900px)", css)
            self.assertIn("@media (max-width: 760px)", css)
            self.assertIn("@media (prefers-reduced-motion: reduce)", css)
            self.assertIn("--header-height: 72px;", css)
            self.assertIn("inset: var(--header-height) 0 0;", css)
            self.assertIn("width: 88px;", css)
            self.assertNotIn("min-width: 820px", css)
            self.assertNotIn(".details-link", css)
            self.assertNotIn(".table-wrap", css)
            self.assertNotIn(".detail-metrics", css)
            self.assertNotIn(".detail-probabilities", css)
            self.assertIn('menu.dataset.state = "opening";', js)
            self.assertIn('menu.dataset.state = "closing";', js)
            self.assertIn("function trapMenuFocus(event)", js)
            self.assertIn('menuSurface.addEventListener("transitionend"', js)
            self.assertEqual(payload["predictions"][0]["home_team"], "Brazil")
            self.assertEqual(payload["predictions"][0]["home_team_id"], "BRA")
            self.assertEqual(payload["predictions"][0]["home_fifa_code"], "BRA")
            self.assertEqual(payload["predictions"][0]["predicted_home_goals"], 1.23456789012345)
            self.assertEqual(payload["predictions"][0]["20min_tip"], "Brazil")
            self.assertEqual(
                payload["predictions"][0]["detail_urls"],
                {
                    "de": "http://127.0.0.1:8000/de/spiele/2026-07-10-bra-jpn/",
                    "en": "http://127.0.0.1:8000/en/matches/2026-07-10-bra-jpn/",
                },
            )
            self.assertNotIn('"twenty_min_', json.dumps(payload, sort_keys=True))
            self.assertNotIn("metadata", payload["predictions"][0])
            for presentation_key in (
                "actual_score",
                "actual_score_label",
                "alternate_links",
                "away_flag",
                "away_team_label",
                "confidence_text",
                "current_url",
                "detail_path",
                "expected_score_display",
                "expected_score_full",
                "hda_title",
                "hda_parts",
                "home_flag",
                "home_team_label",
                "language_switch_links",
                "match",
                "match_display",
                "most_likely_score",
                "provider_tips",
                "record_key",
                "srf_account_display",
                "srf_tip_label",
                "status_label",
                "top_score_matrix",
                "twenty_min_account_display",
                "twenty_min_tip_label",
            ):
                self.assertNotIn(presentation_key, payload["predictions"][0])
            self.assertEqual(payload["summary"]["srf_points"], 6.0)
            self.assertEqual(payload["summary"]["20min_points"], 5.0)
            self.assertEqual(payload["summary"]["srf_points_display"], "6")
            self.assertEqual(payload["summary"]["20min_points_display"], "5")

    def test_published_ledger_replaces_archived_rows_with_corrected_twenty_min_source(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            storage = DuckDBStorage.at_data_root(Path(tmp) / "data")
            fixture_key = "2026-06-13T18:00:00Z|BRA|JPN"
            storage.write_records(
                PUBLISHED_PREDICTION_LEDGER,
                [
                    {
                        "record_key": fixture_key,
                        "fixture_key": fixture_key,
                        "event_date": "2026-06-13T18:00:00Z",
                        "home_team": "Brazil",
                        "away_team": "Japan",
                        "status": "final",
                        "prediction_context": "archived_pre_refactor_prediction",
                        "actual_score": "2:0",
                        "actual_home": 2,
                        "actual_away": 0,
                        "srf_tip": "1:0",
                        "twenty_min_tip": "Japan",
                        "metadata": {},
                    }
                ],
                source="old-site-row",
            )
            storage.write_records(
                PREDICTION_LEDGER,
                [
                    {
                        "record_key": fixture_key,
                        "fixture_key": fixture_key,
                        "event_date": "2026-06-13T18:00:00Z",
                        "home_team": "Brazil",
                        "away_team": "Japan",
                        "status": "past",
                        "prediction_context": "archived_pre_refactor_prediction",
                        "actual_score": "2:0",
                        "actual_home": 2,
                        "actual_away": 0,
                        "srf_tip": "1:0",
                        "twenty_min_tip": "Brazil",
                        "metadata": {"twenty_min_source": "srf_tip_outcome_from_published_prediction_seed"},
                    }
                ],
                source="corrected-ledger-row",
            )

            write_published_prediction_ledger(storage, run_id="corrected")
            rows = storage.read_records(PUBLISHED_PREDICTION_LEDGER, latest_only=True)

            self.assertEqual(rows[0]["twenty_min_tip"], "Brazil")
            self.assertEqual(rows[0]["metadata"]["twenty_min_source"], "srf_tip_outcome_from_published_prediction_seed")
            self.assertTrue(rows[0]["metadata"]["replaced_retrospective_prediction"])

    def test_site_scores_knockout_rows_from_published_metadata_phase(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            storage = DuckDBStorage.at_data_root(root / "data")
            fixture_key = "2026-06-28T19:00:00Z|RSA|CAN"
            storage.write_records(
                PUBLISHED_PREDICTION_LEDGER,
                [
                    {
                        "record_key": fixture_key,
                        "fixture_key": fixture_key,
                        "event_date": "2026-06-28T19:00:00Z",
                        "home_team": "South Africa",
                        "away_team": "Canada",
                        "status": "final",
                        "actual_score": "0:1",
                        "actual_home": 0,
                        "actual_away": 1,
                        "srf_tip": "0:1",
                        "twenty_min_tip": "Canada",
                        "metadata": {"phase": "knockout_stage"},
                    }
                ],
                source="test",
            )

            result = build_site(project_root=root, storage=storage, gtm_container_id="", base_url="https://tippspiel.helga.ch")
            payload = json.loads((result.output_dir / "api" / "predictions").read_text(encoding="utf-8"))

            self.assertEqual(payload["summary"]["srf_points"], 20.0)
            self.assertEqual(payload["summary"]["srf_points_display"], "20")

    def test_site_counts_unpredicted_fixtures_and_keeps_locked_rows_out_of_upcoming_section(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            storage = DuckDBStorage.at_data_root(root / "data")
            future_key = "2026-07-10T18:00:00Z|BRA|JPN"
            locked_key = "2026-06-29T20:30:00Z|GER|PAR"
            final_key = "2026-06-11T19:00:00Z|MEX|RSA"
            storage.write_records(
                PUBLISHED_PREDICTION_LEDGER,
                [
                    {
                        "record_key": future_key,
                        "fixture_key": future_key,
                        "event_date": "2026-07-10T18:00:00Z",
                        "home_team": "Brazil",
                        "away_team": "Japan",
                        "status": "future",
                        "most_likely_home": 1,
                        "most_likely_away": 0,
                    },
                    {
                        "record_key": locked_key,
                        "fixture_key": locked_key,
                        "event_date": "2026-06-29T20:30:00Z",
                        "home_team": "Germany",
                        "away_team": "Paraguay",
                        "status": "locked",
                        "most_likely_home": 2,
                        "most_likely_away": 1,
                    },
                    {
                        "record_key": final_key,
                        "fixture_key": final_key,
                        "event_date": "2026-06-11T19:00:00Z",
                        "home_team": "Mexico",
                        "away_team": "South Africa",
                        "status": "final",
                        "actual_score": "2:0",
                        "most_likely_home": 1,
                        "most_likely_away": 0,
                    },
                ],
                source="test",
            )
            storage.write_records(
                TOURNAMENT_FIXTURES,
                [
                    {
                        "record_key": "2026-07-04T17:00:00Z|CAN|Sieger Sechzehntelfinal 4",
                        "fixture_key": "2026-07-04T17:00:00Z|CAN|Sieger Sechzehntelfinal 4",
                        "event_date": "2026-07-04T17:00:00Z",
                        "home_team": "Canada",
                        "away_team": "Sieger Sechzehntelfinal 4",
                        "home_fifa_code": "CAN",
                        "away_fifa_code": None,
                        "stage": "knockout",
                        "status": "open",
                        "metadata": {"source": "srf_public"},
                    }
                ],
                source="test",
            )

            result = build_site(project_root=root, storage=storage, gtm_container_id="", base_url="https://tippspiel.helga.ch")
            html = (result.output_dir / "de" / "index.html").read_text(encoding="utf-8")
            payload = json.loads((result.output_dir / "api" / "predictions").read_text(encoding="utf-8"))
            future_section = html.split('id="future-title"', 1)[1].split('id="past-title"', 1)[0]
            tipped_section = html.split('id="past-title"', 1)[1]

            self.assertEqual(result.row_count, 4)
            self.assertEqual(result.future_count, 2)
            self.assertEqual(result.locked_count, 1)
            self.assertEqual(result.final_count, 1)
            self.assertEqual(payload["summary"]["future"], 2)
            self.assertEqual(payload["summary"]["tipped"], 2)
            self.assertEqual(len(payload["predictions"]), 4)
            self.assertIn('<span class="match-card__team-name">Sieger Sechzehntelfinal 4</span>', future_section)
            self.assertIn('href="/de/spiele/2026-07-04-can-w76/"', future_section)
            self.assertIn('href="/de/spiele/2026-07-10-bra-jpn/"', future_section)
            self.assertNotIn('href="/de/spiele/2026-06-29-ger-par/"', future_section)
            self.assertIn('href="/de/spiele/2026-06-29-ger-par/"', tipped_section)
            self.assertLess(
                tipped_section.index('href="/de/spiele/2026-06-29-ger-par/"'),
                tipped_section.index('href="/de/spiele/2026-06-11-mex-rsa/"'),
            )
            placeholder = next(row for row in payload["predictions"] if row["event_date"] == "2026-07-04T17:00:00Z")
            self.assertFalse(placeholder["prediction_available"])
            self.assertEqual(placeholder["fixture_key"], "2026-07-04T17:00:00Z|CAN|W76")
            self.assertEqual(placeholder["away_team"], "W76")
            self.assertEqual(placeholder["away_team_id"], "W76")
            self.assertIsNone(placeholder["away_fifa_code"])
            self.assertEqual(
                placeholder["detail_urls"],
                {
                    "de": "https://tippspiel.helga.ch/de/spiele/2026-07-04-can-w76/",
                    "en": "https://tippspiel.helga.ch/en/matches/2026-07-04-can-w76/",
                },
            )
            self.assertNotIn("Sieger", json.dumps(payload, ensure_ascii=False))
            self.assertTrue((result.output_dir / "de" / "spiele" / "2026-07-04-can-w76" / "index.html").exists())
            self.assertTrue((result.output_dir / "en" / "matches" / "2026-07-04-can-w76" / "index.html").exists())

    def test_site_ignores_stale_fixture_placeholders_from_old_source_batches(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            storage = DuckDBStorage.at_data_root(root / "data")
            storage.write_records(
                TOURNAMENT_FIXTURES,
                [
                    {
                        "record_key": "2026-07-04T17:00:00Z|CAN|W75",
                        "fixture_key": "2026-07-04T17:00:00Z|CAN|W75",
                        "event_date": "2026-07-04T17:00:00Z",
                        "home_team": "Canada",
                        "away_team": "W75",
                        "home_fifa_code": "CAN",
                        "away_fifa_code": None,
                        "stage": "knockout",
                        "status": "open",
                        "metadata": {"source": "srf_public"},
                    }
                ],
                source="srf_public",
                run_id="old",
            )
            con = storage._connect()
            try:
                con.execute(
                    """
                    UPDATE structured_records
                    SET observed_at_utc = '2026-01-01T00:00:00Z'
                    WHERE dataset = 'tournament_fixtures' AND source = 'srf_public'
                    """
                )
            finally:
                con.close()
            storage.write_records(
                TOURNAMENT_FIXTURES,
                [
                    {
                        "record_key": "2026-07-04T17:00:00Z|CAN|Sieger Sechzehntelfinal 4",
                        "fixture_key": "2026-07-04T17:00:00Z|CAN|Sieger Sechzehntelfinal 4",
                        "event_date": "2026-07-04T17:00:00Z",
                        "home_team": "Canada",
                        "away_team": "Sieger Sechzehntelfinal 4",
                        "home_fifa_code": "CAN",
                        "away_fifa_code": None,
                        "stage": "knockout",
                        "status": "open",
                        "metadata": {"source": "srf_public"},
                    }
                ],
                source="srf_public",
                run_id="current",
            )

            result = build_site(project_root=root, storage=storage, gtm_container_id="")
            html = (result.output_dir / "de" / "index.html").read_text(encoding="utf-8")
            payload = json.loads((result.output_dir / "api" / "predictions").read_text(encoding="utf-8"))

            self.assertEqual(result.future_count, 1)
            self.assertEqual(payload["summary"]["future"], 1)
            self.assertIn('<span class="match-card__team-name">Kanada</span>', html)
            self.assertIn('<span class="match-card__team-name">Sieger Sechzehntelfinal 4</span>', html)
            self.assertNotIn("W75", html)
            self.assertEqual(payload["predictions"][0]["fixture_key"], "2026-07-04T17:00:00Z|CAN|W76")
            self.assertEqual(payload["predictions"][0]["away_team"], "W76")
            self.assertEqual(payload["predictions"][0]["away_team_id"], "W76")
            self.assertIsNone(payload["predictions"][0]["away_fifa_code"])
            self.assertNotIn("Sieger", json.dumps(payload, ensure_ascii=False))

    def test_site_ignores_unpredicted_placeholder_when_prediction_covers_same_slot(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            storage = DuckDBStorage.at_data_root(root / "data")
            published_key = "2026-07-04T17:00:00Z|CAN|MAR"
            storage.write_records(
                PUBLISHED_PREDICTION_LEDGER,
                [
                    {
                        "record_key": published_key,
                        "fixture_key": published_key,
                        "event_date": "2026-07-04T17:00:00Z",
                        "home_team": "Canada",
                        "away_team": "Morocco",
                        "status": "future",
                        "prediction_context": "latest_live_prediction",
                        "most_likely_home": 0,
                        "most_likely_away": 1,
                    }
                ],
                source="published_prediction_ledger",
            )
            storage.write_records(
                TOURNAMENT_FIXTURES,
                [
                    {
                        "record_key": "2026-07-04T17:00:00Z|CAN|W75",
                        "fixture_key": "2026-07-04T17:00:00Z|CAN|W75",
                        "event_date": "2026-07-04T17:00:00Z",
                        "home_team": "Canada",
                        "away_team": "W75",
                        "home_fifa_code": "CAN",
                        "away_fifa_code": None,
                        "stage": "Round of 16",
                        "status": "scheduled",
                        "source_id": "90",
                        "metadata": {"source": "openfootball/worldcup:cup_finals.txt", "match_number": "90"},
                    },
                    {
                        "record_key": "2026-07-04T17:00:00Z|PAR|W76",
                        "fixture_key": "2026-07-04T17:00:00Z|PAR|W76",
                        "event_date": "2026-07-04T17:00:00Z",
                        "home_team": "Paraguay",
                        "away_team": "W76",
                        "home_fifa_code": "PAR",
                        "away_fifa_code": None,
                        "stage": "Round of 16",
                        "status": "scheduled",
                        "source_id": "91",
                        "metadata": {"source": "openfootball/worldcup:cup_finals.txt", "match_number": "91"},
                    },
                ],
                source="openfootball/worldcup:cup_finals.txt",
            )

            result = build_site(project_root=root, storage=storage, gtm_container_id="")
            html = (result.output_dir / "de" / "index.html").read_text(encoding="utf-8")
            en_html = (result.output_dir / "en" / "index.html").read_text(encoding="utf-8")
            payload = json.loads((result.output_dir / "api" / "predictions").read_text(encoding="utf-8"))

            self.assertEqual(result.row_count, 2)
            self.assertEqual(result.future_count, 2)
            self.assertIn('<span class="match-card__team-name">Marokko</span>', html)
            self.assertIn('<span class="match-card__team-name">Sieger Sechzehntelfinal 4</span>', html)
            self.assertIn('<span class="match-card__team-name">Morocco</span>', en_html)
            self.assertIn('<span class="match-card__team-name">Winner Round of 32 match 4</span>', en_html)
            self.assertNotIn("W75", html)
            self.assertEqual({row["fixture_key"] for row in payload["predictions"]}, {published_key, "2026-07-04T17:00:00Z|PAR|W76"})

    def test_caddy_serves_extensionless_api_as_inline_json(self) -> None:
        caddyfile = Path(__file__).resolve().parents[1].joinpath("Caddyfile").read_text(encoding="utf-8")

        self.assertIn("@json path /api/* /site-manifest.json", caddyfile)
        self.assertIn("@html path / /index.html /de* /en*", caddyfile)
        self.assertIn('>Content-Type "application/json; charset=utf-8"', caddyfile)
        self.assertIn('Content-Disposition "inline"', caddyfile)
        self.assertIn('X-Content-Type-Options "nosniff"', caddyfile)

    def test_baseline_bundle_writes_manifest_and_fingerprints(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            workflow = _workflow(root)
            run = workflow.next_predictions(limit=1)
            storage = workflow.context.storage
            assert storage is not None

            manifest = create_baseline_bundle(
                project_root=root,
                storage=storage,
                run=run,
                plugins=workflow.manager.plugins,
                baseline_id="test-baseline",
            )

            bundle_dir = Path(manifest["path"])
            self.assertTrue((bundle_dir / "metadata.json").exists())
            self.assertTrue((bundle_dir / "dataset_fingerprints.json").exists())
            self.assertTrue((bundle_dir / "predictions.json").exists())
            self.assertEqual(len(storage.read_records(BASELINE_BUNDLES, latest_only=True)), 1)


def _workflow(root: Path) -> PredictionWorkflow:
    manager = PluginManager(
        [
            StaticPredictionPlugin(prediction()),
            SrfChProviderOptimizerPlugin(),
            StructuredOutputPlugin(),
            DebugReportPlugin(),
        ]
    )
    return PredictionWorkflow.from_project_root(root, manager)


if __name__ == "__main__":
    unittest.main()
