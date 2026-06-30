"""Group-state motivation signals."""

from __future__ import annotations

from collections import defaultdict
from typing import Any

from worldcup_predictions.core.contracts import Signal
from worldcup_predictions.tournament.contracts import FixtureRecord, GroupStanding, TournamentState


def build_group_state_rows(state: TournamentState) -> list[dict[str, Any]]:
    """Return conservative group-state rows for still-open group fixtures."""

    result_keys = {result.fixture_key for result in state.results}
    fixtures_by_group: dict[str, list[FixtureRecord]] = defaultdict(list)
    for fixture in state.fixtures:
        if fixture.group:
            fixtures_by_group[_group_label(fixture.group)].append(fixture)

    rows: list[dict[str, Any]] = []
    for fixture in state.fixtures:
        if not fixture.group or fixture.key in result_keys:
            continue
        group = _group_label(fixture.group)
        standings = state.standings.get(group, [])
        rank_by_team = {standing.team.key: rank for rank, standing in enumerate(standings, start=1)}
        standing_by_team = {standing.team.key: standing for standing in standings}
        remaining = _remaining_counts(fixtures_by_group[group], result_keys)
        for team, opponent in (
            (fixture.home_team, fixture.away_team),
            (fixture.away_team, fixture.home_team),
        ):
            standing = standing_by_team.get(team.key) or GroupStanding(team=team, group=group)
            team_remaining = remaining.get(team.key, 0)
            classification = classify_group_motivation(standing, team_remaining)
            rows.append(
                {
                    "record_key": f"{fixture.key}:{team.key}",
                    "fixture_key": fixture.key,
                    "event_date": fixture.event_date,
                    "home_team": fixture.home_team.name,
                    "away_team": fixture.away_team.name,
                    "team": team.name,
                    "team_fifa_code": team.fifa_code,
                    "opponent": opponent.name,
                    "opponent_fifa_code": opponent.fifa_code,
                    "group": group,
                    "rank": rank_by_team.get(team.key),
                    "points": standing.points,
                    "goal_difference": standing.goal_difference,
                    "goals_for": standing.goals_for,
                    "goals_against": standing.goals_against,
                    "played": standing.played,
                    "remaining": team_remaining,
                    **classification,
                }
            )
    return rows


def build_group_state_signals(state: TournamentState) -> list[Signal]:
    signals: list[Signal] = []
    for row in build_group_state_rows(state):
        for name, value in (
            ("group_motivation", row["motivation_score"]),
            ("group_draw_pressure", row["draw_adjustment"]),
            ("group_rotation_risk", row["rotation_risk"]),
            ("group_elimination_risk", row["elimination_risk"]),
        ):
            signals.append(
                Signal(
                    name=name,
                    source="tournament_state",
                    fixture_key=row["fixture_key"],
                    value=value,
                    confidence=row["confidence"],
                    rationale=row["note"],
                    metadata={
                        "team": row["team"],
                        "team_fifa_code": row["team_fifa_code"],
                        "group": row["group"],
                        "rank": row["rank"],
                        "points": row["points"],
                        "remaining": row["remaining"],
                    },
                )
            )
    return signals


def classify_group_motivation(standing: GroupStanding, remaining: int) -> dict[str, Any]:
    points = standing.points
    max_points = points + remaining * 3
    if remaining <= 0:
        return {
            "status": "played",
            "max_points": max_points,
            "motivation_score": 0.0,
            "draw_adjustment": 0.0,
            "must_win": False,
            "draw_enough": False,
            "likely_qualified": False,
            "rotation_risk": False,
            "elimination_risk": False,
            "confidence": 0.8,
            "note": "fixture already played",
        }
    if remaining == 1 and points >= 6:
        return {
            "status": "likely_qualified_rotation_risk",
            "max_points": max_points,
            "motivation_score": 0.20,
            "draw_adjustment": 0.015,
            "must_win": False,
            "draw_enough": True,
            "likely_qualified": True,
            "rotation_risk": True,
            "elimination_risk": False,
            "confidence": 0.65,
            "note": "six or more points before final group match; qualification is likely and rotation risk rises",
        }
    if remaining == 1 and points >= 4:
        return {
            "status": "draw_enough",
            "max_points": max_points,
            "motivation_score": 0.38,
            "draw_adjustment": 0.025,
            "must_win": False,
            "draw_enough": True,
            "likely_qualified": False,
            "rotation_risk": False,
            "elimination_risk": False,
            "confidence": 0.7,
            "note": "four or more points before final group match; draw path is usually useful in 2026 format",
        }
    if remaining == 1 and points <= 1:
        return {
            "status": "must_win_or_eliminated",
            "max_points": max_points,
            "motivation_score": 0.72,
            "draw_adjustment": -0.02,
            "must_win": True,
            "draw_enough": False,
            "likely_qualified": False,
            "rotation_risk": False,
            "elimination_risk": True,
            "confidence": 0.72,
            "note": "low points before final group match; win pressure and elimination risk are high",
        }
    return {
        "status": "active_group_state",
        "max_points": max_points,
        "motivation_score": 0.50,
        "draw_adjustment": 0.0,
        "must_win": False,
        "draw_enough": False,
        "likely_qualified": False,
        "rotation_risk": False,
        "elimination_risk": False,
        "confidence": 0.55,
        "note": "group is still open; no strong motivation edge detected",
    }


def _remaining_counts(fixtures: list[FixtureRecord], result_keys: set[str]) -> dict[str, int]:
    counts: dict[str, int] = defaultdict(int)
    for fixture in fixtures:
        if fixture.key in result_keys:
            continue
        counts[fixture.home_team.key] += 1
        counts[fixture.away_team.key] += 1
    return counts


def _group_label(group: str) -> str:
    return (
        group.replace("GROUP_", "")
        .replace("Group ", "")
        .replace("Gruppe ", "")
        .strip()
        .upper()
    )
