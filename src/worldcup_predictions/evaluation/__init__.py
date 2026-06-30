"""Prediction evaluation helpers."""

from worldcup_predictions.evaluation.backtest import (
    BACKTEST_DATASET,
    DEFAULT_BACKTEST_YEARS,
    backtest_historical,
    backtest_srf,
    evaluate_backtest_row,
    knockout_backtest_summary,
    summarize_backtest,
)
from worldcup_predictions.evaluation.metrics import (
    ranked_probability_score,
    summarize_backtest_by,
    summarize_backtest_rows,
    world_cup_fixtures_by_year,
)
from worldcup_predictions.evaluation.model_calibration import calibrate_baseline_model
from worldcup_predictions.evaluation.provider_knockout_audit import (
    build_provider_knockout_audit_rows,
    write_provider_knockout_audit,
)

__all__ = [
    "BACKTEST_DATASET",
    "DEFAULT_BACKTEST_YEARS",
    "backtest_historical",
    "backtest_srf",
    "evaluate_backtest_row",
    "knockout_backtest_summary",
    "summarize_backtest",
    "summarize_backtest_by",
    "summarize_backtest_rows",
    "ranked_probability_score",
    "world_cup_fixtures_by_year",
    "calibrate_baseline_model",
    "build_provider_knockout_audit_rows",
    "write_provider_knockout_audit",
]
