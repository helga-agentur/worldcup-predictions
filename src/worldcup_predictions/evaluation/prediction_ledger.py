"""Static past/future prediction ledger rows."""

from __future__ import annotations

import datetime as dt
import re
from typing import Any

from worldcup_predictions.core.datasets import (
    OPTIMIZED_TIPS,
    PREDICTION_BACKTEST,
    PREDICTION_LEDGER,
    PREDICTION_SNAPSHOTS,
    PREDICTIONS,
    PUBLISHED_PREDICTION_SEED,
)
from worldcup_predictions.evaluation.published_seed import load_published_prediction_seed_rows
from worldcup_predictions.model.score_matrix import build_score_matrix, outcome_probabilities
from worldcup_predictions.storage.ledger import parse_datetime


DEFINED_TEAM_KEY_RE = re.compile(r"^[A-Z]{3}$")


def build_prediction_ledger_rows(storage) -> list[dict[str, Any]]:
    """Join current predictions and retrospective backtests into one match table."""

    backtest_rows = storage.read_records(PREDICTION_BACKTEST, latest_only=True)
    prediction_rows = storage.read_records(PREDICTIONS, latest_only=True)
    optimized_tips = storage.read_records(OPTIMIZED_TIPS, latest_only=True)
    frozen_snapshots = _frozen_snapshot_rows(storage)
    seed_rows = _published_seed_rows(storage)

    tips_by_fixture: dict[str, list[dict[str, Any]]] = {}
    for tip in optimized_tips:
        tips_by_fixture.setdefault(str(tip.get("fixture_key") or ""), []).append(_strip_record(tip))

    rows = []
    past_fixture_keys = set()
    for row in sorted(backtest_rows, key=lambda item: str(item.get("event_date") or "")):
        fixture_key = str(row.get("fixture_key") or "")
        if not fixture_key or not _has_defined_teams(fixture_key):
            continue
        past_fixture_keys.add(fixture_key)
        frozen_snapshot = frozen_snapshots.get(fixture_key)
        seed_row = seed_rows.get(fixture_key)
        if frozen_snapshot is not None:
            rows.append(_past_row_from_snapshot(row, frozen_snapshot))
        elif seed_row is not None:
            rows.append(_past_row_from_seed(row, seed_row))
        else:
            rows.append(_past_row(row))

    for row in sorted(prediction_rows, key=lambda item: str(item.get("event_date") or "")):
        fixture_key = str(row.get("fixture_key") or "")
        if not fixture_key or fixture_key in past_fixture_keys or not _has_defined_teams(fixture_key):
            continue
        rows.append(_future_row(row, tips_by_fixture.get(fixture_key, [])))

    return rows


def write_prediction_ledger(storage, *, run_id: str | None = None) -> int:
    rows = build_prediction_ledger_rows(storage)
    return storage.write_records(PREDICTION_LEDGER, rows, source="prediction_ledger", run_id=run_id)


def _past_row(row: dict[str, Any]) -> dict[str, Any]:
    tips = [_strip_record(item) for item in (row.get("optimized_tips") or []) if isinstance(item, dict)]
    provider_tips = _provider_tip_map(tips)
    most_likely_home, most_likely_away = _split_score(row.get("most_likely"))
    return {
        "record_key": str(row.get("fixture_key") or ""),
        "fixture_key": row.get("fixture_key"),
        "event_date": row.get("event_date"),
        "home_team": row.get("home_team"),
        "away_team": row.get("away_team"),
        "status": "past",
        "prediction_context": "retrospective_current_model_before_kickoff",
        "actual_score": row.get("actual"),
        "actual_home": row.get("actual_home"),
        "actual_away": row.get("actual_away"),
        "predicted_home_goals": row.get("expected_home_goals"),
        "predicted_away_goals": row.get("expected_away_goals"),
        "most_likely_score": row.get("most_likely"),
        "most_likely_home": most_likely_home,
        "most_likely_away": most_likely_away,
        "prob_home": row.get("prob_home"),
        "prob_draw": row.get("prob_draw"),
        "prob_away": row.get("prob_away"),
        "confidence_label": row.get("confidence_label"),
        "confidence_percent": row.get("confidence_percent"),
        "score_matrix": row.get("score_matrix") or [],
        "provider_tips": provider_tips,
        "srf_tip": _tip_text(provider_tips.get("srf.ch")),
        "srf_expected_points": _expected_points(provider_tips.get("srf.ch")),
        "twenty_min_tip": _tip_text(provider_tips.get("20min.ch")),
        "twenty_min_expected_points": _expected_points(provider_tips.get("20min.ch")),
        "metadata": {
            "source_dataset": PREDICTION_BACKTEST,
            "srf_points": row.get("points"),
            "srf_correct_exact": row.get("correct_exact"),
            "srf_correct_outcome": row.get("correct_outcome"),
            "rps": row.get("rps"),
            "phase": row.get("phase"),
            "ruleset": row.get("ruleset"),
        },
    }


