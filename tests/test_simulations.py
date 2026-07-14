from __future__ import annotations

import datetime as dt
import itertools
import math
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from worldcup_predictions.cli import (
    SIMULATION_LOGIC_VERSION,
    _current_state_simulation_refresh_decision,
    _entity_maintenance_decision,
    _latest_current_state_simulation_fixture_fingerprint,
    _run_scheduled_automation_hooks,
    _simulation_fixture_metadata,
    _simulation_known_results,
    _simulation_known_winners,
    _simulation_score_matrix_provider,
    _simulation_score_matrices,
    build_parser,
)
from worldcup_predictions.core.contracts import (
    Fixture,
    OutcomeProbabilities,
    Prediction,
    ScoreMatrixEntry,
    ScoreTip,
)
from worldcup_predictions.core.datasets import AUTOMATION_HOOKS, PREDICTION_RUN_SUMMARIES, SIMULATION_SUMMARY
from worldcup_predictions.tournament.contracts import FixtureRecord, ResultRecord, TeamRef, TournamentState
from worldcup_predictions.plugins.providers.ch_20min import (
    best_twenty_min_bonus_answers,
    evaluate_twenty_min_bonus_questions,
)
from worldcup_predictions.plugins.providers.ch_srf import (
    best_srf_bonus_answers,
    evaluate_srf_bonus_questions,
)
from worldcup_predictions.simulations import DEFAULT_SIMULATION_ITERATIONS, SimulationInputs, TournamentSimulator, pair_key
from worldcup_predictions.simulations.worldcup_2026 import (
    NEXT_ROUNDS,
    ROUND_OF_32,
    assign_third_place_slots,
    round_of_32_matches,
    third_assignments_from_real_pairs,
)
from worldcup_predictions.storage import DuckDBStorage


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


def _mid_tournament_inputs(
    *,
    knockout_results: dict[str, ScoreTip] | None = None,
    known_winners: dict[str, str] | None = None,
) -> SimulationInputs:
    """Two finished groups plus unresolved knockout fixtures.

    The group stage arrives as stage-only rows (no group labels) and its scores
    build distinct points/goal-difference ladders, so group placements are
    fully deterministic: 2A is South Africa and 2B is Qatar, which meet in
    bracket match M73.
    """

    group_scores = {
        ("Mexico", "South Africa", "South Korea", "Czech Republic"): (ScoreTip(1, 0), ScoreTip(2, 0), ScoreTip(3, 0), ScoreTip(1, 0), ScoreTip(2, 0), ScoreTip(1, 0)),
        ("Canada", "Qatar", "Switzerland", "Bosnia and Herzegovina"): (ScoreTip(2, 0), ScoreTip(3, 0), ScoreTip(5, 0), ScoreTip(2, 0), ScoreTip(3, 0), ScoreTip(2, 0)),
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
        known_results={**known_results, **(knockout_results or {})},
        known_winners=known_winners or {},
        score_matrices=score_matrices,
    )


