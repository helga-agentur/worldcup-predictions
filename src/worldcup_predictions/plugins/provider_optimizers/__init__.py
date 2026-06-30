"""Built-in provider optimizer plugins."""

from worldcup_predictions.plugins.provider_optimizers.ch_20min import TwentyMinChProviderOptimizerPlugin
from worldcup_predictions.plugins.provider_optimizers.ch_srf import SrfChProviderOptimizerPlugin

__all__ = [
    "SrfChProviderOptimizerPlugin",
    "TwentyMinChProviderOptimizerPlugin",
]
