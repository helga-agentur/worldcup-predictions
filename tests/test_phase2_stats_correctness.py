from __future__ import annotations

import unittest

from worldcup_predictions.core.contracts import ScoreTip
from worldcup_predictions.model.baseline import mismatch_blowout_adjustment
from worldcup_predictions.plugins.signals.live_calibration.plugin import (
    PRIOR_DRAW_RATE,
    global_calibration_rows_from_state,
)
from worldcup_predictions.simulations.monte_carlo import SimulationInputs, TournamentSimulator
from worldcup_predictions.tournament import FixtureRecord, ResultRecord, TeamResolver, TournamentState


def _draw_state(resolver: TeamResolver, count: int) -> TournamentState:
    fixtures, results = [], []
    for index in range(count):
        match = FixtureRecord(
            event_date=f"2026-06-{(index % 27) + 1:02d}T18:00:00Z",
            home_team=resolver.resolve("Brazil"),
            away_team=resolver.resolve("Japan"),
        )
        fixtures.append(match)
        results.append(ResultRecord(match.event_date, match.home_team, match.away_team, ScoreTip(1, 1)))
    return TournamentState(fixtures=fixtures, results=results, standings={})


class BayesianLiveCalibrationTest(unittest.TestCase):
    def test_small_sample_shrinks_toward_prior(self) -> None:
        resolver = TeamResolver.default()
        rows = global_calibration_rows_from_state(_draw_state(resolver, 4), [])
        row = rows[0]

        # Observed draw rate is 1.0, but 4 matches must not be trusted as truth.
        self.assertEqual(row["draw_rate"], 1.0)
        self.assertLess(row["posterior_draw_rate"], 0.5)
        self.assertGreater(row["posterior_draw_rate"], PRIOR_DRAW_RATE)
        # The applied draw adjustment stays well below the +0.12 ceiling at small n.
        self.assertLess(row["draw_adjustment"], 0.06)

    def test_more_matches_move_posterior_toward_observed(self) -> None:
        resolver = TeamResolver.default()
        small = global_calibration_rows_from_state(_draw_state(resolver, 4), [])[0]
        large = global_calibration_rows_from_state(_draw_state(resolver, 40), [])[0]

        self.assertGreater(large["posterior_draw_rate"], small["posterior_draw_rate"])
        self.assertGreater(large["draw_adjustment"], small["draw_adjustment"])


class MismatchTriggerTest(unittest.TestCase):
    def test_balanced_lambdas_do_not_trigger(self) -> None:
        self.assertIsNone(mismatch_blowout_adjustment(1.3, 1.1, 0.10))

    def test_zero_weight_disables(self) -> None:
        self.assertIsNone(mismatch_blowout_adjustment(2.6, 0.3, 0.0))

    def test_blowout_lambdas_boost_favorite_and_damp_underdog(self) -> None:
        adjustment = mismatch_blowout_adjustment(2.6, 0.4, 0.10)

        self.assertIsNotNone(adjustment)
        assert adjustment is not None
        self.assertEqual(adjustment["favorite"], "home")
        self.assertGreater(adjustment["favorite_factor"], 1.0)
        self.assertLess(adjustment["underdog_factor"], 1.0)
        # Caps from the legacy rule.
        self.assertLessEqual(adjustment["total_adjustment"], 0.10 + 1e-9)
        self.assertLessEqual(adjustment["favorite_adjustment"], 0.08 + 1e-9)
        self.assertGreaterEqual(adjustment["underdog_factor"], 0.90 - 1e-9)

    def test_favorite_side_follows_larger_lambda(self) -> None:
        adjustment = mismatch_blowout_adjustment(0.4, 2.6, 0.10)
        assert adjustment is not None
        self.assertEqual(adjustment["favorite"], "away")


class PenaltyShootoutTest(unittest.TestCase):
    def _simulator(self, **inputs_kwargs) -> TournamentSimulator:
        return TournamentSimulator(SimulationInputs(fixtures=[], **inputs_kwargs))

    def test_elo_logistic_used_when_ratings_present(self) -> None:
        sim = self._simulator(team_ratings={"A": 1600.0, "B": 1400.0})
        prob = sim._penalty_home_probability("A", "B")
        # 200-point gap under a 500 scale: 1/(1+10^(-0.4)) ~= 0.715.
        self.assertAlmostEqual(prob, 1.0 / (1.0 + 10 ** (-0.4)), places=6)

    def test_equal_ratings_are_even(self) -> None:
        sim = self._simulator(team_ratings={"A": 1500.0, "B": 1500.0})
        self.assertAlmostEqual(sim._penalty_home_probability("A", "B"), 0.5, places=6)

    def test_falls_back_to_strengths_without_ratings(self) -> None:
        sim = self._simulator(team_strengths={"A": 3.0, "B": 1.0})
        self.assertAlmostEqual(sim._penalty_home_probability("A", "B"), 0.75, places=6)

    def test_defaults_to_even_without_any_signal(self) -> None:
        sim = self._simulator()
        self.assertAlmostEqual(sim._penalty_home_probability("A", "B"), 0.5, places=6)


if __name__ == "__main__":
    unittest.main()
