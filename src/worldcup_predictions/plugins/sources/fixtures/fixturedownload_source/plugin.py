"""fixturedownload.com World Cup 2026 fixture-feed result witness plugin."""

from __future__ import annotations

import datetime as dt
import urllib.error
from typing import Any

from worldcup_predictions.core.contracts import Artifact, ScoreTip
from worldcup_predictions.core.datasets import EXTRACTION_DIAGNOSTICS, TOURNAMENT_RESULTS
from worldcup_predictions.core.events import EventName, event_value
from worldcup_predictions.core.extraction import extraction_diagnostic_row
from worldcup_predictions.core.metadata import PluginKind, PluginMetadata, QuotaPolicy
from worldcup_predictions.core.plugin import BasePlugin, PluginResult
from worldcup_predictions.plugins.source_runtime import SourceRuntime
from worldcup_predictions.storage.ledger import SourceRequest, normalize_datetime
from worldcup_predictions.tournament import ResultRecord, TeamRef, TeamResolver
from worldcup_predictions.tournament.repository import write_results


SOURCE_FIXTUREDOWNLOAD = "fixturedownload"
ENDPOINT_FIXTUREDOWNLOAD_WORLDCUP_2026 = "https://fixturedownload.com/feed/json/fifa-world-cup-2026"
FIXTUREDOWNLOAD_EXTRACTOR = "fixturedownload_feed_v1"

_PLACEHOLDER_TEAM_LABELS = frozenset({"to be announced", "to be confirmed", "tba", "tbc", "tbd"})


class FixtureDownloadSourcePlugin(BasePlugin):
    """Fetch the fixturedownload.com JSON feed and store final-score witness rows."""

    id = "fixturedownload_source"
    version = "0.1.0"
    priority = 115
    subscribed_events = (EventName.FIXTURES_REQUESTED.value,)
    metadata = PluginMetadata(
        plugin_id=id,
        kind=PluginKind.SOURCE,
        description="Fetch the public fixturedownload.com World Cup 2026 JSON feed as an additional result-confirmation witness.",
        datasets_written=(TOURNAMENT_RESULTS, EXTRACTION_DIAGNOSTICS),
        quota_policy=QuotaPolicy(
            quota_limited=False,
            ledger_required=True,
            description="One public JSON feed request per run, refreshed through the source ledger at most every 30 minutes.",
        ),
        confidence_policy="fixturedownload result rows are consensus witness observations only; they enter tournament state solely through the central source-consensus policy.",
    )

    def handle(self, event, context, payload):
        runtime = SourceRuntime(self, event, context)
        if context.storage is None:
            return runtime.storage_unavailable_result("fixturedownload data")

        request = SourceRequest(
            source=SOURCE_FIXTUREDOWNLOAD,
            endpoint=ENDPOINT_FIXTUREDOWNLOAD_WORLDCUP_2026,
            purpose="world_cup_json_feed",
            params={},
            quota_cost=0,
            min_refresh_interval=dt.timedelta(minutes=30),
            quota_scope=SOURCE_FIXTUREDOWNLOAD,
            rate_limit_backoff=dt.timedelta(hours=6),
        )
        decision = runtime.should_fetch(request)
        if not decision.should_fetch:
            return runtime.skipped_fetch_result("fixturedownload", decision.reason, metadata=decision.metadata)
        try:
            feed, _headers = runtime.fetch_json(ENDPOINT_FIXTUREDOWNLOAD_WORLDCUP_2026)
        except (OSError, TimeoutError, urllib.error.HTTPError) as exc:
            runtime.record_error(request, exc)
            return runtime.result(
                diagnostics=[
                    runtime.diagnostic(
                        "warning",
                        "fixturedownload fetch failed; stored tournament rows will be used.",
                        metadata={"error": str(exc)},
                    )
                ]
            )

        results, extraction_rows = parse_fixturedownload_matches(feed)
        match_count = len(feed) if isinstance(feed, list) else 0
        not_modified = isinstance(feed, dict) and not feed
        if not results and not not_modified:
            extraction_rows.append(
                extraction_diagnostic_row(
                    source=SOURCE_FIXTUREDOWNLOAD,
                    extractor=FIXTUREDOWNLOAD_EXTRACTOR,
                    status="rejected",
                    reason="no_results_extracted",
                    source_url=ENDPOINT_FIXTUREDOWNLOAD_WORLDCUP_2026,
                    metadata={"matches_in_feed": match_count},
                )
            )
        result_count = write_results(runtime.storage, results, source=self.id, run_id=runtime.context.run_id)
        extraction_count = runtime.write_records(EXTRACTION_DIAGNOSTICS, extraction_rows)
        runtime.record_success(
            request,
            message="Fetched fixturedownload World Cup feed.",
            metadata={"matches": match_count, "results": result_count, "extraction_rows": extraction_count},
        )
        return PluginResult(
            plugin_id=self.id,
            event=event_value(event),
            artifacts=[
                Artifact(TOURNAMENT_RESULTS, "structured_dataset", self.id, data={"rows_written": result_count}),
                Artifact(EXTRACTION_DIAGNOSTICS, "structured_dataset", self.id, data={"rows_written": extraction_count}),
            ],
            metadata={"matches": match_count, "results": result_count, "extraction_rows": extraction_count},
        )


