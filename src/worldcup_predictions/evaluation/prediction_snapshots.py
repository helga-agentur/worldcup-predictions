"""Prediction snapshot and comparison helpers."""

from __future__ import annotations

import datetime as dt
from typing import Any

from worldcup_predictions.core.contracts import OptimizedTip, Prediction
from worldcup_predictions.core.datasets import OPTIMIZED_TIPS, PREDICTION_COMPARISONS, PREDICTION_SNAPSHOTS, PREDICTIONS
from worldcup_predictions.storage.ledger import stable_hash


def utc_label() -> str:
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0).strftime("%Y%m%dT%H%M%SZ")


def prediction_snapshot_rows(snapshot_id: str, predictions: list[Prediction], optimized_tips: list[OptimizedTip]) -> list[dict[str, Any]]:
    tips_by_fixture = {}
    for tip in optimized_tips:
        tips_by_fixture.setdefault(tip.fixture_key, []).append(tip.to_dict())
    rows = []
    for prediction in predictions:
        payload = prediction.to_dict()
        fixture_key = prediction.fixture.key
        rows.append(
            {
                "record_key": f"{snapshot_id}:{fixture_key}",
                "snapshot_id": snapshot_id,
                "fixture_key": fixture_key,
                "event_date": prediction.fixture.event_date,
                "home_team": prediction.fixture.home_team,
                "away_team": prediction.fixture.away_team,
                "most_likely": prediction.most_likely.as_text(),
                "prob_home": prediction.outcome_probabilities.home,
                "prob_draw": prediction.outcome_probabilities.draw,
                "prob_away": prediction.outcome_probabilities.away,
                "expected_home_goals": prediction.expected_home_goals,
                "expected_away_goals": prediction.expected_away_goals,
                "confidence_percent": prediction.confidence_percent,
                "score_matrix": [entry.to_dict() for entry in prediction.score_matrix],
                "optimized_tips": tips_by_fixture.get(fixture_key, []),
                "prediction": payload,
            }
        )
    return rows


def write_prediction_snapshot(storage, snapshot_id: str, predictions: list[Prediction], optimized_tips: list[OptimizedTip], *, run_id: str) -> int:
    rows = prediction_snapshot_rows(snapshot_id, predictions, optimized_tips)
    return storage.write_records(PREDICTION_SNAPSHOTS, rows, source="prediction_snapshot", run_id=run_id)


def write_stored_prediction_snapshot(storage, snapshot_id: str, *, run_id: str | None = None) -> int:
    """Snapshot latest persisted prediction rows without running the workflow."""

    prediction_rows = storage.read_records(PREDICTIONS, latest_only=True)
    tip_rows = storage.read_records(OPTIMIZED_TIPS, latest_only=True)
    tips_by_fixture: dict[str, list[dict[str, Any]]] = {}
    for tip in tip_rows:
        tips_by_fixture.setdefault(str(tip.get("fixture_key") or ""), []).append(_strip_record(tip))
    rows = []
    for row in prediction_rows:
        fixture_key = str(row.get("fixture_key") or "")
        if not fixture_key:
            continue
        score_matrix = row.get("score_matrix") or []
        most_likely = f"{row.get('most_likely_home')}:{row.get('most_likely_away')}"
        rows.append(
            {
                "record_key": f"{snapshot_id}:{fixture_key}",
                "snapshot_id": snapshot_id,
                "fixture_key": fixture_key,
                "event_date": row.get("event_date"),
                "home_team": row.get("home_team"),
                "away_team": row.get("away_team"),
                "most_likely": most_likely,
                "prob_home": row.get("prob_home"),
                "prob_draw": row.get("prob_draw"),
                "prob_away": row.get("prob_away"),
                "expected_home_goals": row.get("expected_home_goals"),
                "expected_away_goals": row.get("expected_away_goals"),
                "confidence_percent": row.get("confidence_percent"),
                "score_matrix": score_matrix,
                "optimized_tips": tips_by_fixture.get(fixture_key, []),
                "prediction": {
                    "fixture": {
                        "key": fixture_key,
                        "stage": row.get("stage"),
                        "group": row.get("group"),
                        "matchday": row.get("matchday"),
                    },
                    "metadata": row.get("metadata") or {},
                },
            }
        )
    return storage.write_records(PREDICTION_SNAPSHOTS, rows, source="stored_prediction_snapshot", run_id=run_id)


def compare_snapshots(storage, baseline_id: str, candidate_id: str, *, run_id: str) -> list[dict[str, Any]]:
    baseline = _snapshot_by_fixture(storage, baseline_id)
    candidate = _snapshot_by_fixture(storage, candidate_id)
    rows = []
    for fixture_key in sorted(set(baseline) | set(candidate)):
        before = baseline.get(fixture_key)
        after = candidate.get(fixture_key)
        if before is None or after is None:
            rows.append(
                {
                    "record_key": stable_hash({"baseline": baseline_id, "candidate": candidate_id, "fixture": fixture_key}),
                    "comparison_id": f"{baseline_id}..{candidate_id}",
                    "fixture_key": fixture_key,
                    "status": "added" if after else "removed",
                    "metadata": {},
                }
            )
            continue
        row = compare_snapshot_rows(before, after, comparison_id=f"{baseline_id}..{candidate_id}")
        rows.append(row)
    storage.write_records(PREDICTION_COMPARISONS, rows, source="prediction_comparison", run_id=run_id)
    return rows


