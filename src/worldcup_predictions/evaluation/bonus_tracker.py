"""Provider bonus-answer status tracking."""

from __future__ import annotations

import re
from collections import Counter
from typing import Any

from worldcup_predictions.core.datasets import (
    PROVIDER_BONUS_TRACKER,
    PROVIDER_POINTS,
    SIMULATION_SUMMARY,
)
from worldcup_predictions.tournament import ResultRecord, TournamentState


STAGE_ORDER = {
    "GROUP_STAGE": 0,
    "LAST_32": 1,
    "LAST_16": 2,
    "QUARTER_FINALS": 3,
    "SEMI_FINALS": 4,
    "FINAL": 5,
    "CHAMPION": 6,
}


def build_bonus_tracker_rows(storage, state: TournamentState, *, provider: str, run_id: str | None = None) -> list[dict[str, Any]]:
    rows = [virtual_match_points_row(storage, state, provider=provider, run_id=run_id)]
    storage.write_records(PROVIDER_BONUS_TRACKER, rows, source="bonus_tracker", run_id=run_id)
    return rows


def track_bonus_answer(answer: dict[str, Any], state: TournamentState, sim_distributions: dict[str, Any] | None = None) -> dict[str, Any]:
    question = str(answer.get("question") or "")
    folded = question.casefold()
    row = {
        "record_key": f"{answer.get('provider')}:{answer.get('question_key')}",
        "provider": answer.get("provider"),
        "question_key": answer.get("question_key"),
        "question": question,
        "submitted_answer": answer.get("answer"),
        "submitted_answer_canonical": answer.get("answer_canonical"),
        "points_if_correct": answer.get("points"),
        "status": "unknown",
        "current_value": "",
        "target_value": answer.get("answer_canonical") or answer.get("answer"),
        "current_state": "",
        "remaining_condition": "",
        "data_source": "tournament_state",
    }
    if "weltmeister" in folded:
        return _track_champion(row, state, sim_distributions)
    if "schweiz" in folded and "tore" in folded:
        return _track_swiss_goals(row, state)
    if "0:0" in folded or "0 0" in folded:
        return _track_nil_nil(row, state)
    if "torsch" in folded:
        return _track_top_scorer_goals(row, state)
    if "wie weit" in folded and "schweiz" in folded:
        return _track_swiss_stage(row, state, sim_distributions)
    row["current_state"] = "No tracker rule exists for this provider question yet."
    return row


def virtual_match_points_row(storage, state: TournamentState, *, provider: str, run_id: str | None = None) -> dict[str, Any]:
    """Summarize points from optimized recommendations as if entered one-to-one."""

    point_rows = [row for row in storage.read_records(PROVIDER_POINTS, latest_only=True) if row.get("provider") == provider]
    if not point_rows:
        from worldcup_predictions.evaluation.provider_points import build_provider_points_rows

        point_rows = build_provider_points_rows(storage, state, provider=provider, run_id=run_id)
    total = sum(float(row.get("points") or 0.0) for row in point_rows)
    played_with_tip = sum(1 for row in point_rows if str(row.get("source") or "") != "missing_tip")
    missing = len(point_rows) - played_with_tip
    return {
        "record_key": f"{provider}:virtual_match_points",
        "provider": provider,
        "question_key": "virtual_match_points",
        "question": "Virtual match-tip points from optimized recommendations",
        "submitted_answer": "optimized recommendations",
        "submitted_answer_canonical": "optimized_recommendations",
        "points_if_correct": "",
        "status": "current_points",
        "current_value": total,
        "target_value": "",
        "current_state": f"{total:.0f} points from {played_with_tip} finished match tip(s) entered exactly as suggested.",
        "remaining_condition": "Future finished matches will update this virtual score once optimized tips and final scores exist.",
        "data_source": "provider_points",
        "metadata": {"finished_rows": len(point_rows), "missing_tip_rows": missing},
    }


