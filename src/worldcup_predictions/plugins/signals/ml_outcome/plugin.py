"""Deterministic calibrated outcome model signal plugin."""

from __future__ import annotations

import datetime as dt
import warnings
from collections import defaultdict, deque
from dataclasses import dataclass
from typing import Any

from worldcup_predictions.core.constants import SOURCE_ML_OUTCOME
from worldcup_predictions.core.contracts import Artifact, Diagnostic, ScoreTip, Signal
from worldcup_predictions.core.datasets import HISTORICAL_RESULTS, ML_OUTCOME_MODELS
from worldcup_predictions.core.events import EventName, event_value
from worldcup_predictions.core.metadata import PluginKind, PluginMetadata
from worldcup_predictions.core.plugin import BasePlugin, PluginResult
from worldcup_predictions.core.signals import ML_HDA_PROBABILITIES
from worldcup_predictions.model import HistoricalResult, load_historical_results
from worldcup_predictions.model.baseline import actual_result, expected_result, goal_diff_multiplier, tournament_weight
from worldcup_predictions.model.contracts import BaselineModelConfig
from worldcup_predictions.tournament import TournamentState
from worldcup_predictions.tournament.repository import load_tournament_state


@dataclass(frozen=True)
class OutcomeBucket:
    bucket: int
    home: float
    draw: float
    away: float
    samples: int


@dataclass(frozen=True)
class SklearnOutcomeModel:
    model_id: str
    pipeline: Any
    labels: list[str]
    builder: "RollingFeatureBuilder"
    samples: int


class MlOutcomePlugin(BasePlugin):
    """Emit optional ML-style H/D/A probabilities from historical calibration."""

    id = "ml_outcome"
    version = "0.1.0"
    priority = 320
    subscribed_events = (EventName.FEATURE_SIGNALS_REQUESTED.value,)
    metadata = PluginMetadata(
        plugin_id=id,
        kind=PluginKind.SIGNAL,
        description="Train a deterministic Elo-delta outcome calibration model and emit H/D/A signals.",
        datasets_read=(HISTORICAL_RESULTS,),
        datasets_written=(ML_OUTCOME_MODELS,),
        signals_emitted=(ML_HDA_PROBABILITIES,),
        confidence_policy="The model is sample-size weighted, smoothed, and blended below market odds.",
    )

    def handle(self, event, context, payload):
        if context.storage is None:
            return PluginResult(
                plugin_id=self.id,
                event=event_value(event),
                diagnostics=[Diagnostic("warning", "Structured storage is unavailable; ML outcome layer was skipped.", self.id)],
            )
        historical_results = load_historical_results(context.storage)
        state = context.state.get("tournament_state")
        if not isinstance(state, TournamentState):
            state = load_tournament_state(context.storage)
            context.state["tournament_state"] = state
        sklearn_model = train_sklearn_outcome_model(historical_results)
        if sklearn_model is not None:
            model_rows = [sklearn_model_record(sklearn_model)]
            signals = sklearn_signals_for_fixtures(state.open_fixtures(), sklearn_model)
        else:
            model = train_outcome_bucket_model(historical_results)
            model_rows = [bucket_to_record(row, model_id=model_id(model)) for row in model]
            signals = ml_signals_for_fixtures(state.open_fixtures(), historical_results, model)
        count = context.storage.write_records(ML_OUTCOME_MODELS, model_rows, source=self.id, run_id=context.run_id)
        diagnostics = []
        if not historical_results:
            diagnostics.append(Diagnostic("info", "No historical results are available; ML outcome layer was skipped.", self.id))
        elif not signals:
            diagnostics.append(Diagnostic("info", "Not enough historical rows to train an ML outcome model.", self.id))
        return PluginResult(
            plugin_id=self.id,
            event=event_value(event),
            signals=signals,
            artifacts=[Artifact(ML_OUTCOME_MODELS, "structured_dataset", self.id, data={"rows": count, "signals": len(signals)})],
            diagnostics=diagnostics,
            metadata={"model_rows": count, "signals": len(signals)},
        )


