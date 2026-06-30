"""Prediction model contracts."""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field
from typing import Any

from worldcup_predictions.core.contracts import ScoreTip
from worldcup_predictions.core.constants import (
    SIGNAL_GROUP_DRAW_PRESSURE_MAX,
    SIGNAL_GROUP_DRAW_PRESSURE_MIN,
    SIGNAL_LIVE_DRAW_ADJUSTMENT_MAX,
    SIGNAL_LIVE_DRAW_ADJUSTMENT_MIN,
    SIGNAL_LIVE_FAVORITE_OUTCOME_FACTOR_MAX,
    SIGNAL_LIVE_FAVORITE_OUTCOME_FACTOR_MIN,
    SIGNAL_LIVE_SCORE_TAIL_FACTOR_MAX,
    SIGNAL_LIVE_SCORE_TAIL_FACTOR_MIN,
    SIGNAL_MARKET_GOAL_DIFF_MAX,
    SIGNAL_MARKET_GOAL_DIFF_MIN,
    SIGNAL_MARKET_TOTAL_GOALS_MAX,
    SIGNAL_MARKET_TOTAL_GOALS_MIN,
    SIGNAL_TEAM_EXPECTED_GOALS_FACTOR_MAX,
    SIGNAL_TEAM_EXPECTED_GOALS_FACTOR_MIN,
    SIGNAL_TOTAL_GOALS_FACTOR_MAX,
    SIGNAL_TOTAL_GOALS_FACTOR_MIN,
    SIGNAL_WEIGHT_EXPERT_HDA,
    SIGNAL_WEIGHT_LIVE_DRAW,
    SIGNAL_WEIGHT_LIVE_FAVORITE,
    SIGNAL_WEIGHT_LIVE_SCORE_TAIL,
    SIGNAL_WEIGHT_MARKET_GOAL_DIFF,
    SIGNAL_WEIGHT_MARKET_HDA,
    SIGNAL_WEIGHT_MARKET_TOTAL_GOALS,
    SIGNAL_WEIGHT_ML_HDA,
)
from worldcup_predictions.storage.ledger import normalize_datetime, stable_hash
from worldcup_predictions.tournament.contracts import TeamRef


@dataclass(frozen=True)
class HistoricalResult:
    """One historical international football result."""

    date: str
    home_team: TeamRef
    away_team: TeamRef
    score: ScoreTip
    tournament: str | None = None
    neutral: bool = True
    source: str = "historical_results"
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def played_on(self) -> dt.date:
        return dt.date.fromisoformat(self.date[:10])

    @property
    def record_key(self) -> str:
        return stable_hash(
            {
                "date": self.date[:10],
                "home": self.home_team.key,
                "away": self.away_team.key,
                "source": self.source,
            }
        )

    def to_record(self) -> dict[str, Any]:
        return {
            "record_key": self.record_key,
            "date": self.date[:10],
            "home_team": self.home_team.name,
            "away_team": self.away_team.name,
            "home_fifa_code": self.home_team.fifa_code,
            "away_fifa_code": self.away_team.fifa_code,
            "home_score": self.score.home,
            "away_score": self.score.away,
            "tournament": self.tournament,
            "neutral": self.neutral,
            "source": self.source,
            "metadata": self.metadata,
        }

    @classmethod
    def from_record(cls, row: dict[str, Any]) -> "HistoricalResult":
        return cls(
            date=str(row["date"])[:10],
            home_team=TeamRef(str(row["home_team"]), row.get("home_fifa_code")),
            away_team=TeamRef(str(row["away_team"]), row.get("away_fifa_code")),
            score=ScoreTip(int(row["home_score"]), int(row["away_score"])),
            tournament=row.get("tournament"),
            neutral=bool(row.get("neutral", True)),
            source=str(row.get("source") or "historical_results"),
            metadata=dict(row.get("metadata") or {}),
        )


