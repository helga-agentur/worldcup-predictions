"""Build current tournament state from structured fixtures and results."""

from __future__ import annotations

import datetime as dt
from collections import defaultdict
from dataclasses import replace
from typing import Iterable

from worldcup_predictions.core.contracts import parse_utc_datetime
from worldcup_predictions.core.constants import (
    CONFIRMED_RESULT_HIGH_AUTHORITY_MIN_SOURCES,
    CONFIRMED_RESULT_KICKOFF_WINDOW_HOURS,
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
from worldcup_predictions.tournament.slots import canonical_slot_code, is_slot_team


def build_tournament_state(
    fixtures: Iterable[FixtureRecord],
    results: Iterable[ResultRecord],
    *,
    now: dt.datetime | None = None,
) -> TournamentState:
    """Reconcile fixtures/results and compute current group standings."""

    now = _normalize_now(now)
    valid_fixtures = [fixture for fixture in fixtures if _valid_fixture(fixture)]
    all_result_rows = _normalized_results(list(results), valid_fixtures)
    result_rows = _preferred_results(all_result_rows, now=now)
    fixture_rows = _latest_fixtures(_canonicalize_fixtures(valid_fixtures, result_rows))
    standings = _build_standings(fixture_rows, result_rows)
    return TournamentState(
        fixtures=fixture_rows,
        results=result_rows,
        standings=standings,
        result_checks=build_result_checks(all_result_rows, now=now),
    )


def standing_records(state: TournamentState) -> list[dict]:
    rows = []
    for group, standings in sorted(state.standings.items()):
        for rank, standing in enumerate(_rank_standings(standings), start=1):
            rows.append(standing.to_record(rank))
    return rows


def build_result_checks(results: list[ResultRecord], *, now: dt.datetime | None = None) -> list[dict]:
    """Compare multiple source results for the same fixture."""

    now = _normalize_now(now)
    by_fixture: dict[str, list[ResultRecord]] = defaultdict(list)
    for result in results:
        by_fixture[result.fixture_key].append(result)

    checks = []
    for fixture_key, fixture_results in sorted(by_fixture.items()):
        due_results = [result for result in fixture_results if _result_is_due(result, now=now)]
        future_results = [result for result in fixture_results if not _result_is_due(result, now=now)]
        score_by_source = {result.source: result.score.as_text() for result in fixture_results}
        future_score_by_source = {result.source: result.score.as_text() for result in future_results}
        primary = _best_result(due_results or fixture_results)
        selected = _confirmed_result(due_results)
        score_groups = _score_groups(due_results)
        confirmed_scores = [_score_text for _score_text, rows in score_groups.items() if _is_confirmed_result_group(rows)]
        if not due_results and future_results:
            status = "future_result_ignored"
            selected_score = ""
        elif selected is not None:
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
                "confirmed_source_count": len(set(_sources_for_score(due_results, selected_score))) if selected_score else 0,
                "high_authority_source_count": len(set(_high_authority_sources_for_score(due_results, selected_score))) if selected_score else 0,
                "confirmed_scores": confirmed_scores,
                "ignored_future_source_count": len(set(future_score_by_source)),
                "ignored_future_scores_by_source": future_score_by_source,
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
        if not _valid_fixture(fixture):
            continue
        current = by_key.get(fixture.key)
        if current is None or _fixture_rank(fixture) < _fixture_rank(current):
            by_key[fixture.key] = fixture
    return sorted(_dedupe_equivalent_fixture_slots(by_key.values()), key=lambda item: (item.event_date, item.home_team.name, item.away_team.name))


def _dedupe_equivalent_fixture_slots(fixtures: Iterable[FixtureRecord]) -> list[FixtureRecord]:
    selected: list[FixtureRecord] = []
    for fixture in sorted(fixtures, key=lambda item: (item.event_date, -_fixture_resolution_score(item), _fixture_rank(item), item.key)):
        replacement_index = None
        for index, existing in enumerate(selected):
            if _same_fixture_slot(existing, fixture):
                replacement_index = index
                break
        if replacement_index is None:
            selected.append(fixture)
            continue
        if _fixture_quality(fixture) > _fixture_quality(selected[replacement_index]):
            selected[replacement_index] = fixture
    return selected


def _same_fixture_slot(left: FixtureRecord, right: FixtureRecord) -> bool:
    if left.event_date != right.event_date:
        return False
    left_tokens = _fixture_identity_tokens(left)
    right_tokens = _fixture_identity_tokens(right)
    if left_tokens & right_tokens:
        return True
    left_slots = _fixture_slot_count(left)
    right_slots = _fixture_slot_count(right)
    # Knockout sources can disagree only by resolution level: W76-W78 versus
    # Brazil-Norway. In that case the kickoff slot is the shared identity.
    return (left_slots > 0 and right_slots == 0) or (right_slots > 0 and left_slots == 0)


def _fixture_identity_tokens(fixture: FixtureRecord) -> set[str]:
    tokens = set()
    for team in (fixture.home_team, fixture.away_team):
        if team.fifa_code:
            tokens.add(f"team:{team.fifa_code}")
        slot_code = _team_slot_code(team)
        if slot_code:
            tokens.add(f"slot:{slot_code}")
    return tokens


def _valid_fixture(fixture: FixtureRecord) -> bool:
    return _team_identity(fixture.home_team) != _team_identity(fixture.away_team)


def _team_identity(team: TeamRef) -> str:
    return canonical_slot_code(team.fifa_code) or canonical_slot_code(team.key) or canonical_slot_code(team.name) or team.fifa_code or team.key or team.name


def _fixture_quality(fixture: FixtureRecord) -> tuple[int, int, int]:
    return (_fixture_resolution_score(fixture), -_fixture_slot_count(fixture), -_fixture_rank(fixture))


def _fixture_resolution_score(fixture: FixtureRecord) -> int:
    return int(bool(fixture.home_team.fifa_code)) + int(bool(fixture.away_team.fifa_code))


def _fixture_slot_count(fixture: FixtureRecord) -> int:
    return int(bool(_team_slot_code(fixture.home_team))) + int(bool(_team_slot_code(fixture.away_team)))


def _canonicalize_fixtures(fixtures: list[FixtureRecord], results: list[ResultRecord]) -> list[FixtureRecord]:
    winner_by_slot = _winner_by_slot(fixtures, results)
    slot_templates = _slot_templates_by_event(fixtures)
    return [
        _canonicalize_fixture(fixture, winner_by_slot=winner_by_slot, slot_template=slot_templates.get(fixture.event_date))
        for fixture in fixtures
    ]


def _canonicalize_fixture(
    fixture: FixtureRecord,
    *,
    winner_by_slot: dict[str, TeamRef],
    slot_template: tuple[str, str] | None,
) -> FixtureRecord:
    home = _canonicalize_team(fixture.home_team, winner_by_slot=winner_by_slot, slot_code=slot_template[0] if slot_template else "")
    away = _canonicalize_team(fixture.away_team, winner_by_slot=winner_by_slot, slot_code=slot_template[1] if slot_template else "")
    if home == fixture.home_team and away == fixture.away_team:
        return fixture
    metadata = {
        **fixture.metadata,
        "canonicalized_fixture_key": FixtureRecord(
            event_date=fixture.event_date,
            home_team=home,
            away_team=away,
        ).key,
        "source_fixture_key": fixture.key,
    }
    return replace(fixture, home_team=home, away_team=away, metadata=metadata)


def _canonicalize_team(
    team: TeamRef,
    *,
    winner_by_slot: dict[str, TeamRef],
    slot_code: str,
) -> TeamRef:
    direct_slot = canonical_slot_code(team.fifa_code) or canonical_slot_code(team.key) or canonical_slot_code(team.name)
    code = direct_slot or (slot_code if _needs_slot_identity(team) else "")
    if not code:
        return team
    winner = winner_by_slot.get(code)
    if winner is not None:
        return winner
    return TeamRef(code, None)


def _needs_slot_identity(team: TeamRef) -> bool:
    return team.fifa_code is None and not is_slot_team(team)


def _winner_by_slot(fixtures: list[FixtureRecord], results: list[ResultRecord]) -> dict[str, TeamRef]:
    match_number_by_fixture = {}
    for fixture in fixtures:
        match_number = _fixture_match_number(fixture)
        if match_number:
            match_number_by_fixture[fixture.key] = match_number

    winners = {}
    for result in results:
        match_number = _result_match_number(result) or match_number_by_fixture.get(result.fixture_key)
        if not match_number:
            continue
        if result.score.home > result.score.away:
            winners[f"W{match_number}"] = result.home_team
        elif result.score.away > result.score.home:
            winners[f"W{match_number}"] = result.away_team
    return winners


def _slot_templates_by_event(fixtures: list[FixtureRecord]) -> dict[str, tuple[str, str]]:
    templates: dict[str, tuple[str, str]] = {}
    ranks: dict[str, tuple[int, int]] = {}
    for fixture in fixtures:
        home_slot = _team_slot_code(fixture.home_team)
        away_slot = _team_slot_code(fixture.away_team)
        if not home_slot and not away_slot:
            continue
        rank = (-int(bool(home_slot)) - int(bool(away_slot)), _fixture_rank(fixture))
        current_rank = ranks.get(fixture.event_date)
        if current_rank is None or rank < current_rank:
            templates[fixture.event_date] = (home_slot, away_slot)
            ranks[fixture.event_date] = rank
    return templates


def _team_slot_code(team: TeamRef) -> str:
    return canonical_slot_code(team.fifa_code) or canonical_slot_code(team.key) or canonical_slot_code(team.name)


def _fixture_match_number(fixture: FixtureRecord) -> str:
    metadata_number = fixture.metadata.get("match_number")
    value = str(metadata_number or fixture.source_id or "").strip()
    return value if value.isdigit() else ""


def _result_match_number(result: ResultRecord) -> str:
    value = str(result.metadata.get("match_number") or "").strip()
    return value if value.isdigit() else ""


def _fixture_rank(fixture: FixtureRecord) -> int:
    return _source_rank(str(fixture.metadata.get("source") or fixture.source_id or ""))


def _normalized_results(results: list[ResultRecord], fixtures: list[FixtureRecord]) -> list[ResultRecord]:
    """Snap result observations onto the canonical fixture kickoff.

    Sources occasionally disagree on kickoff time for the same match. Fixture
    keys embed the timestamp, so without normalization those observations form
    separate consensus pools and can confirm a phantom duplicate fixture next
    to the real one.
    """

    fixtures_by_pair: dict[tuple[str, str], list[FixtureRecord]] = defaultdict(list)
    for fixture in fixtures:
        pair = _team_pair(fixture.home_team, fixture.away_team)
        if pair is not None:
            fixtures_by_pair[pair].append(fixture)
    window = dt.timedelta(hours=CONFIRMED_RESULT_KICKOFF_WINDOW_HOURS)
    normalized = []
    for result in results:
        pair = _team_pair(result.home_team, result.away_team)
        target = _closest_pair_fixture(fixtures_by_pair.get(pair, []) if pair else [], result.event_date, window=window)
        if target is not None and target.event_date != result.event_date:
            normalized.append(replace(result, event_date=target.event_date))
        else:
            normalized.append(result)
    return normalized


def _team_pair(home: TeamRef, away: TeamRef) -> tuple[str, str] | None:
    home_key = str(home.fifa_code or home.name or "").casefold()
    away_key = str(away.fifa_code or away.name or "").casefold()
    if not home_key or not away_key:
        return None
    return tuple(sorted((home_key, away_key)))


def _closest_pair_fixture(
    fixtures: list[FixtureRecord],
    event_date: str,
    *,
    window: dt.timedelta,
) -> FixtureRecord | None:
    try:
        event_at = parse_utc_datetime(event_date)
    except ValueError:
        return None
    if event_at is None:
        return None
    candidates = []
    for fixture in fixtures:
        try:
            fixture_at = parse_utc_datetime(fixture.event_date)
        except ValueError:
            continue
        if fixture_at is None:
            continue
        delta = abs(fixture_at - event_at)
        if delta <= window:
            candidates.append((_fixture_rank(fixture), delta, fixture.event_date, fixture))
    if not candidates:
        return None
    # The authoritative source's kickoff wins over the numerically closest one.
    return sorted(candidates, key=lambda item: item[:3])[0][3]


def _preferred_results(results: Iterable[ResultRecord], *, now: dt.datetime) -> list[ResultRecord]:
    by_fixture: dict[str, list[ResultRecord]] = defaultdict(list)
    for result in results:
        if not _result_is_due(result, now=now):
            continue
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
    if "fifa_match_centre" in source:
        return 1
    if "football_data" in source:
        return 2
    if "openfootball" in source:
        return 3
    if "srf_public" in source:
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
            # High-authority agreement beats any number of scraped votes.
            -len(_high_authority_sources(rows)),
            -len(_unique_sources(rows)),
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
    """Distinct source families supporting a score.

    Scraped families such as ``dynamic_public:<domain>`` share one extractor,
    so their domains are correlated witnesses and count as a single source.
    """

    return {_source_family(result.source) for result in results}


def _source_family(source: str) -> str:
    return str(source or "").split(":", 1)[0].strip()


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


def _normalize_now(now: dt.datetime | None) -> dt.datetime:
    if now is None:
        return dt.datetime.now(dt.timezone.utc)
    if now.tzinfo is None:
        return now.replace(tzinfo=dt.timezone.utc)
    return now.astimezone(dt.timezone.utc)


def _result_is_due(result: ResultRecord, *, now: dt.datetime) -> bool:
    try:
        event_at = parse_utc_datetime(result.event_date)
    except ValueError:
        return True
    if event_at is None:
        return True
    return event_at <= now


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