class TournamentSimulationSamplingRegressionTest(unittest.TestCase):
    """Regression for the degenerate 2026-06-30 simulation summary.

    Stage-only fixture rows without group labels collapsed all teams into one
    pseudo-group, the knockout bracket never resolved, and all 20,000
    iterations returned the identical fallback champion.
    """

    def test_stage_only_group_rows_still_sample_the_knockout(self) -> None:
        summary = TournamentSimulator(_mid_tournament_inputs(), iterations=300, seed=20260611).run()

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

    def test_knockout_dependency_map_matches_fixture_slots(self) -> None:
        round_of_16, quarter_finals, semi_finals, final = NEXT_ROUNDS

        self.assertEqual(
            ROUND_OF_32,
            [
                ("M73", "2A", "2B"),
                ("M74", "1E", "3A/B/C/D/F"),
                ("M75", "1F", "2C"),
                ("M76", "1C", "2F"),
                ("M77", "1I", "3C/D/F/G/H"),
                ("M78", "2E", "2I"),
                ("M79", "1A", "3C/E/F/H/I"),
                ("M80", "1L", "3E/H/I/J/K"),
                ("M81", "1D", "3B/E/F/I/J"),
                ("M82", "1G", "3A/E/H/I/J"),
                ("M83", "2K", "2L"),
                ("M84", "1H", "2J"),
                ("M85", "1B", "3E/F/G/I/J"),
                ("M86", "1J", "2H"),
                ("M87", "1K", "3D/E/I/J/L"),
                ("M88", "2D", "2G"),
            ],
        )
        self.assertEqual(
            round_of_16,
            [
                ("M89", "M74", "M77"),
                ("M90", "M73", "M75"),
                ("M91", "M76", "M78"),
                ("M92", "M79", "M80"),
                ("M93", "M83", "M84"),
                ("M94", "M81", "M82"),
                ("M95", "M86", "M88"),
                ("M96", "M85", "M87"),
            ],
        )
        self.assertEqual(
            quarter_finals,
            [
                ("M97", "M89", "M90"),
                ("M98", "M93", "M94"),
                ("M99", "M91", "M92"),
                ("M100", "M95", "M96"),
            ],
        )
        self.assertEqual(
            semi_finals,
            [("M101", "M97", "M98"), ("M102", "M99", "M100")],
        )
        self.assertEqual(final, [("M104", "M101", "M102")])

    def test_hypothetical_knockout_pairs_use_generated_matrices_before_fallback(self) -> None:
        def provider(_match_id: str, _home: str, _away: str) -> list[ScoreMatrixEntry]:
            return [ScoreMatrixEntry(2, 0, 1.0)]

        inputs = _mid_tournament_inputs()
        inputs = SimulationInputs(
            fixtures=inputs.fixtures,
            known_results=inputs.known_results,
            known_winners=inputs.known_winners,
            score_matrices=inputs.score_matrices,
            score_matrix_provider=provider,
        )
        summary = TournamentSimulator(inputs, iterations=80, seed=20260611).run()

        matrix_sources = summary.metadata["matrix_source_counts"]
        self.assertGreater(matrix_sources.get("generated", 0), 0)
        self.assertEqual(matrix_sources.get("fallback", 0), 0)
        self.assertEqual(summary.metadata["forecast_champion"], summary.distributions["champion"][0]["answer"])
        final = next(row for row in summary.metadata["forecast_results"] if row["match_id"] == "M104")
        self.assertEqual(final["winner"], summary.metadata["forecast_champion"])
        self.assertEqual(final["matrix_source"], "generated")

    def test_generated_knockout_matrices_include_outright_market_prior(self) -> None:
        def provider(_match_id: str, _home: str, _away: str) -> list[ScoreMatrixEntry]:
            return [
                ScoreMatrixEntry(1, 0, 0.35),
                ScoreMatrixEntry(1, 1, 0.30),
                ScoreMatrixEntry(0, 1, 0.35),
            ]

        inputs = _mid_tournament_inputs()
        team_strengths = {
            "Mexico": 0.30,
            "South Africa": 0.02,
            "South Korea": 0.08,
            "Czech Republic": 0.04,
            "Canada": 0.18,
            "Qatar": 0.01,
            "Switzerland": 0.12,
            "Bosnia and Herzegovina": 0.03,
        }
        inputs = SimulationInputs(
            fixtures=inputs.fixtures,
            known_results=inputs.known_results,
            known_winners=inputs.known_winners,
            score_matrices=inputs.score_matrices,
            score_matrix_provider=provider,
            team_strengths=team_strengths,
        )
        summary = TournamentSimulator(inputs, iterations=80, seed=20260611).run()

        self.assertGreater(summary.metadata["matrix_source_counts"].get("generated+outright", 0), 0)
        self.assertGreater(summary.metadata["market_adjustment_counts"].get("generated+outright", 0), 0)
        # The adjustment is cached per pairing: samples (matrix_source_counts)
        # far outnumber adjusted pairings (market_adjustment_counts).
        self.assertLess(
            summary.metadata["market_adjustment_counts"]["generated+outright"],
            summary.metadata["matrix_source_counts"]["generated+outright"],
        )
        self.assertNotIn("champion_market_blend", summary.distributions)


