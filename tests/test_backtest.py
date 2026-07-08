from __future__ import annotations

import unittest

from worldcup_predictions.core.contracts import ScoreTip
from worldcup_predictions.evaluation import backtest_srf
from worldcup_predictions.model import HistoricalResult
from worldcup_predictions.tournament import FixtureRecord, ResultRecord, TeamResolver
from worldcup_predictions.tournament.state import build_tournament_state


class BacktestTest(unittest.TestCase):
    def test_backtest_srf_returns_points_for_finished_fixture(self) -> None:
        resolver = TeamResolver.default()
        fixture = FixtureRecord(
            event_date="2026-07-01T18:00:00Z",
            home_team=resolver.resolve("Brazil"),
            away_team=resolver.resolve("Japan"),
            group="Group A",
            stage="Group Stage",
        )
        results = [
            ResultRecord(
                event_date=fixture.event_date,
                home_team=fixture.home_team,
                away_team=fixture.away_team,
                score=ScoreTip(2, 0),
                source=source,
            )
            for source in ("srf_public", "football_data_org")
        ]
        history = [
            HistoricalResult("2024-01-01", fixture.home_team, fixture.away_team, ScoreTip(3, 0)),
            HistoricalResult("2024-06-01", fixture.home_team, fixture.away_team, ScoreTip(2, 0)),
        ]
        state = build_tournament_state([fixture], results)

        rows = backtest_srf(state, history)

        self.assertEqual(len(rows), 1)
        self.assertGreaterEqual(rows[0]["points"], 0)
        self.assertEqual(rows[0]["ruleset"], "srf.ch:2026-group-stage")


if __name__ == "__main__":
    unittest.main()
