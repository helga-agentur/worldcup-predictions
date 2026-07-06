"""Phase-aware signals for knockout and final group-stage fixtures."""

from __future__ import annotations

import datetime as dt
from collections import defaultdict
from typing import Any

from worldcup_predictions.core.contracts import Artifact, Diagnostic, Signal, parse_utc_datetime
from worldcup_predictions.core.datasets import PHASE_CONTEXT_SIGNALS
from worldcup_predictions.core.events import EventName, event_value
from worldcup_predictions.core.metadata import PluginKind, PluginMetadata
from worldcup_predictions.core.plugin import BasePlugin, PluginResult
from worldcup_predictions.core.signals import LIVE_DRAW_ADJUSTMENT, TEAM_EXPECTED_GOALS_FACTOR, TOTAL_GOALS_FACTOR
from worldcup_predictions.tournament import FixtureRecord, TournamentState
from worldcup_predictions.tournament.contracts import GroupStanding, ResultRecord
from worldcup_predictions.tournament.repository import load_tournament_state


class PhaseContextPlugin(BasePlugin):
    """Emit phase-specific signals without changing group-stage baseline behavior."""

    id = "phase_context"
    version = "0.1.0"
    priority = 258
    subscribed_events = (EventName.FEATURE_SIGNALS_REQUESTED.value,)
    metadata = PluginMetadata(
        plugin_id=id,
        kind=PluginKind.SIGNAL,
        description="Emit knockout-only and final-group-match context signals for tempo, draw risk, rest, and fatigue.",
        datasets_read=("tournament_fixtures", "tournament_results", "tournament_standings"),
        datasets_written=(PHASE_CONTEXT_SIGNALS,),
        signals_emitted=(TOTAL_GOALS_FACTOR, LIVE_DRAW_ADJUSTMENT, TEAM_EXPECTED_GOALS_FACTOR),
        confidence_policy="Signals are phase-gated: knockout effects only apply to non-group fixtures; final-group dynamics only apply when both teams have one group match remaining.",
    )

    def handle(self, event, context, payload):
        if context.storage is None:
            return PluginResult(
                plugin_id=self.id,
                event=event_value(event),
                diagnostics=[Diagnostic("warning", "Structured storage is unavailable; phase context was skipped.", self.id)],
            )
        state = context.state.get("tournament_state")
        if not isinstance(state, TournamentState):
            state = load_tournament_state(context.storage)
            context.state["tournament_state"] = state

        rows = phase_context_rows(state)
        count = context.storage.write_records(PHASE_CONTEXT_SIGNALS, rows, source=self.id, run_id=context.run_id)
        signals = phase_context_signals(rows)
        return PluginResult(
            plugin_id=self.id,
            event=event_value(event),
            signals=signals,
            artifacts=[Artifact(PHASE_CONTEXT_SIGNALS, "structured_dataset", self.id, data={"rows": count, "signals": len(signals)})],
            metadata={
                "rows": count,
                "signals": len(signals),
                "knockout_rows": sum(1 for row in rows if row["phase_context"] == "knockout"),
                "final_group_rows": sum(1 for row in rows if row["phase_context"] == "final_group_match"),
            },
        )


def phase_context_rows(state: TournamentState) -> list[dict[str, Any]]:
    """Return phase-context rows for currently open fixtures."""

    result_keys = {result.fixture_key for result in state.results}
    latest_result_by_team = _latest_result_by_team(state.results)
    group_remaining = _group_remaining_counts(state.fixtures, result_keys)
    standings_by_group = {
        group: {standing.team.key: standing for standing in standings}
        for group, standings in state.standings.items()
    }
    rows: list[dict[str, Any]] = []
    for fixture in state.open_fixtures():
        if _is_knockout_fixture(fixture):
            rows.extend(_knockout_rows(fixture, latest_result_by_team))
            continue
        if _is_final_group_fixture(fixture, group_remaining):
            rows.append(_final_group_row(fixture, standings_by_group))
    return rows


