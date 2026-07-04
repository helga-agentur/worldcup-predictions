"""football-data.org fixture, result, team, and squad source plugin."""

from __future__ import annotations

import datetime as dt
import json
import urllib.error
from typing import Any

from worldcup_predictions.core.constants import (
    ENDPOINT_FOOTBALL_DATA_COMPETITION,
    ENV_FOOTBALL_DATA_API_KEY,
    SOURCE_FOOTBALL_DATA,
)
from worldcup_predictions.core.contracts import Artifact, Diagnostic, ScoreTip
from worldcup_predictions.core.datasets import (
    FOOTBALL_DATA_COMPETITION,
    FOOTBALL_DATA_MATCH_DETAILS,
    FOOTBALL_DATA_STANDINGS,
    FOOTBALL_DATA_TEAMS,
    SQUAD_PLAYERS,
    TOURNAMENT_FIXTURES,
    TOURNAMENT_RESULTS,
)
from worldcup_predictions.core.events import EventName, event_value
from worldcup_predictions.core.metadata import EnvVar, PluginKind, PluginMetadata, QuotaPolicy
from worldcup_predictions.core.plugin import BasePlugin, PluginResult
from worldcup_predictions.plugins.source_runtime import SourceRuntime
from worldcup_predictions.storage.ledger import SourceRequest, normalize_datetime, stable_hash
from worldcup_predictions.tournament import FixtureRecord, ResultRecord, TeamRef, TeamResolver
from worldcup_predictions.tournament.repository import load_tournament_state, write_derived_state, write_fixtures, write_results


