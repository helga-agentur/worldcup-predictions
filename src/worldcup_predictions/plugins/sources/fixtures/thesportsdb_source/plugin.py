"""TheSportsDB World Cup 2026 result source plugin.

TheSportsDB publishes free JSON event data for the FIFA World Cup (league id
4429). This plugin fetches the full 2026 season event list in one call and
stores finished scores as tournament result observations. It is a consensus
WITNESS: rows go through the shared ``tournament_results`` dataset so the
central source-consensus policy decides which scores are confirmed; the plugin
never touches tournament state directly.
"""

from __future__ import annotations

import datetime as dt
import json
import urllib.error
from typing import Any

from worldcup_predictions.core.contracts import Artifact, ScoreTip
from worldcup_predictions.core.datasets import EXTRACTION_DIAGNOSTICS, TOURNAMENT_RESULTS
from worldcup_predictions.core.events import EventName, event_value
from worldcup_predictions.core.extraction import extraction_diagnostic_row
from worldcup_predictions.core.metadata import EnvVar, PluginKind, PluginMetadata, QuotaPolicy
from worldcup_predictions.core.plugin import BasePlugin, PluginResult
from worldcup_predictions.plugins.source_runtime import SourceRuntime
from worldcup_predictions.storage.ledger import SourceRequest
from worldcup_predictions.tournament import ResultRecord, TeamResolver
from worldcup_predictions.tournament.repository import load_tournament_state, write_derived_state, write_results


SOURCE_THESPORTSDB = "thesportsdb"
ENV_THESPORTSDB_API_KEY = "THESPORTSDB_API_KEY"
# "3" is TheSportsDB's documented free public API key.
THESPORTSDB_DEFAULT_API_KEY = "3"
THESPORTSDB_LEAGUE_ID = "4429"
THESPORTSDB_SEASON = "2026"
ENDPOINT_THESPORTSDB_BASE = "https://www.thesportsdb.com/api/v1/json"
THESPORTSDB_EXTRACTOR = "thesportsdb_events_v2"

# The free public key truncates eventsseason.php to the FIRST five season
# events, which makes it useless as a witness for new results. Round queries
# return complete rounds, so fetch the current knockout rounds (TheSportsDB
# codes: 125=quarter-final, 150=semi-final, 160=final; empty until events are
# scheduled) plus eventspastleague.php, which always carries the most recently
# finished match.
THESPORTSDB_KNOCKOUT_ROUNDS = ("125", "150", "160")

# Normalized (uppercased) strStatus values that mark a finished match. The
# AET/PEN family means the stored int scores are the 120-minute totals.
FINAL_STATUSES = frozenset(
    {
        "FT",
        "AET",
        "PEN",
        "AP",
        "FT PEN",
        "AFTER EXTRA TIME",
        "AFTER PENALTIES",
        "MATCH FINISHED",
        "FINISHED",
        "FULL TIME",
    }
)


