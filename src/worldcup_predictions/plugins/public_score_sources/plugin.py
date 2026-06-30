"""Robots-aware public score source adapters."""

from __future__ import annotations

import datetime as dt
import html
import json
import re
import urllib.error
import urllib.parse
import urllib.robotparser
from dataclasses import dataclass
from typing import Any

from worldcup_predictions.core.constants import (
    ENDPOINT_ESPN_SOCCER_SCOREBOARD,
    ENDPOINT_FIFA_WORLDCUP_2026_SCORES,
    ENDPOINT_FOTMOB_MATCH_SITEMAP,
    ENDPOINT_SOFASCORE_FOOTBALL,
    ENDPOINT_TWENTY_MIN_TIPPSPIEL_DETAILS,
    SOURCE_ESPN_SCOREBOARD,
    SOURCE_FIFA_MATCH_CENTRE,
    SOURCE_FOTMOB_PUBLIC,
    SOURCE_SOFASCORE_PUBLIC,
    SOURCE_TWENTY_MIN_PUBLIC,
)
from worldcup_predictions.core.contracts import Artifact, Diagnostic, ScoreTip, parse_utc_datetime
from worldcup_predictions.core.datasets import EXTRACTION_DIAGNOSTICS, PUBLIC_MATCH_ANALYSIS, TOURNAMENT_RESULTS
from worldcup_predictions.core.events import EventName, event_value
from worldcup_predictions.core.extraction import extraction_diagnostic_row
from worldcup_predictions.core.metadata import PluginKind, PluginMetadata, QuotaPolicy
from worldcup_predictions.core.plugin import BasePlugin, PluginResult
from worldcup_predictions.plugins.article_sources import (
    article_base_row,
    article_mentions_fixture,
    article_text,
    classify_public_note,
    classify_tempo_signal,
    extract_postmatch_stats_from_text,
)
from worldcup_predictions.plugins.source_runtime import SourceRuntime
from worldcup_predictions.storage.ledger import SourceRequest, normalize_datetime, stable_hash, utc_now
from worldcup_predictions.tournament import ResultRecord, TeamRef, TeamResolver, TournamentState
from worldcup_predictions.tournament.repository import load_tournament_state, write_derived_state, write_results


@dataclass(frozen=True)
class PublicPageSource:
    source: str
    label: str
    endpoint: str
    purpose: str
    min_refresh: dt.timedelta
    params: dict[str, Any] | None = None