def _track_champion(row: dict[str, Any], state: TournamentState, sim_distributions: dict[str, Any] | None = None) -> dict[str, Any]:
    target = str(row.get("submitted_answer_canonical") or row.get("submitted_answer") or "")
    status = team_alive_status(target, state)
    row["current_value"] = "alive" if status["alive"] else "eliminated"
    row["status"] = "still_possible" if status["alive"] else "impossible"
    row["current_state"] = status["note"]
    row["remaining_condition"] = f"{target} must win the World Cup."
    champion = (sim_distributions or {}).get("champion")
    probability = _distribution_probability(champion, target)
    if probability is not None:
        row["simulated_probability"] = probability
        row["current_state"] = f"{status['note']} Simulated champion probability {probability:.1%}."
        row["data_source"] = "tournament_state+simulation"
    return row


def _track_swiss_goals(row: dict[str, Any], state: TournamentState) -> dict[str, Any]:
    current = sum(result.score.home for result in state.results if result.home_team.key == "SUI")
    current += sum(result.score.away for result in state.results if result.away_team.key == "SUI")
    target = parse_numeric_answer(row.get("submitted_answer"))
    row["current_value"] = current
    row["status"] = numeric_status(current, target, tournament_over(state))
    row["current_state"] = f"Switzerland has scored {current} goals in recorded tournament matches."
    row["remaining_condition"] = numeric_remaining_condition(current, target, subject="Switzerland goals")
    return row


def _track_nil_nil(row: dict[str, Any], state: TournamentState) -> dict[str, Any]:
    current = sum(1 for result in state.results if result.score.home == 0 and result.score.away == 0)
    target = parse_numeric_answer(row.get("submitted_answer"))
    row["current_value"] = current
    row["status"] = numeric_status(current, target, tournament_over(state))
    row["current_state"] = f"{current} recorded matches have ended 0:0."
    row["remaining_condition"] = numeric_remaining_condition(current, target, subject="0:0 matches")
    return row


def _track_top_scorer_goals(row: dict[str, Any], state: TournamentState) -> dict[str, Any]:
    scorers = top_scorers_from_results(state.results)
    current = scorers.most_common(1)[0][1] if scorers else None
    target = parse_numeric_answer(row.get("submitted_answer"))
    row["current_value"] = current if current is not None else ""
    row["status"] = "unknown" if current is None else numeric_status(current, target, tournament_over(state))
    row["current_state"] = "Top scorer data unavailable." if not scorers else ", ".join(f"{name} ({goals})" for name, goals in scorers.most_common(5))
    row["remaining_condition"] = "Need goalscorer text from imported source data." if current is None else numeric_remaining_condition(current, target, subject="top scorer goals")
    return row


def _track_swiss_stage(row: dict[str, Any], state: TournamentState, sim_distributions: dict[str, Any] | None = None) -> dict[str, Any]:
    status = team_alive_status("SUI", state)
    row["current_value"] = status["stage"]
    row["status"] = "still_possible" if status["alive"] else "impossible"
    row["current_state"] = status["note"]
    row["remaining_condition"] = "Requires knockout bracket results to lock precisely."
    team_stage = (sim_distributions or {}).get("team_stage") or {}
    swiss_distribution = team_stage.get("Switzerland") or team_stage.get("SUI")
    if swiss_distribution:
        most_likely = max(swiss_distribution, key=lambda entry: float(entry.get("probability") or 0.0))
        row["simulated_stage_distribution"] = swiss_distribution
        row["current_state"] = (
            f"{status['note']} Simulated most-likely stage: "
            f"{most_likely.get('answer')} ({float(most_likely.get('probability') or 0.0):.0%})."
        )
        row["data_source"] = "tournament_state+simulation"
    return row