class TheSportsDbSourcePlugin(BasePlugin):
    """Fetch TheSportsDB World Cup 2026 events as result-confirmation witnesses."""

    id = "thesportsdb_source"
    version = "0.1.0"
    priority = 115
    subscribed_events = (EventName.FIXTURES_REQUESTED.value,)
    metadata = PluginMetadata(
        plugin_id=id,
        kind=PluginKind.SOURCE,
        description="Fetch TheSportsDB World Cup 2026 knockout-round and recent events and store finished scores as result observations.",
        datasets_written=(TOURNAMENT_RESULTS, EXTRACTION_DIAGNOSTICS),
        env_vars=(
            EnvVar(
                ENV_THESPORTSDB_API_KEY,
                required=False,
                description="TheSportsDB API key; defaults to the free public key '3'.",
            ),
        ),
        quota_policy=QuotaPolicy(
            quota_limited=False,
            ledger_required=True,
            description="A handful of public round/recent-event calls per run, each refreshed through the source ledger every 30 minutes.",
        ),
        confidence_policy="TheSportsDB rows are independent result witnesses; scores enter tournament state only after the central source-consensus policy confirms them.",
    )

    def handle(self, event, context, payload):
        runtime = SourceRuntime(self, event, context)
        if context.storage is None:
            return runtime.storage_unavailable_result("TheSportsDB events")

        api_key = runtime.env_value(ENV_THESPORTSDB_API_KEY) or THESPORTSDB_DEFAULT_API_KEY
        queries = [
            (
                f"{ENDPOINT_THESPORTSDB_BASE}/{api_key}/eventspastleague.php",
                {"id": THESPORTSDB_LEAGUE_ID},
                "world_cup_recent_events",
            ),
            *(
                (
                    f"{ENDPOINT_THESPORTSDB_BASE}/{api_key}/eventsround.php",
                    {"id": THESPORTSDB_LEAGUE_ID, "r": round_code, "s": THESPORTSDB_SEASON},
                    f"world_cup_round_{round_code}_events",
                )
                for round_code in THESPORTSDB_KNOCKOUT_ROUNDS
            ),
        ]
        diagnostics = []
        results: list[ResultRecord] = []
        extraction_rows: list[dict[str, Any]] = []
        fetched_any = False
        for endpoint, params, purpose in queries:
            request = SourceRequest(
                source=SOURCE_THESPORTSDB,
                endpoint=endpoint,
                purpose=purpose,
                params=params,
                quota_cost=0,
                min_refresh_interval=dt.timedelta(minutes=30),
                quota_scope=SOURCE_THESPORTSDB,
                rate_limit_backoff=dt.timedelta(hours=6),
            )
            decision = runtime.should_fetch(request)
            if not decision.should_fetch:
                diagnostics.append(
                    runtime.diagnostic("info", f"TheSportsDB {purpose} fetch skipped: {decision.reason}.")
                )
                continue
            try:
                payload_json, _headers = runtime.fetch_json(endpoint, params)
            except (OSError, TimeoutError, urllib.error.HTTPError, json.JSONDecodeError) as exc:
                runtime.record_error(request, exc)
                diagnostics.append(
                    runtime.diagnostic(
                        "warning",
                        "TheSportsDB events fetch failed; stored tournament rows will be used.",
                        metadata={"purpose": purpose, "error": str(exc)},
                    )
                )
                continue
            fetched_any = True
            # Empty rounds are expected before their fixtures are scheduled, so
            # only the recent-events query treats zero events as noteworthy.
            expect_events = purpose == "world_cup_recent_events"
            query_results, query_extraction_rows = parse_thesportsdb_events(
                payload_json, expect_events=expect_events
            )
            results.extend(query_results)
            extraction_rows.extend(query_extraction_rows)
            runtime.record_success(
                request,
                message=f"Fetched TheSportsDB {purpose}.",
                metadata={"results": len(query_results), "extraction_rows": len(query_extraction_rows)},
            )
        if not fetched_any and not results:
            return runtime.result(diagnostics=diagnostics, metadata={"results": 0, "extraction_rows": 0})
        results = _dedupe_results(results)
        result_count = write_results(runtime.storage, results, source=self.id, run_id=runtime.context.run_id)
        extraction_count = runtime.write_records(EXTRACTION_DIAGNOSTICS, extraction_rows)
        if result_count:
            state = load_tournament_state(runtime.storage)
            write_derived_state(runtime.storage, state, run_id=runtime.context.run_id)
            runtime.context.state["tournament_state"] = state
        return runtime.result(
            diagnostics=diagnostics,
            artifacts=[
                Artifact(TOURNAMENT_RESULTS, "structured_dataset", self.id, data={"rows_written": result_count}),
                Artifact(EXTRACTION_DIAGNOSTICS, "structured_dataset", self.id, data={"rows_written": extraction_count}),
            ],
            metadata={"results": result_count, "extraction_rows": extraction_count},
        )


