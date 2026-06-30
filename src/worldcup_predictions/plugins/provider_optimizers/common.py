"""Shared provider optimizer helpers."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from worldcup_predictions.core.contracts import (
    Fixture,
    OptimizedTip,
    Prediction,
    ProviderRuleset,
    ScoreMatrixEntry,
    ScoreTip,
)


def score_outcome(score: ScoreTip) -> str:
    if score.home > score.away:
        return "home"
    if score.home < score.away:
        return "away"
    return "draw"


def fixture_stage(fixture: Fixture) -> str:
    return str(fixture.stage or fixture.metadata.get("stage") or "").casefold()


def stage_contains(stage: str, *needles: str) -> bool:
    return any(needle in stage for needle in needles)


@dataclass(frozen=True)
class ComponentScoreRules:
    """Additive exact-score rules for one provider phase."""

    provider: str
    version: str
    phase: str
    outcome_points: float
    home_goals_points: float
    away_goals_points: float
    goal_difference_points: float
    exact_score_points: float | None = None
    metadata: dict[str, Any] | None = None

    def ruleset(self) -> ProviderRuleset:
        expected_exact = (
            self.exact_score_points
            if self.exact_score_points is not None
            else self.outcome_points + self.home_goals_points + self.away_goals_points + self.goal_difference_points
        )
        return ProviderRuleset(
            provider=self.provider,
            version=self.version,
            metadata={
                "phase": self.phase,
                "outcome_points": self.outcome_points,
                "home_goals_points": self.home_goals_points,
                "away_goals_points": self.away_goals_points,
                "goal_difference_points": self.goal_difference_points,
                "exact_score_points": expected_exact,
                **dict(self.metadata or {}),
            },
        )

    def points_for_tip(self, tip: ScoreTip, actual: ScoreTip) -> float:
        """Return provider points for one submitted tip and one final score."""

        if tip == actual and self.exact_score_points is not None:
            return self.exact_score_points

        points = 0.0
        tip_outcome = score_outcome(tip)
        actual_outcome = score_outcome(actual)
        if tip_outcome == actual_outcome:
            points += self.outcome_points
        if tip.home == actual.home:
            points += self.home_goals_points
        if tip.away == actual.away:
            points += self.away_goals_points
        if tip_outcome == actual_outcome and (tip.home - tip.away) == (actual.home - actual.away):
            points += self.goal_difference_points
        return points


@dataclass(frozen=True)
class OptimizationCandidate:
    """Expected value for one exact-score candidate."""

    tip: ScoreTip
    expected_points: float
    exact_probability: float

    def to_dict(self) -> dict[str, Any]:
        return {
            "tip": self.tip.as_text(),
            "tip_home": self.tip.home,
            "tip_away": self.tip.away,
            "expected_points": self.expected_points,
            "exact_probability": self.exact_probability,
        }


class ScoreMatrixOptimizer:
    """Optimize provider tips from a neutral exact-score probability matrix."""

    def optimize(
        self,
        prediction: Prediction,
        rules: ComponentScoreRules,
        *,
        optimizer_id: str,
        alternatives_limit: int = 5,
    ) -> OptimizedTip:
        entries = self._normalized_entries(prediction.score_matrix)
        if not entries:
            return OptimizedTip(
                ruleset=rules.ruleset(),
                fixture_key=prediction.fixture.key,
                tip=prediction.most_likely,
                expected_points=0.0,
                optimizer_id=optimizer_id,
                selection=prediction.most_likely.as_text(),
                confidence=prediction.confidence_percent,
                rationale="No score matrix was available; used the neutral most-likely result as a fallback.",
                metadata={"fallback": "missing_score_matrix", "phase": rules.phase},
            )

        candidates = self._rank_candidates(prediction.most_likely, entries, rules)
        best = candidates[0]
        alternatives = [candidate.to_dict() for candidate in candidates[1 : alternatives_limit + 1]]
        return OptimizedTip(
            ruleset=rules.ruleset(),
            fixture_key=prediction.fixture.key,
            tip=best.tip,
            expected_points=best.expected_points,
            optimizer_id=optimizer_id,
            selection=best.tip.as_text(),
            confidence=prediction.confidence_percent,
            rationale=(
                f"Optimized expected {rules.provider} points from {len(entries)} exact-score probabilities "
                f"using {rules.phase} scoring."
            ),
            alternatives=alternatives,
            metadata={
                "phase": rules.phase,
                "exact_probability": best.exact_probability,
                "score_matrix_entries": len(entries),
            },
        )

    def _normalized_entries(self, score_matrix: list[ScoreMatrixEntry]) -> list[ScoreMatrixEntry]:
        positive_entries = [entry for entry in score_matrix if entry.probability > 0]
        total_probability = sum(entry.probability for entry in positive_entries)
        if total_probability <= 0:
            return []
        return [
            ScoreMatrixEntry(
                home=entry.home,
                away=entry.away,
                probability=entry.probability / total_probability,
                metadata=entry.metadata,
            )
            for entry in positive_entries
        ]

    def _rank_candidates(
        self,
        most_likely: ScoreTip,
        entries: list[ScoreMatrixEntry],
        rules: ComponentScoreRules,
    ) -> list[OptimizationCandidate]:
        candidate_tips = {(entry.home, entry.away): entry.as_tip() for entry in entries}
        candidate_tips[(most_likely.home, most_likely.away)] = most_likely
        candidates = [
            self._score_candidate(candidate, entries, rules)
            for candidate in candidate_tips.values()
        ]
        if rules.provider == "srf.ch":
            return sorted(
                candidates,
                key=lambda item: (
                    -item.expected_points,
                    -item.tip.home,
                    -item.tip.away,
                ),
            )
        return sorted(
            candidates,
            key=lambda item: (
                -item.expected_points,
                -item.exact_probability,
                item.tip.home + item.tip.away,
                item.tip.home,
                item.tip.away,
            ),
        )

    def _score_candidate(
        self,
        candidate: ScoreTip,
        entries: list[ScoreMatrixEntry],
        rules: ComponentScoreRules,
    ) -> OptimizationCandidate:
        expected_points = 0.0
        exact_probability = 0.0
        for entry in entries:
            actual = entry.as_tip()
            expected_points += entry.probability * rules.points_for_tip(candidate, actual)
            if actual == candidate:
                exact_probability += entry.probability
        return OptimizationCandidate(
            tip=candidate,
            expected_points=expected_points,
            exact_probability=exact_probability,
        )
