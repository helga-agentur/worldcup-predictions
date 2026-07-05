"""FIFA public match-centre calendar source plugin."""

from __future__ import annotations

import datetime as dt
import urllib.error
from typing import Any

from worldcup_predictions.core.constants import (
    ENDPOINT_FIFA_CALENDAR_MATCHES,
    FIFA_WORLD_CUP_2026_SEASON_ID,
    FIFA_WORLD_CUP_COMPETITION_ID,
    SOURCE_FIFA_MATCH_CENTRE,
)
from worldcup_predictions.core.contracts import Artifact, Diagnostic, ScoreTip
from worldcup_predictions.core.datasets import FIFA_MATCH_DETAILS, TOURNAMENT_FIXTURES, TOURNAMENT_RESULTS
from worldcup_predictions.core.events import EventName, event_value
from worldcup_predictions.core.metadata import PluginKind, PluginMetadata, QuotaPolicy
from worldcup_predictions.core.plugin import BasePlugin, PluginResult
from worldcup_predictions.plugins.source_runtime import SourceRuntime
from worldcup_predictions.storage.ledger import SourceRequest, normalize_datetime
from worldcup_predictions.tournament import FixtureRecord, ResultRecord, TeamRef
from worldcup_predictions.tournament.repository import load_tournament_state, write_derived_state, write_fixtures, write_results
from worldcup_predictions.tournament.slots import slot_team_ref


class FifaMatchCentrePlugin(BasePlugin):
    """Fetch official FIFA World Cup 2026 match metadata from FIFA's public calendar API."""

    id = "fifa_match_centre"
    version = "0.1.0"
    priority = 118
    subscribed_events = (EventName.FIXTURES_REQUESTED.value,)
    metadata = PluginMetadata(
        plugin_id=id,
        kind=PluginKind.SOURCE,
        description="Fetch FIFA public match-centre calendar data for official fixtures, scores, formations, officials, venue, and attendance.",
        datasets_written=(TOURNAMENT_FIXTURES, TOURNAMENT_RESULTS, FIFA_MATCH_DETAILS),
        quota_policy=QuotaPolicy(
            quota_limited=False,
            ledger_required=True,
            description="FIFA's public calendar endpoint is refreshed through the source ledger; raw API responses are not cached.",
        ),
        confidence_policy="FIFA match-centre rows are high-authority fixture/result evidence. They enrich metadata and result consensus but do not provide player-level starting XI data.",
    )

    def handle(self, event, context, payload):
        runtime = SourceRuntime(self, event, context)
        if context.storage is None:
            return runtime.storage_unavailable_result("FIFA match-centre data")

        request = SourceRequest(
            source=SOURCE_FIFA_MATCH_CENTRE,
            endpoint=ENDPOINT_FIFA_CALENDAR_MATCHES,
            purpose="world_cup_2026_calendar_matches",
            params=_calendar_params(),
            quota_cost=0,
            min_refresh_interval=dt.timedelta(minutes=runtime.context.config.source_defaults.default_refresh_minutes),
            quota_scope=SOURCE_FIFA_MATCH_CENTRE,
            rate_limit_backoff=dt.timedelta(hours=6),
        )
        decision = runtime.should_fetch(request)
        if not decision.should_fetch:
            return runtime.skipped_fetch_result("FIFA match centre", decision.reason, metadata=decision.metadata)

        try:
            payload, _headers = runtime.fetch_json(ENDPOINT_FIFA_CALENDAR_MATCHES, dict(request.params))
        except (OSError, TimeoutError, urllib.error.HTTPError) as exc:
            runtime.record_error(request, exc)
            return runtime.result(
                diagnostics=[
                    runtime.diagnostic(
                        "warning",
                        "FIFA match-centre fetch failed; stored FIFA rows will be used.",
                        metadata={"error": str(exc)},
                    )
                ]
            )

        matches = list(payload.get("Results") or []) if isinstance(payload, dict) else []
        fixtures = parse_fifa_match_fixtures(matches)
        results = parse_fifa_match_results(matches)
        detail_rows = parse_fifa_match_details(matches)

        fixture_count = write_fixtures(runtime.storage, fixtures, source=SOURCE_FIFA_MATCH_CENTRE, run_id=runtime.context.run_id)
        result_count = write_results(runtime.storage, results, source=SOURCE_FIFA_MATCH_CENTRE, run_id=runtime.context.run_id)
        detail_count = runtime.write_records(FIFA_MATCH_DETAILS, detail_rows)
        refreshed_state = load_tournament_state(runtime.storage)
        write_derived_state(runtime.storage, refreshed_state, run_id=runtime.context.run_id)
        runtime.context.state["tournament_state"] = refreshed_state
        runtime.record_success(
            request,
            message="Fetched FIFA World Cup 2026 match-centre calendar data.",
            metadata={"matches": len(matches), "fixtures": fixture_count, "results": result_count, "details": detail_count},
        )
        diagnostics = []
        if detail_rows and not any(row.get("home_tactics") or row.get("away_tactics") for row in detail_rows):
            diagnostics.append(
                runtime.diagnostic(
                    "info",
                    "FIFA match-centre data returned no formation/tactics fields in this run.",
                    metadata={"detail_rows": len(detail_rows)},
                )
            )
        return PluginResult(
            plugin_id=self.id,
            event=event_value(event),
            artifacts=[
                Artifact(TOURNAMENT_FIXTURES, "structured_dataset", self.id, data={"rows_written": fixture_count}),
                Artifact(TOURNAMENT_RESULTS, "structured_dataset", self.id, data={"rows_written": result_count}),
                Artifact(FIFA_MATCH_DETAILS, "structured_dataset", self.id, data={"rows_written": detail_count}),
            ],
            diagnostics=diagnostics,
            metadata={"matches": len(matches), "fixtures": fixture_count, "results": result_count, "details": detail_count},
        )


