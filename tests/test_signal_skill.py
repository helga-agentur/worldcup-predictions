from __future__ import annotations

import unittest

from worldcup_predictions.core.contracts import ScoreMatrixEntry, Signal
from worldcup_predictions.evaluation.signal_skill import (
    SKILL_MULTIPLIER_CAP,
    SKILL_MULTIPLIER_FLOOR,
    brier,
    outcome_one_hot,
    skill_multiplier,
)
from worldcup_predictions.model.contracts import ModelSignalPolicy
from worldcup_predictions.model.signal_application import apply_hda_probabilities


class SkillMultiplierTest(unittest.TestCase):
    def test_neutral_without_evidence(self) -> None:
        self.assertEqual(skill_multiplier(0.5, 0), 1.0)
        self.assertAlmostEqual(skill_multiplier(-0.154, 1), 1.0, delta=0.05)

    def test_bad_sources_sink_toward_the_floor_with_evidence(self) -> None:
        # The SRF experts' real tournament record: edge -0.154 over 100.
        value = skill_multiplier(-0.154, 100)
        self.assertLess(value, 0.45)
        self.assertGreaterEqual(value, SKILL_MULTIPLIER_FLOOR)

    def test_good_sources_earn_a_bounded_boost(self) -> None:
        self.assertGreater(skill_multiplier(0.05, 50), 1.0)
        self.assertEqual(skill_multiplier(2.0, 1000), SKILL_MULTIPLIER_CAP)
        self.assertEqual(skill_multiplier(-2.0, 1000), SKILL_MULTIPLIER_FLOOR)

    def test_recovery_is_symmetric_and_dynamic(self) -> None:
        # A source that starts helping again climbs back above its old value.
        worse = skill_multiplier(-0.10, 60)
        better = skill_multiplier(-0.02, 80)
        self.assertGreater(better, worse)

    def test_brier_and_one_hot(self) -> None:
        self.assertEqual(outcome_one_hot(2, 0), (1, 0, 0))
        self.assertEqual(outcome_one_hot(1, 1), (0, 1, 0))
        self.assertEqual(brier((1.0, 0.0, 0.0), (1, 0, 0)), 0.0)
        self.assertEqual(brier((1.0, 0.0, 0.0), (0, 0, 1)), 2.0)


class SkillMultiplierApplicationTest(unittest.TestCase):
    def _matrix(self) -> list[ScoreMatrixEntry]:
        return [
            ScoreMatrixEntry(home=1, away=0, probability=0.4),
            ScoreMatrixEntry(home=1, away=1, probability=0.3),
            ScoreMatrixEntry(home=0, away=1, probability=0.3),
        ]

    def _signal(self) -> Signal:
        return Signal(
            name="expert_hda_probabilities",
            source="srf_experts",
            fixture_key="F",
            value=1.0,
            weight=0.20,
            confidence=0.81,
            metadata={"prob_home": 1.0, "prob_draw": 0.0, "prob_away": 0.0},
        )

    def test_multiplier_scales_the_applied_weight(self) -> None:
        policy = ModelSignalPolicy(signal_skill_multipliers={"expert_hda_probabilities:srf_experts": 0.36})
        _matrix, meta = apply_hda_probabilities(self._matrix(), self._signal(), policy)
        self.assertAlmostEqual(meta["weight"], 0.20 * 0.81 * 0.36, places=6)
        self.assertEqual(meta["skill_multiplier"], 0.36)
        self.assertEqual(meta["source"], "srf_experts")

    def test_unknown_sources_stay_neutral(self) -> None:
        _matrix, meta = apply_hda_probabilities(self._matrix(), self._signal(), ModelSignalPolicy())
        self.assertAlmostEqual(meta["weight"], 0.20 * 0.81, places=6)
        self.assertEqual(meta["skill_multiplier"], 1.0)


if __name__ == "__main__":
    unittest.main()
