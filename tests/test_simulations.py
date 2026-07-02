from __future__ import annotations

import itertools
import math
import unittest

from worldcup_predictions.cli import _simulation_known_results, _simulation_score_matrices, build_parser
from worldcup_predictions.core.contracts import (
    Fixture,
    OutcomeProbabilities,
    Prediction,
    ScoreMatrixEntry,
    ScoreTip,
)
from worldcup_predictions.tournament.contracts import FixtureRecord, ResultRecord, TeamRef, TournamentState
from worldcup_predictions.plugins.provider_optimizers.ch_20min import (
    best_twenty_min_bonus_answers,
    evaluate_twenty_min_bonus_questions,
)
from worldcup_predictions.plugins.provider_optimizers.ch_srf import (
    best_srf_bonus_answers,
    evaluate_srf_bonus_questions,
)
from worldcup_predictions.simulations import SimulationInputs, TournamentSimulator, pair_key


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


class TournamentSimulationSamplingRegressionTest(unittest.TestCase):
    """Regression for the degenerate 2026-06-30 simulation summary.

    Stage-only fixture rows without group labels collapsed all teams into one
    pseudo-group, the knockout bracket never resolved, and all 20,000
    iterations returned the identical fallback champion.
    """

    GROUP_A = ("Mexico", "South Africa", "South Korea", "Czech Republic")
    GROUP_B = ("Canada", "Qatar", "Switzerland", "Bosnia and Herzegovina")

    def _mid_tournament_inputs(self) -> SimulationInputs:
        # Finished group stage delivered as stage-only rows (no group labels).
        # Scores build distinct points/goal-difference ladders, so the collapsed
        # pre-fix pseudo-group would rank fully deterministically.
        group_scores = {
            self.GROUP_A: (ScoreTip(1, 0), ScoreTip(2, 0), ScoreTip(3, 0), ScoreTip(1, 0), ScoreTip(2, 0), ScoreTip(1, 0)),
            self.GROUP_B: (ScoreTip(2, 0), ScoreTip(3, 0), ScoreTip(5, 0), ScoreTip(2, 0), ScoreTip(3, 0), ScoreTip(2, 0)),
        }
        fixtures: list[Fixture] = []
        known_results: dict[str, ScoreTip] = {}
        day = 10
        for teams, scores in group_scores.items():
            for (home, away), score in zip(itertools.combinations(teams, 2), scores):
                day += 1
                fixture = Fixture(
                    event_date=f"2026-06-{day:02d}T18:00:00Z",
                    home_team=home,
                    away_team=away,
                    stage="group",
                )
                fixtures.append(fixture)
                known_results[fixture.key] = score

        # Unresolved knockout fixtures with non-degenerate score matrices, keyed
        # by fixture key and team pair the way the CLI provides them.
        matrix = [
            ScoreMatrixEntry(1, 0, 0.30),
            ScoreMatrixEntry(0, 1, 0.25),
            ScoreMatrixEntry(1, 1, 0.20),
            ScoreMatrixEntry(2, 1, 0.15),
            ScoreMatrixEntry(0, 2, 0.10),
        ]
        score_matrices: dict[str, list[ScoreMatrixEntry]] = {}
        for home, away in (("South Africa", "Qatar"), ("Mexico", "Canada")):
            fixture = Fixture(
                event_date="2026-06-28T18:00:00Z",
                home_team=home,
                away_team=away,
                stage="knockout",
            )
            fixtures.append(fixture)
            score_matrices[fixture.key] = matrix
            score_matrices[pair_key(home, away)] = matrix
        return SimulationInputs(
            fixtures=fixtures,
            known_results=known_results,
            score_matrices=score_matrices,
        )

    def test_stage_only_group_rows_still_sample_the_knockout(self) -> None:
        summary = TournamentSimulator(self._mid_tournament_inputs(), iterations=300, seed=20260611).run()

        champion = summary.distributions["champion"]
        probabilities = [row["probability"] for row in champion]
        self.assertGreater(len(champion), 1)
        self.assertGreater(-sum(probability * math.log(probability) for probability in probabilities), 0.0)
        self.assertAlmostEqual(sum(probabilities), 1.0, places=9)
        for row in champion:
            self.assertGreater(row["probability"], 0.0)

        sources = {result["source"] for result in summary.metadata["sample_results"]}
        stages = {result["stage"] for result in summary.metadata["sample_results"]}
        self.assertIn("fixed", sources)
        self.assertIn("simulated", sources)
        self.assertIn("Final", stages)


class SimulationInputWiringTest(unittest.TestCase):
    def _state(self) -> TournamentState:
        group_fixture = FixtureRecord(
            event_date="2026-06-11T19:00:00Z",
            home_team=TeamRef("Mexico", "MEX"),
            away_team=TeamRef("South Africa", "RSA"),
            stage="First Stage",
            group="Group A",
        )
        knockout_fixture = FixtureRecord(
            event_date="2026-06-30T21:00:00Z",
            home_team=TeamRef("France", "FRA"),
            away_team=TeamRef("Sweden", "SWE"),
            stage="Round of 32",
        )
        results = [
            ResultRecord(
                event_date=fixture.event_date,
                home_team=fixture.home_team,
                away_team=fixture.away_team,
                score=score,
            )
            for fixture, score in ((group_fixture, ScoreTip(2, 0)), (knockout_fixture, ScoreTip(1, 0)))
        ]
        return TournamentState(fixtures=[group_fixture, knockout_fixture], results=results, standings={})

    def test_known_results_gain_pair_keys_for_knockout_matches_only(self) -> None:
        state = self._state()
        known = {result.fixture_key: result.score for result in state.results}

        augmented = _simulation_known_results(state, known)

        self.assertEqual(augmented[pair_key("France", "Sweden")], ScoreTip(1, 0))
        self.assertNotIn(pair_key("Mexico", "South Africa"), augmented)
        self.assertEqual(_simulation_known_results(state, {}), {})

    def test_score_matrices_gain_pair_keys_for_knockout_predictions(self) -> None:
        state = self._state()
        matrix = [ScoreMatrixEntry(1, 0, 0.6), ScoreMatrixEntry(0, 1, 0.4)]
        predictions = [
            Prediction(
                fixture=fixture.to_fixture(),
                most_likely=ScoreTip(1, 0),
                outcome_probabilities=OutcomeProbabilities(0.4, 0.3, 0.3),
                confidence_label="Medium",
                confidence_percent=40.0,
                score_matrix=matrix,
            )
            for fixture in state.fixtures
        ]

        matrices = _simulation_score_matrices(predictions)

        self.assertEqual(matrices[state.fixtures[1].to_fixture().key], matrix)
        self.assertEqual(matrices[pair_key("France", "Sweden")], matrix)
        self.assertNotIn(pair_key("Mexico", "South Africa"), matrices)


if __name__ == "__main__":
    unittest.main()