def train_outcome_bucket_model(results: list[HistoricalResult], *, min_year: int = 2002) -> list[OutcomeBucket]:
    """Train smoothed H/D/A frequencies by pre-match Elo-delta bucket."""

    config = BaselineModelConfig()
    rows = [row for row in sorted(results, key=lambda item: item.date) if row.played_on.year >= min_year]
    if len(rows) < 100:
        return []
    buckets: dict[int, dict[str, float]] = {}
    ratings: dict[str, float] = {}
    for row in rows:
        home_rating = ratings.get(row.home_team.key, config.base_rating)
        away_rating = ratings.get(row.away_team.key, config.base_rating)
        delta = home_rating - away_rating + (0 if row.neutral else config.home_advantage)
        bucket = int(round(delta / 100.0) * 100)
        bucket = max(-800, min(800, bucket))
        current = buckets.setdefault(bucket, {"home": 2.0, "draw": 2.0, "away": 2.0, "samples": 0.0})
        if row.score.home > row.score.away:
            current["home"] += 1.0
        elif row.score.home < row.score.away:
            current["away"] += 1.0
        else:
            current["draw"] += 1.0
        current["samples"] += 1.0
        adjusted_home_rating = home_rating + (0 if row.neutral else config.home_advantage)
        expected_home = expected_result(adjusted_home_rating, away_rating)
        change = tournament_weight(row.tournament) * goal_diff_multiplier(row.score.home - row.score.away) * (actual_result(row.score) - expected_home)
        ratings[row.home_team.key] = home_rating + change
        ratings[row.away_team.key] = away_rating - change
    return [
        OutcomeBucket(
            bucket=bucket,
            home=counts["home"] / (counts["home"] + counts["draw"] + counts["away"]),
            draw=counts["draw"] / (counts["home"] + counts["draw"] + counts["away"]),
            away=counts["away"] / (counts["home"] + counts["draw"] + counts["away"]),
            samples=int(counts["samples"]),
        )
        for bucket, counts in sorted(buckets.items())
    ]


class RollingFeatureBuilder:
    """Rolling features for the optional sklearn ensemble."""

    def __init__(self, *, config: BaselineModelConfig | None = None) -> None:
        self.config = config or BaselineModelConfig()
        self.ratings: dict[str, float] = {}
        self.recent: dict[str, deque[dict[str, float]]] = defaultdict(lambda: deque(maxlen=8))
        self.h2h: dict[tuple[str, str], deque[dict[str, float]]] = defaultdict(lambda: deque(maxlen=6))

    def features(self, row: HistoricalResult) -> list[float]:
        home_rating = self.ratings.get(row.home_team.key, self.config.base_rating)
        away_rating = self.ratings.get(row.away_team.key, self.config.base_rating)
        home_advantage = 0.0 if row.neutral else self.config.home_advantage
        delta = home_rating + home_advantage - away_rating
        home_recent = self._recent_summary(row.home_team.key)
        away_recent = self._recent_summary(row.away_team.key)
        home_form = self._attack_defense_summary(row.home_team.key)
        away_form = self._attack_defense_summary(row.away_team.key)
        h2h = self._h2h_summary(row.home_team.key, row.away_team.key)
        return [
            delta,
            expected_result(home_rating + home_advantage, away_rating),
            1.0 if row.neutral else 0.0,
            home_advantage,
            home_recent["points"],
            away_recent["points"],
            home_recent["points"] - away_recent["points"],
            home_recent["goal_diff"],
            away_recent["goal_diff"],
            home_recent["goal_diff"] - away_recent["goal_diff"],
            h2h["home"],
            h2h["away"],
            h2h["home"] - h2h["away"],
            float(row.played_on.year),
            *tournament_flags(row.tournament),
            home_form["attack"],
            away_form["attack"],
            home_form["attack"] - away_form["attack"],
            home_form["defense"],
            away_form["defense"],
            home_form["defense"] - away_form["defense"],
        ]

    def update(self, row: HistoricalResult) -> None:
        home = row.home_team.key
        away = row.away_team.key
        home_rating = self.ratings.get(home, self.config.base_rating)
        away_rating = self.ratings.get(away, self.config.base_rating)
        adjusted_home_rating = home_rating + (0 if row.neutral else self.config.home_advantage)
        expected_home = expected_result(adjusted_home_rating, away_rating)
        change = tournament_weight(row.tournament) * goal_diff_multiplier(row.score.home - row.score.away) * (actual_result(row.score) - expected_home)
        self.ratings[home] = home_rating + change
        self.ratings[away] = away_rating - change
        if row.score.home > row.score.away:
            home_points, away_points = 3.0, 0.0
        elif row.score.home < row.score.away:
            home_points, away_points = 0.0, 3.0
        else:
            home_points = away_points = 1.0
        self.recent[home].append(
            {"points": home_points, "goal_diff": row.score.home - row.score.away, "goals_for": float(row.score.home), "goals_against": float(row.score.away)}
        )
        self.recent[away].append(
            {"points": away_points, "goal_diff": row.score.away - row.score.home, "goals_for": float(row.score.away), "goals_against": float(row.score.home)}
        )
        self.h2h[tuple(sorted((home, away)))].append({"team": home, "points": home_points})

    def fixture_features(self, fixture) -> list[float]:
        row = HistoricalResult(
            date=(fixture.kickoff_at or dt.datetime.now(dt.timezone.utc)).date().isoformat(),
            home_team=fixture.home_team,
            away_team=fixture.away_team,
            score=ScoreTip(0, 0),
            tournament="FIFA World Cup",
            neutral=True,
            source="ml_fixture",
        )
        return self.features(row)

    def _recent_summary(self, team: str) -> dict[str, float]:
        rows = self.recent[team]
        if not rows:
            return {"points": 1.2, "goal_diff": 0.0}
        return {
            "points": sum(row["points"] for row in rows) / len(rows),
            "goal_diff": sum(row["goal_diff"] for row in rows) / len(rows),
        }

    def _attack_defense_summary(self, team: str) -> dict[str, float]:
        rows = self.recent[team]
        if not rows:
            return {"attack": 1.2, "defense": 1.2}
        return {
            "attack": sum(row.get("goals_for", 0.0) for row in rows) / len(rows),
            "defense": sum(row.get("goals_against", 0.0) for row in rows) / len(rows),
        }

    def _h2h_summary(self, home: str, away: str) -> dict[str, float]:
        rows = self.h2h[tuple(sorted((home, away)))]
        if not rows:
            return {"home": 1.2, "away": 1.2}
        home_points = []
        away_points = []
        for row in rows:
            points = float(row["points"])
            if row["team"] == home:
                home_points.append(points)
                away_points.append(3.0 - points if points != 1.0 else 1.0)
            else:
                away_points.append(points)
                home_points.append(3.0 - points if points != 1.0 else 1.0)
        return {
            "home": sum(home_points) / len(home_points),
            "away": sum(away_points) / len(away_points),
        }