def phase_context_signals(rows: list[dict[str, Any]]) -> list[Signal]:
    signals: list[Signal] = []
    for row in rows:
        fixture_key = row["fixture_key"]
        confidence = float(row["confidence"])
        if row.get("total_goals_factor") is not None:
            signals.append(
                Signal(
                    name=TOTAL_GOALS_FACTOR,
                    source="phase_context",
                    fixture_key=fixture_key,
                    value=float(row["total_goals_factor"]),
                    weight=float(row["weight"]),
                    confidence=confidence,
                    rationale=row["rationale"],
                    metadata=_signal_metadata(row),
                )
            )
        if row.get("draw_adjustment") is not None:
            signals.append(
                Signal(
                    name=LIVE_DRAW_ADJUSTMENT,
                    source="phase_context",
                    fixture_key=fixture_key,
                    value=float(row["draw_adjustment"]),
                    weight=float(row["weight"]),
                    confidence=confidence,
                    rationale=row["rationale"],
                    metadata=_signal_metadata(row),
                )
            )
        for side in ("home", "away"):
            factor = row.get(f"{side}_expected_goals_factor")
            if factor is None:
                continue
            signals.append(
                Signal(
                    name=TEAM_EXPECTED_GOALS_FACTOR,
                    source="phase_context",
                    fixture_key=fixture_key,
                    value=float(factor),
                    weight=float(row["weight"]),
                    confidence=confidence,
                    rationale=row["rationale"],
                    metadata={**_signal_metadata(row), "side": side},
                )
            )
    return signals


def _knockout_rows(fixture: FixtureRecord, latest_result_by_team: dict[str, ResultRecord]) -> list[dict[str, Any]]:
    kickoff = fixture.kickoff_at
    home_rest = _rest_days(kickoff, latest_result_by_team.get(fixture.home_team.key))
    away_rest = _rest_days(kickoff, latest_result_by_team.get(fixture.away_team.key))
    home_factor = _rest_factor(home_rest, away_rest)
    away_factor = _rest_factor(away_rest, home_rest)
    row = _base_row(fixture, "knockout")
    row.update(
        {
            "total_goals_factor": 0.94,
            "draw_adjustment": 0.055,
            "home_expected_goals_factor": home_factor,
            "away_expected_goals_factor": away_factor,
            "home_rest_days": home_rest,
            "away_rest_days": away_rest,
            "weight": 0.42,
            "confidence": 0.68,
            "rationale": "Knockout fixture: lower open-play tempo, higher 90-minute draw risk, and rest/fatigue asymmetry considered.",
        }
    )
    return [row]


def _final_group_row(fixture: FixtureRecord, standings_by_group: dict[str, dict[str, GroupStanding]]) -> dict[str, Any]:
    group = _group_label(str(fixture.group or ""))
    standings = standings_by_group.get(group, {})
    home = standings.get(fixture.home_team.key, GroupStanding(team=fixture.home_team, group=group))
    away = standings.get(fixture.away_team.key, GroupStanding(team=fixture.away_team, group=group))
    row = _base_row(fixture, "final_group_match")
    home_class = _final_group_class(home)
    away_class = _final_group_class(away)
    factor = 1.0
    draw_adjustment = 0.0
    reason = "Final group match: phase dynamics reviewed."
    if home_class == "draw_path" and away_class == "draw_path":
        factor = 0.94
        draw_adjustment = 0.035
        reason = "Final group match where both teams have useful draw paths; tempo and risk appetite are reduced."
    elif home_class == "must_win" and away_class == "must_win":
        factor = 1.05
        draw_adjustment = -0.035
        reason = "Final group match where both teams likely need a win; open-game risk rises."
    elif "rotation" in {home_class, away_class}:
        factor = 0.96
        draw_adjustment = 0.018
        reason = "Final group match with likely-qualified rotation risk; scoring pace is slightly reduced."
    elif "must_win" in {home_class, away_class}:
        factor = 1.02
        draw_adjustment = -0.012
        reason = "Final group match with one team under win pressure; scoring pace is slightly increased."
    row.update(
        {
            "total_goals_factor": factor,
            "draw_adjustment": draw_adjustment,
            "home_expected_goals_factor": None,
            "away_expected_goals_factor": None,
            "home_group_status": home_class,
            "away_group_status": away_class,
            "weight": 0.36,
            "confidence": 0.66,
            "rationale": reason,
        }
    )
    return row