class FootballDataPlugin(BasePlugin):
    """Fetch structured World Cup metadata from football-data.org."""

    id = "football_data"
    version = "0.1.0"
    priority = 120
    subscribed_events = (EventName.FIXTURES_REQUESTED.value, EventName.FEATURE_SIGNALS_REQUESTED.value)
    metadata = PluginMetadata(
        plugin_id=id,
        kind=PluginKind.SOURCE,
        description="Fetch football-data.org World Cup competition, standings, fixtures, results, teams, squads, and match details.",
        datasets_read=(
            TOURNAMENT_FIXTURES,
            TOURNAMENT_RESULTS,
            FOOTBALL_DATA_COMPETITION,
            FOOTBALL_DATA_STANDINGS,
            FOOTBALL_DATA_TEAMS,
            FOOTBALL_DATA_MATCH_DETAILS,
            SQUAD_PLAYERS,
        ),
        datasets_written=(
            TOURNAMENT_FIXTURES,
            TOURNAMENT_RESULTS,
            FOOTBALL_DATA_COMPETITION,
            FOOTBALL_DATA_STANDINGS,
            FOOTBALL_DATA_TEAMS,
            FOOTBALL_DATA_MATCH_DETAILS,
            SQUAD_PLAYERS,
        ),
        env_vars=(EnvVar(ENV_FOOTBALL_DATA_API_KEY, required=False, description="football-data.org API token."),),
        quota_policy=QuotaPolicy(
            quota_limited=True,
            ledger_required=True,
            description="Competition endpoints are skipped while fresh or when the stored quota floor has been reached.",
        ),
        confidence_policy="football-data.org rows enrich canonical tournament state; as a high-authority result source they can confirm scores only through the central source-consensus policy.",
    )

    def handle(self, event, context, payload):
        runtime = SourceRuntime(self, event, context)
        if context.storage is None:
            return runtime.storage_unavailable_result("football-data.org")
        api_key = runtime.env_value(ENV_FOOTBALL_DATA_API_KEY)
        if not api_key:
            return runtime.result(
                diagnostics=[
                    runtime.diagnostic(
                        "info",
                        f"{ENV_FOOTBALL_DATA_API_KEY} is not configured; stored football-data.org rows will be used.",
                    )
                ],
                artifacts=[
                    runtime.structured_artifact(FOOTBALL_DATA_TEAMS),
                    runtime.structured_artifact(SQUAD_PLAYERS),
                ],
            )

        diagnostics: list[Diagnostic] = []
        fixture_count = 0
        result_count = 0
        team_count = 0
        player_count = 0
        competition_count = 0
        standings_count = 0
        match_detail_count = 0

        if event_value(event) == EventName.FIXTURES_REQUESTED.value:
            competition_result = self._fetch_competition(runtime)
            diagnostics.extend(competition_result.diagnostics)
            competition_count += int(competition_result.metadata.get("competition") or 0)
            match_result = self._fetch_matches(runtime)
            diagnostics.extend(match_result.diagnostics)
            fixture_count += int(match_result.metadata.get("fixtures") or 0)
            result_count += int(match_result.metadata.get("results") or 0)
        if event_value(event) == EventName.FEATURE_SIGNALS_REQUESTED.value:
            standings_result = self._fetch_standings(runtime)
            diagnostics.extend(standings_result.diagnostics)
            standings_count += int(standings_result.metadata.get("standings") or 0)
            team_result = self._fetch_teams(runtime)
            diagnostics.extend(team_result.diagnostics)
            team_count += int(team_result.metadata.get("teams") or 0)
            player_count += int(team_result.metadata.get("players") or 0)
            detail_result = self._fetch_match_details(runtime)
            diagnostics.extend(detail_result.diagnostics)
            match_detail_count += int(detail_result.metadata.get("match_details") or 0)

        artifacts = [
            Artifact(TOURNAMENT_FIXTURES, "structured_dataset", self.id, data={"rows_written": fixture_count}),
            Artifact(TOURNAMENT_RESULTS, "structured_dataset", self.id, data={"rows_written": result_count}),
            Artifact(FOOTBALL_DATA_COMPETITION, "structured_dataset", self.id, data={"rows_written": competition_count}),
            Artifact(FOOTBALL_DATA_STANDINGS, "structured_dataset", self.id, data={"rows_written": standings_count}),
            Artifact(FOOTBALL_DATA_TEAMS, "structured_dataset", self.id, data={"rows_written": team_count}),
            Artifact(FOOTBALL_DATA_MATCH_DETAILS, "structured_dataset", self.id, data={"rows_written": match_detail_count}),
            Artifact(SQUAD_PLAYERS, "structured_dataset", self.id, data={"rows_written": player_count}),
        ]
        return PluginResult(
            plugin_id=self.id,
            event=event_value(event),
            artifacts=artifacts,
            diagnostics=diagnostics,
            metadata={
                "fixtures": fixture_count,
                "results": result_count,
                "competition": competition_count,
                "standings": standings_count,
                "teams": team_count,
                "players": player_count,
                "match_details": match_detail_count,
            },
        )

    def _fetch_competition(self, runtime: SourceRuntime) -> PluginResult:
        api_key = runtime.env_value(ENV_FOOTBALL_DATA_API_KEY)
        if not api_key:
            return runtime.result()
        endpoint = ENDPOINT_FOOTBALL_DATA_COMPETITION
        request = SourceRequest(
            source=SOURCE_FOOTBALL_DATA,
            endpoint=endpoint,
            purpose="world_cup_competition_metadata",
            params={},
            quota_cost=1,
            min_refresh_interval=dt.timedelta(days=1),
        )
        decision = runtime.should_fetch(request)
        if not decision.should_fetch:
            return runtime.skipped_fetch_result("football-data.org competition", decision.reason, metadata=decision.metadata)
        try:
            payload, headers = runtime.fetch_json(endpoint, headers={"X-Auth-Token": api_key})
        except (OSError, TimeoutError, urllib.error.HTTPError, json.JSONDecodeError) as exc:
            runtime.record_error(request, exc)
            return runtime.result(diagnostics=[runtime.diagnostic("warning", "football-data.org competition fetch failed.", metadata={"error": str(exc)})])
        rows = parse_football_data_competition(payload)
        count = runtime.write_records(FOOTBALL_DATA_COMPETITION, rows)
        runtime.record_success(request, message="Fetched football-data.org competition metadata.", metadata={"rows": count}, quota_remaining=quota_remaining(headers))
        return runtime.result(metadata={"competition": count})

    def _fetch_matches(self, runtime: SourceRuntime) -> PluginResult:
        api_key = runtime.env_value(ENV_FOOTBALL_DATA_API_KEY)
        if not api_key:
            return runtime.result()
        endpoint = f"{ENDPOINT_FOOTBALL_DATA_COMPETITION}/matches"
        request = SourceRequest(
            source=SOURCE_FOOTBALL_DATA,
            endpoint=endpoint,
            purpose="world_cup_matches",
            params={"season": 2026},
            quota_cost=1,
            min_refresh_interval=dt.timedelta(minutes=runtime.context.config.source_defaults.default_refresh_minutes),
        )
        decision = runtime.should_fetch(request)
        if not decision.should_fetch:
            return runtime.skipped_fetch_result("football-data.org matches", decision.reason, metadata=decision.metadata)
        try:
            payload, headers = runtime.fetch_json(endpoint, {"season": 2026}, headers={"X-Auth-Token": api_key})
        except (OSError, TimeoutError, urllib.error.HTTPError, json.JSONDecodeError) as exc:
            runtime.record_error(request, exc)
            return runtime.result(diagnostics=[runtime.diagnostic("warning", "football-data.org match fetch failed.", metadata={"error": str(exc)})])

        fixtures, results = parse_football_data_matches(payload)
        fixture_count = write_fixtures(runtime.storage, fixtures, source=self.id, run_id=runtime.context.run_id)
        result_count = write_results(runtime.storage, results, source=self.id, run_id=runtime.context.run_id)
        state = load_tournament_state(runtime.storage)
        write_derived_state(runtime.storage, state, run_id=runtime.context.run_id)
        runtime.context.state["tournament_state"] = state
        runtime.record_success(
            request,
            message="Fetched football-data.org matches.",
            metadata={"fixtures": fixture_count, "results": result_count},
            quota_remaining=quota_remaining(headers),
        )
        return runtime.result(metadata={"fixtures": fixture_count, "results": result_count})

    def _fetch_teams(self, runtime: SourceRuntime) -> PluginResult:
        api_key = runtime.env_value(ENV_FOOTBALL_DATA_API_KEY)
        if not api_key:
            return runtime.result()
        endpoint = f"{ENDPOINT_FOOTBALL_DATA_COMPETITION}/teams"
        request = SourceRequest(
            source=SOURCE_FOOTBALL_DATA,
            endpoint=endpoint,
            purpose="world_cup_teams",
            params={"season": 2026},
            quota_cost=1,
            min_refresh_interval=dt.timedelta(minutes=runtime.context.config.source_defaults.default_refresh_minutes),
        )
        decision = runtime.should_fetch(request)
        if not decision.should_fetch:
            return runtime.skipped_fetch_result("football-data.org teams", decision.reason, metadata=decision.metadata)
        try:
            payload, headers = runtime.fetch_json(endpoint, {"season": 2026}, headers={"X-Auth-Token": api_key})
        except (OSError, TimeoutError, urllib.error.HTTPError, json.JSONDecodeError) as exc:
            runtime.record_error(request, exc)
            return runtime.result(diagnostics=[runtime.diagnostic("warning", "football-data.org teams fetch failed.", metadata={"error": str(exc)})])

        teams, squad_players = parse_football_data_teams(payload)
        team_count = runtime.write_records(FOOTBALL_DATA_TEAMS, teams)
        player_count = runtime.write_records(SQUAD_PLAYERS, squad_players)
        runtime.record_success(
            request,
            message="Fetched football-data.org teams.",
            metadata={"teams": team_count, "players": player_count},
            quota_remaining=quota_remaining(headers),
        )
        return runtime.result(metadata={"teams": team_count, "players": player_count})

    def _fetch_standings(self, runtime: SourceRuntime) -> PluginResult:
        api_key = runtime.env_value(ENV_FOOTBALL_DATA_API_KEY)
        if not api_key:
            return runtime.result()
        endpoint = f"{ENDPOINT_FOOTBALL_DATA_COMPETITION}/standings"
        request = SourceRequest(
            source=SOURCE_FOOTBALL_DATA,
            endpoint=endpoint,
            purpose="world_cup_standings",
            params={"season": 2026},
            quota_cost=1,
            min_refresh_interval=dt.timedelta(minutes=runtime.context.config.source_defaults.default_refresh_minutes),
        )
        decision = runtime.should_fetch(request)
        if not decision.should_fetch:
            return runtime.skipped_fetch_result("football-data.org standings", decision.reason, metadata=decision.metadata)
        try:
            payload, headers = runtime.fetch_json(endpoint, {"season": 2026}, headers={"X-Auth-Token": api_key})
        except (OSError, TimeoutError, urllib.error.HTTPError, json.JSONDecodeError) as exc:
            runtime.record_error(request, exc)
            return runtime.result(diagnostics=[runtime.diagnostic("warning", "football-data.org standings fetch failed.", metadata={"error": str(exc)})])
        rows = parse_football_data_standings(payload)
        count = runtime.write_records(FOOTBALL_DATA_STANDINGS, rows)
        runtime.record_success(request, message="Fetched football-data.org standings.", metadata={"rows": count}, quota_remaining=quota_remaining(headers))
        return runtime.result(metadata={"standings": count})

    def _fetch_match_details(self, runtime: SourceRuntime) -> PluginResult:
        api_key = runtime.env_value(ENV_FOOTBALL_DATA_API_KEY)
        if not api_key:
            return runtime.result()
        state = runtime.tournament_state()
        # Reconciled fixtures can carry another source's match id (FIFA ids were
        # sent to the v4 matches endpoint and answered HTTP 400 forever), so
        # details are fetched only with ids from this plugin's own fixture rows.
        own_match_ids = self._own_match_ids(runtime)
        candidates = []
        unmapped = 0
        for fixture in [*state.open_fixtures(), *_recent_finished_fixtures(state)]:
            match_id = own_match_ids.get(fixture.key)
            if match_id:
                candidates.append((fixture, match_id))
            else:
                unmapped += 1
        # Open fixtures (kickoff-ordered) come first, and each detail fetch is
        # ledger-gated so already-fresh fixtures cost no quota; this per-run cap only
        # bounds bursts against the rate-limited free tier. Raised from 12 so a busy
        # match day's open set is covered across consecutive hourly runs.
        fetch_limit = 16
        written = 0
        diagnostics: list[Diagnostic] = []
        for fixture, match_id in candidates[:fetch_limit]:
            endpoint = f"https://api.football-data.org/v4/matches/{match_id}"
            request = SourceRequest(
                source=SOURCE_FOOTBALL_DATA,
                endpoint=endpoint,
                purpose="world_cup_match_detail",
                params={"match_id": match_id},
                fixture_key=fixture.key,
                quota_cost=1,
                min_refresh_interval=dt.timedelta(minutes=runtime.context.config.source_defaults.default_refresh_minutes),
            )
            decision = runtime.should_fetch(request)
            if not decision.should_fetch:
                diagnostics.extend(runtime.skipped_fetch_result("football-data.org match detail", decision.reason, fixture_key=fixture.key, metadata=decision.metadata).diagnostics)
                continue
            try:
                payload, headers = runtime.fetch_json(endpoint, headers={"X-Auth-Token": api_key})
            except (OSError, TimeoutError, urllib.error.HTTPError, json.JSONDecodeError) as exc:
                runtime.record_error(request, exc)
                diagnostics.append(runtime.diagnostic("warning", "football-data.org match-detail fetch failed.", fixture_key=fixture.key, metadata={"error": str(exc)}))
                continue
            rows = parse_football_data_match_detail(payload, fixture)
            count = runtime.write_records(FOOTBALL_DATA_MATCH_DETAILS, rows)
            runtime.record_success(request, message="Fetched football-data.org match detail.", metadata={"rows": count}, quota_remaining=quota_remaining(headers))
            written += count
        return runtime.result(diagnostics=diagnostics, metadata={"match_details": written, "unmapped_fixtures": unmapped})

    def _own_match_ids(self, runtime: SourceRuntime) -> dict[str, str]:
        """Map fixture keys to football-data.org match ids from this plugin's rows."""

        match_ids: dict[str, str] = {}
        for row in runtime.storage.read_records(TOURNAMENT_FIXTURES, source=self.id, latest_only=True):
            fixture_key = str(row.get("fixture_key") or row.get("record_key") or "")
            source_id = str(row.get("source_id") or "")
            if fixture_key and source_id:
                match_ids[fixture_key] = source_id
        return match_ids