def parse_fifa_match_fixtures(matches: list[dict[str, Any]]) -> list[FixtureRecord]:
    fixtures = []
    for match in matches:
        date = normalize_datetime(match.get("Date"))
        home = _team_ref(match.get("Home"), placeholder=match.get("PlaceHolderA"))
        away = _team_ref(match.get("Away"), placeholder=match.get("PlaceHolderB"))
        if not date or home is None or away is None:
            continue
        fixtures.append(
            FixtureRecord(
                event_date=date,
                home_team=home,
                away_team=away,
                stage=_localized(match.get("StageName")),
                group=_localized(match.get("GroupName")),
                matchday=_optional_int(match.get("MatchDay")),
                source_id=str(match.get("IdMatch") or ""),
                venue=_venue_name(match),
                status=_fixture_status(match),
                metadata=_fixture_metadata(match),
            )
        )
    return fixtures


def parse_fifa_match_results(matches: list[dict[str, Any]]) -> list[ResultRecord]:
    results = []
    for match in matches:
        date = normalize_datetime(match.get("Date"))
        home = _team_ref(match.get("Home"))
        away = _team_ref(match.get("Away"))
        home_score = _optional_int(match.get("HomeTeamScore"))
        away_score = _optional_int(match.get("AwayTeamScore"))
        if not _is_finished(match) or not date or home is None or away is None or home_score is None or away_score is None:
            continue
        results.append(
            ResultRecord(
                event_date=date,
                home_team=home,
                away_team=away,
                score=ScoreTip(home_score, away_score),
                source=SOURCE_FIFA_MATCH_CENTRE,
                status="final",
                notes="FIFA public match-centre calendar result.",
                metadata={
                    "fifa_match_id": match.get("IdMatch"),
                    "match_number": match.get("MatchNumber"),
                    "officiality_status": match.get("OfficialityStatus"),
                    "match_status": match.get("MatchStatus"),
                    "winner_id": match.get("Winner"),
                    "home_penalty_score": match.get("HomeTeamPenaltyScore"),
                    "away_penalty_score": match.get("AwayTeamPenaltyScore"),
                },
            )
        )
    return results


