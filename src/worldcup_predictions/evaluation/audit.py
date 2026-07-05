"""Audit frozen pre-match prediction snapshots against confirmed results."""

from __future__ import annotations

import datetime as dt
from typing import Any

from worldcup_predictions.core.contracts import ScoreTip, parse_utc_datetime
from worldcup_predictions.core.datasets import PREDICTION_AUDIT
from worldcup_predictions.evaluation.provider_points import points_for_row
from worldcup_predictions.plugins.providers.common import score_outcome
from worldcup_predictions.tournament import ResultRecord, TournamentState


TIP_CLOSE_BUFFER = dt.timedelta(minutes=5)


def build_prediction_audit_rows(storage, state: TournamentState, *, run_id: str | None = None) -> list[dict[str, Any]]:
    """Score the latest stored prediction snapshot that existed before each kickoff."""

    snapshot_rows = storage.read_records("prediction_snapshots")
    snapshots_by_fixture: dict[str, list[dict[str, Any]]] = {}
    for row in snapshot_rows:
        fixture_key = str(row.get("fixture_key") or "")
        if fixture_key:
            snapshots_by_fixture.setdefault(fixture_key, []).append(row)

    fixtures = {fixture.key: fixture for fixture in state.fixtures}
    rows: list[dict[str, Any]] = []
    for result in sorted(state.results, key=lambda item: item.event_date):
        fixture = fixtures.get(result.fixture_key)
        if fixture is None:
            continue
        snapshot = latest_pre_kickoff_snapshot(snapshots_by_fixture.get(result.fixture_key, []), result)
        if snapshot is None:
            rows.append(_missing_snapshot_row(fixture, result))
            continue
        rows.extend(_audit_snapshot(fixture, result, snapshot))

    storage.write_records(PREDICTION_AUDIT, rows, source="prediction_audit", run_id=run_id)
    return rows


def latest_pre_kickoff_snapshot(rows: list[dict[str, Any]], result: ResultRecord) -> dict[str, Any] | None:
    if not rows:
        return None
    kickoff = parse_utc_datetime(result.event_date)
    eligible: list[tuple[str, dict[str, Any]]] = []
    fallback: list[tuple[str, dict[str, Any]]] = []
    for row in rows:
        observed_at = str(row.get("_record", {}).get("observed_at_utc") or "")
        observed = parse_utc_datetime(observed_at)
        sort_key = observed_at or str(row.get("snapshot_id") or "")
        fallback.append((sort_key, row))
        if kickoff is not None and observed is not None and observed <= kickoff - TIP_CLOSE_BUFFER:
            eligible.append((sort_key, row))
    if eligible:
        return sorted(eligible, key=lambda item: item[0])[-1][1]
    return sorted(fallback, key=lambda item: item[0])[-1][1]


def _audit_snapshot(fixture, result: ResultRecord, snapshot: dict[str, Any]) -> list[dict[str, Any]]:
    snapshot_id = _snapshot_id(snapshot)
    optimized_tips = list(snapshot.get("optimized_tips") or [])
    if not optimized_tips:
        optimized_tips = [_fallback_srf_tip(snapshot)]
    rows = []
    for tip_row in optimized_tips:
        provider = str(tip_row.get("provider") or "")
        if provider not in {"srf.ch", "20min.ch"}:
            continue
        points, tip_text, source = points_for_row(provider, fixture, result, tip_row)
        rows.append(
            {
                "record_key": f"{snapshot_id}:{provider}:{fixture.key}",
                "snapshot_id": snapshot_id,
                "provider": provider,
                "fixture_key": fixture.key,
                "event_date": fixture.event_date,
                "home_team": fixture.home_team.name,
                "away_team": fixture.away_team.name,
                "tip": tip_text,
                "actual": result.score.as_text(),
                "points": points,
                "source": source,
                "snapshot_observed_at": snapshot.get("_record", {}).get("observed_at_utc"),
                "correct_exact": tip_text == result.score.as_text(),
                "correct_outcome": _tip_outcome(tip_text) == score_outcome(result.score) if tip_text else False,
                "metadata": {
                    "most_likely": snapshot.get("most_likely"),
                    "prob_home": snapshot.get("prob_home"),
                    "prob_draw": snapshot.get("prob_draw"),
                    "prob_away": snapshot.get("prob_away"),
                    "selection_type": tip_row.get("selection_type"),
                },
            }
        )
    return rows


def _missing_snapshot_row(fixture, result: ResultRecord) -> dict[str, Any]:
    snapshot_id = f"missing_snapshot:{fixture.key}"
    return {
        "record_key": f"missing:snapshot:{fixture.key}",
        "snapshot_id": snapshot_id,
        "provider": "",
        "fixture_key": fixture.key,
        "event_date": fixture.event_date,
        "home_team": fixture.home_team.name,
        "away_team": fixture.away_team.name,
        "tip": "",
        "actual": result.score.as_text(),
        "points": 0.0,
        "source": "missing_snapshot",
        "snapshot_observed_at": "",
        "correct_exact": False,
        "correct_outcome": False,
        "metadata": {},
    }


def _snapshot_id(snapshot: dict[str, Any]) -> str:
    return str(
        snapshot.get("snapshot_id")
        or snapshot.get("_record", {}).get("record_key")
        or "unknown_snapshot"
    )


def _fallback_srf_tip(snapshot: dict[str, Any]) -> dict[str, Any]:
    tip = parse_score(snapshot.get("most_likely"))
    return {
        "provider": "srf.ch",
        "selection_type": "exact_score",
        "tip": tip.as_text() if tip else "",
        "tip_home": tip.home if tip else None,
        "tip_away": tip.away if tip else None,
        "source": "snapshot_most_likely",
    }


def parse_score(value: Any) -> ScoreTip | None:
    text = str(value or "")
    if ":" not in text:
        return None
    home, away = text.split(":", 1)
    try:
        return ScoreTip(int(float(home)), int(float(away)))
    except ValueError:
        return None


def _tip_outcome(value: str) -> str:
    tip = parse_score(value)
    return score_outcome(tip) if tip else ""