def parse_fixturedownload_matches(feed: Any) -> tuple[list[ResultRecord], list[dict[str, Any]]]:
    """Return witness result rows plus extraction diagnostics for one feed payload."""

    resolver = TeamResolver.default(source=SOURCE_FIXTUREDOWNLOAD)
    results: list[ResultRecord] = []
    extraction_rows: list[dict[str, Any]] = []
    matches = feed if isinstance(feed, list) else []
    for match in matches:
        if not isinstance(match, dict):
            continue
        home_label = " ".join(str(match.get("HomeTeam") or "").split())
        away_label = " ".join(str(match.get("AwayTeam") or "").split())
        if _is_placeholder_team(home_label) or _is_placeholder_team(away_label) or not home_label or not away_label:
            continue
        home_score = _optional_int(match.get("HomeTeamScore"))
        away_score = _optional_int(match.get("AwayTeamScore"))
        if home_score is None or away_score is None:
            continue
        event_date = _normalized_event_date(match.get("DateUtc"))
        if not event_date:
            extraction_rows.append(
                _rejected_row(
                    match,
                    reason="invalid_event_date",
                    title=f"{home_label} vs {away_label}",
                    metadata={"date_utc": match.get("DateUtc")},
                )
            )
            continue
        home = resolver.resolve(home_label)
        away = resolver.resolve(away_label)
        unresolved = [label for label, team in ((home_label, home), (away_label, away)) if not team.fifa_code]
        if unresolved:
            extraction_rows.append(
                _rejected_row(
                    match,
                    reason="unresolved_team",
                    title=f"{home_label} vs {away_label}",
                    metadata={"unresolved_teams": unresolved},
                )
            )
            continue
        metadata: dict[str, Any] = {
            "parser": FIXTUREDOWNLOAD_EXTRACTOR,
            "match_number": match.get("MatchNumber"),
            "round_number": match.get("RoundNumber"),
            "group": match.get("Group"),
            "location": match.get("Location"),
        }
        winner_side = _drawn_knockout_winner_side(match, resolver, home=home, away=away, home_score=home_score, away_score=away_score)
        if winner_side:
            metadata["winner"] = winner_side
            metadata["winner_label"] = " ".join(str(match.get("Winner") or "").split())
        results.append(
            ResultRecord(
                event_date=event_date,
                home_team=home,
                away_team=away,
                score=ScoreTip(home_score, away_score),
                source=SOURCE_FIXTUREDOWNLOAD,
                notes="fixturedownload.com public JSON feed.",
                metadata=metadata,
            )
        )
    return results, extraction_rows


def _drawn_knockout_winner_side(
    match: dict[str, Any],
    resolver: TeamResolver,
    *,
    home: TeamRef,
    away: TeamRef,
    home_score: int,
    away_score: int,
) -> str | None:
    """Advancing side implied by the feed's Winner field for a drawn knockout match.

    Mirrors the football-data.org representation already consumed downstream:
    result metadata "winner" is "HOME_TEAM" or "AWAY_TEAM".
    """

    if home_score != away_score or match.get("Group") is not None:
        return None
    winner_label = " ".join(str(match.get("Winner") or "").split())
    if not winner_label or _is_placeholder_team(winner_label):
        return None
    winner = resolver.resolve(winner_label)
    if winner.key == home.key:
        return "HOME_TEAM"
    if winner.key == away.key:
        return "AWAY_TEAM"
    return None


def _rejected_row(match: dict[str, Any], *, reason: str, title: str, metadata: dict[str, Any]) -> dict[str, Any]:
    return extraction_diagnostic_row(
        source=SOURCE_FIXTUREDOWNLOAD,
        extractor=FIXTUREDOWNLOAD_EXTRACTOR,
        status="rejected",
        reason=reason,
        source_url=ENDPOINT_FIXTUREDOWNLOAD_WORLDCUP_2026,
        title=title,
        severity="warning",
        metadata={"match_number": match.get("MatchNumber"), **metadata},
    )


def _normalized_event_date(value: Any) -> str | None:
    """Normalize the feed's space-separated "YYYY-MM-DD HH:MM:SSZ" into "...T...Z"."""

    text = str(value or "").strip()
    if not text:
        return None
    try:
        return normalize_datetime(text)
    except ValueError:
        return None


def _is_placeholder_team(label: str) -> bool:
    return label.casefold() in _PLACEHOLDER_TEAM_LABELS


def _optional_int(value: Any) -> int | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None
