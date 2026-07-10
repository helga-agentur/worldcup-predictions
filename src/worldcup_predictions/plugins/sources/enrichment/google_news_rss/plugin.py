"""Quota-free Google News RSS supplement to the NewsAPI public-analysis source.

Google News RSS search costs no API quota, so it keeps public match analysis
flowing when the NewsAPI daily budget is exhausted. Retained items are mapped
to the exact article shape the NewsAPI plugin produces and pushed through the
same shared extraction helpers, so every downstream consumer of
``public_match_analysis`` (postmatch stats, automatic notes, tempo signals)
works unchanged. Rows are distinguishable by their storage source column,
which carries this plugin's id.
"""

from __future__ import annotations

import datetime as dt
from collections import Counter
import html
import re
import urllib.error
import xml.etree.ElementTree as ET
from email.utils import parsedate_to_datetime
from typing import Any
from urllib.parse import urlparse

from worldcup_predictions.core.constants import NEWS_API_RELIABLE_DOMAINS
from worldcup_predictions.core.contracts import Diagnostic
from worldcup_predictions.core.datasets import EXTRACTION_DIAGNOSTICS
from worldcup_predictions.core.datasets import PUBLIC_MATCH_ANALYSIS as PUBLIC_ANALYSIS_DATASET
from worldcup_predictions.core.events import EventName, event_value
from worldcup_predictions.core.extraction import extraction_diagnostic_row
from worldcup_predictions.core.metadata import PluginKind, PluginMetadata, QuotaPolicy
from worldcup_predictions.core.plugin import BasePlugin, PluginResult
from worldcup_predictions.plugins.source_runtime import SourceRuntime
from worldcup_predictions.plugins.sources.enrichment.public_analysis.plugin import (
    public_analysis_rows_with_diagnostics,
)
from worldcup_predictions.storage.ledger import SourceRequest, normalize_datetime
from worldcup_predictions.tournament import TournamentState
from worldcup_predictions.tournament.contracts import FixtureRecord

SOURCE_GOOGLE_NEWS_RSS = "google_news_rss"
ENDPOINT_GOOGLE_NEWS_RSS_SEARCH = "https://news.google.com/rss/search"
GOOGLE_NEWS_RSS_MAX_ARTICLES = 10
GOOGLE_NEWS_RSS_REFRESH_INTERVAL = dt.timedelta(hours=3)
GOOGLE_NEWS_RSS_EXTRACTOR = "google_news_rss_v1"