def _future_row(row: dict[str, Any], tips: list[dict[str, Any]]) -> dict[str, Any]:
    provider_tips = _provider_tip_map(tips)
    most_likely = f"{row.get('most_likely_home')}:{row.get('most_likely_away')}"
    return {
        "record_key": str(row.get("fixture_key") or ""),
        "fixture_key": row.get("fixture_key"),
        "event_date": row.get("event_date"),
        "home_team": row.get("home_team"),
        "away_team": row.get("away_team"),
        "status": "future",
        "prediction_context": "latest_live_prediction",
        "actual_score": None,
        "actual_home": None,
        "actual_away": None,
        "predicted_home_goals": row.get("expected_home_goals"),
        "predicted_away_goals": row.get("expected_away_goals"),
        "most_likely_score": most_likely,
        "most_likely_home": row.get("most_likely_home"),
        "most_likely_away": row.get("most_likely_away"),
        "prob_home": row.get("prob_home"),
        "prob_draw": row.get("prob_draw"),
        "prob_away": row.get("prob_away"),
        "confidence_label": row.get("confidence_label"),
        "confidence_percent": row.get("confidence_percent"),
        "score_matrix": row.get("score_matrix") or [],
        "provider_tips": provider_tips,
        "srf_tip": _tip_text(provider_tips.get("srf.ch")),
        "srf_expected_points": _expected_points(provider_tips.get("srf.ch")),
        "twenty_min_tip": _tip_text(provider_tips.get("20min.ch")),
        "twenty_min_expected_points": _expected_points(provider_tips.get("20min.ch")),
        "metadata": {
            "source_dataset": PREDICTIONS,
            "stage": row.get("stage"),
            "group": row.get("group"),
            "matchday": row.get("matchday"),
            "prediction_source": row.get("prediction_source"),
            "prediction_metadata": row.get("metadata") or {},
        },
    }


