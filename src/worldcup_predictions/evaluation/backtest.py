"""Backtesting helpers for provider-neutral predictions."""

from __future__ import annotations

import datetime as dt
from typing import Any

from worldcup_predictions.core.contracts import Signal
from worldcup_predictions.evaluation.metrics import (
    ranked_probability_score,
    summarize_backtest_rows,
    world_cup_fixtures_by_year,
)
from worldcup_predictions.model import BaselineModel, HistoricalResult
from worldcup_predictions.model.signal_application import SignalApplierRegistry
from worldcup_predictions.plugins.provider_optimizers.ch_srf.rules import srf_rules_for_fixture
from worldcup_predictions.plugins.provider_optimizers.ch_20min.rules import optimize_twenty_min_tip
from worldcup_predictions.plugins.provider_optimizers.common import ScoreMatrixOptimizer, score_outcome
from worldcup_predictions.tournament import FixtureRecord, ResultRecord, TournamentState


BACKTEST_DATASET = "prediction_backtest"

DEFAULT_BACKTEST_YEARS = (2014, 2018, 2022)


def evaluate_backtest_row(
    fixture: FixtureRecord,
    actual,
    before_results: list[HistoricalResult],
    *,
    config=None,
    signal_appliers: SignalApplierRegistry | None = None,
    signals: list[Signal] | None = None,
    optimizer: ScoreMatrixOptimizer | None = None,
    source_label: str = "backtest",
    ratings: dict[str, float] | None = None,
    profiles: dict[str, Any] | None = None,
    avg_goals_per_team: float | None = None,
    cutoff: dt.datetime | None = None,
) -> dict[str, Any]:
    """Score one fixture with the transparent baseline and SRF expected-points optimizer.

    When ``ratings``/``profiles``/``avg_goals_per_team``/``cutoff`` are supplied the
    Elo and goal-profile passes are skipped, which lets calibration evaluate the same
    fixture under many candidate configs without recomputing history each time.
    """

    model = BaselineModel(before_results, config=config, signal_appliers=signal_appliers)
    if ratings is not None and profiles is not None and avg_goals_per_team is not None and cutoff is not None:
        prediction = model.predict_with_profiles(
            fixture,
            ratings=ratings,
            profiles=profiles,
            avg_goals_per_team=avg_goals_per_team,
            cutoff=cutoff,
            signals=signals or [],
        )
    else:
        prediction = model.predict_fixture(fixture, signals=signals or [])
    rules = srf_rules_for_fixture(prediction.fixture)
    optimizer = optimizer or ScoreMatrixOptimizer()
    optimized = optimizer.optimize(prediction, rules, optimizer_id=source_label)
    twenty_min_tip = optimize_twenty_min_tip(prediction, optimizer_id=f"{source_label}:20min")
    tip = optimized.tip or prediction.most_likely
    points = rules.points_for_tip(tip, actual)
    return {
        "record_key": fixture.key,
        "fixture_key": fixture.key,
        "event_date": fixture.event_date,
        "home_team": fixture.home_team.name,
        "away_team": fixture.away_team.name,
        "actual": actual.as_text(),
        "actual_home": actual.home,
        "actual_away": actual.away,
        "most_likely": prediction.most_likely.as_text(),
        "tip": tip.as_text(),
        "tip_home": tip.home,
        "tip_away": tip.away,
        "points": points,
        "expected_points": optimized.expected_points,
        "correct_exact": tip == actual,
        "correct_outcome": score_outcome(tip) == score_outcome(actual),
        "rps": ranked_probability_score(prediction.outcome_probabilities, score_outcome(actual)),
        "prob_home": prediction.outcome_probabilities.home,
        "prob_draw": prediction.outcome_probabilities.draw,
        "prob_away": prediction.outcome_probabilities.away,
        "expected_home_goals": prediction.expected_home_goals,
        "expected_away_goals": prediction.expected_away_goals,
        "confidence_percent": prediction.confidence_percent,
        "score_matrix": [entry.to_dict() for entry in prediction.score_matrix],
        "optimized_tips": [optimized.to_dict(), twenty_min_tip.to_dict()],
        "signal_count": len(signals or []),
        "signal_adjustments": prediction.metadata.get("signal_adjustments") or [],
        "srf_tip": optimized.display_text(),
        "srf_tip_home": optimized.tip.home if optimized.tip is not None else None,
        "srf_tip_away": optimized.tip.away if optimized.tip is not None else None,
        "srf_expected_points": optimized.expected_points,
        "twenty_min_tip": twenty_min_tip.display_text(),
        "twenty_min_selection": twenty_min_tip.selection,
        "twenty_min_selection_type": twenty_min_tip.selection_type,
        "twenty_min_expected_points": twenty_min_tip.expected_points,
        "phase": rules.phase,
        "stage": fixture.stage or "",
        "is_knockout": _is_knockout_fixture(fixture),
        "advancement_probabilities": prediction.metadata.get("advancement_probabilities") or {},
        "ruleset": rules.ruleset().key,
    }


def backtest_srf(
    state: TournamentState,
    historical_results: list[HistoricalResult],
    *,
    signals: list[Signal] | None = None,
    signals_by_fixture: dict[str, list[Signal]] | None = None,
) -> list[dict[str, Any]]:
    """Evaluate finished fixtures with the current transparent baseline.

    The baseline model itself filters historical rows by fixture kickoff date,
    so future historical rows do not leak into each prediction.
    """

    results_by_fixture = {result.fixture_key: result for result in state.results}
    optimizer = ScoreMatrixOptimizer()
    rows = []
    for fixture in state.fixtures:
        result = results_by_fixture.get(fixture.key)
        if result is None:
            continue
        before = historical_results + _tournament_history_before(state.results, fixture)
        fixture_signals = list(signals or [])
        if signals_by_fixture:
            fixture_signals.extend(signals_by_fixture.get(fixture.key, []))
        rows.append(
            evaluate_backtest_row(
                fixture,
                result.score,
                before,
                signals=fixture_signals,
                optimizer=optimizer,
                source_label="backtest_srf",
            )
        )
    return rows