class KnockoutShootoutResolutionTest(unittest.TestCase):
    """Fixed knockout ties must advance the real winner when it is known."""

    TIE = {pair_key("South Africa", "Qatar"): ScoreTip(1, 1)}

    def test_decided_knockout_tie_always_advances_the_known_winner(self) -> None:
        inputs = _mid_tournament_inputs(
            knockout_results=self.TIE,
            known_winners={pair_key("South Africa", "Qatar"): "Qatar"},
        )
        summary = TournamentSimulator(inputs, iterations=200, seed=20260611).run()

        south_africa = summary.distributions["team_stage"]["South Africa"]
        self.assertEqual([(row["answer"], row["probability"]) for row in south_africa], [("Round of 32", 1.0)])
        qatar_stages = {row["answer"] for row in summary.distributions["team_stage"]["Qatar"]}
        self.assertNotIn("Group stage", qatar_stages)
        self.assertNotIn("Round of 32", qatar_stages)

    def test_undecided_knockout_tie_still_samples_the_shootout(self) -> None:
        inputs = _mid_tournament_inputs(knockout_results=self.TIE)
        summary = TournamentSimulator(inputs, iterations=200, seed=20260611).run()

        answers = {row["answer"] for row in summary.distributions["team_stage"]["South Africa"]}
        self.assertIn("Round of 32", answers)
        self.assertGreater(len(answers), 1)


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

    def test_cli_score_matrix_provider_generates_hypothetical_knockout_matrix(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            storage = DuckDBStorage.at_data_root(Path(tmp) / "data")
            fixture = FixtureRecord(
                event_date="2026-07-14T19:00:00Z",
                home_team=TeamRef("Spain", "ESP"),
                away_team=TeamRef("France", "FRA"),
                stage="Semi-final",
                metadata={"match_number": 101},
            )
            state = TournamentState(fixtures=[fixture], results=[], standings={})
            workflow = SimpleNamespace(context=SimpleNamespace(storage=storage, event_results=[]))

            provider = _simulation_score_matrix_provider(workflow, state, include_current_results=True)
            matrix = provider("M101", "Spain", "France")

        self.assertTrue(matrix)
        self.assertAlmostEqual(sum(entry.probability for entry in matrix), 1.0, places=9)

    def test_known_winners_require_a_level_score_and_source_consensus(self) -> None:
        group_fixture = FixtureRecord(
            event_date="2026-06-27T19:00:00Z",
            home_team=TeamRef("Algeria", "ALG"),
            away_team=TeamRef("Austria", "AUT"),
            stage="First Stage",
            group="Group I",
        )
        knockout_fixture = FixtureRecord(
            event_date="2026-06-29T20:30:00Z",
            home_team=TeamRef("Germany", "GER"),
            away_team=TeamRef("Paraguay", "PAR"),
            stage="Round of 32",
        )
        state = TournamentState(fixtures=[group_fixture, knockout_fixture], results=[], standings={})
        known = {group_fixture.key: ScoreTip(3, 3), knockout_fixture.key: ScoreTip(1, 1)}

        def result(fixture: FixtureRecord, score: ScoreTip, metadata: dict) -> ResultRecord:
            return ResultRecord(
                event_date=fixture.event_date,
                home_team=fixture.home_team,
                away_team=fixture.away_team,
                score=score,
                metadata=metadata,
            )

        penalty_result = result(knockout_fixture, ScoreTip(1, 1), {"home_penalty_score": 3, "away_penalty_score": 4})
        flag_result = result(knockout_fixture, ScoreTip(4, 5), {"winner": "AWAY_TEAM"})
        group_result = result(group_fixture, ScoreTip(3, 3), {"winner": "DRAW"})

        winners = _simulation_known_winners(state, [penalty_result, flag_result, group_result], known)
        self.assertEqual(winners[knockout_fixture.key], "Paraguay")
        self.assertEqual(winners[pair_key("Germany", "Paraguay")], "Paraguay")
        self.assertNotIn(group_fixture.key, winners)

        # A single explicit winner flag is enough when no source disagrees.
        self.assertEqual(
            _simulation_known_winners(state, [flag_result], known)[knockout_fixture.key],
            "Paraguay",
        )
        # Conflicting sides are dropped, as are winners for non-level scores
        # and day-one runs without known results.
        conflicting = result(knockout_fixture, ScoreTip(1, 1), {"winner": "HOME_TEAM"})
        self.assertEqual(_simulation_known_winners(state, [penalty_result, conflicting], known), {})
        decided_score = {group_fixture.key: ScoreTip(3, 3), knockout_fixture.key: ScoreTip(2, 1)}
        self.assertEqual(_simulation_known_winners(state, [penalty_result], decided_score), {})
        self.assertEqual(_simulation_known_winners(state, [penalty_result], {}), {})

    def test_fixture_state_fingerprint_tracks_unresolved_fixture_participants(self) -> None:
        fixture = FixtureRecord(
            event_date="2026-07-14T20:00:00Z",
            home_team=TeamRef("Spain", "ESP"),
            away_team=TeamRef("France", "FRA"),
            stage="Semi-final",
        )
        changed_fixture = FixtureRecord(
            event_date=fixture.event_date,
            home_team=TeamRef("Spain", "ESP"),
            away_team=TeamRef("Morocco", "MAR"),
            stage=fixture.stage,
        )

        current = _simulation_fixture_metadata(TournamentState(fixtures=[fixture], results=[], standings={}))
        changed = _simulation_fixture_metadata(TournamentState(fixtures=[changed_fixture], results=[], standings={}))
        finished = _simulation_fixture_metadata(
            TournamentState(
                fixtures=[fixture],
                results=[
                    ResultRecord(
                        event_date=fixture.event_date,
                        home_team=fixture.home_team,
                        away_team=fixture.away_team,
                        score=ScoreTip(2, 1),
                    )
                ],
                standings={},
            )
        )

        self.assertEqual(current["active_fixture_count"], 1)
        self.assertEqual(finished["active_fixture_count"], 0)
        self.assertNotEqual(current["active_fixture_fingerprint"], changed["active_fixture_fingerprint"])
        self.assertNotEqual(current["active_fixture_fingerprint"], finished["active_fixture_fingerprint"])

    def test_simulation_state_fingerprint_tracks_confirmed_result_changes(self) -> None:
        open_fixture = FixtureRecord(
            event_date="2026-07-14T20:00:00Z",
            home_team=TeamRef("Spain", "ESP"),
            away_team=TeamRef("France", "FRA"),
            stage="Semi-final",
        )
        played_fixture = FixtureRecord(
            event_date="2026-07-07T20:00:00Z",
            home_team=TeamRef("Argentina", "ARG"),
            away_team=TeamRef("Egypt", "EGY"),
            stage="Round of 16",
        )
        original = _simulation_fixture_metadata(
            TournamentState(
                fixtures=[open_fixture, played_fixture],
                results=[
                    ResultRecord(
                        event_date=played_fixture.event_date,
                        home_team=played_fixture.home_team,
                        away_team=played_fixture.away_team,
                        score=ScoreTip(2, 0),
                    )
                ],
                standings={},
            )
        )
        corrected = _simulation_fixture_metadata(
            TournamentState(
                fixtures=[open_fixture, played_fixture],
                results=[
                    ResultRecord(
                        event_date=played_fixture.event_date,
                        home_team=played_fixture.home_team,
                        away_team=played_fixture.away_team,
                        score=ScoreTip(2, 1),
                    )
                ],
                standings={},
            )
        )

        self.assertEqual(original["active_fixture_fingerprint"], corrected["active_fixture_fingerprint"])
        self.assertNotEqual(original["confirmed_result_fingerprint"], corrected["confirmed_result_fingerprint"])
        self.assertNotEqual(original["active_state_fingerprint"], corrected["active_state_fingerprint"])

    def test_latest_fixture_fingerprint_uses_current_state_simulations_only(self) -> None:
        class FakeStorage:
            def read_records(self, dataset: str, *, latest_only: bool = False) -> list[dict]:
                self.request = (dataset, latest_only)
                return [
                    {
                        "mode": "current_state",
                        "metadata": {
                            "active_fixture_count": 1,
                            "active_fixture_fingerprint": "older",
                            "forecast_results": [],
                            "matrix_source_counts": {},
                            "simulation_logic_version": SIMULATION_LOGIC_VERSION,
                        },
                        "_record": {"observed_at_utc": "2026-07-06T07:00:00Z"},
                    },
                    {
                        "mode": "from_day_one",
                        "metadata": {"active_fixture_fingerprint": "ignored-newer"},
                        "_record": {"observed_at_utc": "2026-07-06T09:00:00Z"},
                    },
                    {
                        "mode": "current_state",
                        "metadata": {
                            "active_fixture_count": 1,
                            "active_fixture_fingerprint": "newer",
                            "forecast_results": [],
                            "matrix_source_counts": {},
                            "simulation_logic_version": SIMULATION_LOGIC_VERSION,
                        },
                        "_record": {"observed_at_utc": "2026-07-06T08:00:00Z"},
                    },
                ]

        self.assertEqual(_latest_current_state_simulation_fixture_fingerprint(FakeStorage()), "newer")

    def test_latest_fixture_fingerprint_treats_old_simulation_logic_as_stale(self) -> None:
        class FakeStorage:
            def read_records(self, dataset: str, *, latest_only: bool = False) -> list[dict]:
                return [
                    {
                        "mode": "current_state",
                        "metadata": {
                            "active_fixture_count": 1,
                            "active_fixture_fingerprint": "old-logic",
                            "forecast_results": [],
                            "matrix_source_counts": {},
                            "simulation_logic_version": "before-outright-matrix-prior",
                        },
                        "_record": {"observed_at_utc": "2026-07-06T08:00:00Z"},
                    },
                ]

        self.assertEqual(_latest_current_state_simulation_fixture_fingerprint(FakeStorage()), "")

    def test_latest_fixture_fingerprint_treats_previous_outright_guard_version_as_stale(self) -> None:
        class FakeStorage:
            def read_records(self, dataset: str, *, latest_only: bool = False) -> list[dict]:
                return [
                    {
                        "mode": "current_state",
                        "metadata": {
                            "active_fixture_count": 1,
                            "active_fixture_fingerprint": "stale-v1",
                            "forecast_results": [],
                            "matrix_source_counts": {},
                            "simulation_logic_version": "outright-matrix-prior-v1",
                        },
                        "_record": {"observed_at_utc": "2026-07-06T08:00:00Z"},
                    },
                ]

        self.assertEqual(_latest_current_state_simulation_fixture_fingerprint(FakeStorage()), "")

    def test_latest_fixture_fingerprint_treats_missing_forecast_path_as_stale(self) -> None:
        class FakeStorage:
            def read_records(self, dataset: str, *, latest_only: bool = False) -> list[dict]:
                return [
                    {
                        "mode": "current_state",
                        "metadata": {
                            "active_fixture_count": 1,
                            "active_fixture_fingerprint": "old-format",
                        },
                        "_record": {"observed_at_utc": "2026-07-06T08:00:00Z"},
                    },
                ]

        self.assertEqual(_latest_current_state_simulation_fixture_fingerprint(FakeStorage()), "")

    def test_current_state_simulation_refreshes_when_interval_elapsed(self) -> None:
        current = {
            "active_fixture_count": 1,
            "active_fixture_fingerprint": "fixtures",
            "active_state_fingerprint": "state",
        }
        storage = FakeDatasetStorage(
            {
                SIMULATION_SUMMARY: [
                    {
                        "mode": "current_state",
                        "metadata": {
                            "active_fixture_count": 1,
                            "active_fixture_fingerprint": "fixtures",
                            "active_state_fingerprint": "state",
                            "forecast_results": [],
                            "matrix_source_counts": {},
                            "simulation_logic_version": SIMULATION_LOGIC_VERSION,
                        },
                        "_record": {"observed_at_utc": "2026-07-08T00:00:00Z"},
                    }
                ]
            }
        )

        fresh = _current_state_simulation_refresh_decision(
            storage,
            current,
            now=dt.datetime(2026, 7, 8, 5, 59, tzinfo=dt.timezone.utc),
        )
        stale = _current_state_simulation_refresh_decision(
            storage,
            current,
            now=dt.datetime(2026, 7, 8, 6, 0, tzinfo=dt.timezone.utc),
        )

        self.assertFalse(fresh["refresh"])
        self.assertEqual(fresh["reason"], "simulation_fresh")
        self.assertTrue(stale["refresh"])
        self.assertEqual(stale["trigger"], "scheduled_simulation_interval")

    def test_current_state_simulation_refreshes_when_result_state_changes(self) -> None:
        storage = FakeDatasetStorage(
            {
                SIMULATION_SUMMARY: [
                    {
                        "mode": "current_state",
                        "metadata": {
                            "active_fixture_count": 1,
                            "active_fixture_fingerprint": "fixtures",
                            "active_state_fingerprint": "old-state",
                            "forecast_results": [],
                            "matrix_source_counts": {},
                            "simulation_logic_version": SIMULATION_LOGIC_VERSION,
                        },
                        "_record": {"observed_at_utc": "2026-07-08T05:00:00Z"},
                    }
                ]
            }
        )

        decision = _current_state_simulation_refresh_decision(
            storage,
            {
                "active_fixture_count": 1,
                "active_fixture_fingerprint": "fixtures",
                "active_state_fingerprint": "new-state",
            },
            now=dt.datetime(2026, 7, 8, 5, 30, tzinfo=dt.timezone.utc),
        )

        self.assertTrue(decision["refresh"])
        self.assertEqual(decision["trigger"], "scheduled_simulation_state_change")

    def test_entity_maintenance_runs_every_24_hours(self) -> None:
        storage = FakeDatasetStorage(
            {
                PREDICTION_RUN_SUMMARIES: [
                    {
                        "maintenance": {"entity_maintenance": {"ran": True}},
                        "_record": {"observed_at_utc": "2026-07-08T00:00:00Z"},
                    }
                ]
            }
        )

        fresh = _entity_maintenance_decision(
            storage,
            now=dt.datetime(2026, 7, 8, 23, 59, tzinfo=dt.timezone.utc),
        )
        stale = _entity_maintenance_decision(
            storage,
            now=dt.datetime(2026, 7, 9, 0, 0, tzinfo=dt.timezone.utc),
        )

        self.assertFalse(fresh["run"])
        self.assertEqual(fresh["reason"], "entity_maintenance_fresh")
        self.assertTrue(stale["run"])
        self.assertEqual(stale["reason"], "entity_maintenance_interval_elapsed")

    def test_scheduled_automation_hook_forces_current_state_simulation_once(self) -> None:
        storage = FakeAutomationStorage()
        workflow = SimpleNamespace(context=SimpleNamespace(storage=storage, run_id="test_run"))
        state = object()
        run = object()
        forced_refresh = {
            "ran": True,
            "simulation_id": "simulation_forced",
            "trigger": "automation_hook:trigger_current_state_simulation",
            "iterations": 20000,
        }

        with patch("worldcup_predictions.cli._run_current_state_simulation", return_value=forced_refresh) as runner:
            hook_results, simulation_refresh = _run_scheduled_automation_hooks(
                workflow,
                state,
                run,
                simulation_refresh={"ran": False, "reason": "fixture_state_unchanged"},
            )

        runner.assert_called_once_with(
            workflow,
            state,
            run,
            trigger="automation_hook:trigger_current_state_simulation",
        )
        self.assertEqual(simulation_refresh, forced_refresh)
        self.assertEqual(hook_results[0]["status"], "success")
        self.assertEqual(hook_results[0]["result"]["simulation_id"], "simulation_forced")

        skipped_results, skipped_refresh = _run_scheduled_automation_hooks(
            workflow,
            state,
            run,
            simulation_refresh={"ran": False, "reason": "fixture_state_unchanged"},
        )

        self.assertEqual(skipped_results[0]["status"], "skipped")
        self.assertEqual(skipped_refresh["reason"], "fixture_state_unchanged")

    def test_scheduled_automation_hook_reuses_existing_simulation_refresh(self) -> None:
        storage = FakeAutomationStorage()
        workflow = SimpleNamespace(context=SimpleNamespace(storage=storage, run_id="test_run"))
        existing_refresh = {
            "ran": True,
            "simulation_id": "simulation_existing",
            "trigger": "scheduled_fixture_state_change",
            "iterations": 20000,
        }

        with patch("worldcup_predictions.cli._run_current_state_simulation") as runner:
            hook_results, simulation_refresh = _run_scheduled_automation_hooks(
                workflow,
                object(),
                object(),
                simulation_refresh=existing_refresh,
            )

        runner.assert_not_called()
        self.assertEqual(simulation_refresh, existing_refresh)
        self.assertEqual(hook_results[0]["status"], "success")
        self.assertEqual(hook_results[0]["result"]["satisfied_by"], "scheduled_simulation_refresh")


class FakeAutomationStorage:
    def __init__(self) -> None:
        self.rows: list[dict] = []

    def read_records(self, dataset: str, *, latest_only: bool = False) -> list[dict]:
        if dataset != AUTOMATION_HOOKS:
            return []
        return list(self.rows)

    def write_records(
        self,
        dataset: str,
        rows,
        *,
        source: str,
        run_id: str | None = None,
        **_kwargs,
    ) -> int:
        if dataset != AUTOMATION_HOOKS:
            return 0
        prepared = [dict(row) for row in rows]
        for row in prepared:
            row["_record"] = {"source": source, "run_id": run_id}
        self.rows.extend(prepared)
        return len(prepared)


class FakeDatasetStorage:
    def __init__(self, rows_by_dataset: dict[str, list[dict]]) -> None:
        self.rows_by_dataset = rows_by_dataset

    def read_records(self, dataset: str, *, latest_only: bool = False) -> list[dict]:
        return list(self.rows_by_dataset.get(dataset, []))


class ThirdPlacePinningTest(unittest.TestCase):
    """The heuristic third-place allocation must yield to the real draw.

    Live regression 2026-07-14: the heuristic assigned a different valid
    allocation than FIFA's actual draw, so Switzerland's known 2:0 over
    Algeria never attached and re-simulated in 40% of iterations, cascading
    phantom eliminations to Argentina's and Spain's paths.
    """

    def _placements(self) -> dict[str, str]:
        placements = {}
        for group in "ABCDEFGHIJKL":
            placements[f"1{group}"] = f"Winner {group}"
            placements[f"2{group}"] = f"Second {group}"
            placements[f"3{group}"] = f"Third {group}"
        return placements

    def _third_rankings(self) -> list[dict]:
        return [{"group": group} for group in "ABCDEFGHIJKL"]

    def test_real_pairs_pin_the_third_slot_groups(self) -> None:
        placements = self._placements()
        heuristic = assign_third_place_slots(self._third_rankings())
        # M74 pairs 1E against a third from A/B/C/D/F: force reality to be
        # a different valid choice than whatever the heuristic picked.
        options = [g for g in "ABCDF" if g != heuristic.get("3A/B/C/D/F")]
        real_group = options[0]
        real_pairs = [("Winner E", f"Third {real_group}")]

        pinned = third_assignments_from_real_pairs(placements, dict(heuristic), real_pairs)

        self.assertEqual(pinned["3A/B/C/D/F"], real_group)

    def test_invalid_or_unknown_real_pairs_change_nothing(self) -> None:
        placements = self._placements()
        heuristic = assign_third_place_slots(self._third_rankings())

        # Group L is not an allowed candidate for the M74 slot.
        pinned = third_assignments_from_real_pairs(placements, dict(heuristic), [("Winner E", "Third L")])
        self.assertEqual(pinned, heuristic)

        # No real pairs (day-one mode) is a strict no-op.
        self.assertEqual(third_assignments_from_real_pairs(placements, dict(heuristic), []), heuristic)

    def test_round_of_32_uses_pinned_pairing(self) -> None:
        placements = self._placements()
        heuristic = assign_third_place_slots(self._third_rankings())
        options = [g for g in "ABCDF" if g != heuristic.get("3A/B/C/D/F")]
        real_pairs = [("Winner E", f"Third {options[0]}")]

        matches = round_of_32_matches(placements, self._third_rankings(), real_pairs)
        m74 = next(m for m in matches if m["match_id"] == "M74")

        self.assertEqual(m74["home"], "Winner E")
        self.assertEqual(m74["away"], f"Third {options[0]}")


if __name__ == "__main__":
    unittest.main()


class SimulationIterationDefaultTest(unittest.TestCase):
    def test_default_iterations_balance_precision_and_host_budget(self) -> None:
        self.assertEqual(DEFAULT_SIMULATION_ITERATIONS, 20_000)
        self.assertEqual(
            TournamentSimulator(SimulationInputs(fixtures=[])).iterations,
            DEFAULT_SIMULATION_ITERATIONS,
        )