def compare_snapshot_rows(before: dict[str, Any], after: dict[str, Any], *, comparison_id: str) -> dict[str, Any]:
    fixture_key = str(after.get("fixture_key") or before.get("fixture_key"))
    matrix_metrics = score_matrix_metrics(before.get("score_matrix") or [], after.get("score_matrix") or [])
    return {
        "record_key": stable_hash({"comparison": comparison_id, "fixture": fixture_key}),
        "comparison_id": comparison_id,
        "fixture_key": fixture_key,
        "event_date": after.get("event_date") or before.get("event_date"),
        "match": f"{after.get('home_team') or before.get('home_team')} - {after.get('away_team') or before.get('away_team')}",
        "status": "changed",
        "most_likely_before": before.get("most_likely"),
        "most_likely_after": after.get("most_likely"),
        "most_likely_changed": before.get("most_likely") != after.get("most_likely"),
        "prob_home_delta": _delta(after, before, "prob_home"),
        "prob_draw_delta": _delta(after, before, "prob_draw"),
        "prob_away_delta": _delta(after, before, "prob_away"),
        "max_hda_probability_delta": max(abs(_delta(after, before, key)) for key in ("prob_home", "prob_draw", "prob_away")),
        "home_xg_delta": _delta(after, before, "expected_home_goals"),
        "away_xg_delta": _delta(after, before, "expected_away_goals"),
        **matrix_metrics,
        "changed_layers": changed_layers(before, after),
        "tip_changed": _provider_tip_map(before) != _provider_tip_map(after),
        "provider_tips_before": _provider_tip_map(before),
        "provider_tips_after": _provider_tip_map(after),
        "metadata": {"baseline_snapshot": before.get("snapshot_id"), "candidate_snapshot": after.get("snapshot_id")},
    }


def comparison_summary(rows: list[dict[str, Any]]) -> dict[str, Any]:
    changed = [row for row in rows if row.get("status") == "changed"]
    return {
        "rows": len(rows),
        "changed_rows": len(changed),
        "added": sum(1 for row in rows if row.get("status") == "added"),
        "removed": sum(1 for row in rows if row.get("status") == "removed"),
        "most_likely_changes": sum(1 for row in changed if row.get("most_likely_changed")),
        "tip_changes": sum(1 for row in changed if row.get("tip_changed")),
        "max_hda_probability_delta": max((abs(float(row.get("max_hda_probability_delta") or 0.0)) for row in changed), default=0.0),
        "max_matrix_total_variation": max((abs(float(row.get("matrix_total_variation") or 0.0)) for row in changed), default=0.0),
    }


def score_matrix_metrics(before: list[dict[str, Any]], after: list[dict[str, Any]]) -> dict[str, Any]:
    before_probs = {(int(row["home"]), int(row["away"])): float(row["probability"]) for row in before}
    after_probs = {(int(row["home"]), int(row["away"])): float(row["probability"]) for row in after}
    l1 = 0.0
    linf = 0.0
    changed_cells = 0
    for key in set(before_probs) | set(after_probs):
        delta = abs(after_probs.get(key, 0.0) - before_probs.get(key, 0.0))
        if delta > 1e-12:
            changed_cells += 1
        l1 += delta
        linf = max(linf, delta)
    return {
        "matrix_l1": l1,
        "matrix_total_variation": l1 / 2,
        "matrix_linf": linf,
        "matrix_changed_cells": changed_cells,
    }


def _provider_tip_map(row: dict[str, Any]) -> dict[str, str]:
    return {
        str(tip.get("provider")): str(tip.get("tip"))
        for tip in (row.get("optimized_tips") or [])
        if isinstance(tip, dict) and tip.get("provider")
    }


def changed_layers(before: dict[str, Any], after: dict[str, Any]) -> list[str]:
    before_adjustments = ((before.get("prediction") or {}).get("metadata") or {}).get("signal_adjustments") or []
    after_adjustments = ((after.get("prediction") or {}).get("metadata") or {}).get("signal_adjustments") or []
    before_signals = {str(item.get("signal")) for item in before_adjustments if isinstance(item, dict)}
    after_signals = {str(item.get("signal")) for item in after_adjustments if isinstance(item, dict)}
    return sorted(before_signals ^ after_signals)


def _snapshot_by_fixture(storage, snapshot_id: str) -> dict[str, dict[str, Any]]:
    rows = storage.read_records(PREDICTION_SNAPSHOTS)
    return {str(row.get("fixture_key")): row for row in rows if row.get("snapshot_id") == snapshot_id}


def _strip_record(row: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in row.items() if key != "_record"}


def _delta(after: dict[str, Any], before: dict[str, Any], key: str) -> float:
    return float(after.get(key) or 0.0) - float(before.get(key) or 0.0)
