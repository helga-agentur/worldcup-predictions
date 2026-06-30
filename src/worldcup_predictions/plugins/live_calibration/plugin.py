"""Live calibration from finished World Cup matches."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from typing import Any

from worldcup_predictions.core.constants import (
    SIGNAL_WEIGHT_LIVE_DRAW,
    SIGNAL_WEIGHT_LIVE_FAVORITE,
    SIGNAL_WEIGHT_LIVE_SCORE_TAIL,
)
from worldcup_predictions.core.contracts import Artifact, Diagnostic, Signal
from worldcup_predictions.core.datasets import CALIBRATION_DECISIONS
from worldcup_predictions.core.datasets import LIVE_GLOBAL_CALIBRATION as LIVE_GLOBAL_CALIBRATION_DATASET
from worldcup_predictions.core.datasets import MATCH_ANALYSIS_TEAM_ADJUSTMENTS
from worldcup_predictions.core.datasets import POSTMATCH_TEAM_PERFORMANCE as POSTMATCH_TEAM_PERFORMANCE_DATASET
from worldcup_predictions.core.datasets import PREDICTION_BACKTEST as PREDICTION_BACKTEST_DATASET
from worldcup_predictions.core.datasets import TEAM_CALIBRATION as TEAM_CALIBRATION_DATASET
from worldcup_predictions.core.events import EventName, event_value
from worldcup_predictions.core.metadata import PluginKind, PluginMetadata
from worldcup_predictions.core.plugin import BasePlugin, PluginResult
from worldcup_predictions.core.signals import LIVE_DRAW_ADJUSTMENT, LIVE_FAVORITE_OUTCOME_FACTOR, LIVE_SCORE_TAIL_FACTOR, TEAM_EXPECTED_GOALS_FACTOR, TOTAL_GOALS_FACTOR
from worldcup_predictions.tournament import TournamentState
from worldcup_predictions.tournament.contracts import FixtureRecord, ResultRecord
from worldcup_predictions.tournament.repository import load_tournament_state


# Bayesian shrinkage toward pre-tournament priors. A handful of early matches cannot be
# trusted as the true draw/scoring rate, so each observed rate is shrunk toward its prior
# with an 18-match pseudo-count before any global factor is derived. With few matches the
# posterior stays near the prior (neutral factors); it converges to the observed rate as
# the tournament progresses.
LIVE_CALIBRATION_PRIOR_MATCHES = 18
PRIOR_DRAW_RATE = 0.27
PRIOR_GOALS_PER_MATCH = 2.55
PRIOR_HIGH_TOTAL_RATE = 0.28


@dataclass
class TeamSample:
    team_key: str
    team_name: str
    fifa_code: str | None
    quality_for: float
    quality_against: float
    weight: float
    source: str


class LiveCalibrationPlugin(BasePlugin):
    """Emit conservative team xG factors from played tournament matches."""

    id = "live_calibration"
    version = "0.1.0"
    priority = 330
    subscribed_events = (EventName.RESULTS_UPDATED.value, EventName.FEATURE_SIGNALS_REQUESTED.value)
    metadata = PluginMetadata(
        plugin_id=id,
        kind=PluginKind.SIGNAL,
        description="Convert finished tournament results and chance-quality rows into team expected-goal factors.",
        datasets_read=(POSTMATCH_TEAM_PERFORMANCE_DATASET, PREDICTION_BACKTEST_DATASET, MATCH_ANALYSIS_TEAM_ADJUSTMENTS),
        datasets_written=(TEAM_CALIBRATION_DATASET, LIVE_GLOBAL_CALIBRATION_DATASET, CALIBRATION_DECISIONS),
        signals_emitted=(TEAM_EXPECTED_GOALS_FACTOR, TOTAL_GOALS_FACTOR, LIVE_DRAW_ADJUSTMENT, LIVE_SCORE_TAIL_FACTOR, LIVE_FAVORITE_OUTCOME_FACTOR),
        confidence_policy="Recent tournament samples are conservative, sample-size weighted, and capped.",
    )

    def handle(self, event, context, payload):
        if context.storage is None:
            return PluginResult(
                plugin_id=self.id,
                event=event_value(event),
                diagnostics=[Diagnostic(level="warning", message="Structured storage is unavailable; live calibration was skipped.", source=self.id)],
            )
        if event_value(event) == EventName.RESULTS_UPDATED.value:
            return self._record_calibration_decisions(event, context, payload)
        state = context.state.get("tournament_state")
        if not isinstance(state, TournamentState):
            state = load_tournament_state(context.storage)
            context.state["tournament_state"] = state
        performance_rows = context.storage.read_records(POSTMATCH_TEAM_PERFORMANCE_DATASET, latest_only=True)
        backtest_rows = context.storage.read_records(PREDICTION_BACKTEST_DATASET, latest_only=True)
        analysis_adjustment_rows = context.storage.read_records(MATCH_ANALYSIS_TEAM_ADJUSTMENTS, latest_only=True)
        calibration_rows = apply_analysis_adjustments(
            calibration_rows_from_state(state, performance_rows),
            analysis_adjustment_rows,
        )
        count = context.storage.write_records(TEAM_CALIBRATION_DATASET, calibration_rows, source=self.id, run_id=context.run_id)
        global_rows = global_calibration_rows_from_state(state, backtest_rows)
        global_count = context.storage.write_records(LIVE_GLOBAL_CALIBRATION_DATASET, global_rows, source=self.id, run_id=context.run_id)
        signals = calibration_signals_for_open_fixtures(state.open_fixtures(), calibration_rows)
        signals.extend(global_calibration_signals(global_rows))
        diagnostics = []
        if not performance_rows:
            diagnostics.append(
                Diagnostic(
                    level="info",
                    message="No postmatch performance rows are available; live calibration used score-only samples.",
                    source=self.id,
                )
            )
        return PluginResult(
            plugin_id=self.id,
            event=event_value(event),
            signals=signals,
            artifacts=[
                Artifact(
                    name=TEAM_CALIBRATION_DATASET,
                    kind="structured_dataset",
                    source=self.id,
                    data={"rows": count, "signals": len(signals)},
                ),
                Artifact(
                    name=LIVE_GLOBAL_CALIBRATION_DATASET,
                    kind="structured_dataset",
                    source=self.id,
                    data={"rows": global_count},
                ),
            ],
            diagnostics=diagnostics,
            metadata={"rows": count, "global_rows": global_count, "signals": len(signals)},
        )

    def _record_calibration_decisions(self, event, context, payload) -> PluginResult:
        state = context.state.get("tournament_state")
        if not isinstance(state, TournamentState):
            state = load_tournament_state(context.storage)
            context.state["tournament_state"] = state
        backtest_rows = context.storage.read_records(PREDICTION_BACKTEST_DATASET, latest_only=True)
        previous_global_rows = context.storage.read_records(LIVE_GLOBAL_CALIBRATION_DATASET, latest_only=True)
        previous_decision_rows = context.storage.read_records(CALIBRATION_DECISIONS, latest_only=True)
        current_global_rows = global_calibration_rows_from_state(state, backtest_rows)
        rows = live_calibration_decision_rows(
            previous_global_rows=previous_global_rows,
            current_global_rows=current_global_rows,
            previous_decision_rows=previous_decision_rows,
            result_update_payload=payload.to_dict() if hasattr(payload, "to_dict") else dict(payload),
            run_id=context.run_id,
        )
        count = context.storage.write_records(CALIBRATION_DECISIONS, rows, source=self.id, run_id=context.run_id)
        diagnostics = []
        if not rows:
            diagnostics.append(Diagnostic("info", "No live calibration decisions were written because no finished-match sample exists.", self.id))
        return PluginResult(
            plugin_id=self.id,
            event=event_value(event),
            artifacts=[Artifact(CALIBRATION_DECISIONS, "structured_dataset", self.id, data={"rows": count})],
            diagnostics=diagnostics,
            metadata={"calibration_decisions": count},
        )


def calibration_rows_from_state(state: TournamentState, performance_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    samples = samples_from_results(state.results, performance_rows)
    if not samples:
        return []
    global_quality = sum(sample.quality_for * sample.weight for sample in samples) / max(0.001, sum(sample.weight for sample in samples))
    by_team: dict[str, list[TeamSample]] = defaultdict(list)
    for sample in samples:
        by_team[sample.team_key].append(sample)
    rows = []
    for team_key, team_samples in sorted(by_team.items()):
        total_weight = sum(sample.weight for sample in team_samples)
        avg_for = sum(sample.quality_for * sample.weight for sample in team_samples) / max(0.001, total_weight)
        avg_against = sum(sample.quality_against * sample.weight for sample in team_samples) / max(0.001, total_weight)
        attack_factor = _clamp(1 + (avg_for - global_quality) * 0.08, 0.92, 1.08)
        defense_leak_factor = _clamp(1 + (avg_against - global_quality) * 0.06, 0.94, 1.06)
        confidence = _clamp(0.25 + total_weight * 0.15, 0.25, 0.80)
        first = team_samples[0]
        rows.append(
            {
                "record_key": team_key,
                "team": first.team_name,
                "fifa_code": first.fifa_code,
                "sample_count": len(team_samples),
                "weighted_matches": total_weight,
                "avg_quality_for": avg_for,
                "avg_quality_against": avg_against,
                "global_quality": global_quality,
                "attack_factor": attack_factor,
                "defense_leak_factor": defense_leak_factor,
                "confidence": confidence,
                "sources": sorted({sample.source for sample in team_samples}),
            }
        )
    return rows


def calibration_signals_for_open_fixtures(fixtures: list[FixtureRecord], calibration_rows: list[dict[str, Any]]) -> list[Signal]:
    by_team = {str(row.get("fifa_code") or row.get("team")): row for row in calibration_rows}
    signals: list[Signal] = []
    for fixture in fixtures:
        home = by_team.get(fixture.home_team.key)
        away = by_team.get(fixture.away_team.key)
        for side, team_row, opponent_row in (("home", home, away), ("away", away, home)):
            if not team_row:
                continue
            attack_factor = float(team_row.get("attack_factor") or 1.0)
            opponent_defense = float(opponent_row.get("defense_leak_factor") or 1.0) if opponent_row else 1.0
            factor = _clamp(attack_factor * opponent_defense, 0.88, 1.12)
            if abs(factor - 1.0) < 0.01:
                continue
            confidence = float(team_row.get("confidence") or 0.25)
            if opponent_row:
                confidence = min(confidence, float(opponent_row.get("confidence") or confidence))
            signals.append(
                Signal(
                    name="team_expected_goals_factor",
                    source="live_calibration",
                    fixture_key=fixture.key,
                    value=factor,
                    weight=0.40,
                    confidence=confidence,
                    rationale="Recent tournament performance calibration.",
                    metadata={
                        "side": side,
                        "team": team_row.get("team"),
                        "attack_factor": attack_factor,
                        "opponent_defense_leak_factor": opponent_defense,
                        "sample_count": team_row.get("sample_count"),
                    },
                )
            )
    return signals


def apply_analysis_adjustments(calibration_rows: list[dict[str, Any]], adjustment_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if not adjustment_rows:
        return calibration_rows
    factor_by_team: dict[str, list[float]] = defaultdict(list)
    for row in adjustment_rows:
        key = str(row.get("fifa_code") or row.get("team") or "")
        if not key:
            continue
        try:
            factor = float(row.get("expected_goals_factor") or 1.0)
            confidence = float(row.get("confidence") or 0.0)
        except (TypeError, ValueError):
            continue
        factor_by_team[key].append(1 + (factor - 1) * _clamp(confidence, 0.0, 1.0))
    adjusted = []
    for row in calibration_rows:
        key = str(row.get("fifa_code") or row.get("team") or "")
        factors = factor_by_team.get(key) or []
        if not factors:
            adjusted.append(row)
            continue
        analysis_factor = _clamp(sum(factors) / len(factors), 0.98, 1.02)
        adjusted.append(
            {
                **row,
                "attack_factor": _clamp(float(row.get("attack_factor") or 1.0) * analysis_factor, 0.90, 1.10),
                "analysis_adjustment_factor": analysis_factor,
                "metadata": {
                    **dict(row.get("metadata") or {}),
                    "analysis_adjustment_count": len(factors),
                },
            }
        )
    return adjusted


def global_calibration_rows_from_state(state: TournamentState, backtest_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    results = list(state.results)
    if not results:
        return []
    sample_count = len(results)
    draw_count = sum(1 for result in results if result.score.home == result.score.away)
    total_goals = sum(result.score.home + result.score.away for result in results)
    high_total_count = sum(1 for result in results if result.score.home + result.score.away >= 4)
    draw_rate = draw_count / sample_count
    goals_per_match = total_goals / sample_count
    high_total_rate = high_total_count / sample_count
    favorite_rows = _favorite_audit_rows(backtest_rows)
    favorite_hit_rate = (
        sum(1 for row in favorite_rows if row["favorite_outcome"] == row["actual_outcome"]) / len(favorite_rows)
        if favorite_rows
        else None
    )
    posterior_draw_rate = _shrink_to_prior(draw_rate, sample_count, PRIOR_DRAW_RATE)
    posterior_goals_per_match = _shrink_to_prior(goals_per_match, sample_count, PRIOR_GOALS_PER_MATCH)
    posterior_high_total_rate = _shrink_to_prior(high_total_rate, sample_count, PRIOR_HIGH_TOTAL_RATE)
    row = {
        "record_key": "global",
        "sample_count": sample_count,
        "draw_rate": draw_rate,
        "goals_per_match": goals_per_match,
        "high_total_rate": high_total_rate,
        "posterior_draw_rate": posterior_draw_rate,
        "posterior_goals_per_match": posterior_goals_per_match,
        "posterior_high_total_rate": posterior_high_total_rate,
        "favorite_sample_count": len(favorite_rows),
        "favorite_hit_rate": favorite_hit_rate,
        "draw_adjustment": _clamp((posterior_draw_rate - PRIOR_DRAW_RATE) * 0.35, -0.10, 0.12),
        "total_goals_factor": _clamp(posterior_goals_per_match / PRIOR_GOALS_PER_MATCH, 0.90, 1.10),
        "score_tail_factor": _clamp((posterior_high_total_rate - PRIOR_HIGH_TOTAL_RATE) * 0.20, -0.06, 0.08),
        "favorite_outcome_factor": _favorite_outcome_factor(favorite_hit_rate),
        "confidence": _clamp(sample_count / 24, 0.20, 0.80),
        "metadata": {
            "draw_count": draw_count,
            "high_total_count": high_total_count,
            "prior_matches": LIVE_CALIBRATION_PRIOR_MATCHES,
            "source": "finished_tournament_matches",
        },
    }
    return [row]


def global_calibration_signals(rows: list[dict[str, Any]]) -> list[Signal]:
    if not rows:
        return []
    row = rows[0]
    sample_count = int(row.get("sample_count") or 0)
    if sample_count < 4:
        return []
    confidence = float(row.get("confidence") or 0.2)
    metadata = {
        "sample_count": sample_count,
        "draw_rate": row.get("draw_rate"),
        "goals_per_match": row.get("goals_per_match"),
        "high_total_rate": row.get("high_total_rate"),
        "favorite_hit_rate": row.get("favorite_hit_rate"),
    }
    signals = [
        Signal(
            name=TOTAL_GOALS_FACTOR,
            source="live_calibration",
            value=row.get("total_goals_factor"),
            weight=0.30,
            confidence=confidence,
            rationale="Global tournament scoring pace calibration.",
            metadata=metadata,
        ),
        Signal(
            name=LIVE_DRAW_ADJUSTMENT,
            source="live_calibration",
            value=row.get("draw_adjustment"),
            weight=0.55,
            confidence=confidence,
            rationale="Global tournament draw-rate calibration.",
            metadata=metadata,
        ),
        Signal(
            name=LIVE_SCORE_TAIL_FACTOR,
            source="live_calibration",
            value=row.get("score_tail_factor"),
            weight=0.45,
            confidence=confidence,
            rationale="Global tournament high-score tail calibration.",
            metadata=metadata,
        ),
    ]
    favorite_factor = row.get("favorite_outcome_factor")
    if favorite_factor not in (None, "") and int(row.get("favorite_sample_count") or 0) >= 4:
        signals.append(
            Signal(
                name=LIVE_FAVORITE_OUTCOME_FACTOR,
                source="live_calibration",
                value=favorite_factor,
                weight=0.35,
                confidence=confidence,
                rationale="Global favorite/underdog calibration from frozen prediction audits.",
                metadata=metadata,
            )
        )
    return signals


def live_calibration_decision_rows(
    *,
    previous_global_rows: list[dict[str, Any]],
    current_global_rows: list[dict[str, Any]],
    previous_decision_rows: list[dict[str, Any]],
    result_update_payload: dict[str, Any],
    run_id: str,
) -> list[dict[str, Any]]:
    """Build report-only rows explaining live-calibration value changes."""

    if not current_global_rows:
        return []
    previous_global = previous_global_rows[0] if previous_global_rows else {}
    current_global = current_global_rows[0]
    previous_decisions = {
        str(row.get("parameter")): row
        for row in previous_decision_rows
        if row.get("parameter")
    }
    result_update_count = len(result_update_payload.get("new_results") or []) + len(result_update_payload.get("changed_results") or [])
    rows = []
    for parameter, label in (
        ("total_goals_factor", "Global tournament scoring pace factor"),
        ("draw_adjustment", "Global tournament draw adjustment"),
        ("score_tail_factor", "Global high-score tail adjustment"),
        ("favorite_outcome_factor", "Global favorite/underdog factor"),
    ):
        new_value = _optional_float(current_global.get(parameter))
        previous_value = _optional_float(previous_global.get(parameter))
        rows.append(
            _decision_row(
                parameter=parameter,
                action=_change_action(previous_value, new_value),
                previous_value=previous_value,
                new_value=new_value,
                recommended_value=new_value,
                run_id=run_id,
                reason=_calibration_value_reason(label, previous_value, new_value, current_global, result_update_count),
                current_global=current_global,
                result_update_payload=result_update_payload,
            )
        )

    recommended_weight = recommended_live_calibration_weight(current_global)
    previous_weight_row = previous_decisions.get("live_calibration_weight_recommendation", {})
    previous_weight = _optional_float(previous_weight_row.get("recommended_value") or previous_weight_row.get("new_value"))
    rows.append(
        _decision_row(
            parameter="live_calibration_weight_recommendation",
            action=_change_action(previous_weight, recommended_weight),
            previous_value=previous_weight,
            new_value=recommended_weight,
            recommended_value=recommended_weight,
            run_id=run_id,
            reason=_weight_recommendation_reason(previous_weight, recommended_weight, current_global, result_update_count),
            current_global=current_global,
            result_update_payload=result_update_payload,
            metadata={
                "report_only": True,
                "runtime_weights": {
                    LIVE_DRAW_ADJUSTMENT: SIGNAL_WEIGHT_LIVE_DRAW,
                    LIVE_SCORE_TAIL_FACTOR: SIGNAL_WEIGHT_LIVE_SCORE_TAIL,
                    LIVE_FAVORITE_OUTCOME_FACTOR: SIGNAL_WEIGHT_LIVE_FAVORITE,
                },
                "evidence": live_calibration_evidence(current_global),
            },
        )
    )
    return rows


def recommended_live_calibration_weight(row: dict[str, Any], *, minimum_matches: int = 20) -> float:
    """Legacy-compatible report-only recommendation for the umbrella live-calibration weight."""

    sample_count = int(row.get("sample_count") or 0)
    evidence = live_calibration_evidence(row)
    if sample_count < minimum_matches:
        return round(_clamp(0.20 + evidence * 0.30, 0.20, 0.32), 4)
    return round(_clamp(0.20 + evidence * 0.45, 0.24, 0.45), 4)


def live_calibration_evidence(row: dict[str, Any]) -> float:
    """Return a bounded signal-strength score from current live tournament behavior."""

    components = [
        abs(float(row.get("posterior_draw_rate") or PRIOR_DRAW_RATE) - PRIOR_DRAW_RATE) / 0.12,
        abs(float(row.get("total_goals_factor") or 1.0) - 1.0) / 0.10,
        abs(float(row.get("posterior_high_total_rate") or PRIOR_HIGH_TOTAL_RATE) - PRIOR_HIGH_TOTAL_RATE) / 0.18,
    ]
    favorite_hit_rate = row.get("favorite_hit_rate")
    if favorite_hit_rate not in (None, ""):
        components.append(abs(float(favorite_hit_rate) - 0.52) / 0.24)
    return round(_clamp(max(components), 0.0, 1.0), 6)


def _decision_row(
    *,
    parameter: str,
    action: str,
    previous_value: float | None,
    new_value: float | None,
    recommended_value: float | None,
    run_id: str,
    reason: str,
    current_global: dict[str, Any],
    result_update_payload: dict[str, Any],
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    sample_count = int(current_global.get("sample_count") or 0)
    return {
        "record_key": parameter,
        "run_id": run_id,
        "parameter": parameter,
        "action": action,
        "previous_value": previous_value,
        "new_value": new_value,
        "recommended_value": recommended_value,
        "changed": action in {"initialized", "changed"},
        "sample_count": sample_count,
        "confidence": current_global.get("confidence"),
        "reason": reason,
        "source_event": result_update_payload.get("source_event", ""),
        "metadata": {
            "current_global": current_global,
            "new_result_rows": len(result_update_payload.get("new_results") or []),
            "changed_result_rows": len(result_update_payload.get("changed_results") or []),
            **dict(metadata or {}),
        },
    }


def _change_action(previous_value: float | None, new_value: float | None) -> str:
    if previous_value is None and new_value is None:
        return "unavailable"
    if previous_value is None:
        return "initialized"
    if new_value is None:
        return "cleared"
    if abs(previous_value - new_value) <= 0.0001:
        return "unchanged"
    return "changed"


def _calibration_value_reason(
    label: str,
    previous_value: float | None,
    new_value: float | None,
    row: dict[str, Any],
    result_update_count: int,
) -> str:
    if previous_value is None:
        return (
            f"{label} initialized from {row.get('sample_count')} finished match(es) "
            f"after {result_update_count} result update row(s)."
        )
    if new_value is None:
        return f"{label} is unavailable after the latest result update."
    if abs(previous_value - new_value) <= 0.0001:
        return (
            f"{label} stayed at {new_value:.4f}; the latest result update did not materially change "
            "the shrunk tournament sample."
        )
    return (
        f"{label} moved from {previous_value:.4f} to {new_value:.4f} because {result_update_count} "
        f"result update row(s) changed the finished-match sample "
        f"(draw rate {float(row.get('draw_rate') or 0.0):.3f}, "
        f"goals/match {float(row.get('goals_per_match') or 0.0):.3f})."
    )


def _weight_recommendation_reason(
    previous_weight: float | None,
    recommended_weight: float,
    row: dict[str, Any],
    result_update_count: int,
) -> str:
    sample_count = int(row.get("sample_count") or 0)
    evidence = live_calibration_evidence(row)
    prefix = (
        f"Report-only live-calibration weight recommendation from {sample_count} finished match(es), "
        f"evidence {evidence:.3f}, after {result_update_count} result update row(s)."
    )
    if previous_weight is None:
        return f"{prefix} Initialized at {recommended_weight:.4f}."
    if abs(previous_weight - recommended_weight) <= 0.0001:
        return f"{prefix} Recommendation stayed at {recommended_weight:.4f}."
    return f"{prefix} Recommendation moved from {previous_weight:.4f} to {recommended_weight:.4f}."


def _optional_float(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def samples_from_results(results: list[ResultRecord], performance_rows: list[dict[str, Any]]) -> list[TeamSample]:
    perf_by_fixture_side = {
        (str(row.get("fixture_key")), str(row.get("side"))): row
        for row in performance_rows
    }
    samples = []
    for result in results:
        for side, team, quality_for, quality_against, weight in _result_side_samples(result):
            performance = perf_by_fixture_side.get((result.fixture_key, side))
            source = "score_only"
            if performance:
                quality_for = float(performance.get("chance_quality_for") or quality_for)
                quality_against = float(performance.get("chance_quality_against") or quality_against)
                weight = float(performance.get("match_weight") or weight)
                source = "postmatch_stats"
            samples.append(
                TeamSample(
                    team_key=team.key,
                    team_name=team.name,
                    fifa_code=team.fifa_code,
                    quality_for=quality_for,
                    quality_against=quality_against,
                    weight=weight,
                    source=source,
                )
            )
    return samples


def _result_side_samples(result: ResultRecord):
    return [
        ("home", result.home_team, float(result.score.home), float(result.score.away), 0.35),
        ("away", result.away_team, float(result.score.away), float(result.score.home), 0.35),
    ]


def _favorite_audit_rows(rows: list[dict[str, Any]]) -> list[dict[str, str]]:
    parsed = []
    for row in rows:
        try:
            prob_home = float(row.get("prob_home") or 0.0)
            prob_away = float(row.get("prob_away") or 0.0)
        except (TypeError, ValueError):
            continue
        actual = str(row.get("actual") or "")
        if ":" not in actual:
            continue
        try:
            home, away = [int(float(part)) for part in actual.split(":", 1)]
        except ValueError:
            continue
        actual_outcome = "home" if home > away else "away" if away > home else "draw"
        favorite_outcome = "home" if prob_home >= prob_away else "away"
        parsed.append({"favorite_outcome": favorite_outcome, "actual_outcome": actual_outcome})
    return parsed


def _favorite_outcome_factor(favorite_hit_rate: float | None) -> float | None:
    if favorite_hit_rate is None:
        return None
    return _clamp(1 + (favorite_hit_rate - 0.52) * 0.18, 0.92, 1.08)


def _shrink_to_prior(observed: float, sample_count: float, prior: float) -> float:
    """Beta/normal-style shrinkage of an observed rate toward a prior with an 18-match pseudo-count."""

    return (observed * sample_count + prior * LIVE_CALIBRATION_PRIOR_MATCHES) / (sample_count + LIVE_CALIBRATION_PRIOR_MATCHES)


def _clamp(value: float, lower: float, upper: float) -> float:
    return max(lower, min(upper, value))
