from __future__ import annotations

import json
import unittest
from pathlib import Path

from worldcup_predictions.core.contracts import OutcomeProbabilities, Prediction, ScoreMatrixEntry, ScoreTip, Signal
from worldcup_predictions.core.datasets import OPTIMIZED_TIPS
from worldcup_predictions.core.events import EventName
from worldcup_predictions.core.plugin import PluginResult
from worldcup_predictions.core.workflow import WorkflowContext, WorkflowRun
from worldcup_predictions.core.signals import LIVE_DRAW_ADJUSTMENT, ML_HDA_PROBABILITIES
from worldcup_predictions.entities.dynamic_aliases import registry_from_generated_alias_rows
from worldcup_predictions.evaluation.model_calibration import calibrate_baseline_model
from worldcup_predictions.evaluation.bonus_tracker import build_bonus_tracker_rows
from worldcup_predictions.evaluation.bonus_tracker import parse_numeric_answer, numeric_status
from worldcup_predictions.evaluation.match_intel import build_match_intel_rows
from worldcup_predictions.evaluation.prediction_snapshots import compare_snapshot_rows, prediction_snapshot_rows
from worldcup_predictions.evaluation.scheduled_update import build_prediction_run_summary_row
from worldcup_predictions.model import HistoricalResult
from worldcup_predictions.plugins.sources.fixtures.football_data.plugin import parse_football_data_matches, parse_football_data_teams
from worldcup_predictions.plugins.signals.ml_outcome.plugin import ml_signals_for_fixtures, train_outcome_bucket_model
from worldcup_predictions.plugins.signals.player_impact.plugin import player_impact_rows, signals_from_impact_rows
from worldcup_predictions.plugins.sources.enrichment.public_analysis.plugin import match_analysis_cause_rows, match_analysis_team_adjustment_rows
from worldcup_predictions.plugins.sources.fixtures.srf_public.plugin import parse_srf_results
from worldcup_predictions.tournament import FixtureRecord, ResultRecord, TeamResolver, TournamentState


def fixture() -> FixtureRecord:
    resolver = TeamResolver.default()
    return FixtureRecord(
        event_date="2026-07-10T18:00:00Z",
        home_team=resolver.resolve("Brazil"),
        away_team=resolver.resolve("Japan"),
        group="Group A",
        stage="Group Stage",
    )