def backtest_historical(
    historical_results: list[HistoricalResult],
    *,
    years: tuple[int, ...] = DEFAULT_BACKTEST_YEARS,
    config=None,
    signal_appliers: SignalApplierRegistry | None = None,
    ml_signal_by_fixture: dict[str, Signal] | None = None,
) -> list[dict[str, Any]]:
    """Backtest the baseline against finished World Cups (2014/2018/2022 by default).

    Each fixture is predicted using only results strictly before its kickoff date so
    no future result leaks into the forecast. ``ml_signal_by_fixture`` supplies the
    history-derived ML signal per fixture when an ML-aware run is requested.
    """

    fixtures_by_year = world_cup_fixtures_by_year(historical_results, years)
    optimizer = ScoreMatrixOptimizer()
    rows: list[dict[str, Any]] = []
    for year, fixtures in fixtures_by_year.items():
        for fixture, actual in fixtures:
            before = sorted(
                (row for row in historical_results if row.date[:10] < fixture.event_date[:10]),
                key=lambda row: row.date,
            )
            signals = []
            if ml_signal_by_fixture and fixture.key in ml_signal_by_fixture:
                signals = [ml_signal_by_fixture[fixture.key]]
            row = evaluate_backtest_row(
                fixture,
                actual,
                before,
                config=config,
                signal_appliers=signal_appliers,
                signals=signals,
                optimizer=optimizer,
                source_label="backtest_historical",
            )
            row["year"] = year
            rows.append(row)
    return rows


def summarize_backtest(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Aggregate backtest rows into mean SRF/accuracy/RPS metrics."""

    return summarize_backtest_rows(rows)


def knockout_backtest_summary(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Return report-only knockout calibration hints from backtest rows."""

    knockout_rows = [row for row in rows if row.get("is_knockout") or _stage_is_knockout(str(row.get("stage") or ""))]
    if not knockout_rows:
        return {
            "sample_count": 0,
            "recommendation": "no_knockout_sample",
            "reason": "No knockout backtest rows are available.",
        }
    draw_rate = sum(1 for row in knockout_rows if int(row.get("actual_home") or 0) == int(row.get("actual_away") or 0)) / len(knockout_rows)
    high_total_rate = sum(1 for row in knockout_rows if int(row.get("actual_home") or 0) + int(row.get("actual_away") or 0) >= 4) / len(knockout_rows)
    favorite_rows = [row for row in knockout_rows if _favorite_outcome(row) is not None]
    favorite_hit_rate = (
        sum(1 for row in favorite_rows if _favorite_outcome(row) == score_outcome_from_row(row)) / len(favorite_rows)
        if favorite_rows
        else None
    )
    return {
        "sample_count": len(knockout_rows),
        "draw_rate": draw_rate,
        "high_total_rate": high_total_rate,
        "favorite_sample_count": len(favorite_rows),
        "favorite_hit_rate": favorite_hit_rate,
        "recommendation": _knockout_recommendation(draw_rate, high_total_rate, favorite_hit_rate),
        "reason": "Report-only KO calibration summary; do not auto-promote without review.",
    }


def _tournament_history_before(results: list[ResultRecord], fixture: FixtureRecord) -> list[HistoricalResult]:
    rows = []
    for result in results:
        result_kickoff = result.event_date
        if result.event_date >= fixture.event_date:
            continue
        rows.append(
            HistoricalResult(
                date=result_kickoff[:10],
                home_team=result.home_team,
                away_team=result.away_team,
                score=result.score,
                tournament="FIFA World Cup",
                neutral=True,
                source=result.source,
                metadata={"fixture_key": result.fixture_key},
            )
        )
    return rows


def _is_knockout_fixture(fixture: FixtureRecord) -> bool:
    if fixture.group:
        return False
    return _stage_is_knockout(str(fixture.stage or ""))


def _stage_is_knockout(stage: str) -> bool:
    stage = stage.casefold()
    return bool(stage and "group" not in stage and "gruppe" not in stage)


def _favorite_outcome(row: dict[str, Any]) -> str | None:
    home = float(row.get("prob_home") or 0.0)
    away = float(row.get("prob_away") or 0.0)
    if abs(home - away) < 0.05:
        return None
    return "home" if home > away else "away"


def score_outcome_from_row(row: dict[str, Any]) -> str:
    home = int(row.get("actual_home") or 0)
    away = int(row.get("actual_away") or 0)
    if home > away:
        return "home"
    if away > home:
        return "away"
    return "draw"


def _knockout_recommendation(draw_rate: float, high_total_rate: float, favorite_hit_rate: float | None) -> str:
    notes = []
    if draw_rate >= 0.32:
        notes.append("watch_or_raise_ko_draw_adjustment")
    elif draw_rate <= 0.20:
        notes.append("watch_or_lower_ko_draw_adjustment")
    if high_total_rate <= 0.18:
        notes.append("watch_or_lower_ko_total_goals")
    elif high_total_rate >= 0.32:
        notes.append("watch_or_raise_ko_total_goals")
    if favorite_hit_rate is not None:
        if favorite_hit_rate <= 0.48:
            notes.append("watch_ko_upset_conservatism")
        elif favorite_hit_rate >= 0.64:
            notes.append("watch_ko_favorite_strength")
    return ", ".join(notes) or "keep_current_ko_phase_context"