class GoogleNewsRssPlugin(BasePlugin):
    """Fetch reliable public pre/postgame articles from Google News RSS."""

    id = SOURCE_GOOGLE_NEWS_RSS
    version = "0.1.0"
    # Runs just before public_analysis (priority 270) so its dataset-wide
    # cause/adjustment/signal derivation folds these rows in during the same run.
    priority = 269
    subscribed_events = (EventName.FEATURE_SIGNALS_REQUESTED.value,)
    metadata = PluginMetadata(
        plugin_id=id,
        kind=PluginKind.SOURCE,
        description="Fetch reliable public pre/postgame articles from quota-free Google News RSS.",
        datasets_written=(PUBLIC_ANALYSIS_DATASET, EXTRACTION_DIAGNOSTICS),
        quota_policy=QuotaPolicy(
            quota_limited=False,
            ledger_required=True,
            description="Quota-free RSS search; per-fixture requests are throttled by a 3-hour refresh interval.",
        ),
        confidence_policy="Only publishers on the shared reliable-domain allowlist are retained; scoring uses shared reliability profiles.",
    )

    def handle(self, event, context, payload):
        runtime = SourceRuntime(self, event, context)
        if context.storage is None:
            return runtime.storage_unavailable_result("Google News RSS analysis")

        state = runtime.tournament_state()

        diagnostics: list[Diagnostic] = []
        written = 0
        extraction_written = 0
        for fixture in state.open_fixtures():
            result = self._fetch_fixture_rss(runtime, fixture, phase="pregame")
            diagnostics.extend(result.diagnostics)
            written += int(result.metadata.get("written_rows") or 0)
            extraction_written += int(result.metadata.get("extraction_diagnostics") or 0)
        for fixture in _recent_finished_fixtures(state):
            result = self._fetch_fixture_rss(runtime, fixture, phase="postgame")
            diagnostics.extend(result.diagnostics)
            written += int(result.metadata.get("written_rows") or 0)
            extraction_written += int(result.metadata.get("extraction_diagnostics") or 0)

        return PluginResult(
            plugin_id=self.id,
            event=event_value(event),
            artifacts=[
                runtime.structured_artifact(PUBLIC_ANALYSIS_DATASET, rows_written=written),
                runtime.structured_artifact(EXTRACTION_DIAGNOSTICS, rows_written=extraction_written),
            ],
            diagnostics=diagnostics,
            metadata={
                "written_rows": written,
                "extraction_diagnostics": extraction_written,
            },
        )

    def _fetch_fixture_rss(self, runtime: SourceRuntime, fixture: FixtureRecord, *, phase: str) -> PluginResult:
        query = google_news_query(fixture)
        params = {"q": query, "hl": "en-US", "gl": "US", "ceid": "US:en"}
        request = SourceRequest(
            source=SOURCE_GOOGLE_NEWS_RSS,
            endpoint=ENDPOINT_GOOGLE_NEWS_RSS_SEARCH,
            purpose=f"{phase}_match_analysis",
            params=params,
            fixture_key=fixture.key,
            quota_cost=0,
            min_refresh_interval=GOOGLE_NEWS_RSS_REFRESH_INTERVAL,
            rate_limit_backoff=dt.timedelta(hours=6),
        )
        decision = runtime.should_fetch(request)
        if not decision.should_fetch:
            return runtime.skipped_fetch_result("Google News RSS", decision.reason, fixture_key=fixture.key, metadata=decision.metadata)
        try:
            body, _headers = runtime.fetch_text(
                ENDPOINT_GOOGLE_NEWS_RSS_SEARCH,
                params,
                headers={"Accept": "application/rss+xml, application/xml, text/xml"},
            )
        except (OSError, TimeoutError, urllib.error.HTTPError) as exc:
            runtime.record_error(request, exc)
            return runtime.result(
                diagnostics=[
                    runtime.diagnostic(
                        level="warning",
                        message="Google News RSS fetch failed; stored public analysis rows will be used.",
                        fixture_key=fixture.key,
                        metadata={"error": str(exc)},
                    )
                ],
            )

        try:
            items = parse_google_news_rss(body)
        except ET.ParseError as exc:
            runtime.record_error(request, exc, metadata={"reason": "invalid_rss_xml"})
            return runtime.result(
                diagnostics=[
                    runtime.diagnostic(
                        level="warning",
                        message="Google News RSS response was not valid RSS XML.",
                        fixture_key=fixture.key,
                        metadata={"error": str(exc)},
                    )
                ],
            )

        articles, drop_diagnostics = reliable_articles_from_items(items, fixture, phase=phase)
        rows, extraction_diagnostics = public_analysis_rows_with_diagnostics(articles, fixture, phase=phase)
        diagnostic_rows = drop_diagnostics + extraction_diagnostics
        runtime.record_success(
            request,
            message="Fetched Google News RSS articles.",
            metadata={
                "items": len(items),
                "articles": len(articles),
                "rows": len(rows),
                "dropped_unreliable": len(drop_diagnostics),
                "query": query,
            },
        )

        diagnostics: list[Diagnostic] = []
        if not rows:
            diagnostic_rows.append(
                extraction_diagnostic_row(
                    source=SOURCE_GOOGLE_NEWS_RSS,
                    extractor=GOOGLE_NEWS_RSS_EXTRACTOR,
                    status="empty",
                    reason="no_usable_items",
                    fixture_key=fixture.key,
                    phase=phase,
                    severity="info",
                    metadata={
                        "items": len(items),
                        "articles": len(articles),
                        "query": query,
                        "home_team": fixture.home_team.name,
                        "away_team": fixture.away_team.name,
                    },
                )
            )
            diagnostics.append(
                runtime.diagnostic(
                    "info",
                    "Google News RSS query returned no usable rows after reliability, timing, and fixture filters.",
                    fixture_key=fixture.key,
                    metadata={"phase": phase, "items": len(items), "articles": len(articles), "query": query},
                )
            )
        count = runtime.write_records(PUBLIC_ANALYSIS_DATASET, rows)
        diagnostic_count = runtime.write_records(EXTRACTION_DIAGNOSTICS, diagnostic_rows)
        return runtime.result(diagnostics=diagnostics, metadata={"written_rows": count, "extraction_diagnostics": diagnostic_count})


def google_news_query(fixture: FixtureRecord) -> str:
    return f"{fixture.home_team.name} {fixture.away_team.name} world cup"


def parse_google_news_rss(body: str) -> list[dict[str, Any]]:
    """Parse Google News RSS 2.0 XML into plain item dicts (stdlib only)."""

    root = ET.fromstring(body)
    items = []
    for item in root.iter("item"):
        source_element = item.find("source")
        publisher = (source_element.text or "").strip() if source_element is not None else ""
        publisher_url = source_element.get("url") if source_element is not None else None
        items.append(
            {
                "title": (item.findtext("title") or "").strip(),
                "link": (item.findtext("link") or "").strip(),
                "description": _strip_html(item.findtext("description") or ""),
                "published_at": _rfc822_to_iso(item.findtext("pubDate")),
                "publisher": publisher,
                "publisher_domain": _publisher_domain(publisher_url),
            }
        )
    return items