def parse_football_data_matches(payload: dict[str, Any]) -> tuple[list[FixtureRecord], list[ResultRecord]]:
    resolver = TeamResolver.default(source=SOURCE_FOOTBALL_DATA)
    fixtures: list[FixtureRecord] = []
    results: list[ResultRecord] = []
    for match in payload.get("matches") or []:
        home = resolve_football_data_team(match.get("homeTeam") or {}, resolver)
        away = resolve_football_data_team(match.get("awayTeam") or {}, resolver)
        event_date = normalize_datetime(match.get("utcDate")) or str(match.get("utcDate") or "")
        if not event_date or not home.name or not away.name:
            continue
        score = match.get("score") or {}
        full_time = score.get("fullTime") or {}
        fixture = FixtureRecord(
            event_date=event_date,
            home_team=home,
            away_team=away,
            stage=match.get("stage"),
            group=match.get("group"),
            matchday=_optional_int(match.get("matchday")),
            source_id=str(match.get("id") or "") or None,
            venue=match.get("venue"),
            status=str(match.get("status") or "scheduled").lower(),
            metadata={"competition": "WC", "winner": score.get("winner")},
        )
        fixtures.append(fixture)
        home_score = _optional_int(full_time.get("home"))
        away_score = _optional_int(full_time.get("away"))
        if home_score is not None and away_score is not None and str(match.get("status") or "").upper() in {"FINISHED", "AWARDED"}:
            results.append(
                ResultRecord(
                    event_date=event_date,
                    home_team=home,
                    away_team=away,
                    score=ScoreTip(home_score, away_score),
                    source=SOURCE_FOOTBALL_DATA,
                    metadata={"match_id": match.get("id"), "winner": score.get("winner")},
                )
            )
    return fixtures, results