def _past_row_from_snapshot(backtest_row: dict[str, Any], snapshot_row: dict[str, Any]) -> dict[str, Any]:
    tips = [_strip_record(item) for item in (snapshot_row.get("optimized_tips") or []) if isinstance(item, dict)]
    provider_tips = _provider_tip_map(tips)
    most_likely_home, most_likely_away = _split_score(snapshot_row.get("most_likely"))
    srf_tip = _tip_text(provider_tips.get("srf.ch"))
    return {
        "record_key": str(backtest_row.get("fixture_key") or snapshot_row.get("fixture_key") or ""),
        "fixture_key": backtest_row.get("fixture_key") or snapshot_row.get("fixture_key"),
        "event_date": backtest_row.get("event_date") or snapshot_row.get("event_date"),
        "home_team": backtest_row.get("home_team") or snapshot_row.get("home_team"),
        "away_team": backtest_row.get("away_team") or snapshot_row.get("away_team"),
        "status": "past",
        "prediction_context": "frozen_prediction_snapshot_before_kickoff",
        "actual_score": backtest_row.get("actual"),
        "actual_home": backtest_row.get("actual_home"),
        "actual_away": backtest_row.get("actual_away"),
        "predicted_home_goals": snapshot_row.get("expected_home_goals"),
        "predicted_away_goals": snapshot_row.get("expected_away_goals"),
        "most_likely_score": snapshot_row.get("most_likely"),
        "most_likely_home": most_likely_home,
        "most_likely_away": most_likely_away,
        "prob_home": snapshot_row.get("prob_home"),
        "prob_draw": snapshot_row.get("prob_draw"),
        "prob_away": snapshot_row.get("prob_away"),
        "confidence_label": backtest_row.get("confidence_label"),
        "confidence_percent": snapshot_row.get("confidence_percent"),
        "score_matrix": snapshot_row.get("score_matrix") or [],
        "provider_tips": provider_tips,
        "srf_tip": srf_tip,
        "srf_expected_points": _expected_points(provider_tips.get("srf.ch")),
        "twenty_min_tip": _tip_text(provider_tips.get("20min.ch")),
        "twenty_min_expected_points": _expected_points(provider_tips.get("20min.ch")),
        "metadata": {
            "source_dataset": PREDICTION_SNAPSHOTS,
            "snapshot_id": snapshot_row.get("snapshot_id"),
            "snapshot_observed_at_utc": _snapshot_observed_at(snapshot_row),
            "srf_points": _score_srf_tip(srf_tip, backtest_row),
            "phase": backtest_row.get("phase"),
            "ruleset": backtest_row.get("ruleset"),
            "current_backtest_metadata": _backtest_metadata(backtest_row),
        },
    }


def _past_row_from_seed(backtest_row: dict[str, Any], seed_row: dict[str, Any]) -> dict[str, Any]:
    backtest_tips = [_strip_record(item) for item in (backtest_row.get("optimized_tips") or []) if isinstance(item, dict)]
    provider_tips = _provider_tip_map(backtest_tips)
    srf_tip = str(seed_row.get("srf_tip") or "")
    if srf_tip:
        provider_tips["srf.ch"] = {
            "provider": "srf.ch",
            "fixture_key": seed_row.get("fixture_key"),
            "tip": srf_tip,
            "tip_home": seed_row.get("srf_tip_home"),
            "tip_away": seed_row.get("srf_tip_away"),
            "expected_points": seed_row.get("srf_expected_points"),
            "source": PUBLISHED_PREDICTION_SEED,
        }
    twenty_min_tip = _twenty_min_tip_from_seed_score(seed_row, srf_tip=srf_tip)
    if twenty_min_tip:
        provider_tips["20min.ch"] = {
            "provider": "20min.ch",
            "fixture_key": seed_row.get("fixture_key"),
            "tip": twenty_min_tip,
            "selection": twenty_min_tip,
            "selection_type": "outcome",
            "source": PUBLISHED_PREDICTION_SEED,
            "rationale": "Derived from the same archived exact-score tip used for SRF accounting.",
        }
    seed_prediction = _seed_prediction_fields(seed_row, srf_tip=srf_tip)
    return {
        "record_key": str(backtest_row.get("fixture_key") or seed_row.get("fixture_key") or ""),
        "fixture_key": backtest_row.get("fixture_key") or seed_row.get("fixture_key"),
        "event_date": backtest_row.get("event_date") or seed_row.get("event_date"),
        "home_team": backtest_row.get("home_team") or seed_row.get("home_team"),
        "away_team": backtest_row.get("away_team") or seed_row.get("away_team"),
        "status": "past",
        "prediction_context": "archived_pre_refactor_prediction",
        "actual_score": backtest_row.get("actual") or seed_row.get("actual_score"),
        "actual_home": backtest_row.get("actual_home") if backtest_row.get("actual_home") is not None else seed_row.get("actual_home"),
        "actual_away": backtest_row.get("actual_away") if backtest_row.get("actual_away") is not None else seed_row.get("actual_away"),
        "predicted_home_goals": seed_prediction["predicted_home_goals"],
        "predicted_away_goals": seed_prediction["predicted_away_goals"],
        "most_likely_score": seed_prediction["most_likely_score"],
        "most_likely_home": seed_prediction["most_likely_home"],
        "most_likely_away": seed_prediction["most_likely_away"],
        "prob_home": seed_prediction["prob_home"],
        "prob_draw": seed_prediction["prob_draw"],
        "prob_away": seed_prediction["prob_away"],
        "confidence_label": backtest_row.get("confidence_label"),
        "confidence_percent": backtest_row.get("confidence_percent"),
        "score_matrix": seed_prediction["score_matrix"],
        "provider_tips": provider_tips,
        "srf_tip": srf_tip,
        "srf_expected_points": seed_row.get("srf_expected_points"),
        "twenty_min_tip": twenty_min_tip,
        "twenty_min_expected_points": _expected_points(provider_tips.get("20min.ch")),
        "metadata": {
            "source_dataset": PUBLISHED_PREDICTION_SEED,
            "seed_id": (seed_row.get("metadata") or {}).get("seed_id") if isinstance(seed_row.get("metadata"), dict) else None,
            "source_snapshot": seed_row.get("source_snapshot"),
            "snapshot_time_utc": seed_row.get("snapshot_time_utc"),
            "twenty_min_source": "srf_tip_outcome_from_published_prediction_seed",
            "srf_points": _score_srf_tip(srf_tip, backtest_row),
            "seed_srf_points": seed_row.get("srf_points"),
            "phase": backtest_row.get("phase") or seed_row.get("phase"),
            "ruleset": backtest_row.get("ruleset"),
            "seed_metadata": seed_row.get("metadata") or {},
            "reconstructed_prediction": seed_prediction["reconstructed"],
            "reconstruction_method": seed_prediction["reconstruction_method"],
            "current_backtest_metadata": _backtest_metadata(backtest_row),
        },
    }


