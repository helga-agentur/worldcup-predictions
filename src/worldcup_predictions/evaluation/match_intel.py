"""Prematch review-priority helpers."""

from __future__ import annotations

from typing import Any

from worldcup_predictions.core.contracts import OptimizedTip, Prediction


def build_match_intel_rows(predictions: list[Prediction], optimized_tips: list[OptimizedTip]) -> list[dict[str, Any]]:
    tips = {(tip.fixture_key, tip.ruleset.provider): tip for tip in optimized_tips}
    rows = []
    for prediction in predictions:
        probs = prediction.outcome_probabilities
        favorite_side = "home" if probs.home >= probs.away else "away"
        underdog_prob = probs.away if favorite_side == "home" else probs.home
        draw_risk = probs.draw
        upset_risk = underdog_prob
        total_goals = (prediction.expected_home_goals or 0.0) + (prediction.expected_away_goals or 0.0)
        if total_goals and total_goals < 2.15:
            draw_risk += 0.03
        if prediction.confidence_percent < 0.50:
            upset_risk += 0.10 if prediction.confidence_percent < 0.40 else 0.03
        signal_adjustments = prediction.metadata.get("signal_adjustments") or []
        adjustment_names = sorted({str(item.get("signal")) for item in signal_adjustments if isinstance(item, dict) and item.get("signal")})
        disagreements = _hda_disagreements(signal_adjustments, favorite_side)
        if "group_draw_pressure" in adjustment_names:
            draw_risk += 0.04
        if "team_expected_goals_factor" in adjustment_names:
            upset_risk += 0.02
        # The model leans against an independent strong signal — a classic review case.
        if "market_hda_probabilities" in disagreements:
            upset_risk += 0.05
        if "expert_hda_probabilities" in disagreements:
            upset_risk += 0.03
        intel_score = min(1.0, 0.55 * draw_risk + 0.45 * upset_risk)
        rows.append(
            {
                "record_key": prediction.fixture.key,
                "fixture_key": prediction.fixture.key,
                "event_date": prediction.fixture.event_date,
                "match": f"{prediction.fixture.home_team} - {prediction.fixture.away_team}",
                "most_likely": prediction.most_likely.as_text(),
                "srf_tip": _tip_text(tips.get((prediction.fixture.key, "srf.ch"))),
                "20min_tip": _tip_text(tips.get((prediction.fixture.key, "20min.ch"))),
                "favorite": prediction.fixture.home_team if favorite_side == "home" else prediction.fixture.away_team,
                "favorite_probability": probs.home if favorite_side == "home" else probs.away,
                "draw_probability": probs.draw,
                "underdog_probability": underdog_prob,
                "expected_total_goals": total_goals,
                "confidence_percent": prediction.confidence_percent,
                "review_priority": risk_label(intel_score),
                "review_reason": review_reason(draw_risk, upset_risk, total_goals, adjustment_names, disagreements),
                "active_signals": adjustment_names,
                "market_disagreement": "market_hda_probabilities" in disagreements,
                "expert_disagreement": "expert_hda_probabilities" in disagreements,
            }
        )
    rows.sort(key=lambda row: ({"high": 0, "medium": 1, "watch": 2, "low": 3}[row["review_priority"]], row["event_date"]))
    return rows


def risk_label(value: float) -> str:
    if value >= 0.65:
        return "high"
    if value >= 0.38:
        return "medium"
    if value >= 0.18:
        return "watch"
    return "low"


def review_reason(draw_risk: float, upset_risk: float, total_goals: float, adjustment_names: list[str], disagreements: list[str] | None = None) -> str:
    disagreements = disagreements or []
    reasons = []
    if draw_risk >= 0.28:
        reasons.append("draw risk")
    if upset_risk >= 0.28:
        reasons.append("upset risk")
    if total_goals and total_goals < 2.15:
        reasons.append("low expected total")
    if "group_draw_pressure" in adjustment_names:
        reasons.append("group-state draw pressure")
    if "team_expected_goals_factor" in adjustment_names:
        reasons.append("team-specific adjustment")
    if "market_hda_probabilities" in disagreements:
        reasons.append("model/market favorite disagreement")
    if "expert_hda_probabilities" in disagreements:
        reasons.append("expert disagreement")
    return "; ".join(reasons) or "normal confidence review"


def _hda_disagreements(signal_adjustments: list[Any], model_favorite_side: str) -> list[str]:
    """Return H/D/A signal names whose implied favorite differs from the model's."""

    disagreements = []
    for item in signal_adjustments:
        if not isinstance(item, dict):
            continue
        name = str(item.get("signal") or "")
        if name not in ("market_hda_probabilities", "expert_hda_probabilities"):
            continue
        target_home = item.get("target_home")
        target_away = item.get("target_away")
        if target_home is None or target_away is None:
            continue
        signal_favorite = "home" if float(target_home) >= float(target_away) else "away"
        if signal_favorite != model_favorite_side:
            disagreements.append(name)
    return disagreements


def _tip_text(tip: OptimizedTip | None) -> str:
    return tip.display_text() if tip else ""
