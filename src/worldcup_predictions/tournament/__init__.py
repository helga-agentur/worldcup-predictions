"""Tournament state, imports, and group-context signals."""

from worldcup_predictions.tournament.contracts import (
    FixtureRecord,
    GroupStanding,
    ResultRecord,
    TeamRef,
    TournamentState,
    fixture_key,
)
from worldcup_predictions.tournament.group_state import (
    build_group_state_rows,
    build_group_state_signals,
    classify_group_motivation,
)
from worldcup_predictions.tournament.openfootball import (
    parse_openfootball_text,
)
from worldcup_predictions.tournament.slots import (
    canonical_slot_code,
    has_defined_teams,
    is_slot_team,
    slot_display_name,
    slot_team_ref,
)
from worldcup_predictions.tournament.state import (
    build_result_checks,
    build_tournament_state,
    standing_records,
)
from worldcup_predictions.tournament.teams import TeamResolver

__all__ = [
    "FixtureRecord",
    "GroupStanding",
    "ResultRecord",
    "TeamRef",
    "TeamResolver",
    "TournamentState",
    "build_group_state_rows",
    "build_group_state_signals",
    "build_result_checks",
    "build_tournament_state",
    "classify_group_motivation",
    "canonical_slot_code",
    "fixture_key",
    "has_defined_teams",
    "is_slot_team",
    "parse_openfootball_text",
    "slot_display_name",
    "slot_team_ref",
    "standing_records",
]
