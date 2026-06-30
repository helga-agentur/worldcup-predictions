"""Shared backtest metrics and historical World Cup fixture helpers."""

from __future__ import annotations

from collections import defaultdict
from typing import Any

from worldcup_predictions.core.contracts import OutcomeProbabilities, ScoreTip
from worldcup_predictions.model import HistoricalResult
from worldcup_predictions.tournament import FixtureRecord


# 32-team World Cups (2014/2018/2022) play 48 group matches before 16 knockout
# matches. Date-sorted, every group match precedes every knockout match, so the
# tail of each year's fixtures is the knockout phase.
KNOCKOUT_MATCH_COUNT = 16


def ranked_probability_score(probabilities: OutcomeProbabilities, actual_outcome: str) -> float:
    """Ordered Brier (RPS) for a home/draw/away forecast."""

    actual = {
        "home": (1.0, 0.0, 0.0),
        "draw": (0.0, 1.0, 0.0),
        "away": (0.0, 0.0, 1.0),
    }[actual_outcome]
    predicted_cdf = (
        probabilities.home,
        probabilities.home + probabilities.draw,
        1.0,
    )
    actual_cdf = (
        actual[0],
        actual[0] + actual[1],
        1.0,
    )
    return sum((predicted - observed) ** 2 for predicted, observed in zip(predicted_cdf, actual_cdf)) / 2


def world_cup_fixtures_by_year(
    historical_results: list[HistoricalResult],
    years: tuple[int, ...],
) -> dict[int, list[tuple[FixtureRecord, ScoreTip]]]:
    """Group finished World Cup results into per-year fixtures with phase stages.

    The first ``len - KNOCKOUT_MATCH_COUNT`` date-sorted matches of each year are
    tagged as group-stage fixtures and the remainder as knockout fixtures so that
    provider scoring applies the correct phase rules during a historical backtest.
    """

    year_set = {str(year) for year in years}
    by_year: dict[str, list[HistoricalResult]] = defaultdict(list)
    for result in historical_results:
        if result.date[:4] not in year_set:
            continue
        tournament = (result.tournament or "").casefold()
        if "fifa world cup" not in tournament or "qualification" in tournament:
            continue
        by_year[result.date[:4]].append(result)

    fixtures: dict[int, list[tuple[FixtureRecord, ScoreTip]]] = {}
    for year_label, rows in by_year.items():
        rows = sorted(rows, key=lambda item: item.date)
        group_count = max(0, len(rows) - KNOCKOUT_MATCH_COUNT)
        year_fixtures: list[tuple[FixtureRecord, ScoreTip]] = []
        for index, result in enumerate(rows):
            is_group = index < group_count
            fixture = FixtureRecord(
                event_date=f"{result.date[:10]}T00:00:00Z",
                home_team=result.home_team,
                away_team=result.away_team,
                stage="Group Stage" if is_group else "Knockout Stage",
                status="final",
                metadata={"neutral": result.neutral, "source": result.source},
            )
            year_fixtures.append((fixture, result.score))
        fixtures[int(year_label)] = year_fixtures
    return dict(sorted(fixtures.items()))


def summarize_backtest_rows(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Aggregate per-match backtest rows into mean SRF/accuracy/calibration metrics."""

    matches = len(rows)
    if not matches:
        return {
            "matches": 0,
            "points": 0.0,
            "points_per_match": 0.0,
            "expected_points_per_match": 0.0,
            "exact_hit_rate": 0.0,
            "outcome_hit_rate": 0.0,
            "rps": 0.0,
        }
    points = sum(float(row.get("points") or 0.0) for row in rows)
    expected = sum(float(row.get("expected_points") or 0.0) for row in rows)
    exact = sum(1 for row in rows if row.get("correct_exact"))
    outcome = sum(1 for row in rows if row.get("correct_outcome"))
    rps = sum(float(row.get("rps") or 0.0) for row in rows)
    return {
        "matches": matches,
        "points": points,
        "points_per_match": points / matches,
        "expected_points_per_match": expected / matches,
        "exact_hit_rate": exact / matches,
        "outcome_hit_rate": outcome / matches,
        "rps": rps / matches,
    }


def summarize_backtest_by(rows: list[dict[str, Any]], key: str) -> dict[Any, dict[str, Any]]:
    """Summarize backtest rows grouped by a row key such as ``phase`` or ``year``."""

    grouped: dict[Any, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        grouped[row.get(key)].append(row)
    return {group: summarize_backtest_rows(group_rows) for group, group_rows in sorted(grouped.items(), key=lambda item: str(item[0]))}
