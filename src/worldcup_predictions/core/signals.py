"""Canonical model signal names and contracts."""

from __future__ import annotations

from worldcup_predictions.core.metadata import SignalContract


GROUP_DRAW_PRESSURE = "group_draw_pressure"
GROUP_MOTIVATION = "group_motivation"
GROUP_ROTATION_RISK = "group_rotation_risk"
GROUP_ELIMINATION_RISK = "group_elimination_risk"
MARKET_HDA_PROBABILITIES = "market_hda_probabilities"
EXPERT_HDA_PROBABILITIES = "expert_hda_probabilities"
ML_HDA_PROBABILITIES = "ml_hda_probabilities"
MARKET_TOTAL_GOALS = "market_total_goals"
MARKET_GOAL_DIFF = "market_goal_diff"
TOTAL_GOALS_FACTOR = "total_goals_factor"
TEAM_EXPECTED_GOALS_FACTOR = "team_expected_goals_factor"
LIVE_DRAW_ADJUSTMENT = "live_draw_adjustment"
LIVE_SCORE_TAIL_FACTOR = "live_score_tail_factor"
LIVE_FAVORITE_OUTCOME_FACTOR = "live_favorite_outcome_factor"


SIGNAL_CONTRACTS: dict[str, SignalContract] = {
    GROUP_DRAW_PRESSURE: SignalContract(
        GROUP_DRAW_PRESSURE,
        "float",
        "Draw probability multiplier for group-state incentives.",
    ),
    GROUP_MOTIVATION: SignalContract(
        GROUP_MOTIVATION,
        "float",
        "Informational group-stage motivation score.",
    ),
    GROUP_ROTATION_RISK: SignalContract(
        GROUP_ROTATION_RISK,
        "bool",
        "Informational group-stage rotation risk signal.",
    ),
    GROUP_ELIMINATION_RISK: SignalContract(
        GROUP_ELIMINATION_RISK,
        "bool",
        "Informational group-stage elimination risk signal.",
    ),
    MARKET_HDA_PROBABILITIES: SignalContract(
        MARKET_HDA_PROBABILITIES,
        "float",
        "No-vig public market home/draw/away probabilities.",
        ("prob_home", "prob_draw", "prob_away"),
    ),
    EXPERT_HDA_PROBABILITIES: SignalContract(
        EXPERT_HDA_PROBABILITIES,
        "float",
        "Expert consensus home/draw/away probabilities.",
        ("prob_home", "prob_draw", "prob_away", "expert_count"),
    ),
    ML_HDA_PROBABILITIES: SignalContract(
        ML_HDA_PROBABILITIES,
        "float",
        "Optional machine-learning outcome probabilities.",
        ("prob_home", "prob_draw", "prob_away", "model_id"),
    ),
    MARKET_TOTAL_GOALS: SignalContract(
        MARKET_TOTAL_GOALS,
        "float",
        "Public totals market expected total-goals line.",
    ),
    MARKET_GOAL_DIFF: SignalContract(
        MARKET_GOAL_DIFF,
        "float",
        "Public spread market goal-difference line from the home-team perspective.",
    ),
    TOTAL_GOALS_FACTOR: SignalContract(
        TOTAL_GOALS_FACTOR,
        "float",
        "Multiplicative adjustment to both teams' expected goals.",
    ),
    TEAM_EXPECTED_GOALS_FACTOR: SignalContract(
        TEAM_EXPECTED_GOALS_FACTOR,
        "float",
        "Side-specific multiplicative adjustment to one team's expected goals.",
        ("side",),
    ),
    LIVE_DRAW_ADJUSTMENT: SignalContract(
        LIVE_DRAW_ADJUSTMENT,
        "float",
        "Global draw-probability adjustment learned from finished tournament matches.",
        ("sample_count", "draw_rate"),
    ),
    LIVE_SCORE_TAIL_FACTOR: SignalContract(
        LIVE_SCORE_TAIL_FACTOR,
        "float",
        "Global exact-score tail/overdispersion adjustment learned from finished tournament matches.",
        ("sample_count", "high_total_rate"),
    ),
    LIVE_FAVORITE_OUTCOME_FACTOR: SignalContract(
        LIVE_FAVORITE_OUTCOME_FACTOR,
        "float",
        "Global favorite/underdog outcome adjustment learned from frozen prediction audits.",
        ("sample_count", "favorite_hit_rate"),
    ),
}


def known_signal_names() -> tuple[str, ...]:
    return tuple(sorted(SIGNAL_CONTRACTS))
