"""srf.ch bonus-question evaluation from neutral simulations."""

from __future__ import annotations

from typing import Any

from worldcup_predictions.simulations.contracts import SimulationSummary


def evaluate_srf_bonus_questions(
    summary: SimulationSummary,
    *,
    swiss_team: str = "Switzerland",
) -> dict[str, Any]:
    """Return SRF bonus-question distributions from a simulation summary."""

    distributions = summary.distributions
    return {
        "provider": "srf.ch",
        "simulation": {
            "iterations": summary.iterations,
            "seed": summary.seed,
        },
        "questions": {
            "world_champion": distributions.get("champion", []),
            "switzerland_stage": distributions.get("team_stage", {}).get(swiss_team, []),
            "switzerland_goals": distributions.get("team_goals", {}).get(swiss_team, []),
            "top_scorer_goals": distributions.get("top_scorer_goals", []),
            "nil_nil_matches": distributions.get("nil_nil", []),
        },
        "supporting_distributions": {
            "all_team_stage": distributions.get("team_stage", {}),
            "all_team_goals": distributions.get("team_goals", {}),
            "group_rank": distributions.get("group_rank", {}),
            "group_qualified": distributions.get("group_qualified", {}),
        },
    }


def best_srf_bonus_answers(
    summary: SimulationSummary,
    *,
    swiss_team: str = "Switzerland",
) -> dict[str, dict[str, Any] | None]:
    """Return the most likely answer for every SRF bonus question."""

    questions = evaluate_srf_bonus_questions(summary, swiss_team=swiss_team)["questions"]
    return {question: _most_likely(distribution) for question, distribution in questions.items()}


def _most_likely(distribution: list[dict[str, Any]]) -> dict[str, Any] | None:
    if not distribution:
        return None
    return max(
        distribution,
        key=lambda item: (float(item.get("probability", 0.0)), int(item.get("count", 0)), str(item.get("answer"))),
    )
