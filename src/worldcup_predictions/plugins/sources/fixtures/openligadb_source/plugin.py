"""OpenLigaDB World Cup 2026 result source plugin.

OpenLigaDB publishes free JSON match data for the FIFA World Cup 2026 under
the ``wm2026`` league shortcut. This plugin fetches the season match list in
one call and stores finished ``Endergebnis`` scores as tournament result
observations. It is a consensus WITNESS: rows go through the shared
``tournament_results`` dataset so the central source-consensus policy decides
which scores are confirmed; the plugin never touches tournament state
directly.

Team names in the payload are German (``Mexiko``, ``Südafrika``), while
``shortName`` carries the FIFA three-letter code (``MEX``, ``RSA``). The
short code is resolved first through the country registry's code index; the
German team name is the fallback because the registry ships ``de`` locale
names and aliases for all 48 participants.
"""

from __future__ import annotations

import datetime as dt
import json
import urllib.error
from typing import Any

from worldcup_predictions.core.contracts import Artifact, ScoreTip
from worldcup_predictions.core.datasets import EXTRACTION_DIAGNOSTICS, TOURNAMENT_RESULTS
from worldcup_predictions.core.events import EventName
from worldcup_predictions.core.extraction import extraction_diagnostic_row
from worldcup_predictions.core.metadata import PluginKind, PluginMetadata, QuotaPolicy
from worldcup_predictions.core.plugin import BasePlugin, PluginResult
from worldcup_predictions.plugins.source_runtime import SourceRuntime
from worldcup_predictions.storage.ledger import SourceRequest
from worldcup_predictions.tournament import ResultRecord, TeamRef, TeamResolver
from worldcup_predictions.tournament.repository import load_tournament_state, write_derived_state, write_results


SOURCE_OPENLIGADB = "openligadb"
OPENLIGADB_LEAGUE_SHORTCUT = "wm2026"
OPENLIGADB_SEASON = "2026"
ENDPOINT_OPENLIGADB_MATCHDATA = (
    f"https://api.openligadb.de/getmatchdata/{OPENLIGADB_LEAGUE_SHORTCUT}/{OPENLIGADB_SEASON}"
)
OPENLIGADB_EXTRACTOR = "openligadb_getmatchdata_v1"

# OpenLigaDB result-entry names. ``Endergebnis`` is the final score of the
# match (including extra time when it was played); ``Verlängerung`` and
# ``Elfmeterschießen`` entries appear only for knockout matches that needed
# them and are kept as row metadata, matching how the other result witnesses
# carry the final score in the row contract itself.
RESULT_NAME_FINAL = "endergebnis"
RESULT_NAME_EXTRA_TIME_MARKER = "verläng"
RESULT_NAME_PENALTY_MARKERS = ("elfmeter", "penalt")


