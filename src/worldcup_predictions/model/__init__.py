"""Prediction model primitives."""

from worldcup_predictions.model.baseline import (
    BaselineModel,
    actual_result,
    compute_elo,
    compute_goal_profiles,
    expected_result,
)
from worldcup_predictions.model.contracts import (
    BaselineModelConfig,
    HistoricalResult,
    ModelSignalPolicy,
    ModelFeatures,
    TeamProfile,
)
from worldcup_predictions.model.historical_results import (
    HISTORICAL_RESULTS_DATASET,
    load_historical_results,
    parse_historical_results_text,
    parse_shootouts_text,
)
from worldcup_predictions.model.score_matrix import (
    build_score_matrix,
    most_likely_score,
    outcome_probabilities,
)
from worldcup_predictions.model.signal_application import SignalApplierRegistry

__all__ = [
    "BaselineModel",
    "BaselineModelConfig",
    "HISTORICAL_RESULTS_DATASET",
    "HistoricalResult",
    "ModelFeatures",
    "ModelSignalPolicy",
    "SignalApplierRegistry",
    "TeamProfile",
    "actual_result",
    "build_score_matrix",
    "compute_elo",
    "compute_goal_profiles",
    "expected_result",
    "load_historical_results",
    "most_likely_score",
    "outcome_probabilities",
    "parse_historical_results_text",
    "parse_shootouts_text",
]
