"""Postmatch learning and review-queue helpers."""

from __future__ import annotations

from typing import Any

from worldcup_predictions.core.contracts import ScoreTip
from worldcup_predictions.core.datasets import (
    MATCH_ANALYSIS_CAUSES,
    MATCH_ANALYSIS_TEAM_ADJUSTMENTS,
    POSTMATCH_LEARNING,
    POSTMATCH_REVIEW_QUEUE,
    POSTMATCH_STATS,
    POSTMATCH_TEAM_PERFORMANCE,
    PREDICTION_BACKTEST,
    TOURNAMENT_RESULTS,
)
from worldcup_predictions.plugins.providers.common import score_outcome


def build_postmatch_learning_rows(storage) -> list[dict[str, Any]]:
    audit_rows = storage.read_records(PREDICTION_BACKTEST, latest_only=True)
    performance_rows = storage.read_records(POSTMATCH_TEAM_PERFORMANCE, latest_only=True)
    performance_by_fixture = _performance_by_fixture(performance_rows)
    causes_by_fixture = _causes_by_fixture(storage.read_records(MATCH_ANALYSIS_CAUSES, latest_only=True))
    adjustments_by_fixture = _adjustments_by_fixture(storage.read_records(MATCH_ANALYSIS_TEAM_ADJUSTMENTS, latest_only=True))
    result_context_by_fixture = _result_context_by_fixture(storage.read_records(TOURNAMENT_RESULTS, latest_only=True))
    stats_by_fixture = _stats_by_fixture(storage.read_records(POSTMATCH_STATS, latest_only=True))
    rows = []
    for audit in audit_rows:
        fixture_key = str(audit.get("fixture_key") or audit.get("record_key") or "")
        if not fixture_key:
            continue
        tip = parse_score(audit.get("tip"))
        actual = parse_score(audit.get("actual"))
        perf = performance_by_fixture.get(fixture_key, {})
        causes = causes_by_fixture.get(fixture_key, [])
        adjustments = adjustments_by_fixture.get(fixture_key, [])
        result_context = result_context_by_fixture.get(fixture_key, {})
        stats = stats_by_fixture.get(fixture_key, {})
        rows.append(
            {
                "record_key": fixture_key,
                "fixture_key": fixture_key,
                "event_date": audit.get("event_date"),
                "home_team": audit.get("home_team"),
                "away_team": audit.get("away_team"),
                "tip": audit.get("tip"),
                "actual": audit.get("actual"),
                "miss_type": miss_type(tip, actual),
                "predicted_outcome": score_outcome(tip) if tip else "",
                "actual_outcome": score_outcome(actual) if actual else "",
                "actual_outcome_probability": actual_outcome_probability(audit, actual),
                "home_chance_quality": perf.get("home_chance_quality"),
                "away_chance_quality": perf.get("away_chance_quality"),
                "home_xg": stats.get("home_xg"),
                "away_xg": stats.get("away_xg"),
                "xg_winner": _xg_winner(stats.get("home_xg"), stats.get("away_xg")),
                "home_red_cards": perf.get("home_red_cards"),
                "away_red_cards": perf.get("away_red_cards"),
                "red_card_downweighted": perf.get("red_card_downweighted"),
                "cause_types": sorted({str(row.get("cause_type")) for row in causes}),
                "cause_count": len(causes),
                "team_adjustment_count": len(adjustments),
                "half_time_score": result_context.get("half_time_score"),
                "goals_text": result_context.get("goals_text"),
                "review_hint": review_hint(tip, actual, perf, causes),
            }
        )
    return rows