def reliable_articles_from_items(
    items: list[dict[str, Any]],
    fixture: FixtureRecord,
    *,
    phase: str,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Map RSS items to NewsAPI-shaped article dicts, dropping unreliable publishers.

    Mirrors public_analysis, which restricts NewsAPI queries to the shared
    NEWS_API_RELIABLE_DOMAINS allowlist at the API level: items whose
    ``<source url="...">`` domain is not on that allowlist never reach the
    shared extraction helpers. Retained articles are capped per fixture.
    """

    articles: list[dict[str, Any]] = []
    dropped: list[dict[str, Any]] = []
    # Google News aggregates hundreds of long-tail publishers, so per-item
    # rejection rows would flood extraction_diagnostics on every cron run
    # (~350 rows per run in live testing). One summary row per fixture query
    # keeps the drop auditable without the noise.
    dropped_domains: Counter[str] = Counter()
    for item in items:
        domain = str(item.get("publisher_domain") or "")
        if not is_reliable_domain(domain):
            dropped_domains[domain or "unknown"] += 1
            continue
        if len(articles) >= GOOGLE_NEWS_RSS_MAX_ARTICLES:
            continue
        articles.append(article_from_rss_item(item))
    if dropped_domains:
        dropped.append(
            extraction_diagnostic_row(
                source=SOURCE_GOOGLE_NEWS_RSS,
                extractor=GOOGLE_NEWS_RSS_EXTRACTOR,
                status="rejected",
                reason="unreliable_source_domains_summary",
                fixture_key=fixture.key,
                phase=phase,
                severity="info",
                metadata={
                    "dropped_items": sum(dropped_domains.values()),
                    "distinct_domains": len(dropped_domains),
                    "top_domains": dict(dropped_domains.most_common(10)),
                    "home_team": fixture.home_team.name,
                    "away_team": fixture.away_team.name,
                },
            )
        )
    return articles, dropped


def article_from_rss_item(item: dict[str, Any]) -> dict[str, Any]:
    """Return one NewsAPI-shaped article dict for the shared extraction helpers."""

    publisher = str(item.get("publisher") or "")
    return {
        "source": {"name": publisher or str(item.get("publisher_domain") or "")},
        "url": item.get("link"),
        "publishedAt": item.get("published_at"),
        "title": strip_publisher_suffix(str(item.get("title") or ""), publisher),
        "description": item.get("description"),
    }


def is_reliable_domain(domain: str) -> bool:
    domain = str(domain or "").casefold()
    return bool(domain) and any(
        domain == reliable or domain.endswith(f".{reliable}") for reliable in NEWS_API_RELIABLE_DOMAINS
    )


def strip_publisher_suffix(title: str, publisher: str) -> str:
    """Strip the trailing " - Publisher" Google News appends to item titles."""

    title = title.strip()
    if publisher:
        suffix = f" - {publisher}"
        if title.casefold().endswith(suffix.casefold()):
            return title[: -len(suffix)].rstrip()
    head, separator, _tail = title.rpartition(" - ")
    if separator and head:
        return head.rstrip()
    return title


def _publisher_domain(url: str | None) -> str:
    if not url:
        return ""
    netloc = urlparse(str(url)).netloc.casefold()
    return netloc.removeprefix("www.")


def _rfc822_to_iso(value: str | None) -> str | None:
    if not value:
        return None
    try:
        parsed = parsedate_to_datetime(str(value).strip())
    except (TypeError, ValueError):
        return None
    return normalize_datetime(parsed)


def _strip_html(value: str) -> str:
    text = re.sub(r"<[^>]+>", " ", str(value or ""))
    return " ".join(html.unescape(text).split())


def _recent_finished_fixtures(state: TournamentState) -> list[FixtureRecord]:
    # Copied from public_analysis.plugin._recent_finished_fixtures: that helper
    # is module-private, so a copy keeps this plugin from reaching into another
    # plugin's internals without modifying any existing file.
    result_keys = {result.fixture_key for result in state.results}
    now = dt.datetime.now(dt.timezone.utc)
    recent = []
    for fixture in state.fixtures:
        kickoff = fixture.kickoff_at
        if fixture.key in result_keys and kickoff and now - dt.timedelta(days=3) <= kickoff <= now:
            recent.append(fixture)
    return sorted(recent, key=lambda item: item.event_date)