def parse_football_data_competition(payload: dict[str, Any]) -> list[dict[str, Any]]:
    if not isinstance(payload, dict) or not payload.get("id"):
        return []
    current_season = payload.get("currentSeason") or {}
    return [
        {
            "record_key": str(payload.get("id")),
            "competition_id": payload.get("id"),
            "code": payload.get("code"),
            "name": payload.get("name"),
            "type": payload.get("type"),
            "emblem": payload.get("emblem"),
            "current_season_start": current_season.get("startDate"),
            "current_season_end": current_season.get("endDate"),
            "current_matchday": current_season.get("currentMatchday"),
            "metadata": {
                "area": (payload.get("area") or {}).get("name"),
                "season_id": current_season.get("id"),
                "winner": current_season.get("winner"),
            },
        }
    ]


def parse_football_data_standings(payload: dict[str, Any]) -> list[dict[str, Any]]:
    resolver = TeamResolver.default(source=SOURCE_FOOTBALL_DATA)
    rows: list[dict[str, Any]] = []
    for standing in payload.get("standings") or []:
        group = standing.get("group")
        for item in standing.get("table") or []:
            team = resolve_football_data_team(item.get("team") or {}, resolver)
            rows.append(
                {
                    "record_key": stable_hash({"group": group, "team": team.key}),
                    "group": group,
                    "stage": standing.get("stage"),
                    "type": standing.get("type"),
                    "team": team.name,
                    "fifa_code": team.fifa_code,
                    "position": item.get("position"),
                    "played": item.get("playedGames"),
                    "won": item.get("won"),
                    "draw": item.get("draw"),
                    "lost": item.get("lost"),
                    "points": item.get("points"),
                    "goals_for": item.get("goalsFor"),
                    "goals_against": item.get("goalsAgainst"),
                    "goal_difference": item.get("goalDifference"),
                    "metadata": {"form": item.get("form")},
                }
            )
    return rows


