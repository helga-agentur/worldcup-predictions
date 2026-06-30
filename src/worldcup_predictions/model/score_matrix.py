"""Exact-score matrix math."""

from __future__ import annotations

import math

from worldcup_predictions.core.contracts import OutcomeProbabilities, ScoreMatrixEntry, ScoreTip


def poisson_probability(goals: int, expected_goals: float) -> float:
    return math.exp(-expected_goals) * (expected_goals**goals) / math.factorial(goals)


def dixon_coles_tau(home_goals: int, away_goals: int, home_lambda: float, away_lambda: float, rho: float) -> float:
    if home_goals == 0 and away_goals == 0:
        return 1 - home_lambda * away_lambda * rho
    if home_goals == 0 and away_goals == 1:
        return 1 + home_lambda * rho
    if home_goals == 1 and away_goals == 0:
        return 1 + away_lambda * rho
    if home_goals == 1 and away_goals == 1:
        return 1 - rho
    return 1.0


def build_score_matrix(
    home_lambda: float,
    away_lambda: float,
    *,
    max_goals: int = 8,
    dixon_coles_rho: float = 0.0,
    overdispersion: float = 0.0,
) -> list[ScoreMatrixEntry]:
    entries = []
    for home_goals in range(max_goals + 1):
        for away_goals in range(max_goals + 1):
            probability = poisson_probability(home_goals, home_lambda) * poisson_probability(away_goals, away_lambda)
            if dixon_coles_rho:
                probability *= max(
                    0.01,
                    dixon_coles_tau(home_goals, away_goals, home_lambda, away_lambda, dixon_coles_rho),
                )
            entries.append(ScoreMatrixEntry(home_goals, away_goals, probability))
    entries = normalize_score_matrix(entries)
    if overdispersion > 0:
        entries = apply_score_overdispersion(entries, strength=overdispersion)
    return entries


def normalize_score_matrix(entries: list[ScoreMatrixEntry]) -> list[ScoreMatrixEntry]:
    total = sum(entry.probability for entry in entries if entry.probability > 0)
    if total <= 0:
        return entries
    return [
        ScoreMatrixEntry(entry.home, entry.away, max(0.0, entry.probability) / total, entry.metadata)
        for entry in entries
    ]


def apply_score_overdispersion(entries: list[ScoreMatrixEntry], *, strength: float) -> list[ScoreMatrixEntry]:
    strength = max(0.0, min(0.20, strength))
    expected_total = sum(entry.probability * (entry.home + entry.away) for entry in entries)
    adjusted = []
    for entry in entries:
        total_goals = entry.home + entry.away
        factor = math.exp(strength * (total_goals - expected_total))
        adjusted.append(
            ScoreMatrixEntry(
                entry.home,
                entry.away,
                entry.probability * max(0.75, min(1.35, factor)),
                entry.metadata,
            )
        )
    return normalize_score_matrix(adjusted)


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


def reweight_draw(entries: list[ScoreMatrixEntry], draw_adjustment: float) -> list[ScoreMatrixEntry]:
    if not draw_adjustment:
        return entries
    adjusted = [
        ScoreMatrixEntry(
            entry.home,
            entry.away,
            entry.probability * (1 + draw_adjustment if entry.home == entry.away else 1.0),
            entry.metadata,
        )
        for entry in entries
    ]
    return normalize_score_matrix(adjusted)


def reweight_outcomes(
    entries: list[ScoreMatrixEntry],
    *,
    target_home: float,
    target_draw: float,
    target_away: float,
    weight: float,
) -> list[ScoreMatrixEntry]:
    """Blend a score matrix toward target H/D/A probabilities."""

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