def train_sklearn_outcome_model(results: list[HistoricalResult], *, min_year: int = 2002) -> SklearnOutcomeModel | None:
    rows = [row for row in sorted(results, key=lambda item: item.date) if row.played_on.year >= min_year]
    if len(rows) < 300:
        return None
    try:
        from sklearn.calibration import CalibratedClassifierCV
        from sklearn.ensemble import ExtraTreesClassifier
        from sklearn.pipeline import make_pipeline
        from sklearn.preprocessing import StandardScaler
    except ModuleNotFoundError:
        return None
    builder = RollingFeatureBuilder()
    features: list[list[float]] = []
    labels: list[str] = []
    weights: list[float] = []
    cutoff = dt.date(2026, 6, 11)
    for row in rows:
        features.append(builder.features(row))
        labels.append(_outcome_label(row))
        weights.append(_recency_weight(row.played_on, cutoff) * max(0.5, tournament_weight(row.tournament) / 25))
        builder.update(row)
    base = ExtraTreesClassifier(
        n_estimators=250,
        min_samples_leaf=8,
        max_features="sqrt",
        class_weight="balanced",
        random_state=20260611,
        n_jobs=-1,
    )
    try:
        classifier = CalibratedClassifierCV(base, method="sigmoid", cv=3)
        pipeline = make_pipeline(StandardScaler(), classifier)
        with warnings.catch_warnings():
            warnings.filterwarnings("ignore", category=RuntimeWarning)
            pipeline.fit(features, labels, calibratedclassifiercv__sample_weight=weights)
    except TypeError:
        pipeline = make_pipeline(StandardScaler(), base)
        pipeline.fit(features, labels, extratreesclassifier__sample_weight=weights)
    return SklearnOutcomeModel(
        model_id=f"sklearn_extra_trees_outcome_v1:{len(rows)}",
        pipeline=pipeline,
        labels=list(getattr(pipeline[-1], "classes_", [])),
        builder=builder,
        samples=len(rows),
    )


def sklearn_signals_for_fixtures(fixtures, model: SklearnOutcomeModel) -> list[Signal]:
    if not fixtures:
        return []
    probabilities = model.pipeline.predict_proba([model.builder.fixture_features(fixture) for fixture in fixtures])
    classes = list(model.pipeline.classes_)
    signals = []
    for fixture, probs in zip(fixtures, probabilities, strict=True):
        mapped = {label: float(probs[classes.index(label)]) if label in classes else 0.0 for label in ("away", "draw", "home")}
        total = sum(mapped.values()) or 1.0
        home = mapped["home"] / total
        draw = mapped["draw"] / total
        away = mapped["away"] / total
        signals.append(
            Signal(
                name=ML_HDA_PROBABILITIES,
                source=SOURCE_ML_OUTCOME,
                fixture_key=fixture.key,
                value=None,
                weight=0.85,
                confidence=min(0.78, 0.35 + model.samples / 5000),
                rationale="Optional sklearn ExtraTrees outcome ensemble.",
                metadata={
                    "model_id": model.model_id,
                    "model": "sklearn_extra_trees_calibrated_v1",
                    "samples": model.samples,
                    "prob_home": home,
                    "prob_draw": draw,
                    "prob_away": away,
                },
            )
        )
    return signals


