"""20min.ch scoring rules."""

from __future__ import annotations

from worldcup_predictions.core.contracts import (
    Fixture,
    OptimizedTip,
    OutcomeProbabilities,
    Prediction,
    ProviderRuleset,
)
from worldcup_predictions.plugins.provider_optimizers.common import fixture_stage, stage_contains


def twenty_min_points_for_fixture(fixture: Fixture) -> tuple[str, int]:
    """Return 20min.ch phase and points for one correct selection."""

    stage = fixture_stage(fixture)
    if fixture.group is not None or stage_contains(stage, "group", "gruppe") or not stage:
        return "group_stage", 5
    if stage_contains(stage, "round of 32", "runde der 32", "sechzehntel", "1/16", "last 32"):
        return "round_of_32", 5
    if stage_contains(stage, "round of 16", "achtel", "1/8", "last 16"):
        return "round_of_16", 10
    if stage_contains(stage, "quarter", "viertel", "1/4"):
        return "quarter_final", 20
    if stage_contains(stage, "semi", "halbfinal", "1/2"):
        return "semi_final", 25
    if stage_contains(stage, "third", "3rd", "platz 3", "place 3", "bronze"):
        return "third_place", 30
    if stage_contains(stage, "final", "endspiel"):
        return "final", 40
    return "knockout_stage", 5


def twenty_min_ruleset_for_fixture(fixture: Fixture) -> ProviderRuleset:
    phase, points = twenty_min_points_for_fixture(fixture)
    return ProviderRuleset(
        provider="20min.ch",
        version=f"2026-{phase}",
        metadata={
            "phase": phase,
            "correct_selection_points": points,
            "source": "https://tippspiel.20min.ch/details",
        },
    )


def _selection_from_probabilities(
    probabilities: OutcomeProbabilities,
    *,
    home_label: str,
    away_label: str,
) -> tuple[str, str, float]:
    choices = [
        ("home", home_label, probabilities.home),
        ("draw", "Draw", probabilities.draw),
        ("away", away_label, probabilities.away),
    ]
    return max(choices, key=lambda item: (item[2], item[0] == "draw"))


def _advancement_probabilities(prediction: Prediction) -> tuple[float, float, str]:
    metadata = prediction.metadata
    candidates = [
        metadata.get("advancement_probabilities"),
        metadata.get("advance_probabilities"),
        metadata.get("qualification_probabilities"),
    ]
    for candidate in candidates:
        if not isinstance(candidate, dict):
            continue
        home = candidate.get("home", candidate.get("home_advances"))
        away = candidate.get("away", candidate.get("away_advances"))
        if isinstance(home, (int, float)) and isinstance(away, (int, float)) and home + away > 0:
            total = float(home + away)
            return float(home) / total, float(away) / total, "prediction_metadata"

    probabilities = prediction.outcome_probabilities
    non_draw = probabilities.home + probabilities.away
    if non_draw <= 0:
        return 0.5, 0.5, "even_split_fallback"
    home_share = probabilities.home / non_draw
    away_share = probabilities.away / non_draw
    return (
        probabilities.home + probabilities.draw * home_share,
        probabilities.away + probabilities.draw * away_share,
        "derived_from_outcome_probabilities",
    )


def optimize_twenty_min_tip(prediction: Prediction, *, optimizer_id: str) -> OptimizedTip:
    """Optimize a 20min.ch selection from the neutral prediction."""

    phase, points = twenty_min_points_for_fixture(prediction.fixture)
    ruleset = twenty_min_ruleset_for_fixture(prediction.fixture)
    if phase == "group_stage":
        outcome, selection, probability = _selection_from_probabilities(
            prediction.outcome_probabilities,
            home_label=prediction.fixture.home_team,
            away_label=prediction.fixture.away_team,
        )
        alternatives = [
            {
                "selection_type": "outcome",
                "outcome": "home",
                "selection": prediction.fixture.home_team,
                "expected_points": prediction.outcome_probabilities.home * points,
                "probability": prediction.outcome_probabilities.home,
            },
            {
                "selection_type": "outcome",
                "outcome": "draw",
                "selection": "Draw",
                "expected_points": prediction.outcome_probabilities.draw * points,
                "probability": prediction.outcome_probabilities.draw,
            },
            {
                "selection_type": "outcome",
                "outcome": "away",
                "selection": prediction.fixture.away_team,
                "expected_points": prediction.outcome_probabilities.away * points,
                "probability": prediction.outcome_probabilities.away,
            },
        ]
        alternatives.sort(key=lambda item: (-float(item["expected_points"]), str(item["selection"])))
        alternatives = [item for item in alternatives if item["outcome"] != outcome]
        return OptimizedTip(
            ruleset=ruleset,
            fixture_key=prediction.fixture.key,
            tip=None,
            expected_points=probability * points,
            optimizer_id=optimizer_id,
            selection=selection,
            selection_type="outcome",
            confidence=probability,
            rationale=f"Optimized 20min group-stage outcome selection for {points} possible points.",
            alternatives=alternatives,
            metadata={
                "phase": phase,
                "outcome": outcome,
                "correct_selection_points": points,
                "selection_probability": probability,
            },
        )

    home_probability, away_probability, probability_source = _advancement_probabilities(prediction)
    if home_probability >= away_probability:
        selection = prediction.fixture.home_team
        selected_probability = home_probability
        outcome = "home_advances"
    else:
        selection = prediction.fixture.away_team
        selected_probability = away_probability
        outcome = "away_advances"

    alternatives = [
        {
            "selection_type": "advancement",
            "outcome": "home_advances",
            "selection": prediction.fixture.home_team,
            "expected_points": home_probability * points,
            "probability": home_probability,
        },
        {
            "selection_type": "advancement",
            "outcome": "away_advances",
            "selection": prediction.fixture.away_team,
            "expected_points": away_probability * points,
            "probability": away_probability,
        },
    ]
    alternatives.sort(key=lambda item: (-float(item["expected_points"]), str(item["selection"])))
    alternatives = [item for item in alternatives if item["outcome"] != outcome]
    return OptimizedTip(
        ruleset=ruleset,
        fixture_key=prediction.fixture.key,
        tip=None,
        expected_points=selected_probability * points,
        optimizer_id=optimizer_id,
        selection=selection,
        selection_type="advancement",
        confidence=selected_probability,
        rationale=f"Optimized 20min knockout advancement selection for {points} possible points.",
        alternatives=alternatives,
        metadata={
            "phase": phase,
            "outcome": outcome,
            "correct_selection_points": points,
            "selection_probability": selected_probability,
            "probability_source": probability_source,
        },
    )