def _seed_prediction_fields(seed_row: dict[str, Any], *, srf_tip: str) -> dict[str, Any]:
    score_matrix = _seed_score_matrix_entries(seed_row.get("score_matrix"))
    most_likely_score = str(seed_row.get("most_likely_score") or srf_tip or "")
    most_likely_home, most_likely_away = _split_score(most_likely_score)
    reconstructed = False
    reconstruction_method = ""

    if not score_matrix and most_likely_home is not None and most_likely_away is not None:
        reconstructed = True
        reconstruction_method = "poisson_centered_on_archived_exact_tip"
        home_lambda = _archived_tip_lambda(most_likely_home)
        away_lambda = _archived_tip_lambda(most_likely_away)
        if most_likely_home > most_likely_away:
            home_lambda += 0.10
            away_lambda = max(0.35, away_lambda - 0.10)
        elif most_likely_away > most_likely_home:
            away_lambda += 0.10
            home_lambda = max(0.35, home_lambda - 0.10)
        matrix_entries = build_score_matrix(home_lambda, away_lambda, max_goals=8, dixon_coles_rho=-0.04)
        score_matrix = [
            {
                "home": entry.home,
                "away": entry.away,
                "probability": entry.probability,
                "metadata": {
                    "source": PUBLISHED_PREDICTION_SEED,
                    "reconstructed": True,
                    "method": reconstruction_method,
                },
            }
            for entry in matrix_entries
        ]
        predicted_home = home_lambda
        predicted_away = away_lambda
    else:
        predicted_home = seed_row.get("predicted_home_goals")
        predicted_away = seed_row.get("predicted_away_goals")

    if predicted_home in (None, "") and most_likely_home is not None:
        predicted_home = _archived_tip_lambda(most_likely_home)
        reconstructed = True
        reconstruction_method = reconstruction_method or "expected_goals_centered_on_archived_exact_tip"
    if predicted_away in (None, "") and most_likely_away is not None:
        predicted_away = _archived_tip_lambda(most_likely_away)
        reconstructed = True
        reconstruction_method = reconstruction_method or "expected_goals_centered_on_archived_exact_tip"

    probabilities = _seed_outcome_probabilities(seed_row, score_matrix)
    return {
        "predicted_home_goals": predicted_home,
        "predicted_away_goals": predicted_away,
        "most_likely_score": most_likely_score,
        "most_likely_home": most_likely_home,
        "most_likely_away": most_likely_away,
        "prob_home": probabilities["home"],
        "prob_draw": probabilities["draw"],
        "prob_away": probabilities["away"],
        "score_matrix": score_matrix,
        "reconstructed": reconstructed,
        "reconstruction_method": reconstruction_method,
    }