def ml_signals_for_fixtures(fixtures, results: list[HistoricalResult], model: list[OutcomeBucket]) -> list[Signal]:
    if not model:
        return []
    config = BaselineModelConfig()
    signals: list[Signal] = []
    by_bucket = {row.bucket: row for row in model}
    available_buckets = sorted(by_bucket)
    for fixture in fixtures:
        cutoff = fixture.kickoff_at or dt.datetime.now(dt.timezone.utc)
        ratings = rolling_ratings_before(results, cutoff, config=config)
        delta = ratings.get(fixture.home_team.key, config.base_rating) - ratings.get(fixture.away_team.key, config.base_rating)
        bucket = int(round(delta / 100.0) * 100)
        nearest = min(available_buckets, key=lambda item: abs(item - bucket))
        row = by_bucket[nearest]
        confidence = max(0.20, min(0.70, row.samples / 260.0))
        signals.append(
            Signal(
                name=ML_HDA_PROBABILITIES,
                source=SOURCE_ML_OUTCOME,
                fixture_key=fixture.key,
                value=None,
                weight=0.75,
                confidence=confidence,
                rationale="Historical Elo-delta outcome calibration.",
                metadata={
                    "model_id": model_id(model),
                    "bucket": nearest,
                    "samples": row.samples,
                    "prob_home": row.home,
                    "prob_draw": row.draw,
                    "prob_away": row.away,
                },
            )
        )
    return signals


def rolling_ratings_before(results: list[HistoricalResult], cutoff: dt.datetime, *, config: BaselineModelConfig) -> dict[str, float]:
    ratings: dict[str, float] = {}
    cutoff_date = cutoff.date()
    for row in sorted(results, key=lambda item: item.date):
        if row.played_on >= cutoff_date:
            break
        home_rating = ratings.get(row.home_team.key, config.base_rating)
        away_rating = ratings.get(row.away_team.key, config.base_rating)
        adjusted_home_rating = home_rating + (0 if row.neutral else config.home_advantage)
        expected_home = expected_result(adjusted_home_rating, away_rating)
        change = tournament_weight(row.tournament) * goal_diff_multiplier(row.score.home - row.score.away) * (actual_result(row.score) - expected_home)
        ratings[row.home_team.key] = home_rating + change
        ratings[row.away_team.key] = away_rating - change
    return ratings


def sklearn_model_record(model: SklearnOutcomeModel) -> dict[str, Any]:
    return {
        "record_key": model.model_id,
        "model_id": model.model_id,
        "model": "sklearn_extra_trees_calibrated_v1",
        "samples": model.samples,
        "metadata": {
            "labels": model.labels,
            "feature_names": [
                "elo_diff",
                "elo_home_win_probability",
                "neutral",
                "home_advantage",
                "home_recent_points",
                "away_recent_points",
                "recent_points_diff",
                "home_recent_goal_diff",
                "away_recent_goal_diff",
                "recent_goal_diff_delta",
                "home_h2h_points",
                "away_h2h_points",
                "h2h_points_diff",
                "match_year",
                "world_cup",
                "world_cup_qualification",
                "continental",
                "friendly",
                "home_recent_attack",
                "away_recent_attack",
                "recent_attack_diff",
                "home_recent_defense",
                "away_recent_defense",
                "recent_defense_diff",
            ],
        },
    }


def tournament_flags(tournament: str | None) -> list[float]:
    name = (tournament or "").casefold()
    return [
        1.0 if name == "fifa world cup" else 0.0,
        1.0 if "world cup qualification" in name or "world cup qualifiers" in name else 0.0,
        1.0 if any(token in name for token in ("euro", "copa", "africa cup", "asian cup", "gold cup")) else 0.0,
        1.0 if "friendly" in name else 0.0,
    ]


def _outcome_label(row: HistoricalResult) -> str:
    if row.score.home > row.score.away:
        return "home"
    if row.score.home < row.score.away:
        return "away"
    return "draw"


def _recency_weight(match_date: dt.date, cutoff: dt.date) -> float:
    days = max(0, (cutoff - match_date).days)
    return 0.5 ** (days / 1460)


def bucket_to_record(bucket: OutcomeBucket, *, model_id: str) -> dict[str, Any]:
    return {
        "record_key": f"{model_id}:{bucket.bucket}",
        "model_id": model_id,
        "bucket": bucket.bucket,
        "prob_home": bucket.home,
        "prob_draw": bucket.draw,
        "prob_away": bucket.away,
        "samples": bucket.samples,
        "metadata": {"model": "elo_delta_bucket_calibration_v1"},
    }


def model_id(model: list[OutcomeBucket]) -> str:
    if not model:
        return "elo_delta_bucket_calibration_v1:empty"
    samples = sum(row.samples for row in model)
    return f"elo_delta_bucket_calibration_v1:{samples}"