def _base_row(fixture: FixtureRecord, phase_context: str) -> dict[str, Any]:
    return {
        "record_key": f"{fixture.key}:{phase_context}",
        "fixture_key": fixture.key,
        "event_date": fixture.event_date,
        "home_team": fixture.home_team.name,
        "away_team": fixture.away_team.name,
        "home_fifa_code": fixture.home_team.fifa_code,
        "away_fifa_code": fixture.away_team.fifa_code,
        "stage": fixture.stage,
        "group": fixture.group,
        "phase_context": phase_context,
    }


def _latest_result_by_team(results: list[ResultRecord]) -> dict[str, ResultRecord]:
    latest: dict[str, ResultRecord] = {}
    for result in results:
        for team in (result.home_team, result.away_team):
            current = latest.get(team.key)
            if current is None or result.event_date > current.event_date:
                latest[team.key] = result
    return latest


def _rest_days(kickoff: dt.datetime | None, previous_result: ResultRecord | None) -> float | None:
    previous_kickoff = parse_utc_datetime(previous_result.event_date if previous_result else None)
    if kickoff is None or previous_kickoff is None:
        return None
    return max(0.0, (kickoff - previous_kickoff).total_seconds() / 86400)


def _rest_factor(team_rest: float | None, opponent_rest: float | None) -> float | None:
    if team_rest is None:
        return None
    factor = 1.0
    if team_rest < 4.0:
        factor -= 0.035
    elif team_rest < 5.0:
        factor -= 0.018
    if opponent_rest is not None:
        rest_gap = opponent_rest - team_rest
        if rest_gap >= 2.0:
            factor -= 0.025
        elif rest_gap >= 1.0:
            factor -= 0.012
        elif rest_gap <= -2.0:
            factor += 0.015
    return round(max(0.92, min(1.04, factor)), 4)


def _group_remaining_counts(fixtures: list[FixtureRecord], result_keys: set[str]) -> dict[tuple[str, str], int]:
    counts: dict[tuple[str, str], int] = defaultdict(int)
    for fixture in fixtures:
        if not fixture.group or fixture.key in result_keys:
            continue
        group = _group_label(fixture.group)
        counts[(group, fixture.home_team.key)] += 1
        counts[(group, fixture.away_team.key)] += 1
    return counts


def _is_final_group_fixture(fixture: FixtureRecord, group_remaining: dict[tuple[str, str], int]) -> bool:
    if not fixture.group:
        return False
    group = _group_label(fixture.group)
    return (
        group_remaining.get((group, fixture.home_team.key), 0) == 1
        and group_remaining.get((group, fixture.away_team.key), 0) == 1
    )


def _is_knockout_fixture(fixture: FixtureRecord) -> bool:
    stage = str(fixture.stage or "").casefold()
    if fixture.group or "group" in stage or "gruppe" in stage:
        return False
    return bool(stage)


def _final_group_class(standing: GroupStanding) -> str:
    if standing.points >= 6:
        return "rotation"
    if standing.points >= 4:
        return "draw_path"
    if standing.points <= 1:
        return "must_win"
    return "active"


def _signal_metadata(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "phase_context": row["phase_context"],
        "stage": row.get("stage"),
        "group": row.get("group"),
        "home_rest_days": row.get("home_rest_days"),
        "away_rest_days": row.get("away_rest_days"),
        "home_group_status": row.get("home_group_status"),
        "away_group_status": row.get("away_group_status"),
    }


def _group_label(group: str) -> str:
    return (
        group.replace("GROUP_", "")
        .replace("Group ", "")
        .replace("Gruppe ", "")
        .strip()
        .upper()
    )
