"""Data contracts for tournament simulations."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from worldcup_predictions.core.contracts import ScoreTip


Distribution = list[dict[str, Any]]


@dataclass(frozen=True)
class SimulationResult:
    """One simulated or fixed match result."""

    match_id: str
    home_team: str
    away_team: str
    score: ScoreTip
    stage: str | None = None
    group: str | None = None
    winner: str | None = None
    source: str = "simulated"
    matrix_source: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "match_id": self.match_id,
            "home_team": self.home_team,
            "away_team": self.away_team,
            "score": self.score.as_text(),
            "home_score": self.score.home,
            "away_score": self.score.away,
            "stage": self.stage,
            "group": self.group,
            "winner": self.winner,
            "source": self.source,
            "matrix_source": self.matrix_source,
        }


@dataclass
class TeamStanding:
    """Group table state for one team."""

    team: str
    group: str
    played: int = 0
    points: int = 0
    goals_for: int = 0
    goals_against: int = 0
    wins: int = 0
    draws: int = 0
    losses: int = 0

    @property
    def goal_difference(self) -> int:
        return self.goals_for - self.goals_against

    def record(self, goals_for: int, goals_against: int) -> None:
        self.played += 1
        self.goals_for += goals_for
        self.goals_against += goals_against
        if goals_for > goals_against:
            self.wins += 1
            self.points += 3
        elif goals_for == goals_against:
            self.draws += 1
            self.points += 1
        else:
            self.losses += 1

    def to_dict(self, rank: int | None = None) -> dict[str, Any]:
        data = {
            "team": self.team,
            "group": self.group,
            "played": self.played,
            "points": self.points,
            "goal_difference": self.goal_difference,
            "goals_for": self.goals_for,
            "goals_against": self.goals_against,
            "wins": self.wins,
            "draws": self.draws,
            "losses": self.losses,
        }
        if rank is not None:
            data["rank"] = rank
        return data


@dataclass(frozen=True)
class SimulationOutcome:
    """One complete tournament simulation run."""

    champion: str | None
    team_stage: dict[str, str]
    team_goals: dict[str, int]
    nil_nil_count: int
    top_scorer_goals: int
    group_ranks: dict[str, int] = field(default_factory=dict)
    group_qualified: set[str] = field(default_factory=set)
    fixture_results: list[SimulationResult] = field(default_factory=list)


@dataclass(frozen=True)
class SimulationSummary:
    """Aggregated Monte Carlo tournament simulation output."""

    iterations: int
    seed: int
    distributions: dict[str, Any]
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "iterations": self.iterations,
            "seed": self.seed,
            "distributions": self.distributions,
            "metadata": self.metadata,
        }
