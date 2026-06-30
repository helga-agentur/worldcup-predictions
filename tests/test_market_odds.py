from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

try:
    import duckdb  # noqa: F401
except ModuleNotFoundError:  # pragma: no cover
    duckdb = None

from worldcup_predictions.core.contracts import Signal
from worldcup_predictions.core.events import EventName
from worldcup_predictions.core.plugin import PluginManager
from worldcup_predictions.core.workflow import PredictionWorkflow
from worldcup_predictions.model import BaselineModel
from worldcup_predictions.plugins.market_odds import MarketOddsPlugin
from worldcup_predictions.plugins.market_odds.plugin import MARKET_ODDS_DATASET, market_signals_from_rows, odds_api_rows
from worldcup_predictions.storage import DuckDBStorage
from worldcup_predictions.tournament import FixtureRecord, TeamResolver
from worldcup_predictions.tournament.repository import write_fixtures


def fixture() -> FixtureRecord:
    resolver = TeamResolver.default()
    return FixtureRecord(
        event_date="2026-07-10T18:00:00Z",
        home_team=resolver.resolve("Brazil"),
        away_team=resolver.resolve("Japan"),
        group="Group A",
        stage="Group Stage",
    )


def odds_payload():
    return [
        {
            "id": "event-1",
            "commence_time": "2026-07-10T18:00:00Z",
            "home_team": "Brazil",
            "away_team": "Japan",
            "bookmakers": [
                {
                    "key": "book_a",
                    "markets": [
                        {
                            "key": "h2h",
                            "outcomes": [
                                {"name": "Brazil", "price": 2.0},
                                {"name": "Draw", "price": 3.5},
                                {"name": "Japan", "price": 4.0},
                            ],
                        },
                        {
                            "key": "totals",
                            "outcomes": [
                                {"name": "Over", "price": 1.9, "point": 2.5},
                                {"name": "Under", "price": 1.9, "point": 2.5},
                            ],
                        },
                        {
                            "key": "spreads",
                            "outcomes": [
                                {"name": "Brazil", "price": 1.9, "point": -1.0},
                                {"name": "Japan", "price": 1.9, "point": 1.0},
                            ],
                        },
                    ],
                },
                {
                    "key": "book_b",
                    "markets": [
                        {
                            "key": "h2h",
                            "outcomes": [
                                {"name": "Brazil", "price": 1.9},
                                {"name": "Draw", "price": 3.4},
                                {"name": "Japan", "price": 4.4},
                            ],
                        },
                    ],
                },
            ],
        }
    ]


class MarketOddsTest(unittest.TestCase):
    def test_odds_api_rows_extract_structured_market_facts(self) -> None:
        rows = odds_api_rows(odds_payload(), [fixture()])

        self.assertEqual(len(rows), 1)
        row = rows[0]
        self.assertEqual(row["fixture_key"], fixture().key)
        self.assertEqual(row["h2h_bookmaker_count"], 2)
        self.assertEqual(row["totals_bookmaker_count"], 1)
        self.assertEqual(row["spreads_bookmaker_count"], 1)
        self.assertAlmostEqual(row["prob_home"] + row["prob_draw"] + row["prob_away"], 1.0)
        self.assertAlmostEqual(row["total_goals"], 2.5)
        self.assertAlmostEqual(row["goal_diff"], 1.0)

    def test_market_rows_emit_model_signals(self) -> None:
        rows = odds_api_rows(odds_payload(), [fixture()])

        signals = market_signals_from_rows(rows)

        self.assertEqual({signal.name for signal in signals}, {"market_hda_probabilities", "market_total_goals", "market_goal_diff"})
        hda = next(signal for signal in signals if signal.name == "market_hda_probabilities")
        self.assertEqual(hda.fixture_key, fixture().key)
        self.assertGreater(hda.metadata["prob_home"], hda.metadata["prob_away"])

    def test_market_rows_aggregate_multiple_public_market_rows(self) -> None:
        match = fixture()
        rows = odds_api_rows(odds_payload(), [match])
        rows.append(
            {
                "record_key": f"secondary_market_feed|{match.key}",
                "fixture_key": match.key,
                "event_id": "secondary-market-feed",
                "commence_time": match.event_date,
                "home_team": match.home_team.name,
                "away_team": match.away_team.name,
                "home_fifa_code": match.home_team.fifa_code,
                "away_fifa_code": match.away_team.fifa_code,
                "market": "aggregate",
                "prob_home": 0.25,
                "prob_draw": 0.25,
                "prob_away": 0.50,
                "total_goals": None,
                "goal_diff": None,
                "h2h_bookmaker_count": 1,
                "totals_bookmaker_count": 0,
                "spreads_bookmaker_count": 0,
                "observed_at_utc": "2026-07-10T08:00:00Z",
                "metadata": {"source": "secondary_market_feed"},
            }
        )

        signals = market_signals_from_rows(rows)
        hda = next(signal for signal in signals if signal.name == "market_hda_probabilities")

        self.assertEqual(hda.source, "market_odds")
        self.assertEqual(hda.metadata["bookmaker_count"], 3)
        self.assertGreater(hda.metadata["prob_home"], 0.25)
        self.assertLess(hda.metadata["prob_home"], 0.60)

    def test_baseline_model_applies_market_hda_signal(self) -> None:
        match = fixture()
        model = BaselineModel([])
        baseline = model.predict_fixture(match)
        market_signal = Signal(
            name="market_hda_probabilities",
            source="test",
            fixture_key=match.key,
            weight=0.42,
            confidence=1.0,
            metadata={"prob_home": 0.15, "prob_draw": 0.20, "prob_away": 0.65},
        )

        adjusted = model.predict_fixture(match, signals=[market_signal])

        self.assertLess(adjusted.outcome_probabilities.home, baseline.outcome_probabilities.home)
        self.assertGreater(adjusted.outcome_probabilities.away, baseline.outcome_probabilities.away)
        self.assertIn("signal_adjustments", adjusted.metadata)


@unittest.skipIf(duckdb is None, "duckdb dependency is not installed")
class MarketOddsStorageTest(unittest.TestCase):
    def test_plugin_reuses_stored_market_rows_without_api_key(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            storage = DuckDBStorage.at_data_root(root / "data")
            match = fixture()
            write_fixtures(storage, [match], source="test")
            storage.write_records(MARKET_ODDS_DATASET, odds_api_rows(odds_payload(), [match]), source="test")
            workflow = PredictionWorkflow.from_project_root(root, PluginManager([MarketOddsPlugin()]))

            results = workflow.manager.emit(EventName.FEATURE_SIGNALS_REQUESTED, workflow.context, {})

            self.assertEqual(len(results), 1)
            self.assertEqual(len(results[0].signals), 3)
            self.assertTrue(any("ODDS_API_KEY" in diagnostic.message for diagnostic in results[0].diagnostics))


if __name__ == "__main__":
    unittest.main()