class PublicScoreSourcesPlugin(BasePlugin):
    """Fetch allowed public score pages from independent result sources."""

    id = "public_score_sources"
    version = "0.1.0"
    priority = 130
    subscribed_events = (EventName.FIXTURES_REQUESTED.value,)
    metadata = PluginMetadata(
        plugin_id=id,
        kind=PluginKind.SOURCE,
        description="Fetch robots-aware FIFA, ESPN, FotMob, SofaScore, and 20min public pages for result verification and page-level analysis.",
        datasets_written=(TOURNAMENT_RESULTS, PUBLIC_MATCH_ANALYSIS, EXTRACTION_DIAGNOSTICS),
        quota_policy=QuotaPolicy(
            quota_limited=False,
            ledger_required=True,
            description="Public pages are robots-gated and source-ledgered; private/disallowed APIs are not fetched.",
        ),
        confidence_policy="Only finished score rows with canonical team-code matches are stored; page analysis rows require recognized pre/postgame signals or stat snippets.",
    )

    def handle(self, event, context, payload):
        runtime = SourceRuntime(self, event, context)
        if context.storage is None:
            return runtime.storage_unavailable_result("public score sources")

        state = runtime.tournament_state()
        diagnostics: list[Diagnostic] = []
        results: list[ResultRecord] = []
        analysis_rows: list[dict[str, Any]] = []
        extraction_rows: list[dict[str, Any]] = []
        robots_cache: dict[str, tuple[bool, str]] = {}
        for source_result in (
            self._fetch_espn(runtime, state, robots_cache),
            self._fetch_public_page(
                runtime,
                state,
                PublicPageSource(
                    source=SOURCE_FIFA_MATCH_CENTRE,
                    label="FIFA match centre",
                    endpoint=ENDPOINT_FIFA_WORLDCUP_2026_SCORES,
                    purpose="fifa_scores_fixtures_page",
                    min_refresh=dt.timedelta(minutes=runtime.context.config.source_defaults.default_refresh_minutes),
                ),
                robots_cache=robots_cache,
            ),
            self._fetch_public_page(
                runtime,
                state,
                PublicPageSource(
                    source=SOURCE_FOTMOB_PUBLIC,
                    label="FotMob public match sitemap",
                    endpoint=ENDPOINT_FOTMOB_MATCH_SITEMAP,
                    purpose="fotmob_match_sitemap_discovery",
                    min_refresh=dt.timedelta(hours=6),
                ),
                robots_cache=robots_cache,
            ),
            self._fetch_public_page(
                runtime,
                state,
                PublicPageSource(
                    source=SOURCE_SOFASCORE_PUBLIC,
                    label="SofaScore public football page",
                    endpoint=ENDPOINT_SOFASCORE_FOOTBALL,
                    purpose="sofascore_public_football_page",
                    min_refresh=dt.timedelta(hours=6),
                ),
                robots_cache=robots_cache,
            ),
            self._fetch_public_page(
                runtime,
                state,
                PublicPageSource(
                    source=SOURCE_TWENTY_MIN_PUBLIC,
                    label="20min public tippspiel page",
                    endpoint=ENDPOINT_TWENTY_MIN_TIPPSPIEL_DETAILS,
                    purpose="twenty_min_tippspiel_public_page",
                    min_refresh=dt.timedelta(minutes=runtime.context.config.source_defaults.default_refresh_minutes),
                ),
                robots_cache=robots_cache,
            ),
        ):
            diagnostics.extend(source_result.diagnostics)
            results.extend(source_result.metadata.get("results") or [])
            analysis_rows.extend(source_result.metadata.get("analysis_rows") or [])
            extraction_rows.extend(source_result.metadata.get("extraction_rows") or [])

        count = write_results(runtime.storage, results, source=self.id, run_id=runtime.context.run_id)
        analysis_count = runtime.write_records(PUBLIC_MATCH_ANALYSIS, analysis_rows)
        extraction_count = runtime.write_records(EXTRACTION_DIAGNOSTICS, extraction_rows)
        if count:
            refreshed_state = load_tournament_state(runtime.storage)
            write_derived_state(runtime.storage, refreshed_state, run_id=runtime.context.run_id)
            runtime.context.state["tournament_state"] = refreshed_state
        return PluginResult(
            plugin_id=self.id,
            event=event_value(event),
            artifacts=[
                Artifact(TOURNAMENT_RESULTS, "structured_dataset", self.id, data={"rows_written": count}),
                Artifact(PUBLIC_MATCH_ANALYSIS, "structured_dataset", self.id, data={"rows_written": analysis_count}),
                Artifact(EXTRACTION_DIAGNOSTICS, "structured_dataset", self.id, data={"rows_written": extraction_count}),
            ],
            diagnostics=diagnostics,
            metadata={"results": count, "analysis_rows": analysis_count, "extraction_rows": extraction_count},
        )

    def _fetch_espn(
        self,
        runtime: SourceRuntime,
        state: TournamentState,
        robots_cache: dict[str, tuple[bool, str]],
    ) -> PluginResult:
        dates = _espn_dates_to_fetch(state)
        if not dates:
            return runtime.result(diagnostics=[runtime.diagnostic("info", "ESPN scoreboard skipped because no due fixture dates are known.")])
        results: list[ResultRecord] = []
        diagnostics: list[Diagnostic] = []
        for date_value in dates:
            source_result = self._fetch_public_page(
                runtime,
                state,
                PublicPageSource(
                    source=SOURCE_ESPN_SCOREBOARD,
                    label="ESPN scoreboard",
                    endpoint=ENDPOINT_ESPN_SOCCER_SCOREBOARD,
                    purpose="espn_worldcup_scoreboard",
                    params={"league": "fifa.world", "dates": date_value},
                    min_refresh=dt.timedelta(minutes=runtime.context.config.source_defaults.default_refresh_minutes),
                ),
                parser=parse_espn_scoreboard_results,
                robots_cache=robots_cache,
            )
            diagnostics.extend(source_result.diagnostics)
            results.extend(source_result.metadata.get("results") or [])
        return runtime.result(diagnostics=diagnostics, metadata={"results": results})

    def _fetch_public_page(
        self,
        runtime: SourceRuntime,
        state: TournamentState,
        source: PublicPageSource,
        *,
        parser=None,
        robots_cache: dict[str, tuple[bool, str]] | None = None,
    ) -> PluginResult:
        allowed, robots_message = robots_allows(runtime, source.endpoint, cache=robots_cache)
        if not allowed:
            return runtime.result(
                diagnostics=[
                    runtime.diagnostic(
                        "info",
                        f"{source.label} skipped because robots.txt does not allow or could not confirm this path.",
                        metadata={"endpoint": source.endpoint, "robots": robots_message},
                    )
                ]
            )

        request = SourceRequest(
            source=source.source,
            endpoint=source.endpoint,
            purpose=source.purpose,
            params=source.params or {},
            quota_cost=0,
            min_refresh_interval=source.min_refresh,
        )
        decision = runtime.should_fetch(request)
        if not decision.should_fetch:
            return runtime.skipped_fetch_result(source.label, decision.reason, metadata=decision.metadata)
        try:
            page, _headers = runtime.fetch_text(source.endpoint, source.params)
        except (OSError, TimeoutError, urllib.error.HTTPError) as exc:
            runtime.record_error(request, exc)
            return runtime.result(diagnostics=[runtime.diagnostic("warning", f"{source.label} fetch failed.", metadata={"error": str(exc)})])
        page_results = (parser or parse_public_score_page_results)(page, state=state, source=source.source)
        analysis_rows, extraction_rows = public_page_analysis_rows(
            page,
            state=state,
            source=source.source,
            source_name=source.label,
            source_url=_url_with_params(source.endpoint, source.params or {}),
        )
        runtime.record_success(
            request,
            message=f"Fetched {source.label}.",
            metadata={"results": len(page_results), "analysis_rows": len(analysis_rows), "extraction_rows": len(extraction_rows)},
        )
        score_sparse_sources = {SOURCE_FIFA_MATCH_CENTRE, SOURCE_FOTMOB_PUBLIC, SOURCE_SOFASCORE_PUBLIC, SOURCE_TWENTY_MIN_PUBLIC}
        if source.source in score_sparse_sources and not page_results and not analysis_rows:
            return runtime.result(
                diagnostics=[
                    runtime.diagnostic(
                        "info",
                        f"{source.label} returned no parseable score or analysis rows from the allowed public page.",
                        metadata={"endpoint": source.endpoint},
                    )
                ],
                metadata={"results": page_results, "analysis_rows": analysis_rows, "extraction_rows": extraction_rows},
            )
        return runtime.result(metadata={"results": page_results, "analysis_rows": analysis_rows, "extraction_rows": extraction_rows})


