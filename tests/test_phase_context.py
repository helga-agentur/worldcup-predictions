from __future__ import annotations

import unittest

from worldcup_predictions.core.contracts import ScoreTip
from worldcup_predictions.core.signals import LIVE_DRAW_ADJUSTMENT, TEAM_EXPECTED_GOALS_FACTOR, TOTAL_GOALS_FACTOR
from worldcup_predictions.model import BaselineModel, HistoricalResult
from worldcup_predictions.plugins.phase_context.plugin import phase_context_rows, phase_context_signals
from worldcup_predictions.tournament import FixtureRecord, ResultRecord, TeamResolver, build_tournament_state


class PhaseContextTest(unittest.TestCase):
    def test_regular_group_fixture_gets_no_phase_context(self) -> None:
        resolver = TeamResolver.default()
        fixture = FixtureRecord(
            event_date="2026-06-13T18:00:00Z",
            home_team=resolver.resolve("Brazil"),
            away_team=resolver.resolve("Japan"),
            group="Group A",
            stage="Group Stage",
        )
        state = build_tournament_state([fixture], [])

        self.assertEqual(phase_context_rows(state), [])

    def test_final_group_fixture_gets_phase_context(self) -> None:
        resolver = TeamResolver.default()
        played = FixtureRecord(
            event_date="2026-06-13T18:00:00Z",
            home_team=resolver.resolve("Brazil"),
            away_team=resolver.resolve("Scotland"),
            group="Group A",
            stage="Group Stage",
        )
        final = FixtureRecord(
            event_date="2026-07-19T18:00:00Z",
            home_team=resolver.resolve("Brazil"),
            away_team=resolver.resolve("Japan"),
            group="Group A",
            stage="Group Stage",
        )
        results = [
            ResultRecord(played.event_date, played.home_team, played.away_team, ScoreTip(2, 0), source=source)
            for source in ("srf_public", "football_data_org")
        ]
        state = build_tournament_state([played, final], results)

        rows = phase_context_rows(state)
        signals = phase_context_signals(rows)

        self.assertEqual(rows[0]["phase_context"], "final_group_match")
        self.assertTrue(any(signal.name == TOTAL_GOALS_FACTOR for signal in signals))
        self.assertTrue(any(signal.name == LIVE_DRAW_ADJUSTMENT for signal in signals))

    def test_knockout_fixture_gets_rest_and_tempo_context(self) -> None:
        resolver = TeamResolver.default()
        played_home = FixtureRecord(
            event_date="2026-06-28T18:00:00Z",
            home_team=resolver.resolve("Brazil"),
            away_team=resolver.resolve("Japan"),
            group="Group A",
            stage="Group Stage",
        )
        played_away = FixtureRecord(
            event_date="2026-06-26T18:00:00Z",
            home_team=resolver.resolve("France"),
            away_team=resolver.resolve("Germany"),
            group="Group B",
            stage="Group Stage",
        )
        knockout = FixtureRecord(
            event_date="2026-07-02T18:00:00Z",
            home_team=resolver.resolve("Brazil"),
            away_team=resolver.resolve("France"),
            stage="Round of 32",
        )
        results = []
        for fixture in (played_home, played_away):
            results.extend(
                [
                    ResultRecord(fixture.event_date, fixture.home_team, fixture.away_team, ScoreTip(1, 0), source=source)
                    for source in ("srf_public", "football_data_org")
                ]
            )
        state = build_tournament_state([played_home, played_away, knockout], results)

        rows = phase_context_rows(state)
        signals = phase_context_signals(rows)

        self.assertEqual(rows[0]["phase_context"], "knockout")
        self.assertLess(rows[0]["total_goals_factor"], 1.0)
        self.assertGreater(rows[0]["draw_adjustment"], 0.0)
        self.assertTrue(any(signal.name == TEAM_EXPECTED_GOALS_FACTOR for signal in signals))

    def test_knockout_prediction_exposes_advancement_probabilities(self) -> None:
        resolver = TeamResolver.default()
        fixture = FixtureRecord(
            event_date="2026-06-29T18:00:00Z",
            home_team=resolver.resolve("Brazil"),
            away_team=resolver.resolve("Japan"),
            stage="Round of 32",
        )
        history = [
            HistoricalResult("2024-01-01", fixture.home_team, fixture.away_team, ScoreTip(2, 0)),
            HistoricalResult("2024-06-01", fixture.home_team, fixture.away_team, ScoreTip(3, 1)),
        ]

        prediction = BaselineModel(history).predict_fixture(fixture)
        advancement = prediction.metadata["advancement_probabilities"]

        self.assertGreater(advancement["home"], advancement["away"])
        self.assertAlmostEqual(advancement["home"] + advancement["away"], 1.0)
        self.assertIn("extra_time_home_share", advancement)
        self.assertIn("penalty_shootout_home_share", advancement)
        self.assertEqual(prediction.metadata["score_basis"], "ninety_minutes")


if __name__ == "__main__":
    unittest.main()