def parse_thesportsdb_events(payload: Any, *, expect_events: bool = True) -> tuple[list[ResultRecord], list[dict[str, Any]]]:
    """Extract finished-score result rows plus extraction diagnostics."""

    events = payload.get("events") if isinstance(payload, dict) else None
    results: list[ResultRecord] = []
    extraction_rows: list[dict[str, Any]] = []
    if not isinstance(events, list) or not events:
        if not expect_events:
            return results, extraction_rows
        extraction_rows.append(
            extraction_diagnostic_row(
                source=SOURCE_THESPORTSDB,
                extractor=THESPORTSDB_EXTRACTOR,
                status="rejected",
                reason="no_events_extracted",
                severity="warning",
                metadata={"payload_keys": sorted(payload) if isinstance(payload, dict) else [type(payload).__name__]},
            )
        )
        return results, extraction_rows

    resolver = TeamResolver.default(source=SOURCE_THESPORTSDB)
    for event in events:
        if not isinstance(event, dict):
            continue
        status = " ".join(str(event.get("strStatus") or "").split()).upper()
        home_score = _optional_int(event.get("intHomeScore"))
        away_score = _optional_int(event.get("intAwayScore"))
        if status not in FINAL_STATUSES or home_score is None or away_score is None:
            continue
        event_date = _event_timestamp(event)
        if not event_date:
            extraction_rows.append(
                extraction_diagnostic_row(
                    source=SOURCE_THESPORTSDB,
                    extractor=THESPORTSDB_EXTRACTOR,
                    status="rejected",
                    reason="missing_kickoff_timestamp",
                    title=event.get("strEvent"),
                    metadata={"event_id": event.get("idEvent")},
                )
            )
            continue
        home = resolver.resolve(str(event.get("strHomeTeam") or ""))
        away = resolver.resolve(str(event.get("strAwayTeam") or ""))
        unresolved = [team.name for team in (home, away) if not team.fifa_code]
        if unresolved:
            extraction_rows.append(
                extraction_diagnostic_row(
                    source=SOURCE_THESPORTSDB,
                    extractor=THESPORTSDB_EXTRACTOR,
                    status="rejected",
                    reason="unresolved_team",
                    title=event.get("strEvent"),
                    metadata={
                        "event_id": event.get("idEvent"),
                        "unresolved_teams": unresolved,
                        "home_team": event.get("strHomeTeam"),
                        "away_team": event.get("strAwayTeam"),
                    },
                )
            )
            continue
        metadata: dict[str, Any] = {
            "event_id": event.get("idEvent"),
            "status": status,
            "round": event.get("intRound"),
            "parser": THESPORTSDB_EXTRACTOR,
        }
        # Shootout specifics are only recorded when TheSportsDB exposes both
        # penalty totals; the row contract itself carries the 90/120-minute
        # score, so an unknown shootout winner is deliberately not guessed.
        penalty_home = _optional_int(event.get("intHomeScorePen"))
        penalty_away = _optional_int(event.get("intAwayScorePen"))
        if penalty_home is not None and penalty_away is not None:
            metadata["penalty_home"] = penalty_home
            metadata["penalty_away"] = penalty_away
        results.append(
            ResultRecord(
                event_date=event_date,
                home_team=home,
                away_team=away,
                score=ScoreTip(home_score, away_score),
                source=SOURCE_THESPORTSDB,
                notes="TheSportsDB season events payload.",
                metadata=metadata,
            )
        )
    return results, extraction_rows


def _event_timestamp(event: dict[str, Any]) -> str:
    """Return the best parseable kickoff timestamp, or "" when none exists.

    TheSportsDB timestamps are UTC; naive values are treated as UTC by the
    shared ``normalize_datetime`` helper when the canonical fixture key is
    built, which keeps keys aligned with the other result sources.
    """

    date_value = str(event.get("dateEvent") or "").strip()
    time_value = str(event.get("strTime") or "").strip()
    candidates = [str(event.get("strTimestamp") or "").strip()]
    if date_value and time_value:
        candidates.append(f"{date_value}T{time_value}")
    candidates.append(date_value)
    for candidate in candidates:
        if not candidate:
            continue
        try:
            dt.datetime.fromisoformat(candidate.replace("Z", "+00:00"))
        except ValueError:
            continue
        return candidate
    return ""


def _optional_int(value: Any) -> int | None:
    if value in (None, ""):
        return None
    try:
        return int(str(value))
    except (TypeError, ValueError):
        return None


def _dedupe_results(results: list[ResultRecord]) -> list[ResultRecord]:
    by_key: dict[str, ResultRecord] = {}
    for result in results:
        by_key[result.record_key] = result
    return list(by_key.values())