def robots_allows(
    runtime: SourceRuntime,
    url: str,
    *,
    cache: dict[str, tuple[bool, str]] | None = None,
) -> tuple[bool, str]:
    if cache is not None and url in cache:
        return cache[url]
    parsed = urllib.parse.urlparse(url)
    robots_url = f"{parsed.scheme}://{parsed.netloc}/robots.txt"
    parser = urllib.robotparser.RobotFileParser()
    try:
        text, _headers = runtime.fetch_text(robots_url)
    except (OSError, TimeoutError, urllib.error.HTTPError) as exc:
        result = (False, str(exc))
        if cache is not None:
            cache[url] = result
        return result
    parser.parse(text.splitlines())
    result = (parser.can_fetch(runtime.context.config.user_agent, url), "robots.txt fetched")
    if cache is not None:
        cache[url] = result
    return result


def parse_espn_scoreboard_results(page: str, *, state: TournamentState, source: str = SOURCE_ESPN_SCOREBOARD) -> list[ResultRecord]:
    payload = _extract_espn_payload(page)
    events = _walk_for_espn_events(payload)
    resolver = TeamResolver.default(source=source)
    results: list[ResultRecord] = []
    fixture_by_codes = {(fixture.home_team.key, fixture.away_team.key, fixture.event_date[:10]): fixture for fixture in state.fixtures}
    for event in events:
        if not event.get("completed") and str((event.get("status") or {}).get("state") or "") != "post":
            continue
        competitors = list(event.get("competitors") or event.get("teams") or [])
        if len(competitors) < 2:
            continue
        parsed = _espn_home_away(competitors)
        if parsed is None:
            continue
        home_row, away_row = parsed
        home_team = resolver.resolve(str(home_row.get("displayName") or home_row.get("shortDisplayName") or home_row.get("abbrev") or ""))
        away_team = resolver.resolve(str(away_row.get("displayName") or away_row.get("shortDisplayName") or away_row.get("abbrev") or ""))
        if not home_team.fifa_code or not away_team.fifa_code:
            continue
        fixture_date = str(event.get("date") or "")[:10]
        fixture = fixture_by_codes.get((home_team.key, away_team.key, fixture_date))
        reversed_fixture = False
        if fixture is None:
            fixture = fixture_by_codes.get((away_team.key, home_team.key, fixture_date))
            reversed_fixture = fixture is not None
        event_date = fixture.event_date if fixture else normalize_datetime(event.get("date")) or str(event.get("date") or "")
        try:
            parsed_score = ScoreTip(int(home_row.get("score")), int(away_row.get("score")))
        except (TypeError, ValueError):
            continue
        if fixture is not None:
            result_home = fixture.home_team
            result_away = fixture.away_team
            score = ScoreTip(parsed_score.away, parsed_score.home) if reversed_fixture else parsed_score
        else:
            result_home = TeamRef(home_team.name, home_team.fifa_code)
            result_away = TeamRef(away_team.name, away_team.fifa_code)
            score = parsed_score
        results.append(
            ResultRecord(
                event_date=event_date,
                home_team=result_home,
                away_team=result_away,
                score=score,
                source=source,
                notes=str(event.get("note") or ""),
                metadata={
                    "espn_event_id": event.get("id"),
                    "status": event.get("status"),
                    "match_url": urllib.parse.urljoin("https://www.espn.com", str(event.get("link") or "")),
                    "source_page": "scoreboard",
                },
            )
        )
    return _dedupe_results(results)


