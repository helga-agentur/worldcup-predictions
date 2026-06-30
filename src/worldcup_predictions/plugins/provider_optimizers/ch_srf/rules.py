"""srf.ch scoring rules."""

from __future__ import annotations

from worldcup_predictions.core.contracts import Fixture
from worldcup_predictions.plugins.provider_optimizers.common import ComponentScoreRules, fixture_stage


def srf_rules_for_fixture(fixture: Fixture) -> ComponentScoreRules:
    """Return the SRF scoring rules for a fixture phase."""

    stage = fixture_stage(fixture)
    is_group_stage = fixture.group is not None or "group" in stage or "gruppe" in stage
    if is_group_stage or not stage:
        return ComponentScoreRules(
            provider="srf.ch",
            version="2026-group-stage",
            phase="group_stage",
            outcome_points=5,
            home_goals_points=1,
            away_goals_points=1,
            goal_difference_points=3,
            exact_score_points=10,
            metadata={"source": "srf-rules-2026"},
        )
    return ComponentScoreRules(
        provider="srf.ch",
        version="2026-knockout-stage",
        phase="knockout_stage",
        outcome_points=10,
        home_goals_points=2,
        away_goals_points=2,
        goal_difference_points=6,
        exact_score_points=20,
        metadata={"source": "srf-rules-2026"},
    )
