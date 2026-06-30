"""Typed data contracts exchanged between workflow plugins."""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field
from typing import Any


def parse_utc_datetime(value: str | None) -> dt.datetime | None:
    if not value:
        return None
    normalized = value.replace("Z", "+00:00")
    parsed = dt.datetime.fromisoformat(normalized)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=dt.timezone.utc)
    return parsed.astimezone(dt.timezone.utc)


@dataclass(frozen=True)
class Fixture:
    """A single match fixture."""

    event_date: str
    home_team: str
    away_team: str
    source_id: str | None = None
    stage: str | None = None
    group: str | None = None
    matchday: int | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def key(self) -> str:
        home_key = self.metadata.get("home_fifa_code") or self.home_team
        away_key = self.metadata.get("away_fifa_code") or self.away_team
        return f"{self.event_date}|{home_key}|{away_key}"

    @property
    def kickoff_at(self) -> dt.datetime | None:
        return parse_utc_datetime(self.event_date)


@dataclass(frozen=True)
class ScoreTip:
    """A concrete exact-score tip."""

    home: int
    away: int

    def as_text(self) -> str:
        return f"{self.home}:{self.away}"


@dataclass(frozen=True)
class ScoreMatrixEntry:
    """One exact-score probability from the neutral score model."""

    home: int
    away: int
    probability: float
    metadata: dict[str, Any] = field(default_factory=dict)

    def as_tip(self) -> ScoreTip:
        return ScoreTip(self.home, self.away)

    def to_dict(self) -> dict[str, Any]:
        return {
            "home": self.home,
            "away": self.away,
            "probability": self.probability,
            "metadata": self.metadata,
        }


@dataclass(frozen=True)
class OutcomeProbabilities:
    """Home/draw/away probabilities."""

    home: float
    draw: float
    away: float

    def max_probability(self) -> float:
        return max(self.home, self.draw, self.away)

    def as_percentages(self) -> str:
        return f"{self.home:.0%} / {self.draw:.0%} / {self.away:.0%}"


@dataclass(frozen=True)
class Signal:
    """A model input signal produced by a plugin."""

    name: str
    source: str
    fixture_key: str | None = None
    value: float | str | bool | None = None
    weight: float | None = None
    confidence: float | None = None
    rationale: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class Artifact:
    """A structured output or file pointer emitted by a plugin."""

    name: str
    kind: str
    source: str
    path: str | None = None
    data: Any = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class Diagnostic:
    """Human-readable workflow diagnostic."""

    level: str
    message: str
    source: str
    fixture_key: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ProviderRuleset:
    """A scoring rule provider and version."""

    provider: str
    version: str
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def key(self) -> str:
        return f"{self.provider}:{self.version}"


@dataclass(frozen=True)
class OptimizedTip:
    """A provider-optimized tip derived from a neutral prediction."""

    ruleset: ProviderRuleset
    fixture_key: str
    tip: ScoreTip | None
    expected_points: float
    optimizer_id: str
    selection: str | None = None
    selection_type: str = "exact_score"
    confidence: float | None = None
    rationale: str | None = None
    alternatives: list[dict[str, Any]] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)

    def display_text(self) -> str:
        if self.selection:
            return self.selection
        if self.tip is not None:
            return self.tip.as_text()
        return ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "provider": self.ruleset.provider,
            "ruleset_version": self.ruleset.version,
            "ruleset_key": self.ruleset.key,
            "fixture_key": self.fixture_key,
            "tip": self.display_text(),
            "selection": self.selection,
            "selection_type": self.selection_type,
            "tip_home": self.tip.home if self.tip is not None else None,
            "tip_away": self.tip.away if self.tip is not None else None,
            "expected_points": self.expected_points,
            "optimizer_id": self.optimizer_id,
            "confidence": self.confidence,
            "rationale": self.rationale,
            "alternatives": self.alternatives,
            "metadata": self.metadata,
        }


@dataclass(frozen=True)
class Prediction:
    """Provider-neutral match prediction.

    This is intentionally independent from any game or scoring provider.
    Provider-specific exact-score choices are represented as OptimizedTip
    artifacts created by optimizer plugins.
    """

    fixture: Fixture
    most_likely: ScoreTip
    outcome_probabilities: OutcomeProbabilities
    confidence_label: str
    confidence_percent: float
    expected_home_goals: float | None = None
    expected_away_goals: float | None = None
    source: str = ""
    score_matrix: list[ScoreMatrixEntry] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "event_date": self.fixture.event_date,
            "home_team": self.fixture.home_team,
            "away_team": self.fixture.away_team,
            "most_likely_result": self.most_likely.as_text(),
            "expected_home_goals": self.expected_home_goals,
            "expected_away_goals": self.expected_away_goals,
            "prob_home": self.outcome_probabilities.home,
            "prob_draw": self.outcome_probabilities.draw,
            "prob_away": self.outcome_probabilities.away,
            "confidence_label": self.confidence_label,
            "confidence_percent": self.confidence_percent,
            "source": self.source,
            "fixture": {
                "key": self.fixture.key,
                "stage": self.fixture.stage,
                "group": self.fixture.group,
                "matchday": self.fixture.matchday,
                "source_id": self.fixture.source_id,
                "metadata": self.fixture.metadata,
            },
            "score_matrix": [entry.to_dict() for entry in self.score_matrix],
            "metadata": self.metadata,
        }


def confidence_label(probability: float) -> str:
    if probability >= 0.70:
        return "High"
    if probability >= 0.55:
        return "Medium-high"
    if probability >= 0.45:
        return "Medium-low"
    return "Low"
