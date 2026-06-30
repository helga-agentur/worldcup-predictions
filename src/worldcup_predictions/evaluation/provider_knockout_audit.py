"""Provider-specific knockout optimization audit rows."""

from __future__ import annotations

from typing import Any

from worldcup_predictions.core.datasets import PROVIDER_KNOCKOUT_AUDIT
from worldcup_predictions.storage.ledger import stable_hash


def build_provider_knockout_audit_rows(backtest_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Compare knockout provider optimizers from existing backtest rows."""

    rows: list[dict[str, Any]] = []
    for row in backtest_rows:
        if not (row.get("is_knockout") or _stage_is_knockout(str(row.get("stage") or ""))):
            continue
        tips = list(row.get("optimized_tips") or [])
        for tip in tips:
            provider = str(tip.get("provider") or "")
            if provider not in {"srf.ch", "20min.ch"}:
                continue
            rows.append(
                {
                    "record_key": stable_hash({"fixture_key": row.get("fixture_key"), "provider": provider}),
                    "fixture_key": row.get("fixture_key"),
                    "event_date": row.get("event_date"),
                    "home_team": row.get("home_team"),
                    "away_team": row.get("away_team"),
                    "stage": row.get("stage"),
                    "provider": provider,
                    "selection_type": tip.get("selection_type"),
                    "selection": tip.get("selection"),
                    "tip": tip.get("tip"),
                    "expected_points": tip.get("expected_points"),
                    "confidence": tip.get("confidence"),
                    "advancement_probabilities": row.get("advancement_probabilities") or {},
                    "srf_tip": row.get("srf_tip"),
                    "twenty_min_tip": row.get("twenty_min_tip"),
                    "optimizer_divergence": _optimizer_divergence(tips, row),
                    "rationale": tip.get("rationale"),
                    "metadata": {
                        "ruleset_key": tip.get("ruleset_key"),
                        "phase": tip.get("metadata", {}).get("phase"),
                    },
                }
            )
    return rows


def write_provider_knockout_audit(storage, backtest_rows: list[dict[str, Any]], *, run_id: str | None = None) -> list[dict[str, Any]]:
    rows = build_provider_knockout_audit_rows(backtest_rows)
    storage.write_records(PROVIDER_KNOCKOUT_AUDIT, rows, source="provider_knockout_audit", run_id=run_id)
    return rows


def _optimizer_divergence(tips: list[dict[str, Any]], row: dict[str, Any]) -> str:
    by_provider = {str(tip.get("provider") or ""): tip for tip in tips}
    srf = by_provider.get("srf.ch") or {}
    twenty = by_provider.get("20min.ch") or {}
    if not srf or not twenty:
        return "missing_provider"
    if str(twenty.get("selection_type") or "") != "advancement":
        return "not_advancement"
    srf_selection = _tip_outcome_team(srf, row)
    if not srf_selection:
        return "srf_draw_or_unknown"
    if srf_selection == twenty.get("selection"):
        return "aligned"
    return "different_exact_score_vs_advancement_choice"


def _tip_outcome_team(srf_tip: dict[str, Any], row: dict[str, Any]) -> str:
    home = row.get("home_team") or ""
    away = row.get("away_team") or ""
    tip_home = srf_tip.get("tip_home")
    tip_away = srf_tip.get("tip_away")
    try:
        home_goals = int(tip_home)
        away_goals = int(tip_away)
    except (TypeError, ValueError):
        return ""
    if home_goals > away_goals:
        return str(home)
    if away_goals > home_goals:
        return str(away)
    return ""


def _stage_is_knockout(stage: str) -> bool:
    stage = stage.casefold()
    return bool(stage and "group" not in stage and "gruppe" not in stage)
