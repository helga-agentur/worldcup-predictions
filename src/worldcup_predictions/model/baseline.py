"""Transparent baseline prediction model."""

from __future__ import annotations

import datetime as dt
import math
from collections import defaultdict

from worldcup_predictions.core.contracts import Fixture, Prediction, ScoreTip, Signal, confidence_label
from worldcup_predictions.core.signals import GROUP_DRAW_PRESSURE
from worldcup_predictions.model.contracts import BaselineModelConfig, HistoricalResult, ModelFeatures, TeamProfile
from worldcup_predictions.model.score_matrix import (
    build_score_matrix,
    most_likely_score,
    outcome_probabilities,
)
from worldcup_predictions.model.signal_application import SignalApplierRegistry
from worldcup_predictions.tournament.contracts import FixtureRecord, TeamRef


class BaselineModel:
    """Elo plus recent-goal-profile model for neutral score predictions."""

    def __init__(
        self,
        results: list[HistoricalResult],
        config: BaselineModelConfig | None = None,
        signal_appliers: SignalApplierRegistry | None = None,
    ) -> None:
        self.results = sorted(results, key=lambda item: item.date)
        self.config = config or BaselineModelConfig()
        self.signal_appliers = signal_appliers or SignalApplierRegistry.default()

    def predict_fixture(self, fixture: FixtureRecord, *, signals: list[Signal] | None = None) -> Prediction:
        cutoff = fixture.kickoff_at or dt.datetime.now(dt.timezone.utc)
        ratings = compute_elo(self.results, cutoff=cutoff, config=self.config)
        profiles, avg_goals = compute_goal_profiles(self.results, cutoff=cutoff, config=self.config)
        return self.predict_with_profiles(
            fixture,
            ratings=ratings,
            profiles=profiles,
            avg_goals_per_team=avg_goals,
            cutoff=cutoff,
            signals=signals,
        )

    def predict_with_profiles(
        self,
        fixture: FixtureRecord,
        *,
        ratings: dict[str, float],
        profiles: dict[str, TeamProfile],
        avg_goals_per_team: float,
        cutoff: dt.datetime,
        signals: list[Signal] | None = None,
    ) -> Prediction:
        """Predict a fixture from already-computed ratings and goal profiles.

        Elo ratings and goal profiles depend only on history and the config fields
        that are not part of the calibration grid, so callers that score the same
        fixture under many candidate configs can compute them once and reuse them.
        """

        home_lambda, away_lambda, features = self.expected_goals(
            fixture.home_team,
            fixture.away_team,
            ratings=ratings,
            profiles=profiles,
            avg_goals_per_team=avg_goals_per_team,
            cutoff=cutoff,
            neutral=_is_neutral(fixture),
        )
        active_signals = signals or []
        home_lambda, away_lambda, lambda_adjustments = self.signal_appliers.apply_expected_goals(
            fixture.key,
            home_lambda,
            away_lambda,
            active_signals,
            self.config,
        )
        score_matrix = build_score_matrix(
            home_lambda,
            away_lambda,
            max_goals=self.config.max_goals,
            dixon_coles_rho=self.config.dixon_coles_rho,
            overdispersion=self.config.score_overdispersion,
        )
        score_matrix, matrix_adjustments = self.signal_appliers.apply_score_matrix(fixture.key, score_matrix, active_signals)
        draw_adjustment = sum(
            float(adjustment.get("draw_adjustment") or 0.0)
            for adjustment in matrix_adjustments
            if adjustment.get("signal") == GROUP_DRAW_PRESSURE
        )
        probabilities = outcome_probabilities(score_matrix)
        most_likely = most_likely_score(score_matrix)
        knockout_advancement = knockout_advancement_probabilities(
            fixture,
            probabilities,
            home_lambda=home_lambda,
            away_lambda=away_lambda,
        )
        metadata = {
            "model": "baseline_elo_goal_profile",
            "features": features.to_dict(),
            "draw_adjustment": draw_adjustment,
            "signal_adjustments": lambda_adjustments + matrix_adjustments,
            "score_basis": "ninety_minutes",
        }
        if knockout_advancement is not None:
            metadata["advancement_probabilities"] = knockout_advancement
        return Prediction(
            fixture=fixture.to_fixture(),
            most_likely=most_likely,
            outcome_probabilities=probabilities,
            confidence_label=confidence_label(probabilities.max_probability()),
            confidence_percent=probabilities.max_probability(),
            expected_home_goals=home_lambda,
            expected_away_goals=away_lambda,
            source="baseline_model",
            score_matrix=score_matrix,
            metadata=metadata,
        )

    def expected_goals(
        self,
        home_team: TeamRef,
        away_team: TeamRef,
        *,
        ratings: dict[str, float],
        profiles: dict[str, TeamProfile],
        avg_goals_per_team: float,
        cutoff: dt.datetime,
        neutral: bool,
    ) -> tuple[float, float, ModelFeatures]:
        home_rating = ratings.get(home_team.key, self.config.base_rating)
        away_rating = ratings.get(away_team.key, self.config.base_rating)
        home_profile = profiles.get(home_team.key, TeamProfile())
        away_profile = profiles.get(away_team.key, TeamProfile())
        elo_delta = home_rating - away_rating
        if not neutral:
            elo_delta += self.config.home_advantage
        home_lambda = (
            avg_goals_per_team
            * math.sqrt(home_profile.attack * away_profile.defense)
            * math.exp(elo_delta / 900.0)
            * (1.08 if not neutral else 1.0)
        )
        away_lambda = (
            avg_goals_per_team
            * math.sqrt(away_profile.attack * home_profile.defense)
            * math.exp(-elo_delta / 900.0)
            * (0.95 if not neutral else 1.0)
        )
        mismatch_adjustment = mismatch_blowout_adjustment(home_lambda, away_lambda, self.config.mismatch_blowout_weight)
        if mismatch_adjustment:
            if mismatch_adjustment["favorite"] == "home":
                home_lambda *= mismatch_adjustment["favorite_factor"]
                away_lambda *= mismatch_adjustment["underdog_factor"]
            else:
                away_lambda *= mismatch_adjustment["favorite_factor"]
                home_lambda *= mismatch_adjustment["underdog_factor"]
        features = ModelFeatures(
            home_rating=home_rating,
            away_rating=away_rating,
            home_profile=home_profile,
            away_profile=away_profile,
            avg_goals_per_team=avg_goals_per_team,
            historical_results=len(_played_before(self.results, cutoff)),
            cutoff=cutoff.isoformat(),
            metadata={"mismatch_adjustment": mismatch_adjustment},
        )
        return (
            _clamp(home_lambda, self.config.min_expected_goals, self.config.max_expected_goals),
            _clamp(away_lambda, self.config.min_expected_goals, self.config.max_expected_goals),
            features,
        )


