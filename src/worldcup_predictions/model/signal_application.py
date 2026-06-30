"""Apply typed model signals to expected goals and score matrices."""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Callable

from worldcup_predictions.core.contracts import ScoreMatrixEntry, Signal
from worldcup_predictions.core.signals import (
    EXPERT_HDA_PROBABILITIES,
    GROUP_DRAW_PRESSURE,
    LIVE_DRAW_ADJUSTMENT,
    LIVE_FAVORITE_OUTCOME_FACTOR,
    LIVE_SCORE_TAIL_FACTOR,
    MARKET_GOAL_DIFF,
    MARKET_HDA_PROBABILITIES,
    MARKET_TOTAL_GOALS,
    ML_HDA_PROBABILITIES,
    TEAM_EXPECTED_GOALS_FACTOR,
    TOTAL_GOALS_FACTOR,
)
from worldcup_predictions.model.contracts import BaselineModelConfig, ModelSignalPolicy
from worldcup_predictions.model.score_matrix import reweight_draw, reweight_outcomes
from worldcup_predictions.model.score_matrix import apply_score_overdispersion, normalize_score_matrix, outcome_probabilities


ExpectedGoalsApplier = Callable[[float, float, Signal, BaselineModelConfig, ModelSignalPolicy], tuple[float, float, dict | None]]
ScoreMatrixApplier = Callable[[list[ScoreMatrixEntry], Signal, ModelSignalPolicy], tuple[list[ScoreMatrixEntry], dict | None]]


@dataclass
class SignalApplierRegistry:
    """Registry of model signal appliers."""

    expected_goal_appliers: dict[str, ExpectedGoalsApplier] = field(default_factory=dict)
    score_matrix_appliers: dict[str, ScoreMatrixApplier] = field(default_factory=dict)
    policy: ModelSignalPolicy = field(default_factory=ModelSignalPolicy)

    @classmethod
    def default(cls, policy: ModelSignalPolicy | None = None) -> "SignalApplierRegistry":
        registry = cls(policy=policy or ModelSignalPolicy())
        registry.expected_goal_appliers[TOTAL_GOALS_FACTOR] = apply_total_goals_factor
        registry.expected_goal_appliers[MARKET_TOTAL_GOALS] = apply_market_total_goals
        registry.expected_goal_appliers[MARKET_GOAL_DIFF] = apply_market_goal_diff
        registry.expected_goal_appliers[TEAM_EXPECTED_GOALS_FACTOR] = apply_team_expected_goals_factor
        registry.score_matrix_appliers[GROUP_DRAW_PRESSURE] = apply_group_draw_pressure
        registry.score_matrix_appliers[LIVE_DRAW_ADJUSTMENT] = apply_live_draw_adjustment
        registry.score_matrix_appliers[LIVE_SCORE_TAIL_FACTOR] = apply_live_score_tail_factor
        registry.score_matrix_appliers[LIVE_FAVORITE_OUTCOME_FACTOR] = apply_live_favorite_outcome_factor
        registry.score_matrix_appliers[MARKET_HDA_PROBABILITIES] = apply_hda_probabilities
        registry.score_matrix_appliers[EXPERT_HDA_PROBABILITIES] = apply_hda_probabilities
        registry.score_matrix_appliers[ML_HDA_PROBABILITIES] = apply_hda_probabilities
        return registry

    def apply_expected_goals(
        self,
        fixture_key: str,
        home_lambda: float,
        away_lambda: float,
        signals: list[Signal],
        config: BaselineModelConfig,
    ) -> tuple[float, float, list[dict]]:
        adjustments: list[dict] = []
        for signal in signals:
            if signal.fixture_key not in (None, fixture_key):
                continue
            applier = self.expected_goal_appliers.get(signal.name)
            if applier is None:
                continue
            home_lambda, away_lambda, adjustment = applier(home_lambda, away_lambda, signal, config, self.policy)
            if adjustment:
                adjustments.append(adjustment)
        return (
            _clamp(home_lambda, config.min_expected_goals, config.max_expected_goals),
            _clamp(away_lambda, config.min_expected_goals, config.max_expected_goals),
            adjustments,
        )

    def apply_score_matrix(
        self,
        fixture_key: str,
        score_matrix: list[ScoreMatrixEntry],
        signals: list[Signal],
    ) -> tuple[list[ScoreMatrixEntry], list[dict]]:
        adjustments: list[dict] = []
        for signal in signals:
            if signal.fixture_key not in (None, fixture_key):
                continue
            applier = self.score_matrix_appliers.get(signal.name)
            if applier is None:
                continue
            score_matrix, adjustment = applier(score_matrix, signal, self.policy)
            if adjustment:
                adjustments.append(adjustment)
        return score_matrix, adjustments


