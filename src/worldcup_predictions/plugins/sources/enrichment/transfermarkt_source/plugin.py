"""Robots-aware Transfermarkt search-result source plugin."""

from __future__ import annotations

import datetime as dt
import html
import re
import urllib.error
import urllib.parse
import urllib.robotparser
from typing import Any

from worldcup_predictions.core.constants import ENDPOINT_TRANSFERMARKT_SEARCH, SOURCE_TRANSFERMARKT
from worldcup_predictions.core.contracts import Artifact, Diagnostic
from worldcup_predictions.core.datasets import FOOTBALL_DATA_TEAMS, TRANSFERMARKT_SEARCH_RESULTS
from worldcup_predictions.core.events import EventName, event_value
from worldcup_predictions.core.metadata import PluginKind, PluginMetadata, QuotaPolicy
from worldcup_predictions.core.plugin import BasePlugin, PluginResult
from worldcup_predictions.plugins.source_runtime import SourceRuntime
from worldcup_predictions.storage.ledger import SourceRequest, stable_hash


class TransfermarktSourcePlugin(BasePlugin):
    """Fetch public Transfermarkt search pages only when robots.txt allows it."""

    id = "transfermarkt_source"
    version = "0.1.0"
    priority = 127
    subscribed_events = (EventName.FEATURE_SIGNALS_REQUESTED.value,)
    metadata = PluginMetadata(
        plugin_id=id,
        kind=PluginKind.SOURCE,
        description="Robots-aware Transfermarkt team search extraction for optional player/squad enrichment discovery.",
        datasets_read=(FOOTBALL_DATA_TEAMS, TRANSFERMARKT_SEARCH_RESULTS),
        datasets_written=(TRANSFERMARKT_SEARCH_RESULTS,),
        quota_policy=QuotaPolicy(
            quota_limited=False,
            ledger_required=True,
            description="Search pages refresh weekly and are skipped if robots.txt disallows the request.",
        ),
        confidence_policy="Search results are discovery hints only; model features still require structured squad/player rows.",
    )

    def handle(self, event, context, payload):
        runtime = SourceRuntime(self, event, context)
        if context.storage is None:
            return runtime.storage_unavailable_result("Transfermarkt search")
        team_rows = runtime.storage.read_records(FOOTBALL_DATA_TEAMS, latest_only=True)
        team_names = sorted({str(row.get("team") or "") for row in team_rows if row.get("team")})[:48]
        if not team_names:
            return runtime.result(
                diagnostics=[runtime.diagnostic("info", "Transfermarkt search skipped because no football-data team rows exist yet.")]
            )
        if not robots_allows(runtime, ENDPOINT_TRANSFERMARKT_SEARCH):
            return runtime.result(
                diagnostics=[
                    runtime.diagnostic(
                        "info",
                        "Transfermarkt search skipped because robots.txt does not allow this user agent for the search path.",
                    )
                ]
            )
        rows: list[dict[str, Any]] = []
        diagnostics: list[Diagnostic] = []
        for team in team_names:
            result = self._fetch_team_search(runtime, team)
            diagnostics.extend(result.diagnostics)
            rows.extend(result.metadata.get("rows") or [])
        count = runtime.write_records(TRANSFERMARKT_SEARCH_RESULTS, rows)
        return PluginResult(
            plugin_id=self.id,
            event=event_value(event),
            artifacts=[Artifact(TRANSFERMARKT_SEARCH_RESULTS, "structured_dataset", self.id, data={"rows": count})],
            diagnostics=diagnostics,
            metadata={"teams": len(team_names), "rows": count},
        )

    def _fetch_team_search(self, runtime: SourceRuntime, team: str) -> PluginResult:
        request = SourceRequest(
            source=SOURCE_TRANSFERMARKT,
            endpoint=ENDPOINT_TRANSFERMARKT_SEARCH,
            purpose="team_search",
            params={"query": team},
            quota_cost=0,
            min_refresh_interval=dt.timedelta(days=7),
            quota_scope=SOURCE_TRANSFERMARKT,
            rate_limit_backoff=dt.timedelta(hours=6),
        )
        decision = runtime.should_fetch(request)
        if not decision.should_fetch:
            return runtime.skipped_fetch_result("Transfermarkt search", decision.reason, metadata={"team": team, **decision.metadata})
        try:
            page, _headers = runtime.fetch_text(
                ENDPOINT_TRANSFERMARKT_SEARCH,
                {"query": team},
                headers={
                    "Accept-Language": "en-US,en;q=0.9",
                    "Referer": "https://www.transfermarkt.com/",
                },
            )
        except (OSError, TimeoutError, urllib.error.HTTPError) as exc:
            runtime.record_error(request, exc)
            return runtime.result(diagnostics=[runtime.diagnostic("warning", "Transfermarkt search fetch failed.", metadata={"team": team, "error": str(exc)})])
        rows = transfermarkt_search_rows(page, team=team)
        runtime.record_success(request, message="Fetched Transfermarkt search page.", metadata={"team": team, "rows": len(rows)})
        return runtime.result(metadata={"rows": rows})


def robots_allows(runtime: SourceRuntime, url: str) -> bool:
    parsed = urllib.parse.urlparse(url)
    robots_url = f"{parsed.scheme}://{parsed.netloc}/robots.txt"
    parser = urllib.robotparser.RobotFileParser()
    try:
        text, _headers = runtime.fetch_text(robots_url)
    except (OSError, TimeoutError, urllib.error.HTTPError):
        return False
    parser.parse(text.splitlines())
    return parser.can_fetch(runtime.context.config.user_agent, url)


def transfermarkt_search_rows(page: str, *, team: str) -> list[dict[str, Any]]:
    rows = []
    for index, link_match in enumerate(re.finditer(r'<a\b[^>]+href="([^"]+)"[^>]*>(.*?)</a>', page, flags=re.IGNORECASE | re.DOTALL)):
        href = html.unescape(link_match.group(1))
        title = _strip_tags(link_match.group(2))
        if not title or not href.startswith("/") or "/profil/" not in href:
            continue
        rows.append(
            {
                "record_key": stable_hash({"team": team, "href": href, "title": title}),
                "team": team,
                "result_title": title,
                "result_url": urllib.parse.urljoin("https://www.transfermarkt.com", href),
                "result_index": index,
                "metadata": {"source": SOURCE_TRANSFERMARKT},
            }
        )
    return rows[:20]


def _strip_tags(value: str) -> str:
    return " ".join(html.unescape(re.sub(r"<[^>]+>", " ", value)).split())