def compute_elo(
    results: list[HistoricalResult],
    *,
    cutoff: dt.datetime,
    config: BaselineModelConfig | None = None,
) -> dict[str, float]:
    config = config or BaselineModelConfig()
    ratings: dict[str, float] = defaultdict(lambda: config.base_rating)
    for result in _played_before(results, cutoff):
        home = result.home_team.key
        away = result.away_team.key
        home_rating = ratings[home] + (0 if result.neutral else config.home_advantage)
        expected_home = expected_result(home_rating, ratings[away])
        actual_home = actual_result(result.score)
        k_factor = tournament_weight(result.tournament) * goal_diff_multiplier(result.score.home - result.score.away)
        change = k_factor * (actual_home - expected_home)
        ratings[home] += change
        ratings[away] -= change
    return dict(ratings)


def compute_goal_profiles(
    results: list[HistoricalResult],
    *,
    cutoff: dt.datetime,
    config: BaselineModelConfig | None = None,
) -> tuple[dict[str, TeamProfile], float]:
    config = config or BaselineModelConfig()
    team_for: dict[str, float] = defaultdict(float)
    team_against: dict[str, float] = defaultdict(float)
    team_weight: dict[str, float] = defaultdict(float)
    total_goals = 0.0
    total_team_weight = 0.0
    for result in _played_before(results, cutoff):
        if result.played_on < config.profile_since:
            continue
        weight = recency_weight(result.played_on, cutoff.date(), config.half_life_days)
        home = result.home_team.key
        away = result.away_team.key
        team_for[home] += weight * result.score.home
        team_against[home] += weight * result.score.away
        team_weight[home] += weight
        team_for[away] += weight * result.score.away
        team_against[away] += weight * result.score.home
        team_weight[away] += weight
        total_goals += weight * (result.score.home + result.score.away)
        total_team_weight += 2 * weight

    avg_goals_per_team = total_goals / total_team_weight if total_team_weight else 1.25
    if avg_goals_per_team <= 0:
        avg_goals_per_team = 1.25
    profiles = {}
    for team, weight in team_weight.items():
        scored = team_for[team] / weight
        conceded = team_against[team] / weight
        profiles[team] = TeamProfile(
            attack=_clamp(scored / avg_goals_per_team, 0.35, 2.6),
            defense=_clamp(conceded / avg_goals_per_team, 0.35, 2.6),
            matches_weighted=weight,
        )
    return profiles, avg_goals_per_team


def expected_result(rating_a: float, rating_b: float) -> float:
    return 1.0 / (1.0 + 10 ** (-(rating_a - rating_b) / 400.0))


def actual_result(score: ScoreTip) -> float:
    if score.home > score.away:
        return 1.0
    if score.home < score.away:
        return 0.0
    return 0.5