def apply_total_goals_factor(
    home_lambda: float,
    away_lambda: float,
    signal: Signal,
    _config: BaselineModelConfig,
    policy: ModelSignalPolicy,
) -> tuple[float, float, dict | None]:
    total_goals = home_lambda + away_lambda
    if signal.value is None or total_goals <= 0:
        return home_lambda, away_lambda, None
    factor = _clamp(float(signal.value), policy.total_goals_factor_min, policy.total_goals_factor_max)
    weight = _clamp(signal_weight(signal), 0.0, 1.0)
    applied_factor = 1 + (factor - 1) * weight
    return (
        home_lambda * applied_factor,
        away_lambda * applied_factor,
        {"signal": signal.name, "weight": weight, "factor": factor},
    )


def apply_market_total_goals(
    home_lambda: float,
    away_lambda: float,
    signal: Signal,
    _config: BaselineModelConfig,
    policy: ModelSignalPolicy,
) -> tuple[float, float, dict | None]:
    total_goals = home_lambda + away_lambda
    if signal.value is None or total_goals <= 0:
        return home_lambda, away_lambda, None
    target_total = _clamp(float(signal.value), policy.market_total_goals_min, policy.market_total_goals_max)
    weight = _clamp(signal_weight(signal), 0.0, policy.market_total_goals_max_weight)
    blended_total = total_goals * (1 - weight) + target_total * weight
    factor = _clamp(blended_total / total_goals, policy.total_goals_factor_min, policy.total_goals_factor_max)
    return (
        home_lambda * factor,
        away_lambda * factor,
        {"signal": signal.name, "weight": weight, "target_total_goals": target_total},
    )


def apply_market_goal_diff(
    home_lambda: float,
    away_lambda: float,
    signal: Signal,
    _config: BaselineModelConfig,
    policy: ModelSignalPolicy,
) -> tuple[float, float, dict | None]:
    total_goals = home_lambda + away_lambda
    if signal.value is None or total_goals <= 0:
        return home_lambda, away_lambda, None
    goal_diff = home_lambda - away_lambda
    target_diff = _clamp(float(signal.value), policy.market_goal_diff_min, policy.market_goal_diff_max)
    weight = _clamp(signal_weight(signal), 0.0, policy.market_goal_diff_max_weight)
    blended_diff = goal_diff * (1 - weight) + target_diff * weight
    blended_diff = _clamp(blended_diff, -total_goals + 0.1, total_goals - 0.1)
    return (
        (total_goals + blended_diff) / 2,
        (total_goals - blended_diff) / 2,
        {"signal": signal.name, "weight": weight, "target_goal_diff": target_diff},
    )


def apply_team_expected_goals_factor(
    home_lambda: float,
    away_lambda: float,
    signal: Signal,
    _config: BaselineModelConfig,
    policy: ModelSignalPolicy,
) -> tuple[float, float, dict | None]:
    if signal.value is None:
        return home_lambda, away_lambda, None
    side = str(signal.metadata.get("side") or "")
    factor = _clamp(float(signal.value), policy.team_expected_goals_factor_min, policy.team_expected_goals_factor_max)
    weight = _clamp(signal_weight(signal), 0.0, 1.0)
    applied_factor = 1 + (factor - 1) * weight
    if side == "home":
        home_lambda *= applied_factor
    elif side == "away":
        away_lambda *= applied_factor
    else:
        return home_lambda, away_lambda, None
    return home_lambda, away_lambda, {"signal": signal.name, "weight": weight, "side": side, "factor": factor}


def apply_group_draw_pressure(score_matrix: list[ScoreMatrixEntry], signal: Signal, policy: ModelSignalPolicy) -> tuple[list[ScoreMatrixEntry], dict | None]:
    adjustment = _clamp(float(signal.value or 0.0), policy.group_draw_pressure_min, policy.group_draw_pressure_max)
    if not adjustment:
        return score_matrix, None
    return reweight_draw(score_matrix, adjustment), {"signal": signal.name, "draw_adjustment": adjustment}


def apply_live_draw_adjustment(score_matrix: list[ScoreMatrixEntry], signal: Signal, policy: ModelSignalPolicy) -> tuple[list[ScoreMatrixEntry], dict | None]:
    adjustment = _clamp(float(signal.value or 0.0), policy.live_draw_adjustment_min, policy.live_draw_adjustment_max)
    weight = _clamp(signal_weight(signal), 0.0, policy.live_draw_max_weight)
    applied_adjustment = adjustment * weight
    if abs(applied_adjustment) < 0.001:
        return score_matrix, None
    return (
        reweight_draw(score_matrix, applied_adjustment),
        {"signal": signal.name, "weight": weight, "draw_adjustment": applied_adjustment},
    )


