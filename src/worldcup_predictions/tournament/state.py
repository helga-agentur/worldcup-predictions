"""Build current tournament state from structured fixtures and results."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import replace
from typing import Iterable

from worldcup_predictions.core.constants import (
    CONFIRMED_RESULT_HIGH_AUTHORITY_MIN_SOURCES,
    CONFIRMED_RESULT_MIN_SOURCES,
    HIGH_AUTHORITY_RESULT_SOURCES,
)
from worldcup_predictions.tournament.contracts import (
    FixtureRecord,
    GroupStanding,
    ResultRecord,
    TeamRef,
    TournamentState,
)


def build_tournament_state(
    fixtures: Iterable[FixtureRecord],
    results: Iterable[ResultRecord],
) -> TournamentState:
    """Reconcile fixtures/results and compute current group standings."""

    all_result_rows = list(results)
    fixture_rows = _latest_fixtures(fixtures)
    result_rows = _preferred_results(all_result_rows)
    standings = _build_standings(fixture_rows, result_rows)
    return TournamentState(
        fixtures=fixture_rows,
        results=result_rows,
        standings=standings,
        result_checks=build_result_checks(all_result_rows),
    )


def standing_records(state: TournamentState) -> list[dict]:
    rows = []
    for group, standings in sorted(state.standings.items()):
        for rank, standing in enumerate(_rank_standings(standings), start=1):
            rows.append(standing.to_record(rank))
    return rows


def build_result_checks(results: list[ResultRecord]) -> list[dict]:
    """Compare multiple source results for the same fixture."""

    by_fixture: dict[str, list[ResultRecord]] = defaultdict(list)
    for result in results:
        by_fixture[result.fixture_key].append(result)

    checks = []
    for fixture_key, fixture_results in sorted(by_fixture.items()):
        score_by_source = {result.source: result.score.as_text() for result in fixture_results}
        primary = _best_result(fixture_results)
        selected = _confirmed_result(fixture_results)
        score_groups = _score_groups(fixture_results)
        confirmed_scores = [_score_text for _score_text, rows in score_groups.items() if _is_confirmed_result_group(rows)]
        if selected is not None:
            status = "confirmed"
            selected_score = selected.score.as_text()
        elif len(score_groups) > 1:
            status = "unconfirmed_conflict"
            selected_score = ""
        else:
            status = "unconfirmed"
            selected_score = ""
        checks.append(
            {
                "record_key": fixture_key,
                "fixture_key": fixture_key,
                "event_date": primary.event_date,
                "home_team": primary.home_team.name,
                "away_team": primary.away_team.name,
                "home_fifa_code": primary.home_team.fifa_code,
                "away_fifa_code": primary.away_team.fifa_code,
                "status": status,
                "selected_score": selected_score,
                "candidate_score": primary.score.as_text(),
                "scores_by_source": score_by_source,
                "source_count": len(set(score_by_source)),
                "confirmed_source_count": len(set(_sources_for_score(fixture_results, selected_score))) if selected_score else 0,
                "high_authority_source_count": len(set(_high_authority_sources_for_score(fixture_results, selected_score))) if selected_score else 0,
                "confirmed_scores": confirmed_scores,
                "policy": {
                    "min_sources": CONFIRMED_RESULT_MIN_SOURCES,
                    "high_authority_min_sources": CONFIRMED_RESULT_HIGH_AUTHORITY_MIN_SOURCES,
                    "high_authority_sources": list(HIGH_AUTHORITY_RESULT_SOURCES),
                },
            }
        )
    return checks


def _latest_fixtures(fixtures: Iterable[FixtureRecord]) -> list[FixtureRecord]:
    by_key: dict[str, FixtureRecord] = {}
    for fixture in fixtures:
        by_key[fixture.key] = fixture
    return sorted(by_key.values(), key=lambda item: (item.event_date, item.home_team.name, item.away_team.name))


def _preferred_results(results: Iterable[ResultRecord]) -> list[ResultRecord]:
    by_fixture: dict[str, list[ResultRecord]] = defaultdict(list)
    for result in results:
        by_fixture[result.fixture_key].append(result)
    selected = [
        result
        for result in (_confirmed_result(rows) for rows in by_fixture.values())
        if result is not None
    ]
    return sorted(selected, key=lambda item: (item.event_date, item.home_team.name, item.away_team.name))


def _best_result(results: list[ResultRecord]) -> ResultRecord:
    return sorted(
        results,
        key=lambda item: (
            _source_rank(item.source),
            item.event_date,
            item.home_team.name,
            item.away_team.name,
        ),
    )[0]


def _source_rank(source: str) -> int:
    source = source.casefold()
    if "srf_public" in source:
        return 1
    if "fifa_match_centre" in source:
        return 2
    if "football_data" in source:
        return 3
    if "openfootball" in source:
        return 4
    if "espn_scoreboard" in source:
        return 5
    if "fotmob_public" in source:
        return 6
    if "sofascore_public" in source:
        return 7
    return 10


def _confirmed_result(results: list[ResultRecord]) -> ResultRecord | None:
    confirmed_groups = [
        rows
        for rows in _score_groups(results).values()
        if _is_confirmed_result_group(rows)
    ]
    if not confirmed_groups:
        return None
    selected_group = sorted(
        confirmed_groups,
        key=lambda rows: (
            -len(_unique_sources(rows)),
            -len(_high_authority_sources(rows)),
            _source_rank(_best_result(rows).source),
        ),
    )[0]
    selected = _best_result(selected_group)
    return replace(
        selected,
        metadata={
            **selected.metadata,
            "confirmation": {
                "status": "confirmed",
                "score": selected.score.as_text(),
                "sources": sorted(_unique_sources(selected_group)),
                "source_count": len(_unique_sources(selected_group)),
                "high_authority_sources": sorted(_high_authority_sources(selected_group)),
                "high_authority_source_count": len(_high_authority_sources(selected_group)),
                "policy": {
                    "min_sources": CONFIRMED_RESULT_MIN_SOURCES,
                    "high_authority_min_sources": CONFIRMED_RESULT_HIGH_AUTHORITY_MIN_SOURCES,
                },
            },
        },
    )


def _score_groups(results: list[ResultRecord]) -> dict[str, list[ResultRecord]]:
    by_score: dict[str, list[ResultRecord]] = defaultdict(list)
    for result in results:
        by_score[result.score.as_text()].append(result)
    return by_score


def _is_confirmed_result_group(results: list[ResultRecord]) -> bool:
    sources = _unique_sources(results)
    if len(sources) >= CONFIRMED_RESULT_MIN_SOURCES:
        return True
    return len(_high_authority_sources(results)) >= CONFIRMED_RESULT_HIGH_AUTHORITY_MIN_SOURCES


def _unique_sources(results: list[ResultRecord]) -> set[str]:
    return {result.source for result in results}


def _high_authority_sources(results: list[ResultRecord]) -> set[str]:
    return {
        result.source
        for result in results
        if _is_high_authority_source(result.source)
    }


def _sources_for_score(results: list[ResultRecord], score: str) -> list[str]:
    return [result.source for result in results if result.score.as_text() == score]


def _high_authority_sources_for_score(results: list[ResultRecord], score: str) -> list[str]:
    return [
        result.source
        for result in results
        if result.score.as_text() == score and _is_high_authority_source(result.source)
    ]


def _is_high_authority_source(source: str) -> bool:
    source_key = source.casefold()
    return any(authority.casefold() in source_key for authority in HIGH_AUTHORITY_RESULT_SOURCES)


def _build_standings(
    fixtures: list[FixtureRecord],
    results: list[ResultRecord],
) -> dict[str, list[GroupStanding]]:
    result_by_fixture = {result.fixture_key: result for result in results}
    tables: dict[str, dict[str, GroupStanding]] = defaultdict(dict)
    team_refs: dict[str, TeamRef] = {}
    for fixture in fixtures:
        if not fixture.group:
            continue
        group = _group_label(fixture.group)
        for team in (fixture.home_team, fixture.away_team):
            team_refs[team.key] = team
            tables[group].setdefault(team.key, GroupStanding(team=team, group=group))
        result = result_by_fixture.get(fixture.key)
        if result is None:
            continue
        tables[group][fixture.home_team.key].record(result.score.home, result.score.away)
        tables[group][fixture.away_team.key].record(result.score.away, result.score.home)

    return {
        group: _rank_standings(list(team_table.values()))
        for group, team_table in sorted(tables.items())
    }


def _rank_standings(standings: list[GroupStanding]) -> list[GroupStanding]:
    return sorted(
        standings,
        key=lambda row: (
            -row.points,
            -row.goal_difference,
            -row.goals_for,
            row.goals_against,
            row.team.name,
        ),
    )


def _group_label(group: str) -> str:
    return (
        group.replace("GROUP_", "")
        .replace("Group ", "")
        .replace("Gruppe ", "")
        .strip()
        .upper()
    )