class LegacyFeatureMigrationTest(unittest.TestCase):
    def test_football_data_match_parser_writes_fixtures_and_results(self) -> None:
        payload = {
            "matches": [
                {
                    "id": 1,
                    "utcDate": "2026-07-10T18:00:00Z",
                    "status": "FINISHED",
                    "stage": "GROUP_STAGE",
                    "group": "Group A",
                    "matchday": 1,
                    "homeTeam": {"name": "Brazil", "tla": "BRA"},
                    "awayTeam": {"name": "Japan", "tla": "JPN"},
                    "score": {"winner": "HOME_TEAM", "fullTime": {"home": 2, "away": 0}},
                }
            ]
        }

        fixtures, results = parse_football_data_matches(payload)

        self.assertEqual(fixtures[0].home_team.fifa_code, "BRA")
        self.assertEqual(results[0].score, ScoreTip(2, 0))

    def test_football_data_team_parser_writes_squad_players(self) -> None:
        payload = {
            "teams": [
                {
                    "id": 10,
                    "name": "Brazil",
                    "tla": "BRA",
                    "squad": [{"id": 99, "name": "Player One", "position": "Attacker"}],
                }
            ]
        }

        teams, players = parse_football_data_teams(payload)

        self.assertEqual(teams[0]["fifa_code"], "BRA")
        self.assertEqual(players[0]["team"], "Brazil")

    def test_player_impact_rows_emit_capped_signals(self) -> None:
        squad_rows = [
            {
                "team": "Brazil",
                "fifa_code": "BRA",
                "player_name": f"Brazil Player {index}",
                "position": "attack" if index < 8 else "defense",
                "market_value_in_eur": 50_000_000 - index,
                "match_score": 1.0,
            }
            for index in range(16)
        ] + [
            {
                "team": "Japan",
                "fifa_code": "JPN",
                "player_name": f"Japan Player {index}",
                "position": "defense",
                "market_value_in_eur": 5_000_000 - index,
                "match_score": 1.0,
            }
            for index in range(16)
        ]

        rows = player_impact_rows(squad_rows)
        signals = signals_from_impact_rows([fixture()], rows)

        self.assertEqual({row["fifa_code"] for row in rows}, {"BRA", "JPN"})
        self.assertTrue(any(signal.name == "team_expected_goals_factor" for signal in signals))

    def test_ml_outcome_bucket_model_emits_hda_signal(self) -> None:
        resolver = TeamResolver.default()
        results = []
        for index in range(120):
            home = resolver.resolve("Brazil" if index % 2 == 0 else "Japan")
            away = resolver.resolve("Japan" if index % 2 == 0 else "Brazil")
            results.append(HistoricalResult(f"2020-01-{(index % 28) + 1:02d}", home, away, ScoreTip(2, 0)))

        model = train_outcome_bucket_model(results, min_year=2019)
        signals = ml_signals_for_fixtures([fixture()], results, model)

        self.assertTrue(model)
        self.assertEqual(signals[0].name, ML_HDA_PROBABILITIES)
        self.assertIn("prob_home", signals[0].metadata)

    def test_prediction_snapshot_comparison_measures_matrix_delta(self) -> None:
        match = fixture().to_fixture()
        before = Prediction(
            fixture=match,
            most_likely=ScoreTip(1, 0),
            outcome_probabilities=OutcomeProbabilities(0.5, 0.3, 0.2),
            confidence_label="Medium",
            confidence_percent=0.5,
            score_matrix=[ScoreMatrixEntry(1, 0, 0.4), ScoreMatrixEntry(0, 0, 0.3)],
        )
        after = Prediction(
            fixture=match,
            most_likely=ScoreTip(1, 1),
            outcome_probabilities=OutcomeProbabilities(0.4, 0.4, 0.2),
            confidence_label="Medium",
            confidence_percent=0.4,
            score_matrix=[ScoreMatrixEntry(1, 0, 0.2), ScoreMatrixEntry(1, 1, 0.4)],
        )

        before_row = prediction_snapshot_rows("before", [before], [])[0]
        after_row = prediction_snapshot_rows("after", [after], [])[0]
        comparison = compare_snapshot_rows(before_row, after_row, comparison_id="test")

        self.assertTrue(comparison["most_likely_changed"])
        self.assertGreater(comparison["matrix_total_variation"], 0)

    def test_match_intel_flags_fragile_predictions(self) -> None:
        prediction = Prediction(
            fixture=fixture().to_fixture(),
            most_likely=ScoreTip(0, 0),
            outcome_probabilities=OutcomeProbabilities(0.36, 0.34, 0.30),
            confidence_label="Low",
            confidence_percent=0.36,
            expected_home_goals=0.9,
            expected_away_goals=0.8,
        )

        rows = build_match_intel_rows([prediction], [])

        self.assertEqual(rows[0]["review_priority"], "medium")
        self.assertIn("draw risk", rows[0]["review_reason"])

    def test_srf_public_final_results_parser(self) -> None:
        props = {
            "bet": {
                "type": "score",
                "event_date": "2026-07-10T18:00:00Z",
                "event_state": "over",
                "teams": [{"name": "Brazil"}, {"name": "Japan"}],
                "final_results": [2, 0],
            }
        }
        html = '<div data-react-class="ScoreBet" data-react-props="' + json.dumps(props).replace('"', "&quot;") + '"></div>'

        rows = parse_srf_results(html)

        self.assertEqual(rows[0].source, "srf_public")
        self.assertEqual(rows[0].score, ScoreTip(2, 0))

    def test_bonus_numeric_status(self) -> None:
        target = parse_numeric_answer("7")

        self.assertEqual(numeric_status(8, target, False), "impossible")
        self.assertEqual(numeric_status(7, target, False), "still_possible_at_limit")

    def test_twenty_min_virtual_tracker_scores_optimized_recommendations(self) -> None:
        storage = InMemoryStorage()
        match = fixture()
        storage.write_records(
            OPTIMIZED_TIPS,
            [
                {
                    "record_key": f"20min.ch|{match.key}",
                    "provider": "20min.ch",
                    "fixture_key": match.key,
                    "selection_type": "outcome",
                    "selection": "Brazil",
                    "source": "optimized_tip",
                }
            ],
            source="test",
        )
        state = TournamentState(
            fixtures=[match],
            results=[
                ResultRecord(
                    event_date=match.event_date,
                    home_team=match.home_team,
                    away_team=match.away_team,
                    score=ScoreTip(2, 0),
                )
            ],
            standings={},
        )

        rows = build_bonus_tracker_rows(storage, state, provider="20min.ch")

        virtual = next(row for row in rows if row["question_key"] == "virtual_match_points")
        self.assertEqual(virtual["current_value"], 5.0)
        self.assertEqual(virtual["status"], "current_points")

    def test_model_calibration_ranks_candidate_configs(self) -> None:
        resolver = TeamResolver.default()
        rows = []
        for year in (2014, 2018, 2022):
            for index in range(8):
                home = resolver.resolve("Brazil" if index % 2 == 0 else "Japan")
                away = resolver.resolve("Japan" if index % 2 == 0 else "Brazil")
                rows.append(HistoricalResult(f"{year}-06-{index + 10:02d}", home, away, ScoreTip(2, 0), tournament="FIFA World Cup"))
        calibration = calibrate_baseline_model(rows)

        self.assertTrue(calibration)
        self.assertTrue(calibration[0]["selected"])
        self.assertIn("dixon_coles_rho", calibration[0]["parameters"])

    def test_public_analysis_causes_create_team_adjustments(self) -> None:
        rows = [
            {
                "fixture_key": fixture().key,
                "event_date": fixture().event_date,
                "phase": "postgame",
                "home_team": "Brazil",
                "away_team": "Japan",
                "home_fifa_code": "BRA",
                "away_fifa_code": "JPN",
                "title": "Brazil Japan report",
                "description": "Brazil were wasteful from big chances and won through set piece pressure.",
                "signal_type": "finishing_context",
                "source_url": "https://www.bbc.com/sport/football/test",
                "source_name": "BBC Sport",
                "reliability": 0.90,
                "metadata": {"note": {"categories": ["finishing_context", "set_piece_context"]}},
            }
        ]

        causes = match_analysis_cause_rows(rows)
        adjustments = match_analysis_team_adjustment_rows(causes)

        self.assertEqual({cause["cause_type"] for cause in causes}, {"finishing_context", "set_piece_context"})
        self.assertTrue(adjustments)
        self.assertGreater(adjustments[0]["expected_goals_factor"], 1.0)

    def test_generated_alias_registry_marks_ambiguous_player_aliases(self) -> None:
        rows = [
            {"entity_type": "player", "canonical_id": "BRA:alex one", "canonical_name": "Alex One", "alias": "Alex", "ambiguous": True},
            {"entity_type": "player", "canonical_id": "JPN:alex two", "canonical_name": "Alex Two", "alias": "Alex", "ambiguous": True},
        ]

        registry = registry_from_generated_alias_rows(rows, include_static=False)
        resolved = registry.resolve("Alex", entity_type="player")

        self.assertIsNotNone(resolved)
        assert resolved is not None
        self.assertIsNone(resolved.canonical_id)
        self.assertEqual(resolved.method, "ambiguous")

    def test_live_global_calibration_emits_draw_signal(self) -> None:
        from worldcup_predictions.plugins.signals.live_calibration.plugin import global_calibration_rows_from_state, global_calibration_signals

        resolver = TeamResolver.default()
        fixtures = []
        results = []
        for index in range(4):
            match = FixtureRecord(
                event_date=f"2026-06-{index + 10:02d}T18:00:00Z",
                home_team=resolver.resolve("Brazil"),
                away_team=resolver.resolve("Japan"),
            )
            fixtures.append(match)
            results.append(ResultRecord(match.event_date, match.home_team, match.away_team, ScoreTip(1, 1)))
        state = TournamentState(fixtures=fixtures, results=results, standings={})

        rows = global_calibration_rows_from_state(state, [])
        signals = global_calibration_signals(rows)

        self.assertTrue(any(signal.name == LIVE_DRAW_ADJUSTMENT for signal in signals))

    def test_scheduled_run_summary_records_plugin_and_signal_manifest(self) -> None:
        match = fixture().to_fixture()
        prediction = Prediction(
            fixture=match,
            most_likely=ScoreTip(1, 0),
            outcome_probabilities=OutcomeProbabilities(0.5, 0.3, 0.2),
            confidence_label="Medium-low",
            confidence_percent=0.5,
        )
        context = WorkflowContext(project_root=Path("."), data_root=Path("data"))
        context.record_result(
            PluginResult(
                plugin_id="test_source",
                event=EventName.FEATURE_SIGNALS_REQUESTED.value,
                signals=[
                    Signal(
                        name=LIVE_DRAW_ADJUSTMENT,
                        source="live_calibration",
                        value=0.02,
                    )
                ],
            )
        )
        run = WorkflowRun(context=context, predictions=[prediction], optimized_tips=[], diagnostics=[])

        row = build_prediction_run_summary_row(run, snapshot_id="scheduled_test", snapshot_rows=1)

        self.assertEqual(row["snapshot_id"], "scheduled_test")
        self.assertEqual(row["prediction_count"], 1)
        self.assertEqual(row["signal_names"][LIVE_DRAW_ADJUSTMENT], 1)
        self.assertEqual(row["plugin_events"][0]["plugin_id"], "test_source")


class InMemoryStorage:
    def __init__(self) -> None:
        self.rows: dict[str, list[dict]] = {}

    def read_records(self, dataset: str, *, latest_only: bool = False, **_kwargs):
        return list(self.rows.get(dataset, []))

    def write_records(self, dataset: str, rows, **_kwargs):
        materialized = [dict(row) for row in rows]
        self.rows.setdefault(dataset, []).extend(materialized)
        return len(materialized)


if __name__ == "__main__":
    unittest.main()