def tournament_weight(tournament: str | None) -> float:
    name = (tournament or "").casefold()
    if "fifa world cup" in name and "qualification" not in name:
        return 40.0
    if "world cup qualification" in name or "world cup qualifiers" in name:
        return 25.0
    if any(token in name for token in ("euro", "copa", "africa cup", "asian cup", "gold cup")):
        return 30.0
    if "friendly" in name:
        return 12.0
    return 18.0


def goal_diff_multiplier(goal_diff: int) -> float:
    diff = abs(goal_diff)
    if diff <= 1:
        return 1.0
    if diff == 2:
        return 1.5
    return (11 + diff) / 8


def recency_weight(match_date: dt.date, cutoff: dt.date, half_life_days: int) -> float:
    days = max(0, (cutoff - match_date).days)
    return 0.5 ** (days / half_life_days)


def mismatch_blowout_adjustment(home_lambda: float, away_lambda: float, weight: float) -> dict[str, float | str] | None:
    """Return a tiny favorite/underdog xG adjustment for extreme expected-goal mismatches.

    The trigger is the favorite's expected-goal share and lead over the underdog (a
    full-model "is this a blowout" signal) rather than a raw Elo gap, so the bump only
    fires when the goal model — not just the rating delta — agrees the match is lopsided.
    The favorite is boosted and the underdog damped with capped factors.
    """

    total = home_lambda + away_lambda
    if weight <= 0 or total <= 0:
        return None
    favorite = "home" if home_lambda >= away_lambda else "away"
    favorite_lambda = max(home_lambda, away_lambda)
    underdog_lambda = min(home_lambda, away_lambda)
    share = favorite_lambda / total
    diff = favorite_lambda - underdog_lambda
    if share < 0.66 or diff < 0.85:
        return None
    pressure = min(1.0, (share - 0.66) / 0.22 + max(0.0, diff - 0.85) / 2.8)
    total_adjustment = min(0.10, pressure * weight)
    if total_adjustment <= 0:
        return None
    favorite_adjustment = min(0.08, total_adjustment * 0.80)
    return {
        "favorite": favorite,
        "share": share,
        "diff": diff,
        "total_adjustment": total_adjustment,
        "favorite_adjustment": favorite_adjustment,
        "favorite_factor": 1 + favorite_adjustment,
        "underdog_factor": max(0.90, 1 - total_adjustment * 0.30),
    }


def _played_before(results: list[HistoricalResult], cutoff: dt.datetime) -> list[HistoricalResult]:
    cutoff_date = cutoff.date()
    return [result for result in results if result.played_on < cutoff_date]


def _is_neutral(fixture: FixtureRecord) -> bool:
    neutral = fixture.metadata.get("neutral")
    if neutral is None:
        return True
    return str(neutral).casefold() not in {"0", "false", "no"}


def knockout_advancement_probabilities(
    fixture: FixtureRecord,
    probabilities,
    *,
    home_lambda: float,
    away_lambda: float,
) -> dict[str, float | str] | None:
    """Estimate advancement probabilities from 90-minute odds plus ET/penalty edge."""

    if not _is_knockout_fixture(fixture):
        return None
    draw_probability = float(probabilities.draw)
    home_extra_time_penalty_share = _extra_time_penalty_home_share(home_lambda, away_lambda)
    home_extra_time_share = _extra_time_penalty_home_share(home_lambda * 0.34, away_lambda * 0.34)
    home_penalty_share = _extra_time_penalty_home_share(home_lambda * 0.16, away_lambda * 0.16)
    home = float(probabilities.home) + draw_probability * home_extra_time_penalty_share
    away = float(probabilities.away) + draw_probability * (1 - home_extra_time_penalty_share)
    total = home + away
    if total <= 0:
        home = away = 0.5
    else:
        home /= total
        away /= total
    return {
        "home": home,
        "away": away,
        "draw_90": draw_probability,
        "extra_time_penalty_home_share": home_extra_time_penalty_share,
        "home_win_90": float(probabilities.home),
        "away_win_90": float(probabilities.away),
        "home_advances_after_90_draw": draw_probability * home_extra_time_penalty_share,
        "away_advances_after_90_draw": draw_probability * (1 - home_extra_time_penalty_share),
        "extra_time_home_share": home_extra_time_share,
        "penalty_shootout_home_share": home_penalty_share,
        "source": "ninety_minute_matrix_plus_modest_strength_edge",
    }


def _extra_time_penalty_home_share(home_lambda: float, away_lambda: float) -> float:
    # Extra time and penalties should preserve only a modest strength edge.
    return _clamp(1 / (1 + math.exp(-0.85 * (home_lambda - away_lambda))), 0.35, 0.65)


def _is_knockout_fixture(fixture: FixtureRecord) -> bool:
    stage = str(fixture.stage or "").casefold()
    if fixture.group or "group" in stage or "gruppe" in stage:
        return False
    return bool(stage)


def _clamp(value: float, lower: float, upper: float) -> float:
    return max(lower, min(upper, value))
