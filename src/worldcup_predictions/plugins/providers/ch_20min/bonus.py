"""20min.ch bonus-question support from neutral simulations."""

from __future__ import annotations

from typing import Any

from worldcup_predictions.simulations.contracts import SimulationSummary


def evaluate_twenty_min_bonus_questions(summary: SimulationSummary) -> dict[str, Any]:
    """Return 20min.ch-ready bonus distributions from a simulation summary."""

    distributions = summary.distributions
    return {
        "provider": "20min.ch",
        "simulation": {
            "iterations": summary.iterations,
            "seed": summary.seed,
        },
        "questions": {
            "world_champion": distributions.get("champion", []),
            "top_scorer_goals": distributions.get("top_scorer_goals", []),
            "nil_nil_matches": distributions.get("nil_nil", []),
            "team_stage": distributions.get("team_stage", {}),
            "group_winners": _group_winner_distributions(
                distributions.get("group_rank", {}),
                distributions.get("team_groups", {}),
            ),
            "group_qualified": distributions.get("group_qualified", {}),
        },
        "supporting_distributions": {
            "team_goals": distributions.get("team_goals", {}),
            "group_rank": distributions.get("group_rank", {}),
        },
    }


def best_twenty_min_bonus_answers(summary: SimulationSummary) -> dict[str, Any]:
    """Return the most likely available 20min.ch bonus answers."""

    questions = evaluate_twenty_min_bonus_questions(summary)["questions"]
    return {
        "world_champion": _most_likely(questions["world_champion"]),
        "top_scorer_goals": _most_likely(questions["top_scorer_goals"]),
        "nil_nil_matches": _most_likely(questions["nil_nil_matches"]),
        "group_winners": {
            group: _most_likely(distribution)
            for group, distribution in questions["group_winners"].items()
        },
        "group_qualified": {
            team: _most_likely(distribution)
            for team, distribution in questions["group_qualified"].items()
        },
    }


def _group_winner_distributions(
    group_rank: dict[str, list[dict[str, Any]]],
    team_groups: dict[str, str],
) -> dict[str, list[dict[str, Any]]]:
    winners_by_group: dict[str, list[dict[str, Any]]] = {}
    for team, distribution in group_rank.items():
        group = team_groups.get(team)
        if not group:
            continue
        win_probability = next(
            (float(item.get("probability", 0.0)) for item in distribution if item.get("answer") == "1"),
            0.0,
        )
        if win_probability <= 0:
            continue
        winners_by_group.setdefault(group, []).append(
            {
                "answer": team,
                "probability": win_probability,
            }
        )
    for group, distribution in winners_by_group.items():
        distribution.sort(key=lambda item: (-float(item["probability"]), str(item["answer"])))
    return winners_by_group


def _most_likely(distribution: list[dict[str, Any]]) -> dict[str, Any] | None:
    if not distribution:
        return None
    return max(
        distribution,
        key=lambda item: (float(item.get("probability", 0.0)), int(item.get("count", 0)), str(item.get("answer"))),
    )