def apply_live_score_tail_factor(score_matrix: list[ScoreMatrixEntry], signal: Signal, policy: ModelSignalPolicy) -> tuple[list[ScoreMatrixEntry], dict | None]:
    factor = _clamp(float(signal.value or 0.0), policy.live_score_tail_factor_min, policy.live_score_tail_factor_max)
    weight = _clamp(signal_weight(signal), 0.0, policy.live_score_tail_max_weight)
    applied_factor = factor * weight
    if abs(applied_factor) < 0.001:
        return score_matrix, None
    if applied_factor < 0:
        return (
            _compress_score_tail(score_matrix, strength=abs(applied_factor)),
            {"signal": signal.name, "weight": weight, "tail_factor": applied_factor},
        )
    return (
        apply_score_overdispersion(score_matrix, strength=max(0.0, applied_factor)),
        {"signal": signal.name, "weight": weight, "tail_factor": applied_factor},
    )


def apply_live_favorite_outcome_factor(score_matrix: list[ScoreMatrixEntry], signal: Signal, policy: ModelSignalPolicy) -> tuple[list[ScoreMatrixEntry], dict | None]:
    factor = _clamp(
        float(signal.value or 1.0),
        policy.live_favorite_outcome_factor_min,
        policy.live_favorite_outcome_factor_max,
    )
    weight = _clamp(signal_weight(signal), 0.0, policy.live_favorite_outcome_max_weight)
    if abs(factor - 1.0) * weight < 0.001:
        return score_matrix, None
    current = outcome_probabilities(score_matrix)
    home = current.home
    draw = current.draw
    away = current.away
    favorite = "home" if home >= away else "away"
    if favorite == "home":
        home *= factor
        away *= 2 - factor
    else:
        away *= factor
        home *= 2 - factor
    return (
        reweight_outcomes(score_matrix, target_home=home, target_draw=draw, target_away=away, weight=weight),
        {"signal": signal.name, "weight": weight, "favorite": favorite, "factor": factor},
    )


def apply_hda_probabilities(score_matrix: list[ScoreMatrixEntry], signal: Signal, policy: ModelSignalPolicy) -> tuple[list[ScoreMatrixEntry], dict | None]:
    metadata = signal.metadata or {}
    try:
        target_home = float(metadata["prob_home"])
        target_draw = float(metadata["prob_draw"])
        target_away = float(metadata["prob_away"])
    except (KeyError, TypeError, ValueError):
        return score_matrix, None
    max_weight = _hda_max_weight(signal.name, policy)
    weight = _clamp(signal_weight(signal), 0.0, max_weight)
    if weight <= 0:
        return score_matrix, None
    return (
        reweight_outcomes(
            score_matrix,
            target_home=target_home,
            target_draw=target_draw,
            target_away=target_away,
            weight=weight,
        ),
        {
            "signal": signal.name,
            "weight": weight,
            "target_home": target_home,
            "target_draw": target_draw,
            "target_away": target_away,
        },
    )


def signal_weight(signal: Signal, *, default: float = 0.0) -> float:
    if signal.weight is None:
        return default
    confidence = signal.confidence if signal.confidence is not None else 1.0
    return float(signal.weight) * _clamp(float(confidence), 0.0, 1.0)


def _clamp(value: float, lower: float, upper: float) -> float:
    return max(lower, min(upper, value))


def _hda_max_weight(signal_name: str, policy: ModelSignalPolicy) -> float:
    if signal_name == MARKET_HDA_PROBABILITIES:
        return policy.market_hda_max_weight
    if signal_name == ML_HDA_PROBABILITIES:
        return policy.ml_hda_max_weight
    return policy.expert_hda_max_weight


def _compress_score_tail(entries: list[ScoreMatrixEntry], *, strength: float) -> list[ScoreMatrixEntry]:
    strength = _clamp(strength, 0.0, 0.12)
    expected_total = sum(entry.probability * (entry.home + entry.away) for entry in entries)
    adjusted = []
    for entry in entries:
        total_goals = entry.home + entry.away
        factor = math.exp(-strength * max(0.0, total_goals - expected_total))
        adjusted.append(ScoreMatrixEntry(entry.home, entry.away, entry.probability * max(0.75, min(1.20, factor)), entry.metadata))
    return normalize_score_matrix(adjusted)
