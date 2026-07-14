"""Provider-specific point tracking helpers."""

from __future__ import annotations

from typing import Any

from worldcup_predictions.core.contracts import ScoreTip
from worldcup_predictions.core.datasets import OPTIMIZED_TIPS, PREDICTION_BACKTEST, PREDICTION_LEDGER, PROVIDER_POINTS
from worldcup_predictions.plugins.providers.ch_srf.rules import srf_rules_for_fixture
from worldcup_predictions.plugins.providers.common import score_outcome
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
    ledger_tips_by_fixture = _ledger_tips_by_fixture(storage, provider)
    backtest_tips_by_fixture = _backtest_tips_by_fixture(storage, provider)
    fixtures = {fixture.key: fixture for fixture in state.fixtures}
    results = {result.fixture_key: result for result in state.results}
    rows = []
    cumulative = 0.0
    for fixture_key, result in sorted(results.items(), key=lambda item: item[1].event_date):
        fixture = fixtures.get(fixture_key)
        if not fixture:
            continue
        source_row = (
            tips_by_fixture.get(fixture_key)
            or ledger_tips_by_fixture.get(fixture_key)
            or backtest_tips_by_fixture.get(fixture_key)
        )
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


def _ledger_tips_by_fixture(storage, provider: str) -> dict[str, dict[str, Any]]:
    rows = {}
    for row in _safe_read_records(storage, PREDICTION_LEDGER):
        fixture_key = str(row.get("fixture_key") or "")
        provider_tips = row.get("provider_tips") if isinstance(row.get("provider_tips"), dict) else {}
        tip = provider_tips.get(provider)
        if fixture_key and isinstance(tip, dict):
            rows[fixture_key] = {
                **tip,
                "provider": provider,
                "fixture_key": fixture_key,
                "source": tip.get("source") or row.get("prediction_context") or PREDICTION_LEDGER,
            }
    return rows


def _backtest_tips_by_fixture(storage, provider: str) -> dict[str, dict[str, Any]]:
    rows = {}
    for row in _safe_read_records(storage, PREDICTION_BACKTEST):
        fixture_key = str(row.get("fixture_key") or "")
        if not fixture_key:
            continue
        if provider == "srf.ch":
            rows[fixture_key] = {
                "provider": provider,
                "fixture_key": fixture_key,
                "tip": row.get("srf_tip") or row.get("tip"),
                "tip_home": row.get("srf_tip_home") if row.get("srf_tip_home") is not None else row.get("tip_home"),
                "tip_away": row.get("srf_tip_away") if row.get("srf_tip_away") is not None else row.get("tip_away"),
                "source": PREDICTION_BACKTEST,
            }
        elif provider == "20min.ch":
            rows[fixture_key] = {
                "provider": provider,
                "fixture_key": fixture_key,
                "selection": row.get("twenty_min_selection") or row.get("twenty_min_tip"),
                "selection_type": row.get("twenty_min_selection_type") or "outcome",
                "source": PREDICTION_BACKTEST,
            }
    return rows


def _safe_read_records(storage, dataset: str) -> list[dict[str, Any]]:
    try:
        return storage.read_records(dataset, latest_only=True)
    except Exception:  # noqa: BLE001 - provider points should degrade to missing-tip rows.
        return []


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
    from worldcup_predictions.plugins.providers.ch_20min.rules import twenty_min_points_for_fixture

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
    if not winner:
        # 20min knockout points score ADVANCEMENT: a drawn match is decided by
        # the shootout, whose winner the confirmed result carries in metadata.
        # Treating draws as "no winner" silently dropped earned points
        # (Morocco and Egypt shootout picks, 2026-07-14 audit).
        winner = _shootout_winner(fixture, result) or ""
    return float(points if selection and selection == winner else 0)


def _shootout_winner(fixture: FixtureRecord, result: ResultRecord) -> str | None:
    metadata = result.metadata or {}
    try:
        home_pens = int(metadata["home_penalty_score"])
        away_pens = int(metadata["away_penalty_score"])
    except (KeyError, TypeError, ValueError):
        home_pens = away_pens = None
    if home_pens is not None and home_pens != away_pens:
        return fixture.home_team.name if home_pens > away_pens else fixture.away_team.name
    flag = str(metadata.get("winner") or "")
    if flag == "HOME_TEAM":
        return fixture.home_team.name
    if flag == "AWAY_TEAM":
        return fixture.away_team.name
    return None


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
