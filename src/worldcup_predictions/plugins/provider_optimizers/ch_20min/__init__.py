"""20min.ch provider optimizer."""

from worldcup_predictions.plugins.provider_optimizers.ch_20min.bonus import (
    best_twenty_min_bonus_answers,
    evaluate_twenty_min_bonus_questions,
)
from worldcup_predictions.plugins.provider_optimizers.ch_20min.plugin import TwentyMinChProviderOptimizerPlugin
from worldcup_predictions.plugins.provider_optimizers.ch_20min.rules import (
    optimize_twenty_min_tip,
    twenty_min_points_for_fixture,
    twenty_min_ruleset_for_fixture,
)

__all__ = [
    "TwentyMinChProviderOptimizerPlugin",
    "best_twenty_min_bonus_answers",
    "evaluate_twenty_min_bonus_questions",
    "optimize_twenty_min_tip",
    "twenty_min_points_for_fixture",
    "twenty_min_ruleset_for_fixture",
]