def _latest_simulation_distributions(storage) -> dict[str, Any]:
    """Distributions from the most recent stored daily simulation, or empty if none."""

    rows = storage.read_records(SIMULATION_SUMMARY, latest_only=True)
    if not rows:
        return {}
    latest = max(
        rows,
        key=lambda row: str(row.get("simulation_id") or (row.get("_record") or {}).get("observed_at_utc") or ""),
    )
    return dict(latest.get("distributions") or {})


def _distribution_probability(distribution: list[dict[str, Any]] | None, answer: str) -> float | None:
    target = str(answer or "").casefold()
    if not target:
        return None
    for entry in distribution or []:
        if str(entry.get("answer") or "").casefold() == target:
            return float(entry.get("probability") or 0.0)
    return None


def team_alive_status(team_key: str, state: TournamentState) -> dict[str, Any]:
    team_key = str(team_key)
    team_results = [result for result in state.results if team_key in {result.home_team.key, result.away_team.key, result.home_team.name, result.away_team.name}]
    knockout_losses = []
    for result in team_results:
        fixture = next((item for item in state.fixtures if item.key == result.fixture_key), None)
        stage = str((fixture.stage if fixture else "") or "").upper()
        if "GROUP" in stage:
            continue
        if result.score.home == result.score.away:
            continue
        winner_key = result.home_team.key if result.score.home > result.score.away else result.away_team.key
        if winner_key != team_key:
            knockout_losses.append(stage or "KNOCKOUT")
    if knockout_losses:
        return {"alive": False, "stage": knockout_losses[-1], "note": f"{team_key} lost in {knockout_losses[-1]}."}
    return {"alive": True, "stage": "alive", "note": f"{team_key} has no recorded knockout elimination."}


def parse_numeric_answer(value: Any) -> dict[str, Any]:
    text = str(value or "")
    if "mehr als 15" in text.casefold() or "more than 15" in text.casefold():
        return {"kind": "more_than", "value": 15, "label": text}
    match = re.search(r"\d+", text)
    if not match:
        return {"kind": "unknown", "value": None, "label": text}
    return {"kind": "exact", "value": int(match.group(0)), "label": text}


def numeric_status(current: int | None, target: dict[str, Any], tournament_is_over: bool) -> str:
    if current is None or target["kind"] == "unknown":
        return "unknown"
    if target["kind"] == "more_than":
        if current > int(target["value"]):
            return "correct_locked" if tournament_is_over else "still_possible"
        return "impossible" if tournament_is_over else "still_possible"
    target_value = int(target["value"])
    if current > target_value:
        return "impossible"
    if tournament_is_over:
        return "correct_locked" if current == target_value else "impossible"
    if current == target_value:
        return "still_possible_at_limit"
    return "still_possible"


def numeric_remaining_condition(current: int | None, target: dict[str, Any], *, subject: str) -> str:
    if current is None or target["kind"] == "unknown":
        return "No numeric target could be parsed."
    if target["kind"] == "more_than":
        return f"{subject} must finish above {target['value']}."
    remaining = int(target["value"]) - current
    if remaining < 0:
        return "Already exceeded the submitted target."
    if remaining == 0:
        return "Current value is exactly at the submitted target; it must not increase."
    return f"Needs exactly {remaining} more."


def tournament_over(state: TournamentState) -> bool:
    final_fixtures = [fixture for fixture in state.fixtures if "FINAL" in str(fixture.stage or "").upper()]
    result_keys = {result.fixture_key for result in state.results}
    return bool(final_fixtures) and all(fixture.key in result_keys for fixture in final_fixtures)


def top_scorers_from_results(results: list[ResultRecord]) -> Counter:
    counter = Counter()
    for result in results:
        goals_text = str(result.metadata.get("goals_text") or "")
        for name in re.findall(r"([A-Z][A-Za-z .'-]{2,})\s+\d+(?:\+\d+)?'", goals_text):
            if " og" not in name.casefold():
                counter.update([name.strip()])
    return counter
