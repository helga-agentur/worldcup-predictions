from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

try:
    import duckdb  # noqa: F401
except ModuleNotFoundError:  # pragma: no cover
    duckdb = None

from worldcup_predictions.core.contracts import ScoreTip, Signal
from worldcup_predictions.core.plugin import PluginManager
from worldcup_predictions.core.workflow import PredictionWorkflow
from worldcup_predictions.model import BaselineModel, HistoricalResult, ModelSignalPolicy, SignalApplierRegistry, build_score_matrix
from worldcup_predictions.core.datasets import HISTORICAL_RESULTS
from worldcup_predictions.model.historical_results import parse_historical_results_text
from worldcup_predictions.plugins import builtin_plugins
from worldcup_predictions.storage import DuckDBStorage
from worldcup_predictions.tournament import FixtureRecord, TeamResolver
from worldcup_predictions.tournament.repository import write_fixtures


class BaselineModelTest(unittest.TestCase):
    def test_score_matrix_is_normalized(self) -> None:
        matrix = build_score_matrix(1.4, 0.9, max_goals=6, dixon_coles_rho=-0.08)

        self.assertAlmostEqual(sum(entry.probability for entry in matrix), 1.0)
        self.assertGreater(len(matrix), 0)

    def test_baseline_model_uses_historical_strength(self) -> None:
        resolver = TeamResolver.default()
        results = [
            HistoricalResult("2024-01-01", resolver.resolve("Brazil"), resolver.resolve("Japan"), ScoreTip(3, 0)),
            HistoricalResult("2024-06-01", resolver.resolve("Brazil"), resolver.resolve("Japan"), ScoreTip(2, 0)),
            HistoricalResult("2024-09-01", resolver.resolve("Japan"), resolver.resolve("Brazil"), ScoreTip(1, 2)),
        ]
        fixture = FixtureRecord(
            event_date="2026-07-10T18:00:00Z",
            home_team=resolver.resolve("Brazil"),
            away_team=resolver.resolve("Japan"),
            group="Group A",
            stage="Group Stage",
        )

        prediction = BaselineModel(results).predict_fixture(fixture)

        self.assertGreater(prediction.expected_home_goals, prediction.expected_away_goals)
        self.assertGreater(prediction.outcome_probabilities.home, prediction.outcome_probabilities.away)
        self.assertAlmostEqual(sum(entry.probability for entry in prediction.score_matrix), 1.0)

    def test_model_signal_policy_can_cap_market_hda_impact(self) -> None:
        resolver = TeamResolver.default()
        fixture = FixtureRecord(
            event_date="2026-07-10T18:00:00Z",
            home_team=resolver.resolve("Brazil"),
            away_team=resolver.resolve("Japan"),
            group="Group A",
            stage="Group Stage",
        )
        signal = Signal(
            name="market_hda_probabilities",
            source="test",
            fixture_key=fixture.key,
            weight=1.0,
            confidence=1.0,
            metadata={"prob_home": 0.10, "prob_draw": 0.20, "prob_away": 0.70},
        )
        model = BaselineModel(
            [],
            signal_appliers=SignalApplierRegistry.default(ModelSignalPolicy(market_hda_max_weight=0.0)),
        )

        baseline = model.predict_fixture(fixture)
        adjusted = model.predict_fixture(fixture, signals=[signal])

        self.assertAlmostEqual(adjusted.outcome_probabilities.home, baseline.outcome_probabilities.home)
        self.assertAlmostEqual(adjusted.outcome_probabilities.away, baseline.outcome_probabilities.away)

    def test_historical_text_parser_parses_common_dataset_shape(self) -> None:
        results = parse_historical_results_text(
            "date,home_team,away_team,home_score,away_score,tournament,neutral\n"
            "2024-01-01,Brazil,Japan,3,0,Friendly,TRUE\n"
        )

        self.assertEqual(len(results), 1)
        self.assertEqual(results[0].home_team.fifa_code, "BRA")
        self.assertEqual(results[0].away_team.fifa_code, "JPN")
        self.assertEqual(results[0].score, ScoreTip(3, 0))

    @unittest.skipIf(duckdb is None, "duckdb dependency is not installed")
    def test_prediction_workflow_emits_baseline_predictions_and_provider_tips(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            storage = DuckDBStorage.at_data_root(root / "data")
            resolver = TeamResolver.default()
            fixture = FixtureRecord(
                event_date="2026-07-10T18:00:00Z",
                home_team=resolver.resolve("Brazil"),
                away_team=resolver.resolve("Japan"),
                group="Group A",
                stage="Group Stage",
            )
            write_fixtures(storage, [fixture], source="test")
            history_rows = [
                HistoricalResult("2024-01-01", resolver.resolve("Brazil"), resolver.resolve("Japan"), ScoreTip(3, 0)),
                HistoricalResult("2024-06-01", resolver.resolve("Brazil"), resolver.resolve("Japan"), ScoreTip(2, 0)),
                HistoricalResult("2024-09-01", resolver.resolve("Japan"), resolver.resolve("Brazil"), ScoreTip(1, 2)),
            ]
            storage.write_records(HISTORICAL_RESULTS, [row.to_record() for row in history_rows], source="test")
            workflow = PredictionWorkflow.from_project_root(root, PluginManager(builtin_plugins()))

            run = workflow.next_predictions(limit=1)

            self.assertEqual(len(run.predictions), 1)
            self.assertEqual(run.predictions[0].source, "baseline_model")
            self.assertGreaterEqual(len(run.optimized_tips), 2)
            self.assertEqual({tip.ruleset.provider for tip in run.optimized_tips}, {"srf.ch", "20min.ch"})


if __name__ == "__main__":
    unittest.main()
