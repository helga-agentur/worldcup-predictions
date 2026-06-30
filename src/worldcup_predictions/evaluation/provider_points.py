"""Provider-specific point tracking helpers."""

from __future__ import annotations

from typing import Any

from worldcup_predictions.core.contracts import ScoreTip
from worldcup_predictions.core.datasets import OPTIMIZED_TIPS, PROVIDER_POINTS
from worldcup_predictions.plugins.provider_optimizers.ch_srf.rules import srf_rules_for_fixture
from worldcup_predictions.plugins.provider_optimizers.common import score_outcome
from worldcup_predictions.tournament import FixtureRecord, ResultRecord, TournamentState


def build_provider_points_rows(
    storage,
    state: TournamentState,
    *,
    provider: str,
    run_id: str | None = None,
) -> list[dict[str, Any]]:
    optimized_rows = storage.read_records(OPTIMIZED_TIPS, latest_only=True)
    tips_by_fixture = {
        str(row.get("fixture_key")): row
        for row in optimized_rows
        if row.get("provider") == provider
    }
    fixtures = {fixture.key: fixture for fixture in state.fixtures}
    results = {result.fixture_key: result for result in state.results}
    rows = []
    cumulative = 0.0
    for fixture_key, result in sorted(results.items(), key=lambda item: item[1].event_date):
        fixture = fixtures.get(fixture_key)
        if not fixture:
            continue
        source_row = tips_by_fixture.get(fixture_key)
        points, tip_text, source = points_for_row(provider, fixture, result, source_row)
        cumulative += points
        rows.append(
            {
                "record_key": f"{provider}:{fixture_key}",
                "provider": provider,
                "fixture_key": fixture_key,
                "event_date": fixture.event_date,
                "home_team": fixture.home_team.name,
                "away_team": fixture.away_team.name,
                "tip": tip_text,
                "actual": result.score.as_text(),
                "points": points,
                "cumulative_points": cumulative,
                "source": source,
                "correct_exact": tip_text == result.score.as_text(),
                "correct_outcome": _tip_outcome(tip_text) == score_outcome(result.score) if tip_text else False,
            }
        )
    storage.write_records(PROVIDER_POINTS, rows, source="provider_points", run_id=run_id)
    return rows


def points_for_row(provider: str, fixture: FixtureRecord, result: ResultRecord, row: dict[str, Any] | None) -> tuple[float, str, str]:
    if not row:
        return 0.0, "", "missing_tip"
    if provider == "srf.ch":
        tip = tip_from_row(row)
        if tip is None:
            return 0.0, "", str(row.get("source") or "missing_tip")
        return srf_rules_for_fixture(fixture.to_fixture()).points_for_tip(tip, result.score), tip.as_text(), str(row.get("source") or "optimized_tip")
    if provider == "20min.ch":
        selection_type = str(row.get("selection_type") or "")
        selection = str(row.get("selection") or "")
        return twenty_min_points_for_selection(fixture, result, selection_type, selection), selection, str(row.get("source") or "optimized_tip")
    return 0.0, "", "unknown_provider"


def twenty_min_points_for_selection(fixture: FixtureRecord, result: ResultRecord, selection_type: str, selection: str) -> float:
    from worldcup_predictions.plugins.provider_optimizers.ch_20min.rules import twenty_min_points_for_fixture

    phase, points = twenty_min_points_for_fixture(fixture.to_fixture())
    if phase == "group_stage":
        if selection.casefold() == "draw":
            return float(points if result.score.home == result.score.away else 0)
        if selection == fixture.home_team.name:
            return float(points if result.score.home > result.score.away else 0)
        if selection == fixture.away_team.name:
            return float(points if result.score.away > result.score.home else 0)
        return 0.0
    winner = fixture.home_team.name if result.score.home > result.score.away else fixture.away_team.name if result.score.away > result.score.home else ""
    return float(points if selection and selection == winner else 0)


def tip_from_row(row: dict[str, Any]) -> ScoreTip | None:
    home = row.get("tip_home")
    away = row.get("tip_away")
    if home in (None, "") or away in (None, ""):
        tip = str(row.get("tip") or "")
        if ":" not in tip:
            return None
        home, away = tip.split(":", 1)
    try:
        return ScoreTip(int(float(home)), int(float(away)))
    except (TypeError, ValueError):
        return None


def _tip_outcome(value: str) -> str:
    if ":" not in str(value):
        return ""
    home, away = str(value).split(":", 1)
    try:
        return score_outcome(ScoreTip(int(float(home)), int(float(away))))
    except ValueError:
        return ""