@dataclass(frozen=True)
class BaselineModelConfig:
    """Transparent baseline model configuration."""

    base_rating: float = 1500.0
    home_advantage: float = 65.0
    dixon_coles_rho: float = 0.0
    score_overdispersion: float = 0.06
    max_goals: int = 8
    profile_since: dt.date = dt.date(2018, 1, 1)
    half_life_days: int = 1460
    min_expected_goals: float = 0.15
    max_expected_goals: float = 4.5
    mismatch_blowout_weight: float = 0.08


@dataclass(frozen=True)
class ModelSignalPolicy:
    """Capped blend policy for typed model signals."""

    total_goals_factor_min: float = SIGNAL_TOTAL_GOALS_FACTOR_MIN
    total_goals_factor_max: float = SIGNAL_TOTAL_GOALS_FACTOR_MAX
    market_total_goals_min: float = SIGNAL_MARKET_TOTAL_GOALS_MIN
    market_total_goals_max: float = SIGNAL_MARKET_TOTAL_GOALS_MAX
    market_goal_diff_min: float = SIGNAL_MARKET_GOAL_DIFF_MIN
    market_goal_diff_max: float = SIGNAL_MARKET_GOAL_DIFF_MAX
    team_expected_goals_factor_min: float = SIGNAL_TEAM_EXPECTED_GOALS_FACTOR_MIN
    team_expected_goals_factor_max: float = SIGNAL_TEAM_EXPECTED_GOALS_FACTOR_MAX
    group_draw_pressure_min: float = SIGNAL_GROUP_DRAW_PRESSURE_MIN
    group_draw_pressure_max: float = SIGNAL_GROUP_DRAW_PRESSURE_MAX
    live_draw_adjustment_min: float = SIGNAL_LIVE_DRAW_ADJUSTMENT_MIN
    live_draw_adjustment_max: float = SIGNAL_LIVE_DRAW_ADJUSTMENT_MAX
    live_score_tail_factor_min: float = SIGNAL_LIVE_SCORE_TAIL_FACTOR_MIN
    live_score_tail_factor_max: float = SIGNAL_LIVE_SCORE_TAIL_FACTOR_MAX
    live_favorite_outcome_factor_min: float = SIGNAL_LIVE_FAVORITE_OUTCOME_FACTOR_MIN
    live_favorite_outcome_factor_max: float = SIGNAL_LIVE_FAVORITE_OUTCOME_FACTOR_MAX
    market_hda_max_weight: float = SIGNAL_WEIGHT_MARKET_HDA
    market_total_goals_max_weight: float = SIGNAL_WEIGHT_MARKET_TOTAL_GOALS
    market_goal_diff_max_weight: float = SIGNAL_WEIGHT_MARKET_GOAL_DIFF
    expert_hda_max_weight: float = SIGNAL_WEIGHT_EXPERT_HDA
    ml_hda_max_weight: float = SIGNAL_WEIGHT_ML_HDA
    live_draw_max_weight: float = SIGNAL_WEIGHT_LIVE_DRAW
    live_score_tail_max_weight: float = SIGNAL_WEIGHT_LIVE_SCORE_TAIL
    live_favorite_outcome_max_weight: float = SIGNAL_WEIGHT_LIVE_FAVORITE


@dataclass(frozen=True)
class TeamProfile:
    """Attack/defense goal profile for one team."""

    attack: float = 1.0
    defense: float = 1.0
    matches_weighted: float = 0.0

    def to_dict(self) -> dict[str, float]:
        return {
            "attack": self.attack,
            "defense": self.defense,
            "matches_weighted": self.matches_weighted,
        }


@dataclass(frozen=True)
class ModelFeatures:
    """Diagnostics used to explain a baseline prediction."""

    home_rating: float
    away_rating: float
    home_profile: TeamProfile
    away_profile: TeamProfile
    avg_goals_per_team: float
    historical_results: int
    cutoff: str
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "home_rating": self.home_rating,
            "away_rating": self.away_rating,
            "rating_delta": self.home_rating - self.away_rating,
            "home_profile": self.home_profile.to_dict(),
            "away_profile": self.away_profile.to_dict(),
            "avg_goals_per_team": self.avg_goals_per_team,
            "historical_results": self.historical_results,
            "cutoff": normalize_datetime(self.cutoff) or self.cutoff,
            "metadata": self.metadata,
        }
