from __future__ import annotations

import datetime as dt
import unittest

from worldcup_predictions.evaluation.backtest import backtest_historical, evaluate_backtest_row
from worldcup_predictions.evaluation.metrics import (
    ranked_probability_score,
    summarize_backtest_rows,
    world_cup_fixtures_by_year,
)
from worldcup_predictions.evaluation.model_calibration import calibrate_baseline_model
from worldcup_predictions.core.contracts import OutcomeProbabilities, ScoreTip
from worldcup_predictions.model import BaselineModel, HistoricalResult
from worldcup_predictions.model.baseline import compute_elo, compute_goal_profiles
from worldcup_predictions.tournament import FixtureRecord, TeamResolver


def _world_cup_history(resolver: TeamResolver, year: int, count: int) -> list[HistoricalResult]:
    teams = [resolver.resolve(name) for name in ("Brazil", "Japan", "Germany", "Mexico")]
    rows = []
    for index in range(count):
        home = teams[index % len(teams)]
        away = teams[(index + 1) % len(teams)]
        # spread matches across the tournament window so date-sort is stable
        day = 11 + index
        date = f"{year}-06-{day:02d}" if day <= 30 else f"{year}-07-{day - 30:02d}"
        rows.append(
            HistoricalResult(
                date=date,
                home_team=home,
                away_team=away,
                score=ScoreTip(1 + (index % 3), index % 2),
                tournament="FIFA World Cup",
            )
        )
    return rows


class Phase1MetricsTest(unittest.TestCase):
    def test_ranked_probability_score_bounds(self) -> None:
        certain = OutcomeProbabilities(1.0, 0.0, 0.0)
        self.assertAlmostEqual(ranked_probability_score(certain, "home"), 0.0)
        self.assertGreater(ranked_probability_score(certain, "away"), 0.9)

    def test_world_cup_fixtures_split_group_and_knockout(self) -> None:
        resolver = TeamResolver.default()
        history = _world_cup_history(resolver, 2018, 20)

        fixtures_by_year = world_cup_fixtures_by_year(history, (2018,))
        fixtures = fixtures_by_year[2018]

        stages = [fixture.stage for fixture, _ in fixtures]
        # 20 matches -> first 4 group, last 16 knockout.
        self.assertEqual(stages[:4], ["Group Stage"] * 4)
        self.assertEqual(stages[4:], ["Knockout Stage"] * 16)

    def test_backtest_historical_emits_expected_points_and_rps(self) -> None:
        resolver = TeamResolver.default()
        history = _world_cup_history(resolver, 2014, 18) + _world_cup_history(resolver, 2018, 18)

        rows = backtest_historical(history, years=(2014, 2018))

        self.assertEqual(len(rows), 36)
        self.assertEqual({row["year"] for row in rows}, {2014, 2018})
        for row in rows:
            self.assertIn("expected_points", row)
            self.assertIn("rps", row)
            self.assertIn(row["phase"], {"group_stage", "knockout_stage"})
        summary = summarize_backtest_rows(rows)
        self.assertEqual(summary["matches"], 36)
        self.assertGreaterEqual(summary["points_per_match"], 0.0)

    def test_precomputed_ratings_match_full_prediction(self) -> None:
        resolver = TeamResolver.default()
        history = _world_cup_history(resolver, 2014, 16)
        fixture = FixtureRecord(
            event_date="2018-06-14T18:00:00Z",
            home_team=resolver.resolve("Brazil"),
            away_team=resolver.resolve("Germany"),
            stage="Group Stage",
        )
        actual = ScoreTip(1, 1)

        full = evaluate_backtest_row(fixture, actual, history, source_label="t")

        cutoff = fixture.kickoff_at or dt.datetime.now(dt.timezone.utc)
        ratings = compute_elo(history, cutoff=cutoff)
        profiles, avg_goals = compute_goal_profiles(history, cutoff=cutoff)
        precomputed = evaluate_backtest_row(
            fixture,
            actual,
            history,
            source_label="t",
            ratings=ratings,
            profiles=profiles,
            avg_goals_per_team=avg_goals,
            cutoff=cutoff,
        )

        for key in ("points", "expected_points", "prob_home", "prob_draw", "prob_away", "most_likely", "rps"):
            self.assertEqual(full[key], precomputed[key], msg=key)

    def test_calibration_grid_includes_ml_weight_dimension(self) -> None:
        resolver = TeamResolver.default()
        history = (
            _world_cup_history(resolver, 2014, 18)
            + _world_cup_history(resolver, 2018, 18)
            + _world_cup_history(resolver, 2022, 18)
        )

        rows = calibrate_baseline_model(history)

        # 3 rho x 3 overdispersion x 4 ml_weight candidates.
        self.assertEqual(len(rows), 36)
        self.assertTrue(rows[0]["selected"])
        self.assertEqual(rows[0]["rank"], 1)
        ml_weights = {row["parameters"]["ml_hda_max_weight"] for row in rows}
        self.assertEqual(ml_weights, {0.0, 0.18, 0.30, 0.40})
        for row in rows:
            self.assertIn("expected_points_per_match", row)
            self.assertIn("dixon_coles_rho", row["parameters"])


if __name__ == "__main__":
    unittest.main()
