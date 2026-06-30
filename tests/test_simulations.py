from __future__ import annotations

import unittest

from worldcup_predictions.cli import build_parser
from worldcup_predictions.core.contracts import Fixture, ScoreTip
from worldcup_predictions.plugins.provider_optimizers.ch_20min import (
    best_twenty_min_bonus_answers,
    evaluate_twenty_min_bonus_questions,
)
from worldcup_predictions.plugins.provider_optimizers.ch_srf import (
    best_srf_bonus_answers,
    evaluate_srf_bonus_questions,
)
from worldcup_predictions.simulations import SimulationInputs, TournamentSimulator


class TournamentSimulationTest(unittest.TestCase):
    def build_summary(self):
        fixture = Fixture(
            event_date="2026-06-29T18:00:00Z",
            home_team="Brazil",
            away_team="Japan",
            group="Group A",
            stage="Group Stage",
        )
        inputs = SimulationInputs(
            fixtures=[fixture],
            known_results={fixture.key: ScoreTip(2, 0)},
        )
        return TournamentSimulator(inputs, iterations=5, seed=123).run()

    def test_simulator_uses_known_results_and_tracks_group_state(self) -> None:
        summary = self.build_summary()

        sample_result = summary.metadata["sample_results"][0]
        self.assertEqual(sample_result["home_team"], "Brazil")
        self.assertEqual(sample_result["away_team"], "Japan")
        self.assertEqual(sample_result["score"], "2:0")
        self.assertEqual(sample_result["source"], "fixed")

        brazil_rank = summary.distributions["group_rank"]["Brazil"]
        japan_rank = summary.distributions["group_rank"]["Japan"]
        self.assertEqual(brazil_rank[0]["answer"], "1")
        self.assertEqual(brazil_rank[0]["probability"], 1.0)
        self.assertEqual(japan_rank[0]["answer"], "2")
        self.assertEqual(japan_rank[0]["probability"], 1.0)

        self.assertEqual(summary.distributions["team_groups"]["Brazil"], "A")
        self.assertEqual(summary.distributions["team_groups"]["Japan"], "A")

    def test_srf_bonus_adapter_reads_neutral_summary(self) -> None:
        summary = self.build_summary()

        report = evaluate_srf_bonus_questions(summary, swiss_team="Brazil")
        best = best_srf_bonus_answers(summary, swiss_team="Brazil")

        self.assertEqual(report["provider"], "srf.ch")
        self.assertIn("world_champion", report["questions"])
        self.assertIn("switzerland_stage", report["questions"])
        self.assertIsNotNone(best["world_champion"])
        self.assertIsNotNone(best["switzerland_stage"])

    def test_twenty_min_bonus_adapter_reads_neutral_summary(self) -> None:
        summary = self.build_summary()

        report = evaluate_twenty_min_bonus_questions(summary)
        best = best_twenty_min_bonus_answers(summary)

        self.assertEqual(report["provider"], "20min.ch")
        self.assertEqual(report["questions"]["group_winners"]["A"][0]["answer"], "Brazil")
        self.assertEqual(report["questions"]["group_winners"]["A"][0]["probability"], 1.0)
        self.assertEqual(best["group_winners"]["A"]["answer"], "Brazil")

    def test_simulation_command_accepts_from_day_one_mode(self) -> None:
        args = build_parser().parse_args(["simulate-tournament", "--from-day-one"])

        self.assertTrue(args.from_day_one)


if __name__ == "__main__":
    unittest.main()
