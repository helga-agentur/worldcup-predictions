from __future__ import annotations

import unittest

from worldcup_predictions.core.contracts import ScoreTip
from worldcup_predictions.plugins.signals.live_calibration.plugin import calibration_rows_from_state, calibration_signals_for_open_fixtures
from worldcup_predictions.plugins.sources.enrichment.postmatch_stats.plugin import chance_quality, team_performance_rows
from worldcup_predictions.tournament import FixtureRecord, ResultRecord, TeamResolver
from worldcup_predictions.tournament.state import build_tournament_state


class PostmatchCalibrationTest(unittest.TestCase):
    def test_chance_quality_prefers_xg_and_falls_back_to_shots(self) -> None:
        self.assertEqual(chance_quality({"home_xg": 1.8, "home_shots": 30}, "home"), 1.8)
        proxy = chance_quality({"home_shots": 10, "home_shots_on_target": 5, "home_corners": 4}, "home")
        self.assertIsNotNone(proxy)
        assert proxy is not None
        self.assertGreater(proxy, 1.0)

    def test_team_performance_downweights_red_card_matches(self) -> None:
        rows = team_performance_rows(
            [
                {
                    "fixture_key": "fixture-1",
                    "home_team": "Brazil",
                    "away_team": "Japan",
                    "home_score": 3,
                    "away_score": 0,
                    "home_xg": 2.2,
                    "away_xg": 0.4,
                    "home_red_cards": 0,
                    "away_red_cards": 1,
                }
            ]
        )

        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]["match_weight"], 0.5)
        self.assertTrue(rows[0]["metadata"]["red_card_downweighted"])

    def test_calibration_emits_future_fixture_signal(self) -> None:
        resolver = TeamResolver.default()
        played = FixtureRecord(
            event_date="2026-06-10T18:00:00Z",
            home_team=resolver.resolve("Brazil"),
            away_team=resolver.resolve("Japan"),
            group="Group A",
        )
        future = FixtureRecord(
            event_date="2026-06-20T18:00:00Z",
            home_team=resolver.resolve("Brazil"),
            away_team=resolver.resolve("France"),
            group="Group A",
        )
        results = [
            ResultRecord(
                event_date=played.event_date,
                home_team=played.home_team,
                away_team=played.away_team,
                score=ScoreTip(3, 0),
                source=source,
            )
            for source in ("srf_public", "football_data_org")
        ]
        state = build_tournament_state([played, future], results)
        performance = team_performance_rows(
            [
                {
                    "fixture_key": results[0].fixture_key,
                    "home_team": "Brazil",
                    "home_fifa_code": "BRA",
                    "away_team": "Japan",
                    "away_fifa_code": "JPN",
                    "home_score": 3,
                    "away_score": 0,
                    "home_xg": 2.8,
                    "away_xg": 0.3,
                }
            ]
        )

        rows = calibration_rows_from_state(state, performance)
        signals = calibration_signals_for_open_fixtures([future], rows)

        self.assertTrue(any(row["fifa_code"] == "BRA" for row in rows))
        self.assertTrue(any(signal.metadata["side"] == "home" for signal in signals))


if __name__ == "__main__":
    unittest.main()
