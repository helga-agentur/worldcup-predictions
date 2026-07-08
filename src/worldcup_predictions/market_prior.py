"""Apply tournament outright strengths as a bounded match-matrix prior."""

from __future__ import annotations

from typing import Any

from worldcup_predictions.core.contracts import (
    OutcomeProbabilities,
    Prediction,
    ScoreMatrixEntry,
    ScoreTip,
    confidence_label,
)
from worldcup_predictions.core.signals import MARKET_HDA_PROBABILITIES

OUTRIGHT_MARKET_MATRIX_WEIGHT = 0.20
OUTRIGHT_STRENGTH_POWER = 0.5
OUTRIGHT_MIN_NON_DRAW_SHARE = 0.25
OUTRIGHT_MAX_NON_DRAW_SHARE = 0.75
OUTRIGHT_MIN_EDGE = 0.01


def team_strengths_from_outrights(rows: list[dict[str, Any]]) -> dict[str, float]:
    """Return lookup strengths keyed by public team name and FIFA code."""

    strengths: dict[str, float] = {}
    for row in rows:
        probability = row.get("fair_probability") or row.get("avg_implied_probability")
        if probability in (None, ""):
            continue
        try:
            strength = max(0.01, float(probability))
        except (TypeError, ValueError):
            continue
        if row.get("team"):
            strengths[str(row["team"])] = strength
        if row.get("fifa_code"):
            strengths[str(row["fifa_code"])] = strength
    return strengths


def adjust_prediction_for_outrights(prediction: Prediction, team_strengths: dict[str, float]) -> Prediction:
    """Return a prediction whose score matrix includes the tournament-outright prior.

    Fixture-level match odds and tournament outrights come from the same
    bookmakers, so predictions that already carry a ``market_hda_probabilities``
    adjustment keep their matrix unchanged; the outright prior only stands in
    while no fixture-specific market signal is available.
    """

    if _has_fixture_market_adjustment(prediction.metadata):
        return prediction
    adjusted_matrix, adjustment = adjust_score_matrix_for_outrights(
        prediction.score_matrix,
        prediction.fixture.home_team,
        prediction.fixture.away_team,
        team_strengths,
        home_code=prediction.fixture.metadata.get("home_fifa_code"),
        away_code=prediction.fixture.metadata.get("away_fifa_code"),
    )
    if adjustment is None:
        return prediction

    probabilities = outcome_probabilities(adjusted_matrix)
    metadata = dict(prediction.metadata)
    signal_adjustments = list(metadata.get("signal_adjustments") or [])
    signal_adjustments.append(adjustment)
    metadata["signal_adjustments"] = signal_adjustments
    metadata["outright_market_adjustment"] = adjustment
    return Prediction(
        fixture=prediction.fixture,
        most_likely=most_likely_score(adjusted_matrix),
        outcome_probabilities=probabilities,
        confidence_label=confidence_label(probabilities.max_probability()),
        confidence_percent=probabilities.max_probability() * 100,
        expected_home_goals=_expected_goals(adjusted_matrix, side="home"),
        expected_away_goals=_expected_goals(adjusted_matrix, side="away"),
        source=prediction.source,
        score_matrix=adjusted_matrix,
        metadata=metadata,
    )


