"""Market movement (trend) signal plugin."""

from worldcup_predictions.plugins.market_trend.plugin import (
    MarketTrendPlugin,
    market_trend_rows,
    market_trend_signals,
)

__all__ = ["MarketTrendPlugin", "market_trend_rows", "market_trend_signals"]