class OpenLigaDbSourcePlugin(BasePlugin):
    """Fetch OpenLigaDB World Cup 2026 matches as result-confirmation witnesses."""

    id = "openligadb_source"
    version = "0.1.0"
    priority = 115
    subscribed_events = (EventName.FIXTURES_REQUESTED.value,)
    metadata = PluginMetadata(
        plugin_id=id,
        kind=PluginKind.SOURCE,
        description="Fetch OpenLigaDB World Cup 2026 match data and store finished scores as result observations.",
        datasets_written=(TOURNAMENT_RESULTS, EXTRACTION_DIAGNOSTICS),
        quota_policy=QuotaPolicy(
            quota_limited=False,
            ledger_required=True,
            description="One public getmatchdata call per run, refreshed through the source ledger every 30 minutes.",
        ),
        confidence_policy="OpenLigaDB rows are independent result witnesses; scores enter tournament state only after the central source-consensus policy confirms them.",
    )

    def handle(self, event, context, payload):
        runtime = SourceRuntime(self, event, context)
        if context.storage is None:
            return runtime.storage_unavailable_result("OpenLigaDB match data")

        request = SourceRequest(
            source=SOURCE_OPENLIGADB,
            endpoint=ENDPOINT_OPENLIGADB_MATCHDATA,
            purpose="world_cup_match_data",
            params={},
            quota_cost=0,
            min_refresh_interval=dt.timedelta(minutes=30),
            quota_scope=SOURCE_OPENLIGADB,
            rate_limit_backoff=dt.timedelta(hours=6),
        )
        decision = runtime.should_fetch(request)
        if not decision.should_fetch:
            return runtime.skipped_fetch_result("OpenLigaDB match data", decision.reason, metadata=decision.metadata)
        try:
            payload_json, _headers = runtime.fetch_json(ENDPOINT_OPENLIGADB_MATCHDATA)
        except (OSError, TimeoutError, urllib.error.HTTPError, json.JSONDecodeError) as exc:
            runtime.record_error(request, exc)
            return runtime.result(
                diagnostics=[
                    runtime.diagnostic(
                        "warning",
                        "OpenLigaDB match-data fetch failed; stored tournament rows will be used.",
                        metadata={"error": str(exc)},
                    )
                ]
            )

        if isinstance(payload_json, list) and not payload_json:
            # An empty array is a valid answer while the league is not
            # populated yet; it is a successful probe, not an extraction
            # failure.
            runtime.record_success(
                request,
                message="OpenLigaDB league is not populated yet.",
                metadata={"matches": 0, "results": 0, "empty_match_list": True},
            )
            return runtime.result(
                artifacts=[
                    Artifact(TOURNAMENT_RESULTS, "structured_dataset", self.id, data={"rows_written": 0}),
                    Artifact(EXTRACTION_DIAGNOSTICS, "structured_dataset", self.id, data={"rows_written": 0}),
                ],
                metadata={"matches": 0, "results": 0, "extraction_rows": 0, "empty_match_list": True},
            )

        results, extraction_rows = parse_openligadb_matches(payload_json)
        result_count = write_results(runtime.storage, results, source=self.id, run_id=runtime.context.run_id)
        extraction_count = runtime.write_records(EXTRACTION_DIAGNOSTICS, extraction_rows)
        if result_count:
            state = load_tournament_state(runtime.storage)
            write_derived_state(runtime.storage, state, run_id=runtime.context.run_id)
            runtime.context.state["tournament_state"] = state
        match_count = len(payload_json) if isinstance(payload_json, list) else 0
        runtime.record_success(
            request,
            message="Fetched OpenLigaDB World Cup match data.",
            metadata={"matches": match_count, "results": result_count, "extraction_rows": extraction_count},
        )
        return runtime.result(
            artifacts=[
                Artifact(TOURNAMENT_RESULTS, "structured_dataset", self.id, data={"rows_written": result_count}),
                Artifact(EXTRACTION_DIAGNOSTICS, "structured_dataset", self.id, data={"rows_written": extraction_count}),
            ],
            metadata={"matches": match_count, "results": result_count, "extraction_rows": extraction_count},
        )