def parse_football_data_match_detail(payload: dict[str, Any], fixture: FixtureRecord) -> list[dict[str, Any]]:
    match = payload.get("match") if isinstance(payload.get("match"), dict) else payload
    if not isinstance(match, dict):
        return []
    score = match.get("score") or {}
    full_time = score.get("fullTime") or {}
    home_lineup = _extract_lineup(match, "homeTeam")
    away_lineup = _extract_lineup(match, "awayTeam")
    return [
        {
            "record_key": f"{fixture.key}:football_data_detail",
            "fixture_key": fixture.key,
            "event_date": fixture.event_date,
            "source_match_id": match.get("id") or fixture.source_id,
            "status": match.get("status"),
            "home_team": fixture.home_team.name,
            "away_team": fixture.away_team.name,
            "home_fifa_code": fixture.home_team.fifa_code,
            "away_fifa_code": fixture.away_team.fifa_code,
            "home_score": full_time.get("home"),
            "away_score": full_time.get("away"),
            "winner": score.get("winner"),
            "home_lineup_count": len(home_lineup),
            "away_lineup_count": len(away_lineup),
            "home_lineup": home_lineup,
            "away_lineup": away_lineup,
            "metadata": {
                "stage": match.get("stage"),
                "group": match.get("group"),
                "matchday": match.get("matchday"),
                "last_updated": match.get("lastUpdated"),
            },
        }
    ]


