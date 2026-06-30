"""Model calibration helpers for historical backtests.

Calibration evaluates transparent baseline parameter candidates *and* the
history-derivable signal weights (currently the ML outcome weight) against past
World Cups, then reports the best candidate. Historical bookmaker odds and expert
picks are not available, so market/expert weights cannot be tuned here; they stay
explicit policy defaults and are validated forward on the live tournament instead.

The result is reported only. Promoting a calibration result into the runtime
defaults remains a deliberate code change (see docs/architecture.md).
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, replace
from typing import Any

from worldcup_predictions.core.contracts import ScoreTip, Signal
from worldcup_predictions.core.datasets import MODEL_CALIBRATION
from worldcup_predictions.evaluation.metrics import (
    ranked_probability_score,
    summarize_backtest_rows,
    world_cup_fixtures_by_year,
)
from worldcup_predictions.model import BaselineModel, BaselineModelConfig, HistoricalResult
from worldcup_predictions.model.baseline import compute_elo, compute_goal_profiles
from worldcup_predictions.model.contracts import ModelSignalPolicy, TeamProfile
from worldcup_predictions.model.signal_application import SignalApplierRegistry
from worldcup_predictions.plugins.ml_outcome.plugin import (
    ml_signals_for_fixtures,
    sklearn_signals_for_fixtures,
    train_outcome_bucket_model,
    train_sklearn_outcome_model,
)
from worldcup_predictions.plugins.provider_optimizers.ch_srf.rules import srf_rules_for_fixture
from worldcup_predictions.plugins.provider_optimizers.common import ScoreMatrixOptimizer, score_outcome
from worldcup_predictions.storage.ledger import stable_hash, utc_now
from worldcup_predictions.tournament import FixtureRecord


DEFAULT_TUNING_YEARS = (2014, 2018, 2022)

# History-derivable grid. dixon_coles_rho/score_overdispersion shape the exact-score
# matrix; ml_hda_max_weight caps the historical-outcome signal blend.
CANDIDATE_DIXON_COLES_RHO = (-0.12, -0.08, -0.04)
CANDIDATE_SCORE_OVERDISPERSION = (0.04, 0.06, 0.08)
CANDIDATE_ML_WEIGHTS = (0.0, 0.18, 0.30, 0.40)


@dataclass
class _FixtureContext:
    """Precomputed per-fixture inputs reused across every candidate."""

    fixture: FixtureRecord
    actual: ScoreTip
    before: list[HistoricalResult]
    ratings: dict[str, float]
    profiles: dict[str, TeamProfile]
    avg_goals_per_team: float
    cutoff: dt.datetime
    ml_signal: Signal | None
    year: int = 0


def calibrate_baseline_model(
    historical_results: list[HistoricalResult],
    *,
    years: tuple[int, ...] = DEFAULT_TUNING_YEARS,
) -> list[dict[str, Any]]:
    """Evaluate baseline config and ML-weight candidates on past World Cups."""

    fixtures_by_year = world_cup_fixtures_by_year(historical_results, years)
    if not any(fixtures_by_year.values()):
        return []
    contexts = _fixture_contexts(historical_results, fixtures_by_year)
    rows = [_evaluate_candidate(contexts, config, policy, params) for config, policy, params in _candidate_specs()]
    rows.sort(key=lambda row: (-float(row["srf_points_per_match"]), float(row["rps"]), str(row["calibration_id"])))
    best_id = rows[0]["calibration_id"]
    return [{**row, "rank": index + 1, "selected": row["calibration_id"] == best_id} for index, row in enumerate(rows)]


def write_model_calibration(storage, historical_results: list[HistoricalResult], *, run_id: str | None = None) -> int:
    rows = calibrate_baseline_model(historical_results)
    return storage.write_records(MODEL_CALIBRATION, rows, source="model_calibration", run_id=run_id)


def _candidate_specs() -> list[tuple[BaselineModelConfig, ModelSignalPolicy, dict[str, Any]]]:
    base_config = BaselineModelConfig()
    base_policy = ModelSignalPolicy()
    specs: list[tuple[BaselineModelConfig, ModelSignalPolicy, dict[str, Any]]] = []
    for rho in CANDIDATE_DIXON_COLES_RHO:
        for overdispersion in CANDIDATE_SCORE_OVERDISPERSION:
            for ml_weight in CANDIDATE_ML_WEIGHTS:
                config = replace(base_config, dixon_coles_rho=rho, score_overdispersion=overdispersion)
                policy = replace(base_policy, ml_hda_max_weight=ml_weight)
                params = {
                    "dixon_coles_rho": rho,
                    "score_overdispersion": overdispersion,
                    "ml_hda_max_weight": ml_weight,
                    "mismatch_blowout_weight": config.mismatch_blowout_weight,
                    "half_life_days": config.half_life_days,
                }
                specs.append((config, policy, params))
    return specs


def _fixture_contexts(
    historical_results: list[HistoricalResult],
    fixtures_by_year: dict[int, list[tuple[FixtureRecord, ScoreTip]]],
) -> list[_FixtureContext]:
    """Precompute ratings, goal profiles, and a leak-free ML signal per fixture.

    The ML model for a tournament is trained only on results before the first match
    of that tournament, and each fixture's Elo/goal-profile pass uses only results
    strictly before its kickoff, so nothing from the evaluated tournament leaks in.
    """

    default_config = BaselineModelConfig()
    contexts: list[_FixtureContext] = []
    for year, fixtures in fixtures_by_year.items():
        if not fixtures:
            continue
        tournament_start = min(fixture.event_date for fixture, _ in fixtures)[:10]
        before_tournament = sorted(
            (row for row in historical_results if row.date[:10] < tournament_start),
            key=lambda row: row.date,
        )
        sklearn_model = train_sklearn_outcome_model(before_tournament)
        bucket_model = None if sklearn_model is not None else train_outcome_bucket_model(before_tournament)
        for fixture, actual in fixtures:
            cutoff = fixture.kickoff_at or dt.datetime.now(dt.timezone.utc)
            before = sorted(
                (row for row in historical_results if row.date[:10] < fixture.event_date[:10]),
                key=lambda row: row.date,
            )
            ratings = compute_elo(before, cutoff=cutoff, config=default_config)
            profiles, avg_goals = compute_goal_profiles(before, cutoff=cutoff, config=default_config)
            ml_signal = _ml_signal_for_fixture(fixture, before, sklearn_model, bucket_model)
            contexts.append(
                _FixtureContext(
                    fixture=fixture,
                    actual=actual,
                    before=before,
                    ratings=ratings,
                    profiles=profiles,
                    avg_goals_per_team=avg_goals,
                    cutoff=cutoff,
                    ml_signal=ml_signal,
                    year=year,
                )
            )
    return contexts


def _ml_signal_for_fixture(fixture, before, sklearn_model, bucket_model) -> Signal | None:
    if sklearn_model is not None:
        signals = sklearn_signals_for_fixtures([fixture], sklearn_model)
        return signals[0] if signals else None
    if bucket_model:
        signals = ml_signals_for_fixtures([fixture], before, bucket_model)
        return signals[0] if signals else None
    return None


def _evaluate_candidate(
    contexts: list[_FixtureContext],
    config: BaselineModelConfig,
    policy: ModelSignalPolicy,
    params: dict[str, Any],
) -> dict[str, Any]:
    appliers = SignalApplierRegistry.default(policy)
    optimizer = ScoreMatrixOptimizer()
    use_ml = policy.ml_hda_max_weight > 0
    points = 0.0
    expected_points = 0.0
    exact_hits = 0
    outcome_hits = 0
    rps_total = 0.0
    for ctx in contexts:
        signals = [ctx.ml_signal] if use_ml and ctx.ml_signal is not None else []
        model = BaselineModel(ctx.before, config=config, signal_appliers=appliers)
        prediction = model.predict_with_profiles(
            ctx.fixture,
            ratings=ctx.ratings,
            profiles=ctx.profiles,
            avg_goals_per_team=ctx.avg_goals_per_team,
            cutoff=ctx.cutoff,
            signals=signals,
        )
        rules = srf_rules_for_fixture(prediction.fixture)
        optimized = optimizer.optimize(prediction, rules, optimizer_id="model_calibration")
        tip = optimized.tip or prediction.most_likely
        points += rules.points_for_tip(tip, ctx.actual)
        expected_points += optimized.expected_points
        exact_hits += int(tip == ctx.actual)
        outcome_hits += int(score_outcome(tip) == score_outcome(ctx.actual))
        rps_total += ranked_probability_score(prediction.outcome_probabilities, score_outcome(ctx.actual))
    matches = max(1, len(contexts))
    return {
        "record_key": stable_hash({"model": "baseline", "params": params}),
        "calibration_id": stable_hash({"model": "baseline", "params": params})[:16],
        "model": "baseline_elo_goal_profile",
        "created_at_utc": utc_now().isoformat().replace("+00:00", "Z"),
        "sample_matches": len(contexts),
        "years": list(sorted({str(ctx.year) for ctx in contexts})),
        "uses_ml_signal": use_ml,
        "srf_points": points,
        "srf_points_per_match": points / matches,
        "expected_points_per_match": expected_points / matches,
        "exact_hit_rate": exact_hits / matches,
        "outcome_hit_rate": outcome_hits / matches,
        "rps": rps_total / matches,
        "parameters": params,
    }


__all__ = [
    "DEFAULT_TUNING_YEARS",
    "calibrate_baseline_model",
    "write_model_calibration",
    "ranked_probability_score",
]