def _archived_tip_lambda(goals: int) -> float:
    if goals <= 0:
        return 0.55
    return float(goals) + 0.25


def _seed_outcome_probabilities(seed_row: dict[str, Any], score_matrix: list[dict[str, Any]]) -> dict[str, float | Any]:
    if all(seed_row.get(key) not in (None, "") for key in ("prob_home", "prob_draw", "prob_away")):
        return {
            "home": seed_row.get("prob_home"),
            "draw": seed_row.get("prob_draw"),
            "away": seed_row.get("prob_away"),
        }
    if not score_matrix:
        return {"home": seed_row.get("prob_home"), "draw": seed_row.get("prob_draw"), "away": seed_row.get("prob_away")}
    probabilities = outcome_probabilities(
        [
            _score_matrix_entry_from_dict(entry)
            for entry in score_matrix
            if isinstance(entry, dict)
        ]
    )
    return {"home": probabilities.home, "draw": probabilities.draw, "away": probabilities.away}


def _score_matrix_entry_from_dict(entry: dict[str, Any]):
    from worldcup_predictions.core.contracts import ScoreMatrixEntry

    return ScoreMatrixEntry(
        home=int(entry.get("home") or 0),
        away=int(entry.get("away") or 0),
        probability=float(entry.get("probability") or 0.0),
        metadata=entry.get("metadata") if isinstance(entry.get("metadata"), dict) else {},
    )


def _twenty_min_tip_from_seed_score(seed_row: dict[str, Any], *, srf_tip: str) -> str:
    score_source = srf_tip or str(seed_row.get("most_likely_score") or "")
    home, away = _split_score(score_source)
    if home is None or away is None:
        return ""
    if home > away:
        return str(seed_row.get("home_team") or "")
    if away > home:
        return str(seed_row.get("away_team") or "")
    return "Draw"


