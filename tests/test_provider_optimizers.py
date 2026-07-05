from __future__ import annotations

import unittest

from worldcup_predictions.core.contracts import (
    Fixture,
    OutcomeProbabilities,
    Prediction,
    ScoreMatrixEntry,
    ScoreTip,
)
from worldcup_predictions.core.events import EventName
from worldcup_predictions.plugins.providers import (
    SrfChProviderOptimizerPlugin,
    TwentyMinChProviderOptimizerPlugin,
)
from worldcup_predictions.evaluation.provider_knockout_audit import build_provider_knockout_audit_rows
from worldcup_predictions.providers import ScoreMatrixOptimizer, srf_rules_for_fixture


class DummyContext:
    def __init__(self) -> None:
        self.state = {}


def build_prediction(
    score_matrix: list[ScoreMatrixEntry] | None = None,
    *,
    stage: str = "Group Stage",
    group: str | None = "A",
    metadata: dict | None = None,
) -> Prediction:
    probabilities = OutcomeProbabilities(home=0.55, draw=0.25, away=0.20)
    return Prediction(
        fixture=Fixture(
            event_date="2026-06-29T18:00:00Z",
            home_team="Brazil",
            away_team="Japan",
            stage=stage,
            group=group,
        ),
        most_likely=ScoreTip(1, 0),
        outcome_probabilities=probabilities,
        confidence_label="Medium-high",
        confidence_percent=probabilities.max_probability(),
        expected_home_goals=1.4,
        expected_away_goals=0.8,
        source="test",
        score_matrix=score_matrix or [],
        metadata=metadata or {},
    )


class ProviderOptimizerTest(unittest.TestCase):
    def test_srf_group_stage_points_are_additive(self) -> None:
        rules = srf_rules_for_fixture(
            Fixture(
                event_date="2026-06-29T18:00:00Z",
                home_team="Brazil",
                away_team="Japan",
                group="A",
            )
        )

        self.assertEqual(rules.points_for_tip(ScoreTip(2, 1), ScoreTip(2, 1)), 10)
        self.assertEqual(rules.points_for_tip(ScoreTip(2, 1), ScoreTip(3, 2)), 8)
        self.assertEqual(rules.points_for_tip(ScoreTip(2, 1), ScoreTip(2, 0)), 6)
        self.assertEqual(rules.points_for_tip(ScoreTip(1, 1), ScoreTip(2, 2)), 8)

    def test_optimizer_picks_expected_points_tip_from_score_matrix(self) -> None:
        prediction = build_prediction(
            [
                ScoreMatrixEntry(1, 0, 0.25),
                ScoreMatrixEntry(2, 0, 0.20),
                ScoreMatrixEntry(1, 1, 0.18),
                ScoreMatrixEntry(0, 0, 0.10),
                ScoreMatrixEntry(2, 1, 0.07),
            ]
        )

        optimized = ScoreMatrixOptimizer().optimize(
            prediction,
            srf_rules_for_fixture(prediction.fixture),
            optimizer_id="test_optimizer",
        )

        self.assertEqual(optimized.ruleset.provider, "srf.ch")
        self.assertEqual(optimized.tip, ScoreTip(1, 0))
        self.assertGreater(optimized.expected_points, 0)
        self.assertGreater(len(optimized.alternatives), 0)

    def test_srf_optimizer_uses_pre_refactor_high_score_tie_break(self) -> None:
        prediction = build_prediction(
            [
                ScoreMatrixEntry(0, 0, 0.5),
                ScoreMatrixEntry(1, 1, 0.5),
            ]
        )

        optimized = ScoreMatrixOptimizer().optimize(
            prediction,
            srf_rules_for_fixture(prediction.fixture),
            optimizer_id="test_optimizer",
        )

        self.assertEqual(optimized.tip, ScoreTip(1, 1))

    def test_srf_plugin_falls_back_without_score_matrix(self) -> None:
        plugin = SrfChProviderOptimizerPlugin()
        prediction = build_prediction()

        result = plugin.handle(
            EventName.PROVIDER_OPTIMIZATION_REQUESTED,
            DummyContext(),
            {"prediction": prediction},
        )

        self.assertEqual(result.optimized_tips[0].tip, prediction.most_likely)
        self.assertEqual(result.diagnostics[0].level, "warning")

    def test_twenty_min_plugin_optimizes_group_outcome_selection(self) -> None:
        plugin = TwentyMinChProviderOptimizerPlugin()
        prediction = build_prediction()

        result = plugin.handle(
            EventName.PROVIDER_OPTIMIZATION_REQUESTED,
            DummyContext(),
            {"prediction": prediction},
        )

        optimized = result.optimized_tips[0]
        self.assertEqual(optimized.ruleset.provider, "20min.ch")
        self.assertEqual(optimized.selection_type, "outcome")
        self.assertEqual(optimized.selection, "Brazil")
        self.assertAlmostEqual(optimized.expected_points, 2.75)
        self.assertEqual(result.diagnostics, [])

    def test_twenty_min_plugin_optimizes_knockout_advancement_selection(self) -> None:
        plugin = TwentyMinChProviderOptimizerPlugin()
        prediction = build_prediction(
            stage="Final",
            group=None,
            metadata={"advancement_probabilities": {"home": 0.48, "away": 0.52}},
        )

        result = plugin.handle(
            EventName.PROVIDER_OPTIMIZATION_REQUESTED,
            DummyContext(),
            {"prediction": prediction},
        )

        optimized = result.optimized_tips[0]
        self.assertEqual(optimized.selection_type, "advancement")
        self.assertEqual(optimized.selection, "Japan")
        self.assertAlmostEqual(optimized.expected_points, 20.8)
        self.assertEqual(optimized.metadata["correct_selection_points"], 40)

    def test_provider_knockout_audit_compares_exact_score_and_advancement(self) -> None:
        prediction = build_prediction(
            stage="Final",
            group=None,
            metadata={"advancement_probabilities": {"home": 0.48, "away": 0.52}},
        )
        srf_tip = ScoreMatrixOptimizer().optimize(
            prediction,
            srf_rules_for_fixture(prediction.fixture),
            optimizer_id="test:srf",
        )
        twenty_tip = TwentyMinChProviderOptimizerPlugin().handle(
            EventName.PROVIDER_OPTIMIZATION_REQUESTED,
            DummyContext(),
            {"prediction": prediction},
        ).optimized_tips[0]

        rows = build_provider_knockout_audit_rows(
            [
                {
                    "fixture_key": prediction.fixture.key,
                    "event_date": prediction.fixture.event_date,
                    "home_team": prediction.fixture.home_team,
                    "away_team": prediction.fixture.away_team,
                    "stage": "Final",
                    "is_knockout": True,
                    "optimized_tips": [srf_tip.to_dict(), twenty_tip.to_dict()],
                    "advancement_probabilities": prediction.metadata["advancement_probabilities"],
                    "srf_tip": srf_tip.display_text(),
                    "twenty_min_tip": twenty_tip.display_text(),
                }
            ]
        )

        self.assertEqual({row["provider"] for row in rows}, {"srf.ch", "20min.ch"})
        self.assertTrue(all(row["optimizer_divergence"] for row in rows))


if __name__ == "__main__":
    unittest.main()