def parse_football_data_teams(payload: dict[str, Any]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    resolver = TeamResolver.default(source=SOURCE_FOOTBALL_DATA)
    team_rows: list[dict[str, Any]] = []
    player_rows: list[dict[str, Any]] = []
    for raw_team in payload.get("teams") or []:
        team = resolve_football_data_team(raw_team, resolver)
        team_record_key = team.fifa_code or stable_hash({"team": team.name, "source_id": raw_team.get("id")})
        team_rows.append(
            {
                "record_key": team_record_key,
                "team": team.name,
                "fifa_code": team.fifa_code,
                "source_team_id": raw_team.get("id"),
                "tla": raw_team.get("tla"),
                "short_name": raw_team.get("shortName"),
                "area": (raw_team.get("area") or {}).get("name"),
                "coach": (raw_team.get("coach") or {}).get("name"),
                "squad_size": len(raw_team.get("squad") or []),
                "metadata": {"source": SOURCE_FOOTBALL_DATA},
            }
        )
        for player in raw_team.get("squad") or []:
            player_name = str(player.get("name") or "")
            if not player_name:
                continue
            player_rows.append(
                {
                    "record_key": stable_hash({"team": team_record_key, "player": player.get("id") or player_name}),
                    "team": team.name,
                    "fifa_code": team.fifa_code,
                    "player_name": player_name,
                    "source_player_id": player.get("id"),
                    "position": player.get("position"),
                    "date_of_birth": player.get("dateOfBirth"),
                    "nationality": player.get("nationality"),
                    "current_club_name": player.get("currentTeam", {}).get("name") if isinstance(player.get("currentTeam"), dict) else None,
                    "market_value_in_eur": None,
                    "match_score": 1.0,
                    "metadata": {"source": SOURCE_FOOTBALL_DATA},
                }
            )
    return team_rows, player_rows


def _extract_lineup(match: dict[str, Any], team_key: str) -> list[dict[str, Any]]:
    team = match.get(team_key) or {}
    candidates = []
    for key in ("lineup", "startingLineup", "squad"):
        value = team.get(key)
        if isinstance(value, list):
            candidates = value
            break
    rows = []
    for player in candidates:
        if isinstance(player, dict):
            rows.append(
                {
                    "name": player.get("name"),
                    "id": player.get("id"),
                    "position": player.get("position"),
                    "shirt_number": player.get("shirtNumber"),
                }
            )
    return rows


def resolve_football_data_team(raw_team: dict[str, Any], resolver: TeamResolver) -> TeamRef:
    for label in (raw_team.get("tla"), raw_team.get("name"), raw_team.get("shortName")):
        if label:
            resolved = resolver.resolve(str(label))
            if resolved.fifa_code:
                return resolved
    return resolver.resolve(str(raw_team.get("name") or raw_team.get("shortName") or raw_team.get("tla") or ""))


def quota_remaining(headers: dict[str, str]) -> int | None:
    for key in ("X-Requests-Available-Minute", "X-RequestsAvailable", "X-RateLimit-Remaining"):
        value = headers.get(key) or headers.get(key.lower())
        if value is not None:
            try:
                return int(value)
            except ValueError:
                return None
    return None


def _optional_int(value: Any) -> int | None:
    if value in (None, ""):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _recent_finished_fixtures(state, *, days: int = 3) -> list[FixtureRecord]:
    now = dt.datetime.now(dt.timezone.utc)
    result_keys = {result.fixture_key for result in state.results}
    fixtures = []
    for fixture in state.fixtures:
        kickoff = fixture.kickoff_at
        if fixture.key in result_keys and kickoff and now - dt.timedelta(days=days) <= kickoff <= now:
            fixtures.append(fixture)
    return sorted(fixtures, key=lambda item: item.event_date)
