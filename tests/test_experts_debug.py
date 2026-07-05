from __future__ import annotations

import unittest

from worldcup_predictions.core.contracts import Fixture, OutcomeProbabilities, Prediction, ScoreTip, Signal
from worldcup_predictions.plugins.diagnostics.debug_report.plugin import debug_rows, signal_impact_rows
from worldcup_predictions.plugins.sources.enrichment.srf_experts.plugin import parse_srf_expert_rows, srf_expert_signals_from_rows
from worldcup_predictions.tournament import FixtureRecord, TeamResolver


def fixture_record() -> FixtureRecord:
    resolver = TeamResolver.default()
    return FixtureRecord(
        event_date="2026-07-10T18:00:00Z",
        home_team=resolver.resolve("Brazil"),
        away_team=resolver.resolve("Japan"),
        group="Group A",
        stage="Group Stage",
    )


class ExpertsDebugTest(unittest.TestCase):
    def test_srf_expert_parser_extracts_score_near_fixture_names(self) -> None:
        fixture = fixture_record()
        html = "<html><body><div>Brazil gegen Japan Tipp 2:1 im Gruppenspiel</div></body></html>"

        rows = parse_srf_expert_rows(html, expert_id="test_expert", expert_url="https://example.test", fixtures=[fixture])

        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["tip_home"], 2)
        self.assertEqual(rows[0]["tip_away"], 1)

    def test_srf_expert_rows_emit_consensus_signal(self) -> None:
        fixture = fixture_record()
        rows = [
            {"fixture_key": fixture.key, "expert_id": "a", "tip_home": 2, "tip_away": 1},
            {"fixture_key": fixture.key, "expert_id": "b", "tip_home": 1, "tip_away": 1},
            {"fixture_key": fixture.key, "expert_id": "c", "tip_home": 0, "tip_away": 1},
        ]

        signals = srf_expert_signals_from_rows(rows)

        self.assertEqual(len(signals), 1)
        self.assertEqual(signals[0].name, "expert_hda_probabilities")
        self.assertAlmostEqual(signals[0].metadata["prob_home"], 1 / 3)
        self.assertAlmostEqual(signals[0].metadata["prob_draw"], 1 / 3)
        self.assertAlmostEqual(signals[0].metadata["prob_away"], 1 / 3)

    def test_debug_rows_include_missing_sources_and_adjustments(self) -> None:
        prediction = Prediction(
            fixture=Fixture(event_date="2026-07-10T18:00:00Z", home_team="Brazil", away_team="Japan"),
            most_likely=ScoreTip(1, 0),
            outcome_probabilities=OutcomeProbabilities(0.5, 0.3, 0.2),
            confidence_label="Medium-low",
            confidence_percent=0.5,
            metadata={"signal_adjustments": [{"signal": "total_goals_factor"}]},
        )
        signals = [Signal(name="total_goals_factor", source="open_meteo", fixture_key=prediction.fixture.key, value=0.95)]

        rows = debug_rows([prediction], [], signals)

        self.assertEqual(len(rows), 1)
        self.assertIn("open_meteo", rows[0]["signal_sources"])
        self.assertIn("market_odds", rows[0]["missing_signal_sources"])
        self.assertEqual(rows[0]["signal_adjustments"][0]["signal"], "total_goals_factor")

    def test_signal_impacts_include_global_signals_per_fixture(self) -> None:
        prediction = Prediction(
            fixture=Fixture(event_date="2026-07-10T18:00:00Z", home_team="Brazil", away_team="Japan"),
            most_likely=ScoreTip(1, 0),
            outcome_probabilities=OutcomeProbabilities(0.5, 0.3, 0.2),
            confidence_label="Medium-low",
            confidence_percent=0.5,
            metadata={"signal_adjustments": [{"signal": "live_draw_adjustment"}]},
        )
        signals = [
            Signal(
                name="live_draw_adjustment",
                source="live_calibration",
                fixture_key=None,
                value=0.04,
                weight=0.55,
                confidence=0.5,
            )
        ]

        rows = signal_impact_rows([prediction], signals)

        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["signal_scope"], "global")
        self.assertTrue(rows[0]["applied"])


if __name__ == "__main__":
    unittest.main()