def parse_fifa_match_details(matches: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows = []
    for match in matches:
        date = normalize_datetime(match.get("Date"))
        home = _team_ref(match.get("Home"), placeholder=match.get("PlaceHolderA"))
        away = _team_ref(match.get("Away"), placeholder=match.get("PlaceHolderB"))
        if not date or home is None or away is None:
            continue
        fixture_key = FixtureRecord(event_date=date, home_team=home, away_team=away).key
        possession = _possession(match)
        rows.append(
            {
                "record_key": f"{fixture_key}:fifa_match_centre",
                "fixture_key": fixture_key,
                "event_date": date,
                "fifa_match_id": str(match.get("IdMatch") or ""),
                "fifa_competition_id": str(match.get("IdCompetition") or ""),
                "fifa_season_id": str(match.get("IdSeason") or ""),
                "fifa_stage_id": str(match.get("IdStage") or ""),
                "fifa_group_id": str(match.get("IdGroup") or ""),
                "match_number": match.get("MatchNumber"),
                "match_status": match.get("MatchStatus"),
                "officiality_status": match.get("OfficialityStatus"),
                "result_type": match.get("ResultType"),
                "match_time": match.get("MatchTime"),
                "home_team": home.name,
                "away_team": away.name,
                "home_fifa_code": home.fifa_code,
                "away_fifa_code": away.fifa_code,
                "home_score": _optional_int(match.get("HomeTeamScore")),
                "away_score": _optional_int(match.get("AwayTeamScore")),
                "home_tactics": _team_value(match.get("Home"), "Tactics"),
                "away_tactics": _team_value(match.get("Away"), "Tactics"),
                "home_possession": possession.get("home"),
                "away_possession": possession.get("away"),
                "attendance": _optional_int(match.get("Attendance")),
                "stadium": _venue_name(match),
                "city": _localized((match.get("Stadium") or {}).get("CityName")),
                "stadium_country": (match.get("Stadium") or {}).get("IdCountry"),
                "referee": _official_name(match, official_type=1),
                "fourth_official": _official_name(match, official_type=4),
                "var": _official_name(match, official_type=5),
                "weather_type": _localized((match.get("Weather") or {}).get("TypeLocalized")),
                "weather_temperature": _optional_float((match.get("Weather") or {}).get("Temperature")),
                "weather_humidity": _optional_float((match.get("Weather") or {}).get("Humidity")),
                "weather_wind_speed": _optional_float((match.get("Weather") or {}).get("WindSpeed")),
                "metadata": {
                    "stage": _localized(match.get("StageName")),
                    "group": _localized(match.get("GroupName")),
                    "competition": _localized(match.get("CompetitionName")),
                    "season": _localized(match.get("SeasonName")),
                    "local_date": normalize_datetime(match.get("LocalDate")),
                    "home_fifa_team_id": _team_value(match.get("Home"), "IdTeam"),
                    "away_fifa_team_id": _team_value(match.get("Away"), "IdTeam"),
                    "home_association": _team_value(match.get("Home"), "IdAssociation"),
                    "away_association": _team_value(match.get("Away"), "IdAssociation"),
                    "officials": _officials(match),
                    "source_url": _match_url(match),
                },
            }
        )
    return rows


def _calendar_params() -> dict[str, Any]:
    return {
        "language": "en",
        "count": 200,
        "idCompetition": FIFA_WORLD_CUP_COMPETITION_ID,
        "idSeason": FIFA_WORLD_CUP_2026_SEASON_ID,
    }


def _team_ref(raw: Any, *, placeholder: Any = None) -> TeamRef | None:
    if not isinstance(raw, dict):
        slot = slot_team_ref(placeholder)
        return slot
    code = str(raw.get("IdCountry") or raw.get("Abbreviation") or "").strip().upper()
    name = _localized(raw.get("TeamName")) or str(raw.get("ShortClubName") or code).strip()
    if not name:
        return None
    slot = slot_team_ref(code) or slot_team_ref(name)
    if slot is not None:
        return slot
    return TeamRef(name=name, fifa_code=code or None)


def _localized(values: Any, locale: str = "en-GB") -> str | None:
    if not isinstance(values, list):
        return None
    fallback = None
    for value in values:
        if not isinstance(value, dict):
            continue
        description = value.get("Description")
        if not description:
            continue
        fallback = str(description)
        if str(value.get("Locale") or "").casefold() == locale.casefold():
            return fallback
    return fallback


def _fixture_status(match: dict[str, Any]) -> str:
    if _is_finished(match):
        return "finished"
    if _optional_int(match.get("HomeTeamScore")) is not None and _optional_int(match.get("AwayTeamScore")) is not None:
        return "observed"
    return "scheduled"


def _is_finished(match: dict[str, Any]) -> bool:
    return (
        _optional_int(match.get("MatchStatus")) == 0
        and _optional_int(match.get("HomeTeamScore")) is not None
        and _optional_int(match.get("AwayTeamScore")) is not None
    )


def _fixture_metadata(match: dict[str, Any]) -> dict[str, Any]:
    possession = _possession(match)
    return {
        "source": SOURCE_FIFA_MATCH_CENTRE,
        "fifa_match_id": match.get("IdMatch"),
        "fifa_competition_id": match.get("IdCompetition"),
        "fifa_season_id": match.get("IdSeason"),
        "match_number": match.get("MatchNumber"),
        "officiality_status": match.get("OfficialityStatus"),
        "home_tactics": _team_value(match.get("Home"), "Tactics"),
        "away_tactics": _team_value(match.get("Away"), "Tactics"),
        "home_possession": possession.get("home"),
        "away_possession": possession.get("away"),
        "attendance": _optional_int(match.get("Attendance")),
    }


def _possession(match: dict[str, Any]) -> dict[str, float | None]:
    possession = match.get("BallPossession") or {}
    return {
        "home": _optional_float(possession.get("OverallHome")),
        "away": _optional_float(possession.get("OverallAway")),
    }


def _venue_name(match: dict[str, Any]) -> str | None:
    return _localized((match.get("Stadium") or {}).get("Name"))


def _team_value(raw: Any, key: str) -> Any:
    return raw.get(key) if isinstance(raw, dict) else None


def _officials(match: dict[str, Any]) -> list[dict[str, Any]]:
    officials = []
    for official in match.get("Officials") or []:
        if not isinstance(official, dict):
            continue
        officials.append(
            {
                "official_id": official.get("OfficialId"),
                "country": official.get("IdCountry"),
                "type": official.get("OfficialType"),
                "type_label": _localized(official.get("TypeLocalized")),
                "name": _localized(official.get("Name")),
                "short_name": _localized(official.get("NameShort")),
            }
        )
    return officials


def _official_name(match: dict[str, Any], *, official_type: int) -> str | None:
    for official in _officials(match):
        if official.get("type") == official_type:
            return official.get("name") or official.get("short_name")
    return None


def _match_url(match: dict[str, Any]) -> str | None:
    match_id = match.get("IdMatch")
    competition_id = match.get("IdCompetition")
    season_id = match.get("IdSeason")
    stage_id = match.get("IdStage")
    if not all((match_id, competition_id, season_id, stage_id)):
        return None
    return f"https://www.fifa.com/en/match-centre/match/{competition_id}/{season_id}/{stage_id}/{match_id}"


def _optional_int(value: Any) -> int | None:
    if value in (None, ""):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _optional_float(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