def adjust_score_matrix_for_outrights(
    matrix: list[ScoreMatrixEntry],
    home_team: str,
    away_team: str,
    team_strengths: dict[str, float],
    *,
    home_code: str | None = None,
    away_code: str | None = None,
    weight: float = OUTRIGHT_MARKET_MATRIX_WEIGHT,
) -> tuple[list[ScoreMatrixEntry], dict[str, Any] | None]:
    """Blend a score matrix toward an outright-market home/away prior.

    Tournament outrights are not an exact match market, so they only shift the
    non-draw home/away share and leave the matrix's draw rate and score shape
    intact. Fixture-specific markets can still dominate through the normal
    signal pipeline.
    """

    if not matrix or not team_strengths or weight <= 0:
        return matrix, None
    home_strength = _strength_for(team_strengths, home_team, home_code)
    away_strength = _strength_for(team_strengths, away_team, away_code)
    if home_strength is None or away_strength is None or home_strength <= 0 or away_strength <= 0:
        return matrix, None

    home_power = home_strength**OUTRIGHT_STRENGTH_POWER
    away_power = away_strength**OUTRIGHT_STRENGTH_POWER
    total_power = home_power + away_power
    if total_power <= 0:
        return matrix, None
    target_home_non_draw = home_power / total_power
    target_home_non_draw = max(
        OUTRIGHT_MIN_NON_DRAW_SHARE,
        min(OUTRIGHT_MAX_NON_DRAW_SHARE, target_home_non_draw),
    )
    if abs(target_home_non_draw - 0.5) < OUTRIGHT_MIN_EDGE:
        return matrix, None

    current = outcome_probabilities(matrix)
    non_draw = max(0.0, 1.0 - current.draw)
    if non_draw <= 0:
        return matrix, None
    target_home = non_draw * target_home_non_draw
    target_away = non_draw * (1.0 - target_home_non_draw)
    adjusted = reweight_outcomes(
        matrix,
        target_home=target_home,
        target_draw=current.draw,
        target_away=target_away,
        weight=weight,
    )
    adjusted_probabilities = outcome_probabilities(adjusted)
    adjustment = {
        "signal": "market_outright_strength",
        "source": "market_outrights",
        "weight": weight,
        "home_team": home_team,
        "away_team": away_team,
        "home_strength": home_strength,
        "away_strength": away_strength,
        "target_home_non_draw": target_home_non_draw,
        "target_home": target_home,
        "target_draw": current.draw,
        "target_away": target_away,
        "prob_home_before": current.home,
        "prob_draw_before": current.draw,
        "prob_away_before": current.away,
        "prob_home_after": adjusted_probabilities.home,
        "prob_draw_after": adjusted_probabilities.draw,
        "prob_away_after": adjusted_probabilities.away,
    }
    return adjusted, adjustment


def outcome_probabilities(entries: list[ScoreMatrixEntry]) -> OutcomeProbabilities:
    home = draw = away = 0.0
    for entry in entries:
        if entry.home > entry.away:
            home += entry.probability
        elif entry.home < entry.away:
            away += entry.probability
        else:
            draw += entry.probability
    return OutcomeProbabilities(home=home, draw=draw, away=away)


def most_likely_score(entries: list[ScoreMatrixEntry]) -> ScoreTip:
    if not entries:
        return ScoreTip(1, 1)
    best = max(entries, key=lambda entry: (entry.probability, -(entry.home + entry.away), -entry.home, -entry.away))
    return best.as_tip()


def reweight_outcomes(
    entries: list[ScoreMatrixEntry],
    *,
    target_home: float,
    target_draw: float,
    target_away: float,
    weight: float,
) -> list[ScoreMatrixEntry]:
    if not entries:
        return entries
    weight = max(0.0, min(1.0, weight))
    if weight <= 0:
        return entries
    target_total = target_home + target_draw + target_away
    if target_total <= 0:
        return entries

    target_home /= target_total
    target_draw /= target_total
    target_away /= target_total
    current = outcome_probabilities(entries)
    desired = {
        "home": current.home * (1 - weight) + target_home * weight,
        "draw": current.draw * (1 - weight) + target_draw * weight,
        "away": current.away * (1 - weight) + target_away * weight,
    }
    current_by_outcome = {
        "home": current.home,
        "draw": current.draw,
        "away": current.away,
    }
    adjusted = []
    for entry in entries:
        outcome = "home" if entry.home > entry.away else "away" if entry.home < entry.away else "draw"
        current_probability = current_by_outcome[outcome]
        factor = desired[outcome] / current_probability if current_probability > 0 else 1.0
        adjusted.append(ScoreMatrixEntry(entry.home, entry.away, entry.probability * factor, entry.metadata))
    return normalize_score_matrix(adjusted)


def normalize_score_matrix(entries: list[ScoreMatrixEntry]) -> list[ScoreMatrixEntry]:
    total = sum(entry.probability for entry in entries if entry.probability > 0)
    if total <= 0:
        return entries
    return [
        ScoreMatrixEntry(entry.home, entry.away, max(0.0, entry.probability) / total, entry.metadata)
        for entry in entries
    ]


def _has_fixture_market_adjustment(metadata: dict[str, Any]) -> bool:
    for adjustment in metadata.get("signal_adjustments") or []:
        if isinstance(adjustment, dict) and str(adjustment.get("signal") or "") == MARKET_HDA_PROBABILITIES:
            return True
    return False


def _strength_for(team_strengths: dict[str, float], team: str, fifa_code: str | None) -> float | None:
    if fifa_code and fifa_code in team_strengths:
        return team_strengths[fifa_code]
    return team_strengths.get(team)


def _expected_goals(matrix: list[ScoreMatrixEntry], *, side: str) -> float:
    if side == "home":
        return sum(entry.home * entry.probability for entry in matrix)
    return sum(entry.away * entry.probability for entry in matrix)