def parse_openligadb_matches(payload: Any) -> tuple[list[ResultRecord], list[dict[str, Any]]]:
    """Extract finished-score result rows plus extraction diagnostics.

    Callers handle the empty-array case separately (a not-yet-populated league
    is a success); this parser only reports a zero-usable-rows diagnostic for
    non-empty payloads that yielded no result rows.
    """

    matches = payload if isinstance(payload, list) else None
    results: list[ResultRecord] = []
    extraction_rows: list[dict[str, Any]] = []
    if matches is None:
        extraction_rows.append(
            extraction_diagnostic_row(
                source=SOURCE_OPENLIGADB,
                extractor=OPENLIGADB_EXTRACTOR,
                status="rejected",
                reason="no_matches_extracted",
                severity="warning",
                metadata={"payload_keys": sorted(payload) if isinstance(payload, dict) else [type(payload).__name__]},
            )
        )
        return results, extraction_rows
    if not matches:
        return results, extraction_rows

    resolver = TeamResolver.default(locale="de", source=SOURCE_OPENLIGADB)
    for match in matches:
        if not isinstance(match, dict):
            continue
        if not match.get("matchIsFinished"):
            continue
        final = _final_result_entry(match)
        if final is None:
            continue
        home_score = _optional_int(final.get("pointsTeam1"))
        away_score = _optional_int(final.get("pointsTeam2"))
        if home_score is None or away_score is None:
            continue
        event_date = str(match.get("matchDateTimeUTC") or "").strip()
        if not event_date:
            extraction_rows.append(
                extraction_diagnostic_row(
                    source=SOURCE_OPENLIGADB,
                    extractor=OPENLIGADB_EXTRACTOR,
                    status="rejected",
                    reason="missing_kickoff_timestamp",
                    title=_match_title(match),
                    metadata={"match_id": match.get("matchID")},
                )
            )
            continue
        home = resolve_openligadb_team(match.get("team1") or {}, resolver)
        away = resolve_openligadb_team(match.get("team2") or {}, resolver)
        unresolved = [team.name for team in (home, away) if not team.fifa_code]
        if unresolved:
            extraction_rows.append(
                extraction_diagnostic_row(
                    source=SOURCE_OPENLIGADB,
                    extractor=OPENLIGADB_EXTRACTOR,
                    status="rejected",
                    reason="unresolved_team",
                    title=_match_title(match),
                    metadata={
                        "match_id": match.get("matchID"),
                        "unresolved_teams": unresolved,
                        "home_team": (match.get("team1") or {}).get("teamName"),
                        "away_team": (match.get("team2") or {}).get("teamName"),
                    },
                )
            )
            continue
        metadata: dict[str, Any] = {
            "match_id": match.get("matchID"),
            "group_name": (match.get("group") or {}).get("groupName"),
            "parser": OPENLIGADB_EXTRACTOR,
        }
        metadata.update(_extra_period_metadata(match))
        results.append(
            ResultRecord(
                event_date=event_date,
                home_team=home,
                away_team=away,
                score=ScoreTip(home_score, away_score),
                source=SOURCE_OPENLIGADB,
                notes="OpenLigaDB getmatchdata payload.",
                metadata=metadata,
            )
        )
    if not results:
        extraction_rows.append(
            extraction_diagnostic_row(
                source=SOURCE_OPENLIGADB,
                extractor=OPENLIGADB_EXTRACTOR,
                status="rejected",
                reason="no_results_extracted",
                severity="warning",
                metadata={"matches": len(matches), "match_rejections": len(extraction_rows)},
            )
        )
    return results, extraction_rows


def resolve_openligadb_team(raw_team: dict[str, Any], resolver: TeamResolver) -> TeamRef:
    """Resolve one OpenLigaDB team dict into a canonical team reference.

    ``shortName`` is the FIFA three-letter code and hits the registry's code
    index directly; the German ``teamName`` is the fallback for payload rows
    where the short code is missing or unknown.
    """

    for label in (raw_team.get("shortName"), raw_team.get("teamName")):
        if label:
            resolved = resolver.resolve(str(label))
            if resolved.fifa_code:
                return resolved
    return resolver.resolve(str(raw_team.get("teamName") or raw_team.get("shortName") or ""))


def _final_result_entry(match: dict[str, Any]) -> dict[str, Any] | None:
    for entry in match.get("matchResults") or []:
        if isinstance(entry, dict) and _result_name(entry) == RESULT_NAME_FINAL:
            return entry
    return None


def _extra_period_metadata(match: dict[str, Any]) -> dict[str, Any]:
    """Keep extra-time and shootout entries as metadata on the result row.

    The shared ``tournament_results`` contract has no dedicated AET/shootout
    representation, so the row carries the final ``Endergebnis`` score and the
    extra-period observations stay auditable in metadata.
    """

    metadata: dict[str, Any] = {}
    for entry in match.get("matchResults") or []:
        if not isinstance(entry, dict):
            continue
        name = _result_name(entry)
        home = _optional_int(entry.get("pointsTeam1"))
        away = _optional_int(entry.get("pointsTeam2"))
        if home is None or away is None:
            continue
        if RESULT_NAME_EXTRA_TIME_MARKER in name:
            metadata["extra_time_home"] = home
            metadata["extra_time_away"] = away
        elif any(marker in name for marker in RESULT_NAME_PENALTY_MARKERS):
            metadata["penalty_home"] = home
            metadata["penalty_away"] = away
    return metadata


def _result_name(entry: dict[str, Any]) -> str:
    return " ".join(str(entry.get("resultName") or "").split()).casefold()


def _match_title(match: dict[str, Any]) -> str:
    home = (match.get("team1") or {}).get("teamName") or ""
    away = (match.get("team2") or {}).get("teamName") or ""
    return f"{home} vs {away}".strip()


def _optional_int(value: Any) -> int | None:
    if value in (None, ""):
        return None
    try:
        return int(str(value))
    except (TypeError, ValueError):
        return None
