"""Provider optimizer plugin groups."""

from worldcup_predictions.plugins.providers.ch_20min import TwentyMinChProviderOptimizerPlugin
from worldcup_predictions.plugins.providers.ch_srf import SrfChProviderOptimizerPlugin

__all__ = [
    "SrfChProviderOptimizerPlugin",
    "TwentyMinChProviderOptimizerPlugin",
]