def parse_public_score_page_results(page: str, *, state: TournamentState, source: str) -> list[ResultRecord]:
    text = _html_to_text(page)
    rows = []
    for fixture in state.fixtures:
        if fixture.home_team.name not in text or fixture.away_team.name not in text:
            continue
        score = _nearby_score(text, fixture.home_team.name, fixture.away_team.name)
        if score is None:
            continue
        rows.append(
            ResultRecord(
                event_date=fixture.event_date,
                home_team=fixture.home_team,
                away_team=fixture.away_team,
                score=score,
                source=source,
                metadata={"parser": "public_page_text"},
            )
        )
    return rows


def public_page_analysis_rows(
    page: str,
    *,
    state: TournamentState,
    source: str,
    source_name: str,
    source_url: str,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    text = _html_to_text(page)
    observed_at = normalize_datetime(utc_now()) or ""
    title = _extract_title(page) or source_name
    description = _extract_meta_description(page)
    result_keys = {result.fixture_key for result in state.results}
    rows: list[dict[str, Any]] = []
    diagnostics: list[dict[str, Any]] = []
    for fixture in state.fixtures:
        phase = _analysis_phase_for_fixture(fixture, result_keys)
        if phase is None:
            continue
        fixture_text = _fixture_text_window(text, fixture.home_team.name, fixture.away_team.name)
        article = {
            "source": {"name": source_name},
            "url": source_url,
            "publishedAt": observed_at,
            "title": title,
            "description": description,
            "content": fixture_text,
        }
        if not article_mentions_fixture(article, fixture):
            continue
        normalized_text = article_text(article)
        signal_type, factor = classify_tempo_signal(normalized_text)
        note_type, note_metadata = classify_public_note(normalized_text)
        postmatch_stats = extract_postmatch_stats_from_text(normalized_text, fixture) if phase == "postgame" else {}
        if signal_type is None and note_type is None and not postmatch_stats:
            diagnostics.append(
                extraction_diagnostic_row(
                    source=source,
                    extractor="public_page_analysis_v1",
                    status="rejected",
                    reason="fixture_mentioned_but_no_supported_signal_or_stat",
                    fixture_key=fixture.key,
                    phase=phase,
                    source_name=source_name,
                    source_url=source_url,
                    title=title,
                    metadata={"home_team": fixture.home_team.name, "away_team": fixture.away_team.name},
                )
            )
            continue
        row = article_base_row(article, fixture, phase=phase, observed_at=observed_at)
        signal = signal_type or note_type
        row.update(
            {
                "record_key": stable_hash({"fixture_key": fixture.key, "phase": phase, "url": source_url, "signal": signal}),
                "signal_type": signal,
                "total_goals_factor": factor,
                "postmatch_stats": postmatch_stats,
                "metadata": {
                    "extractor": "public_page_analysis_v1",
                    "page_source": source,
                    "tempo_extractor": "keyword_tempo_v1" if signal_type else "",
                    "note": note_metadata,
                    "postmatch_stats": postmatch_stats,
                },
            }
        )
        rows.append(row)
        diagnostics.append(
            extraction_diagnostic_row(
                source=source,
                extractor="public_page_analysis_v1",
                status="accepted",
                reason="accepted",
                fixture_key=fixture.key,
                phase=phase,
                source_name=source_name,
                source_url=source_url,
                title=title,
                metadata={"signal_type": signal, "has_postmatch_stats": bool(postmatch_stats), "home_team": fixture.home_team.name, "away_team": fixture.away_team.name},
            )
        )
    return rows, diagnostics


def _extract_espn_payload(page: str) -> dict[str, Any]:
    match = re.search(r"window\['__espnfitt__'\]\s*=\s*({.*?});\s*</script>", page, flags=re.DOTALL)
    if not match:
        match = re.search(r"window\[\"__espnfitt__\"\]\s*=\s*({.*?});\s*</script>", page, flags=re.DOTALL)
    if not match:
        return {}
    try:
        return json.loads(html.unescape(match.group(1)))
    except json.JSONDecodeError:
        return {}


def _walk_for_espn_events(value: Any) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    if isinstance(value, dict):
        if "competitors" in value and ("completed" in value or "status" in value):
            events.append(value)
        for nested in value.values():
            events.extend(_walk_for_espn_events(nested))
    elif isinstance(value, list):
        for nested in value:
            events.extend(_walk_for_espn_events(nested))
    return events


def _espn_home_away(competitors: list[dict[str, Any]]) -> tuple[dict[str, Any], dict[str, Any]] | None:
    home = next((row for row in competitors if row.get("isHome") is True), None)
    away = next((row for row in competitors if row.get("isHome") is False), None)
    if home and away:
        return home, away
    if len(competitors) >= 2:
        return competitors[1], competitors[0]
    return None


def _fixture_date_for_query(event_date: str) -> str:
    parsed = parse_utc_datetime(event_date)
    if parsed is None:
        return ""
    return parsed.strftime("%Y%m%d")


def _espn_dates_to_fetch(state: TournamentState, *, now: dt.datetime | None = None) -> list[str]:
    """Return only due ESPN scoreboard dates, including US-local date fallback."""

    current = now or dt.datetime.now(dt.UTC)
    dates: set[str] = set()
    for fixture in state.fixtures:
        kickoff = parse_utc_datetime(fixture.event_date)
        if kickoff is None or kickoff > current:
            continue
        dates.add(kickoff.strftime("%Y%m%d"))
        dates.add((kickoff - dt.timedelta(hours=12)).strftime("%Y%m%d"))
    return sorted(dates)


def _dedupe_results(results: list[ResultRecord]) -> list[ResultRecord]:
    by_key: dict[str, ResultRecord] = {}
    for result in results:
        by_key[result.record_key] = result
    return list(by_key.values())


def _analysis_phase_for_fixture(fixture, result_keys: set[str]) -> str | None:
    kickoff = fixture.kickoff_at
    now = dt.datetime.now(dt.UTC)
    if fixture.key in result_keys:
        return "postgame"
    if kickoff is None:
        return None
    if kickoff > now:
        return "pregame"
    return None


def _fixture_text_window(text: str, home: str, away: str, *, radius: int = 1000) -> str:
    if len(text) <= radius * 3:
        return text
    normalized = text.casefold()
    positions = [position for term in (home, away) if term and (position := normalized.find(term.casefold())) >= 0]
    if not positions:
        return text[: radius * 2]
    center = min(positions)
    start = max(0, center - radius)
    end = min(len(text), max(positions) + radius)
    return text[start:end]


def _extract_title(page: str) -> str:
    match = re.search(r"<title[^>]*>(.*?)</title>", page, flags=re.IGNORECASE | re.DOTALL)
    if not match:
        return ""
    return " ".join(html.unescape(re.sub(r"<[^>]+>", " ", match.group(1))).split())


def _extract_meta_description(page: str) -> str:
    match = re.search(r'<meta\s+[^>]*name=["\']description["\'][^>]*content=["\']([^"\']+)["\']', page, flags=re.IGNORECASE | re.DOTALL)
    if not match:
        match = re.search(r'<meta\s+[^>]*content=["\']([^"\']+)["\'][^>]*name=["\']description["\']', page, flags=re.IGNORECASE | re.DOTALL)
    if not match:
        return ""
    return " ".join(html.unescape(match.group(1)).split())


def _url_with_params(endpoint: str, params: dict[str, Any]) -> str:
    if not params:
        return endpoint
    return f"{endpoint}?{urllib.parse.urlencode(params)}"


def _nearby_score(text: str, home: str, away: str) -> ScoreTip | None:
    home_index = text.find(home)
    away_index = text.find(away)
    if home_index < 0 or away_index < 0:
        return None
    start = max(0, min(home_index, away_index) - 120)
    end = min(len(text), max(home_index, away_index) + 120)
    window = text[start:end]
    if not re.search(r"\b(Final|Full Time|FT|FT-Pens|After Penalties)\b", window, flags=re.IGNORECASE):
        return None
    for score_match in re.finditer(r"\b(\d{1,2})\s*[-:]\s*(\d{1,2})\b", window):
        return ScoreTip(int(score_match.group(1)), int(score_match.group(2)))
    return None


def _html_to_text(value: str) -> str:
    text = re.sub(r"<script\b.*?</script>", " ", value, flags=re.IGNORECASE | re.DOTALL)
    text = re.sub(r"<style\b.*?</style>", " ", text, flags=re.IGNORECASE | re.DOTALL)
    text = re.sub(r"<[^>]+>", " ", text)
    return " ".join(html.unescape(text).split())