def _stats_by_fixture(rows: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    by_fixture: dict[str, dict[str, Any]] = {}
    for row in rows:
        fixture_key = str(row.get("fixture_key") or "")
        if not fixture_key:
            continue
        # Prefer a row that actually carries xG over one that does not.
        existing = by_fixture.get(fixture_key)
        if existing is None or (row.get("home_xg") is not None and existing.get("home_xg") is None):
            by_fixture[fixture_key] = row
    return by_fixture


def _xg_winner(home_xg: Any, away_xg: Any) -> str | None:
    if home_xg is None or away_xg is None:
        return None
    try:
        home_value = float(home_xg)
        away_value = float(away_xg)
    except (TypeError, ValueError):
        return None
    if home_value > away_value:
        return "home"
    if away_value > home_value:
        return "away"
    return "draw"


def build_postmatch_review_queue_rows(learning_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows = []
    for row in learning_rows:
        if row.get("miss_type") == "exact":
            continue
        tip = parse_score(row.get("tip"))
        actual = parse_score(row.get("actual"))
        if not tip or not actual:
            continue
        rows.append(
            {
                "record_key": row.get("fixture_key"),
                "fixture_key": row.get("fixture_key"),
                "priority": review_priority(row, tip, actual),
                "event_date": row.get("event_date"),
                "home_team": row.get("home_team"),
                "away_team": row.get("away_team"),
                "tip": row.get("tip"),
                "actual": row.get("actual"),
                "miss_type": row.get("miss_type"),
                "actual_outcome_probability": row.get("actual_outcome_probability"),
                "tip_total_goals": tip.home + tip.away,
                "actual_total_goals": actual.home + actual.away,
                "total_goal_error": abs((actual.home + actual.away) - (tip.home + tip.away)),
                "review_reason": review_reason(row, tip, actual),
                "suggested_research_query": suggested_research_query(row),
                "review_status": "open",
            }
        )
    rank = {"high": 0, "medium": 1, "watch": 2}
    rows.sort(key=lambda item: (rank.get(str(item.get("priority")), 3), str(item.get("event_date") or "")))
    return rows


def write_postmatch_outputs(storage, *, run_id: str) -> tuple[int, int, dict[str, Any]]:
    learning = build_postmatch_learning_rows(storage)
    review = build_postmatch_review_queue_rows(learning)
    learning_count = storage.write_records(POSTMATCH_LEARNING, learning, source="postmatch_learning", run_id=run_id)
    review_count = storage.write_records(POSTMATCH_REVIEW_QUEUE, review, source="postmatch_review_queue", run_id=run_id)
    summary = {
        "learning_rows": learning_count,
        "review_rows": review_count,
        "high_priority": sum(1 for row in review if row.get("priority") == "high"),
        "medium_priority": sum(1 for row in review if row.get("priority") == "medium"),
    }
    return learning_count, review_count, summary


def parse_score(value: Any) -> ScoreTip | None:
    if isinstance(value, ScoreTip):
        return value
    if not value or ":" not in str(value):
        return None
    home, away = str(value).split(":", 1)
    try:
        return ScoreTip(int(float(home)), int(float(away)))
    except ValueError:
        return None


def miss_type(tip: ScoreTip | None, actual: ScoreTip | None) -> str:
    if not tip or not actual:
        return "missing"
    if tip == actual:
        return "exact"
    if score_outcome(tip) == score_outcome(actual):
        return "scoreline"
    return "outcome"


def actual_outcome_probability(audit: dict[str, Any], actual: ScoreTip | None) -> float | None:
    if not actual:
        return None
    key = {
        "home": "prob_home",
        "draw": "prob_draw",
        "away": "prob_away",
    }[score_outcome(actual)]
    value = audit.get(key)
    return float(value) if value not in (None, "") else None


def review_hint(tip: ScoreTip | None, actual: ScoreTip | None, perf: dict[str, Any], causes: list[dict[str, Any]] | None = None) -> str:
    if not tip or not actual:
        return "missing score data"
    reasons = []
    if score_outcome(tip) != score_outcome(actual):
        reasons.append("outcome miss")
    if abs((actual.home + actual.away) - (tip.home + tip.away)) >= 2:
        reasons.append("total-goals miss")
    if perf.get("red_card_downweighted"):
        reasons.append("red-card match")
    cause_types = {str(row.get("cause_type")) for row in causes or []}
    for cause_type in sorted(cause_types):
        if cause_type:
            reasons.append(cause_type.replace("_", " "))
    return "; ".join(reasons) or "minor scoreline miss"


def review_priority(row: dict[str, Any], tip: ScoreTip, actual: ScoreTip) -> str:
    probability = float(row.get("actual_outcome_probability") or 0.0)
    total_error = abs((actual.home + actual.away) - (tip.home + tip.away))
    margin_error = abs((actual.home - actual.away) - (tip.home - tip.away))
    if row.get("miss_type") == "outcome" and (probability < 0.30 or total_error >= 2 or margin_error >= 3):
        return "high"
    if total_error >= 2 or actual.home + actual.away >= 4 or probability < 0.30:
        return "medium"
    return "watch"


def review_reason(row: dict[str, Any], tip: ScoreTip, actual: ScoreTip) -> str:
    reasons = []
    if row.get("miss_type") == "outcome":
        reasons.append("outcome miss")
    total_error = abs((actual.home + actual.away) - (tip.home + tip.away))
    if total_error >= 2:
        reasons.append(f"total goals off by {total_error}")
    margin_error = abs((actual.home - actual.away) - (tip.home - tip.away))
    if margin_error >= 3:
        reasons.append(f"margin off by {margin_error}")
    probability = float(row.get("actual_outcome_probability") or 0.0)
    if probability and probability < 0.30:
        reasons.append("low modeled probability for actual outcome")
    if row.get("red_card_downweighted"):
        reasons.append("red-card match")
    cause_types = row.get("cause_types") or []
    if cause_types:
        reasons.append("analysis causes: " + ", ".join(str(cause).replace("_", " ") for cause in cause_types[:3]))
    return "; ".join(reasons) or "scoreline miss"


def suggested_research_query(row: dict[str, Any]) -> str:
    return f"\"{row.get('home_team')}\" \"{row.get('away_team')}\" post match analysis xG lineup injuries tactics World Cup 2026"


def _performance_by_fixture(rows: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    grouped: dict[str, dict[str, Any]] = {}
    for row in rows:
        fixture_key = str(row.get("fixture_key") or "")
        side = str(row.get("side") or "")
        if not fixture_key or side not in {"home", "away"}:
            continue
        target = grouped.setdefault(fixture_key, {})
        target[f"{side}_chance_quality"] = row.get("chance_quality_for")
        target[f"{side}_red_cards"] = row.get("red_cards_for")
        if (row.get("metadata") or {}).get("red_card_downweighted"):
            target["red_card_downweighted"] = True
    return grouped


def _causes_by_fixture(rows: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    grouped: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        fixture_key = str(row.get("fixture_key") or "")
        if fixture_key:
            grouped.setdefault(fixture_key, []).append(row)
    return grouped


def _adjustments_by_fixture(rows: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    grouped: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        fixture_key = str(row.get("fixture_key") or "")
        if fixture_key:
            grouped.setdefault(fixture_key, []).append(row)
    return grouped


def _result_context_by_fixture(rows: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    context: dict[str, dict[str, Any]] = {}
    for row in rows:
        fixture_key = str(row.get("fixture_key") or "")
        metadata = dict(row.get("metadata") or {})
        if not fixture_key:
            continue
        half_home = metadata.get("half_home_score")
        half_away = metadata.get("half_away_score")
        context[fixture_key] = {
            "half_time_score": f"{half_home}:{half_away}" if half_home not in (None, "") and half_away not in (None, "") else None,
            "goals_text": metadata.get("goals_text"),
        }
    return context
