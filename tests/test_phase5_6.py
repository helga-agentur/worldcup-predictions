from __future__ import annotations

import unittest
from pathlib import Path

from worldcup_predictions.core.contracts import OutcomeProbabilities, Prediction, ScoreTip
from worldcup_predictions.core.workflow import WorkflowContext, WorkflowRun
from worldcup_predictions.evaluation.bonus_tracker import _track_champion
from worldcup_predictions.evaluation.match_intel import build_match_intel_rows
from worldcup_predictions.evaluation.postmatch import _xg_winner
from worldcup_predictions.evaluation.prediction_snapshots import compare_snapshot_rows
from worldcup_predictions.evaluation.scheduled_update import build_prediction_run_summary_row
from worldcup_predictions.tournament import FixtureRecord, TeamResolver, TournamentState


def _fixture():
    resolver = TeamResolver.default()
    return FixtureRecord(
        event_date="2026-06-20T18:00:00Z",
        home_team=resolver.resolve("Brazil"),
        away_team=resolver.resolve("Japan"),
        stage="Group Stage",
    ).to_fixture()


class TipChangedTest(unittest.TestCase):
    def test_tip_change_detected(self) -> None:
        before = {"fixture_key": "F", "score_matrix": [], "optimized_tips": [{"provider": "srf.ch", "tip": "2:1"}]}
        after = {"fixture_key": "F", "score_matrix": [], "optimized_tips": [{"provider": "srf.ch", "tip": "1:1"}]}
        self.assertTrue(compare_snapshot_rows(before, after, comparison_id="c")["tip_changed"])
        self.assertFalse(compare_snapshot_rows(before, before, comparison_id="c")["tip_changed"])


class MatchIntelDisagreementTest(unittest.TestCase):
    def test_market_disagreement_flagged(self) -> None:
        prediction = Prediction(
            fixture=_fixture(),
            most_likely=ScoreTip(2, 1),
            outcome_probabilities=OutcomeProbabilities(0.50, 0.25, 0.25),
            confidence_label="Medium",
            confidence_percent=0.50,
            expected_home_goals=1.6,
            expected_away_goals=1.1,
            metadata={
                "signal_adjustments": [
                    {"signal": "market_hda_probabilities", "target_home": 0.20, "target_draw": 0.30, "target_away": 0.50}
                ]
            },
        )
        row = build_match_intel_rows([prediction], [])[0]
        self.assertTrue(row["market_disagreement"])
        self.assertIn("market", row["review_reason"])


class PostmatchXgTest(unittest.TestCase):
    def test_xg_winner(self) -> None:
        self.assertEqual(_xg_winner(2.1, 0.8), "home")
        self.assertEqual(_xg_winner(0.5, 1.2), "away")
        self.assertEqual(_xg_winner(1.0, 1.0), "draw")
        self.assertIsNone(_xg_winner(None, 1.0))


class BonusTrackerSimEnrichmentTest(unittest.TestCase):
    def _row(self) -> dict:
        # track_bonus_answer pre-populates these before dispatching to _track_champion.
        return {"submitted_answer_canonical": "Brazil", "current_state": "", "data_source": "tournament_state"}

    def test_champion_enriched_with_simulated_probability(self) -> None:
        state = TournamentState(fixtures=[], results=[], standings={})
        out = _track_champion(self._row(), state, {"champion": [{"answer": "Brazil", "probability": 0.25}]})
        self.assertEqual(out["simulated_probability"], 0.25)
        self.assertIn("25.0%", out["current_state"])
        self.assertEqual(out["data_source"], "tournament_state+simulation")

    def test_champion_without_sim_falls_back(self) -> None:
        state = TournamentState(fixtures=[], results=[], standings={})
        out = _track_champion(self._row(), state, {})
        self.assertNotIn("simulated_probability", out)
        self.assertEqual(out["data_source"], "tournament_state")


class RunSummaryRollupTest(unittest.TestCase):
    def test_prediction_summary_records_outcome_distribution(self) -> None:
        prediction = Prediction(
            fixture=_fixture(),
            most_likely=ScoreTip(2, 1),
            outcome_probabilities=OutcomeProbabilities(0.5, 0.3, 0.2),
            confidence_label="Medium",
            confidence_percent=0.5,
            expected_home_goals=1.5,
            expected_away_goals=1.0,
        )
        context = WorkflowContext(project_root=Path("."), data_root=Path("data"))
        run = WorkflowRun(context=context, predictions=[prediction], optimized_tips=[], diagnostics=[])
        row = build_prediction_run_summary_row(run, snapshot_id="s", snapshot_rows=1)
        summary = row["prediction_summary"]
        self.assertEqual(summary["most_likely_outcomes"]["home"], 1)
        self.assertAlmostEqual(summary["average_confidence"], 0.5, places=6)
        self.assertAlmostEqual(summary["average_expected_total_goals"], 2.5, places=6)


if __name__ == "__main__":
    unittest.main()
