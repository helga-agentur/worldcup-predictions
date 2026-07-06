"""srf.ch provider optimizer."""

from worldcup_predictions.plugins.providers.ch_srf.bonus import (
    best_srf_bonus_answers,
    evaluate_srf_bonus_questions,
)
from worldcup_predictions.plugins.providers.ch_srf.plugin import SrfChProviderOptimizerPlugin
from worldcup_predictions.plugins.providers.ch_srf.rules import srf_rules_for_fixture

__all__ = [
    "SrfChProviderOptimizerPlugin",
    "best_srf_bonus_answers",
    "evaluate_srf_bonus_questions",
    "srf_rules_for_fixture",
]
