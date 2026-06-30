from __future__ import annotations

import datetime as dt
import json
import tempfile
import unittest
from pathlib import Path

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
from worldcup_predictions.plugins.debug_report import DebugReportPlugin
from worldcup_predictions.plugins.provider_optimizers import SrfChProviderOptimizerPlugin
from worldcup_predictions.plugins.public_analysis.plugin import public_analysis_rows_with_diagnostics
from worldcup_predictions.plugins.structured_output import StructuredOutputPlugin
from worldcup_predictions.site import build_site
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

            result = build_site(project_root=root, storage=storage, gtm_container_id="")
            self.assertEqual(result.row_count, 1)
            self.assertTrue((result.output_dir / "index.html").exists())
            self.assertTrue((result.output_dir / "api" / "predictions").exists())
            self.assertEqual(len(list((result.output_dir / "assets").glob("site.*.css"))), 1)
            self.assertEqual(len(list((result.output_dir / "assets").glob("theme.*.js"))), 1)
            self.assertTrue((result.output_dir / "assets" / "fonts" / "CadizWeb-Regular.woff2").exists())
            self.assertTrue((result.output_dir / "assets" / "fonts" / "Degular-Regular.woff2").exists())
            self.assertIn("assets/fonts/CadizWeb-Regular.woff2", result.asset_files)
            self.assertIn("assets/fonts/Degular-Regular.woff2", result.asset_files)
            html = (result.output_dir / "index.html").read_text(encoding="utf-8")
            css = next((result.output_dir / "assets").glob("site.*.css")).read_text(encoding="utf-8")
            js = next((result.output_dir / "assets").glob("theme.*.js")).read_text(encoding="utf-8")
            detail = (result.output_dir / "spiele" / "2026-07-10-bra-jpn" / "index.html").read_text(encoding="utf-8")
            payload = json.loads((result.output_dir / "api" / "predictions").read_text(encoding="utf-8"))
            self.assertIn("helga_theme", html)
            self.assertIn("data-theme-toggle", html)
            self.assertIn("Dark Mode", html)
            self.assertNotIn("<span>Tippspiel Prognosen</span>", html)
            self.assertIn('<nav class="breadcrumb" aria-label="Breadcrumb">\n  <a href="/" aria-current="page">Prognosen</a>\n</nav>', html)
            self.assertIn('<header class="page-intro" aria-labelledby="page-title">', html)
            self.assertIn('<section class="summary-dashboard" aria-labelledby="summary-title">', html)
            self.assertIn('<h2 id="summary-title" class="visually-hidden">Prognosen Übersicht</h2>', html)
            self.assertIn('<dl class="summary-metrics">', html)
            self.assertIn('<dt class="summary-label">Getippte Tipps</dt>', html)
            self.assertIn('<dt class="summary-label">Offene Tipps</dt>', html)
            self.assertIn('<dt class="summary-label">SRF Punkte</dt>', html)
            self.assertIn('<dd class="summary-value">6</dd>', html)
            self.assertIn('<dt class="summary-label">20min Punkte</dt>', html)
            self.assertIn('<dd class="summary-value">5</dd>', html)
            self.assertNotIn("<aside", html)
            self.assertNotIn("intro-copy", html)
            self.assertNotIn("summary-panel", html)
            self.assertIn("prefers-color-scheme", js)
            self.assertIn("Max-Age=31536000", js)
            self.assertIn("/assets/fonts/CadizWeb-Regular.woff2", css)
            self.assertIn("/assets/fonts/Degular-Regular.woff2", css)
            self.assertNotIn("helga.ch/themes/custom/customer/dist/webfonts", css)
            self.assertIn("🇧🇷 Brasilien - Japan 🇯🇵", html)
            self.assertIn('<span class="match">🇧🇷 Brasilien - Japan 🇯🇵</span>', html)
            self.assertNotIn('<a class="match"', html)
            self.assertIn("10.07.2026, 20:00", html)
            self.assertIn("Start", html)
            self.assertIn("Resultat", html)
            self.assertIn("SRF Tipp", html)
            self.assertIn("20min Tipp", html)
            self.assertIn("Aktion", html)
            self.assertIn("Details", html)
            self.assertIn("Getippt", html)
            self.assertNotIn("Läuft", html)
            self.assertNotIn("Gespielt", html)
            self.assertNotIn("Tippabgabe geschlossen", html)
            self.assertNotIn("Diese Werte können sich beim nächsten stündlichen Lauf noch bewegen.", html)
            self.assertIn("Diese Daten können sich bis zum jeweiligen Spielstart noch verändern.", html)
            self.assertNotIn("Anpfiff", html)
            self.assertNotIn("Exakte Torerwartung", html)
            self.assertNotIn("H / U / A", html)
            self.assertNotIn("Top Scores", html)
            self.assertIn("JSON API", html)
            self.assertIn('href="/api/predictions"', html)
            self.assertNotIn('href="/">Prognosen</a>', html)
            self.assertIn("Heimsieg / Unentschieden / Auswärtssieg", detail)
            self.assertIn('<header class="page-intro" aria-labelledby="match-title">', detail)
            self.assertIn('<dl class="summary-metrics detail-metrics">', detail)
            self.assertIn('<dl class="summary-metrics detail-probabilities">', detail)
            self.assertIn("Start", detail)
            self.assertIn("Status", detail)
            self.assertIn("Resultat", detail)
            self.assertIn("Vorhersage", detail)
            self.assertIn("Torerwartung", detail)
            self.assertIn("SRF Tippspiel", detail)
            self.assertIn("20min Tippspiel", detail)
            self.assertIn("Sicherheit", detail)
            metric_order = [
                detail.index("<dt class=\"summary-label\">Status</dt>"),
                detail.index("<dt class=\"summary-label\">Start</dt>"),
                detail.index("<dt class=\"summary-label\">20min Tippspiel</dt>"),
                detail.index("<dt class=\"summary-label\">Sicherheit</dt>"),
                detail.index("<dt class=\"summary-label\">Torerwartung</dt>"),
                detail.index("<dt class=\"summary-label\">Vorhersage</dt>"),
                detail.index("<dt class=\"summary-label\">SRF Tippspiel</dt>"),
                detail.index("<dt class=\"summary-label\">Resultat</dt>"),
            ]
            self.assertEqual(metric_order, sorted(metric_order))
            self.assertIn("Mittel (52.0%)", detail)
            self.assertIn('<span class="match-name match-name-title">', detail)
            h1_match_name = detail.split('<h1 id="match-title">', 1)[1].split("</h1>", 1)[0]
            breadcrumb_match_name = detail.split('<a href="/spiele/2026-07-10-bra-jpn/" aria-current="page">', 1)[1].split("</a>", 1)[0]
            self.assertIn('<span class="match-name-label">Brasilien</span>', h1_match_name)
            self.assertIn('<span class="match-name-label">Japan</span>', h1_match_name)
            self.assertNotIn("match-name-flag", h1_match_name)
            self.assertIn('<span class="match-name-flag" aria-hidden="true">🇧🇷</span>&nbsp;<span class="match-name-label">Brasilien</span>', breadcrumb_match_name)
            self.assertIn('<span class="match-name-flag" aria-hidden="true">🇯🇵</span>&nbsp;<span class="match-name-label">Japan</span>', breadcrumb_match_name)
            self.assertIn("10.07.2026, 20:00", detail)
            self.assertIn('title="1.23456789012345:0.87654321098765"', detail)
            self.assertIn("1.23:0.88", detail)
            self.assertNotIn("Exakte Torerwartung", detail)
            self.assertNotIn("Voller Wert", detail)
            self.assertIn("Wahrscheinlichste Resultate", detail)
            self.assertIn("Zurück zur Übersicht", detail)
            self.assertIn('<a href="/">Prognosen</a>', detail)
            self.assertIn('<a href="/spiele/2026-07-10-bra-jpn/" aria-current="page"><span class="match-name match-name-breadcrumb">', detail)
            self.assertIn("--space-lg: 24px;", css)
            self.assertIn('html[data-theme="dark"]', css)
            self.assertIn("--helga-blue: #8fa2ff;", css)
            self.assertIn(".summary-dashboard", css)
            self.assertIn(".page-intro", css)
            self.assertIn("font-size: clamp(2rem, 5.4vw, 5rem);", css)
            self.assertIn(".summary-metrics", css)
            self.assertIn(".summary-card", css)
            self.assertIn(".summary-value-text", css)
            self.assertIn(".detail-metrics", css)
            self.assertIn(".detail-probabilities", css)
            self.assertIn(".match-name", css)
            self.assertIn(".match-name-title", css)
            self.assertIn(".match-name-separator", css)
            self.assertIn(".match-name-flag", css)
            self.assertIn("#match-title", css)
            self.assertIn("padding-top: var(--space-sm);", css)
            self.assertIn("gap: var(--space-8);", css)
            self.assertIn("justify-items: center;", css)
            self.assertNotIn(".match-name-title .match-name-separator {\n  margin-top", css)
            self.assertNotIn(".match-name-title .match-name-away {\n  margin-top", css)
            self.assertIn("grid-template-columns: repeat(4, minmax(0, 1fr));", css)
            self.assertIn("overflow-wrap: anywhere;", css)
            self.assertNotIn("grid-template-columns: repeat(auto-fit, minmax(160px, 1fr));", css)
            self.assertIn("text-align: center;", css)
            self.assertIn(".breadcrumb a", css)
            self.assertIn(".back-link", css)
            self.assertIn("text-decoration: underline;", css)
            self.assertIn("text-underline-offset: 0.16em;", css)
            self.assertIn(".back-link:hover {\n  color: var(--ink);\n  text-decoration: none;", css)
            self.assertIn("text-decoration: none;", css)
            self.assertIn(".site-header nav a {", css)
            self.assertNotIn(".site-header nav a,\n.back-link", css)
            self.assertNotIn("\nnav a,\n.back-link", css)
            self.assertNotIn("\nnav a:hover,\n.back-link:hover", css)
            self.assertIn("margin: 0;\n  margin-top: var(--space-2);", css)
            self.assertIn("margin-left: var(--space-md);", css)
            self.assertIn("margin-top: var(--space-lg);", css)
            self.assertIn(".detail-probabilities {\n  grid-template-columns: repeat(3, minmax(0, 1fr));\n  margin-top: var(--space-lg);", css)
            self.assertIn(".table-wrap-compact {\n  max-width: 560px;\n  margin-top: var(--space-lg);", css)
            self.assertIn("@media (max-width: 760px)", css)
            self.assertIn("min-height: 72px;", css)
            self.assertIn("width: 88px;", css)
            self.assertNotIn(".site-header,\n  .section-heading", css)
            self.assertNotIn("margin-left: var(--space-xs);", css)
            self.assertIn("thead th {", css)
            self.assertIn("tbody tr:last-child > * {\n  border-bottom: 0;", css)
            self.assertIn("white-space: nowrap;", css)
            self.assertNotIn("margin-bottom", css)
            self.assertNotIn("detail-header", css)
            self.assertNotIn(".detail-grid", css)
            self.assertNotIn(".metric-value", css)
            self.assertNotIn(".probability-list", css)
            self.assertIn("border-radius: 999px;", css)
            self.assertNotIn("max-width: 9ch", css)
            self.assertNotIn("max-width: 680px", css)
            self.assertEqual(payload["predictions"][0]["home_team"], "Brazil")
            self.assertEqual(payload["predictions"][0]["predicted_home_goals"], 1.23456789012345)
            self.assertEqual(payload["summary"]["srf_points"], 6.0)
            self.assertEqual(payload["summary"]["twenty_min_points"], 5.0)
            self.assertEqual(payload["summary"]["srf_points_display"], "6")
            self.assertEqual(payload["summary"]["twenty_min_points_display"], "5")

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

            result = build_site(project_root=root, storage=storage, gtm_container_id="")
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

            result = build_site(project_root=root, storage=storage, gtm_container_id="")
            html = (result.output_dir / "index.html").read_text(encoding="utf-8")
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
            self.assertIn("🇨🇦 Kanada - Sieger Sechzehntelfinal 4", future_section)
            self.assertIn("🇧🇷 Brasilien - Japan 🇯🇵", future_section)
            self.assertNotIn("🇩🇪 Deutschland - Paraguay 🇵🇾", future_section)
            self.assertIn("🇩🇪 Deutschland - Paraguay 🇵🇾", tipped_section)
            self.assertLess(
                tipped_section.index("🇩🇪 Deutschland - Paraguay 🇵🇾"),
                tipped_section.index("🇲🇽 Mexiko - Südafrika 🇿🇦"),
            )
            placeholder = next(row for row in payload["predictions"] if row["fixture_key"].endswith("Sieger Sechzehntelfinal 4"))
            self.assertFalse(placeholder["prediction_available"])
            self.assertTrue((result.output_dir / "spiele" / "2026-07-04-can-sieger-sechzehntelfinal-4" / "index.html").exists())

    def test_caddy_serves_extensionless_api_as_inline_json(self) -> None:
        caddyfile = Path(__file__).resolve().parents[1].joinpath("Caddyfile").read_text(encoding="utf-8")

        self.assertIn("@json path /api/* /site-manifest.json", caddyfile)
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