def _provider_tip_map(tips: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    return {
        str(tip.get("provider")): tip
        for tip in tips
        if tip.get("provider")
    }


def _has_defined_teams(fixture_key: str) -> bool:
    parts = fixture_key.split("|")
    if len(parts) != 3:
        return False
    return bool(DEFINED_TEAM_KEY_RE.match(parts[1]) and DEFINED_TEAM_KEY_RE.match(parts[2]))


def _split_score(value: Any) -> tuple[int | None, int | None]:
    text = str(value or "")
    if ":" not in text:
        return None, None
    home, away = text.split(":", 1)
    try:
        return int(float(home)), int(float(away))
    except ValueError:
        return None, None


def _tip_text(tip: dict[str, Any] | None) -> str:
    if not tip:
        return ""
    return str(tip.get("tip") or tip.get("selection") or "")


def _expected_points(tip: dict[str, Any] | None) -> float | None:
    if not tip or tip.get("expected_points") is None:
        return None
    return float(tip["expected_points"])


def _strip_record(row: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in row.items() if key != "_record"}


def _frozen_snapshot_rows(storage) -> dict[str, dict[str, Any]]:
    try:
        rows = storage.read_records(PREDICTION_SNAPSHOTS, latest_only=False)
    except Exception:
        return {}
    best: dict[str, dict[str, Any]] = {}
    best_time: dict[str, dt.datetime] = {}
    for row in rows:
        fixture_key = str(row.get("fixture_key") or "")
        event_at = parse_datetime(str(row.get("event_date") or ""))
        observed_at = _snapshot_observed_datetime(row)
        if not fixture_key or event_at is None or observed_at is None:
            continue
        if observed_at > event_at:
            continue
        if fixture_key not in best_time or observed_at > best_time[fixture_key]:
            best[fixture_key] = _strip_record(row)
            best_time[fixture_key] = observed_at
    return best


def _published_seed_rows(storage) -> dict[str, dict[str, Any]]:
    rows = load_published_prediction_seed_rows()
    try:
        stored_rows = storage.read_records(PUBLISHED_PREDICTION_SEED, latest_only=True)
    except Exception:
        stored_rows = []
    rows.extend(_strip_record(row) for row in stored_rows)
    return {
        str(row.get("fixture_key")): _strip_record(row)
        for row in rows
        if row.get("fixture_key")
    }


def _snapshot_observed_datetime(row: dict[str, Any]) -> dt.datetime | None:
    observed = _snapshot_observed_at(row)
    parsed = parse_datetime(observed)
    if parsed is not None:
        return parsed
    snapshot_id = str(row.get("snapshot_id") or "")
    for prefix in ("scheduled_", "snapshot_"):
        if snapshot_id.startswith(prefix):
            label = snapshot_id.removeprefix(prefix)
            try:
                return dt.datetime.strptime(label, "%Y%m%dT%H%M%SZ").replace(tzinfo=dt.timezone.utc)
            except ValueError:
                return None
    return None


def _snapshot_observed_at(row: dict[str, Any]) -> str:
    record = row.get("_record") if isinstance(row.get("_record"), dict) else {}
    return str(row.get("snapshot_time_utc") or record.get("observed_at_utc") or "")


def _seed_score_matrix_entries(score_matrix: Any) -> list[dict[str, Any]]:
    if isinstance(score_matrix, list):
        return score_matrix
    if not isinstance(score_matrix, dict):
        return []
    probabilities = score_matrix.get("probabilities")
    home_axis = score_matrix.get("home_goals_axis")
    away_axis = score_matrix.get("away_goals_axis")
    if not isinstance(probabilities, list) or not isinstance(home_axis, list) or not isinstance(away_axis, list):
        return []
    entries = []
    for home_index, row in enumerate(probabilities):
        if not isinstance(row, list) or home_index >= len(home_axis):
            continue
        for away_index, probability in enumerate(row):
            if away_index >= len(away_axis):
                continue
            entries.append(
                {
                    "home": home_axis[home_index],
                    "away": away_axis[away_index],
                    "probability": probability,
                    "metadata": {"source": PUBLISHED_PREDICTION_SEED},
                }
            )
    return entries


def _score_srf_tip(tip: str, backtest_row: dict[str, Any]) -> float | None:
    home, away = _split_score(tip)
    actual_home = backtest_row.get("actual_home")
    actual_away = backtest_row.get("actual_away")
    if home is None or away is None or actual_home in (None, "") or actual_away in (None, ""):
        return None
    try:
        actual_home_int = int(float(actual_home))
        actual_away_int = int(float(actual_away))
    except (TypeError, ValueError):
        return None
    phase = str(backtest_row.get("phase") or "")
    knockout = "knockout" in phase
    outcome_points = 10 if knockout else 5
    home_points = 2 if knockout else 1
    away_points = 2 if knockout else 1
    goal_diff_points = 6 if knockout else 3
    points = 0.0
    if _outcome(home, away) == _outcome(actual_home_int, actual_away_int):
        points += outcome_points
        if home - away == actual_home_int - actual_away_int:
            points += goal_diff_points
    if home == actual_home_int:
        points += home_points
    if away == actual_away_int:
        points += away_points
    return points


def _outcome(home: int, away: int) -> str:
    if home > away:
        return "home"
    if home < away:
        return "away"
    return "draw"


def _backtest_metadata(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "source_dataset": PREDICTION_BACKTEST,
        "srf_points": row.get("points"),
        "srf_correct_exact": row.get("correct_exact"),
        "srf_correct_outcome": row.get("correct_outcome"),
        "rps": row.get("rps"),
    }
