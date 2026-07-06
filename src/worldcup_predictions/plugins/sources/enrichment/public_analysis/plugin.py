"""Reliable public pregame and postgame analysis plugin."""

from __future__ import annotations

import datetime as dt
import json
import urllib.error
from typing import Any

from worldcup_predictions.core.constants import (
    ENDPOINT_NEWS_API_EVERYTHING,
    ENV_NEWS_API_KEY,
    NEWS_API_DEFAULT_PAGE_SIZE,
    RELIABILITY_SIGNAL_FLOOR,
    SOURCE_NEWS_API,
    SOURCE_PUBLIC_ANALYSIS,
)
from worldcup_predictions.core.contracts import Diagnostic, Signal
from worldcup_predictions.core.datasets import EXTRACTION_DIAGNOSTICS
from worldcup_predictions.core.datasets import PUBLIC_MATCH_ANALYSIS as PUBLIC_ANALYSIS_DATASET
from worldcup_predictions.core.datasets import MATCH_ANALYSIS_CAUSES, MATCH_ANALYSIS_TEAM_ADJUSTMENTS
from worldcup_predictions.core.events import EventName, event_value
from worldcup_predictions.core.extraction import extraction_diagnostic_row
from worldcup_predictions.core.metadata import EnvVar, PluginKind, PluginMetadata, QuotaPolicy
from worldcup_predictions.core.plugin import BasePlugin, PluginResult
from worldcup_predictions.core.signals import TOTAL_GOALS_FACTOR
from worldcup_predictions.plugins.article_sources import (
    analysis_query,
    article_base_row,
    article_mentions_fixture,
    article_is_pregame_for_fixture,
    article_text,
    classify_public_note,
    classify_tempo_signal,
    extract_postmatch_stats_from_text,
    fetch_news_api,
)
from worldcup_predictions.plugins.source_runtime import SourceRuntime
from worldcup_predictions.storage.ledger import SourceRequest, normalize_datetime, stable_hash, utc_now
from worldcup_predictions.tournament import TournamentState
from worldcup_predictions.tournament.contracts import FixtureRecord


class PublicAnalysisPlugin(BasePlugin):
    """Extract conservative tactical/tempo notes from reliable public articles."""

    id = "public_analysis"
    version = "0.1.0"
    priority = 270
    subscribed_events = (EventName.FEATURE_SIGNALS_REQUESTED.value,)
    metadata = PluginMetadata(
        plugin_id=id,
        kind=PluginKind.SOURCE,
        description="Fetch reliable public pre/postgame articles and emit conservative tempo signals.",
        datasets_read=(PUBLIC_ANALYSIS_DATASET,),
        datasets_written=(PUBLIC_ANALYSIS_DATASET, MATCH_ANALYSIS_CAUSES, MATCH_ANALYSIS_TEAM_ADJUSTMENTS, EXTRACTION_DIAGNOSTICS),
        signals_emitted=(TOTAL_GOALS_FACTOR,),
        env_vars=(EnvVar(ENV_NEWS_API_KEY, required=False, description="NewsAPI key for bounded article discovery."),),
        quota_policy=QuotaPolicy(
            quota_limited=True,
            ledger_required=True,
            description="Fixture-specific NewsAPI calls are skipped while fresh or near the quota floor.",
        ),
        confidence_policy="Only reliable domains above threshold are aggregated into capped total-goal factors.",
    )

    def handle(self, event, context, payload):
        runtime = SourceRuntime(self, event, context)
        if context.storage is None:
            return runtime.storage_unavailable_result("public analysis")

        state = runtime.tournament_state()

        diagnostics: list[Diagnostic] = []
        written = 0
        extraction_written = 0
        api_key = runtime.env_value(ENV_NEWS_API_KEY)
        if api_key:
            for fixture in state.open_fixtures():
                result = self._fetch_fixture_analysis(runtime, fixture, phase="pregame")
                diagnostics.extend(result.diagnostics)
                written += int(result.metadata.get("written_rows") or 0)
                extraction_written += int(result.metadata.get("extraction_diagnostics") or 0)
            for fixture in _recent_finished_fixtures(state):
                result = self._fetch_fixture_analysis(runtime, fixture, phase="postgame")
                diagnostics.extend(result.diagnostics)
                written += int(result.metadata.get("written_rows") or 0)
                extraction_written += int(result.metadata.get("extraction_diagnostics") or 0)
        else:
            diagnostics.append(
                runtime.diagnostic(
                    level="info",
                    message=f"{ENV_NEWS_API_KEY} is not configured; using stored public analysis only.",
                )
            )

        rows = runtime.read_latest(PUBLIC_ANALYSIS_DATASET)
        causes = match_analysis_cause_rows(rows)
        adjustments = match_analysis_team_adjustment_rows(causes)
        cause_count = runtime.write_records(MATCH_ANALYSIS_CAUSES, causes)
        adjustment_count = runtime.write_records(MATCH_ANALYSIS_TEAM_ADJUSTMENTS, adjustments)
        signals = public_analysis_signals_from_rows(rows)
        return PluginResult(
            plugin_id=self.id,
            event=event_value(event),
            signals=signals,
            artifacts=[
                runtime.structured_artifact(PUBLIC_ANALYSIS_DATASET, rows_written=written, signals=len(signals)),
                runtime.structured_artifact(MATCH_ANALYSIS_CAUSES, rows_written=cause_count),
                runtime.structured_artifact(MATCH_ANALYSIS_TEAM_ADJUSTMENTS, rows_written=adjustment_count),
                runtime.structured_artifact(EXTRACTION_DIAGNOSTICS, rows_written=extraction_written),
            ],
            diagnostics=diagnostics,
            metadata={
                "written_rows": written,
                "cause_rows": cause_count,
                "team_adjustment_rows": adjustment_count,
                "extraction_diagnostics": extraction_written,
                "signals": len(signals),
            },
        )

    def _fetch_fixture_analysis(self, runtime: SourceRuntime, fixture: FixtureRecord, *, phase: str) -> PluginResult:
        api_key = runtime.env_value(ENV_NEWS_API_KEY)
        if not api_key:
            return runtime.result()
        query = analysis_query(fixture, phase=phase)
        request = SourceRequest(
            source=SOURCE_NEWS_API,
            endpoint=ENDPOINT_NEWS_API_EVERYTHING,
            purpose=f"{phase}_match_analysis",
            params={"q": query, "language": "en", "pageSize": NEWS_API_DEFAULT_PAGE_SIZE},
            fixture_key=fixture.key,
            quota_cost=1,
            min_refresh_interval=dt.timedelta(minutes=runtime.context.config.source_defaults.default_refresh_minutes),
            quota_remaining_floor=runtime.context.config.source_defaults.news_quota_remaining_floor,
            quota_scope=SOURCE_NEWS_API,
            rate_limit_backoff=dt.timedelta(hours=6),
        )
        decision = runtime.should_fetch(request)
        if not decision.should_fetch:
            return runtime.skipped_fetch_result("Public analysis", decision.reason, fixture_key=fixture.key, metadata=decision.metadata)
        try:
            articles, _headers = fetch_news_api(
                query=query,
                page_size=NEWS_API_DEFAULT_PAGE_SIZE,
                fetcher=runtime.fetch_json,
            )
        except (OSError, TimeoutError, urllib.error.HTTPError, json.JSONDecodeError) as exc:
            runtime.record_error(request, exc)
            return runtime.result(
                diagnostics=[
                    runtime.diagnostic(
                        level="warning",
                        message="Public analysis fetch failed; stored analysis rows will be used.",
                        fixture_key=fixture.key,
                        metadata={"error": str(exc)},
                    )
                ],
            )

        rows, extraction_diagnostics = public_analysis_rows_with_diagnostics(articles, fixture, phase=phase)
        runtime.record_success(
            request,
            message="Fetched public analysis articles.",
            metadata={"articles": len(articles), "rows": len(rows), "rejections": _rejection_count(extraction_diagnostics), "query": query},
        )
        count = runtime.write_records(PUBLIC_ANALYSIS_DATASET, rows)
        diagnostic_count = runtime.write_records(EXTRACTION_DIAGNOSTICS, extraction_diagnostics)
        diagnostics = []
        if not rows:
            diagnostics.append(
                runtime.diagnostic(
                    "info",
                    "Public analysis query returned no usable rows after reliability, timing, and fixture filters.",
                    fixture_key=fixture.key,
                    metadata={"phase": phase, "articles": len(articles), "query": query},
                )
            )
        return runtime.result(diagnostics=diagnostics, metadata={"written_rows": count, "extraction_diagnostics": diagnostic_count})


def public_analysis_rows_from_articles(
    articles: list[dict[str, Any]],
    fixture: FixtureRecord,
    *,
    phase: str,
) -> list[dict[str, Any]]:
    rows, _diagnostics = public_analysis_rows_with_diagnostics(articles, fixture, phase=phase)
    return rows


def public_analysis_rows_with_diagnostics(
    articles: list[dict[str, Any]],
    fixture: FixtureRecord,
    *,
    phase: str,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    observed_at = normalize_datetime(utc_now()) or ""
    rows = []
    diagnostics = []
    for article in articles:
        if phase == "pregame" and not article_is_pregame_for_fixture(article, fixture):
            diagnostics.append(_article_diagnostic(article, fixture, phase=phase, status="rejected", reason="published_after_kickoff"))
            continue
        if not article_mentions_fixture(article, fixture):
            diagnostics.append(_article_diagnostic(article, fixture, phase=phase, status="rejected", reason="fixture_not_mentioned"))
            continue
        text = article_text(article)
        signal_type, factor = classify_tempo_signal(text)
        note_type, note_metadata = classify_public_note(text)
        postmatch_stats = extract_postmatch_stats_from_text(text, fixture) if phase == "postgame" else {}
        if signal_type is None and note_type is None and not postmatch_stats:
            diagnostics.append(_article_diagnostic(article, fixture, phase=phase, status="rejected", reason="no_supported_signal_or_stat"))
            continue
        row = article_base_row(article, fixture, phase=phase, observed_at=observed_at)
        signal = signal_type or note_type
        row.update(
            {
                "record_key": stable_hash({"fixture_key": fixture.key, "phase": phase, "url": row.get("source_url"), "signal": signal}),
                "signal_type": signal,
                "total_goals_factor": factor,
                "postmatch_stats": postmatch_stats,
                "metadata": {
                    "extractor": "public_analysis_v2",
                    "tempo_extractor": "keyword_tempo_v1" if signal_type else "",
                    "note": note_metadata,
                    "postmatch_stats": postmatch_stats,
                },
            }
        )
        rows.append(row)
        diagnostics.append(
            _article_diagnostic(
                article,
                fixture,
                phase=phase,
                status="accepted",
                reason="accepted",
                metadata={"signal_type": signal, "has_postmatch_stats": bool(postmatch_stats), "reliability": row.get("reliability")},
            )
        )
    return rows, diagnostics


def public_analysis_signals_from_rows(rows: list[dict[str, Any]]) -> list[Signal]:
    grouped: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        if row.get("phase") != "pregame":
            continue
        reliability = float(row.get("reliability") or 0.0)
        if reliability < RELIABILITY_SIGNAL_FLOOR:
            continue
        factor = row.get("total_goals_factor")
        if factor is None:
            continue
        fixture_key = str(row.get("fixture_key") or "")
        if fixture_key:
            grouped.setdefault(fixture_key, []).append(row)

    signals = []
    for fixture_key, fixture_rows in grouped.items():
        weighted_factor = sum(float(row["total_goals_factor"]) * float(row.get("reliability") or 0.0) for row in fixture_rows)
        total_weight = sum(float(row.get("reliability") or 0.0) for row in fixture_rows)
        if total_weight <= 0:
            continue
        factor = weighted_factor / total_weight
        reliability = min(0.90, total_weight / len(fixture_rows))
        signals.append(
            Signal(
                name="total_goals_factor",
                source=SOURCE_PUBLIC_ANALYSIS,
                fixture_key=fixture_key,
                value=factor,
                weight=0.35,
                confidence=reliability,
                rationale=f"Reliable public analysis consensus from {len(fixture_rows)} article(s).",
                metadata={
                    "signal_types": sorted({str(row.get("signal_type")) for row in fixture_rows}),
                    "source_urls": [row.get("source_url") for row in fixture_rows[:5]],
                    "reliability": reliability,
                },
            )
        )
    return signals


def match_analysis_cause_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    cause_rows = []
    for row in rows:
        if row.get("phase") != "postgame":
            continue
        reliability = float(row.get("reliability") or 0.0)
        if reliability < 0.60:
            continue
        note = dict((row.get("metadata") or {}).get("note") or {})
        categories = list(note.get("categories") or [])
        if not categories and row.get("signal_type"):
            categories = [str(row["signal_type"])]
        for category in categories:
            side = _mentioned_side_from_row(row)
            cause_rows.append(
                {
                    "record_key": stable_hash(
                        {
                            "fixture_key": row.get("fixture_key"),
                            "source_url": row.get("source_url"),
                            "cause_type": category,
                            "side": side,
                        }
                    ),
                    "fixture_key": row.get("fixture_key"),
                    "event_date": row.get("event_date"),
                    "home_team": row.get("home_team"),
                    "away_team": row.get("away_team"),
                    "home_fifa_code": row.get("home_fifa_code"),
                    "away_fifa_code": row.get("away_fifa_code"),
                    "cause_type": category,
                    "affected_side": side,
                    "affected_team": _team_for_side(row, side),
                    "confidence": min(0.90, reliability),
                    "source_name": row.get("source_name"),
                    "source_url": row.get("source_url"),
                    "evidence": " ".join(str(row.get(key) or "") for key in ("title", "description"))[:500],
                    "metadata": {
                        "source_tier": row.get("source_tier"),
                        "reliability_reasons": row.get("reliability_reasons"),
                        "postmatch_stats": row.get("postmatch_stats"),
                    },
                }
            )
    return cause_rows


def match_analysis_team_adjustment_rows(cause_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for row in cause_rows:
        side = str(row.get("affected_side") or "")
        if side not in {"home", "away"}:
            continue
        team = str(row.get("affected_team") or "")
        fixture_key = str(row.get("fixture_key") or "")
        if not team or not fixture_key:
            continue
        grouped.setdefault((fixture_key, team), []).append(row)

    rows = []
    for (fixture_key, team), team_causes in sorted(grouped.items()):
        factor = 1.0
        reasons = []
        confidence = max(float(row.get("confidence") or 0.0) for row in team_causes)
        for cause in team_causes:
            cause_type = str(cause.get("cause_type") or "")
            if cause_type == "red_card_context":
                factor *= 0.995
                reasons.append("red-card context makes the result slightly less predictive")
            elif cause_type == "finishing_context":
                factor *= 1.010
                reasons.append("finishing/chance-quality context")
            elif cause_type == "set_piece_context":
                factor *= 1.005
                reasons.append("set-piece context")
            elif cause_type == "weather_context":
                factor *= 0.998
                reasons.append("weather context makes the result slightly less predictive")
            elif cause_type == "suspension_context":
                factor *= 0.992
                reasons.append("suspension/card context makes the result less representative")
            elif cause_type in {"extra_time_context", "penalty_shootout_context"}:
                factor *= 0.996
                reasons.append("knockout resolution context makes the result slightly less predictive")
        if abs(factor - 1.0) < 0.001:
            continue
        first = team_causes[0]
        rows.append(
            {
                "record_key": stable_hash({"fixture_key": fixture_key, "team": team, "causes": sorted({row.get("cause_type") for row in team_causes})}),
                "fixture_key": fixture_key,
                "event_date": first.get("event_date"),
                "team": team,
                "fifa_code": first.get("home_fifa_code") if first.get("affected_side") == "home" else first.get("away_fifa_code"),
                "expected_goals_factor": _clamp(factor, 0.97, 1.03),
                "confidence": min(0.65, confidence),
                "cause_types": sorted({str(row.get("cause_type")) for row in team_causes}),
                "rationale": "; ".join(dict.fromkeys(reasons)),
            }
        )
    return rows


def _mentioned_side_from_row(row: dict[str, Any]) -> str:
    description = str(row.get("description") or "").casefold()
    title = str(row.get("title") or "").casefold()
    home = str(row.get("home_team") or "").casefold()
    away = str(row.get("away_team") or "").casefold()
    for text in (description, f"{title} {description}"):
        home_mentioned = bool(home and home in text)
        away_mentioned = bool(away and away in text)
        if home_mentioned and not away_mentioned:
            return "home"
        if away_mentioned and not home_mentioned:
            return "away"
    return "both"


def _team_for_side(row: dict[str, Any], side: str) -> str | None:
    if side == "home":
        return row.get("home_team")
    if side == "away":
        return row.get("away_team")
    return None


def _clamp(value: float, lower: float, upper: float) -> float:
    return max(lower, min(upper, value))


def _recent_finished_fixtures(state: TournamentState) -> list[FixtureRecord]:
    result_keys = {result.fixture_key for result in state.results}
    now = dt.datetime.now(dt.timezone.utc)
    recent = []
    for fixture in state.fixtures:
        kickoff = fixture.kickoff_at
        if fixture.key in result_keys and kickoff and now - dt.timedelta(days=3) <= kickoff <= now:
            recent.append(fixture)
    return sorted(recent, key=lambda item: item.event_date)


def _article_diagnostic(
    article: dict[str, Any],
    fixture: FixtureRecord,
    *,
    phase: str,
    status: str,
    reason: str,
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    source = article.get("source") or {}
    return extraction_diagnostic_row(
        source=SOURCE_PUBLIC_ANALYSIS,
        extractor="public_analysis_v2",
        status=status,
        reason=reason,
        fixture_key=fixture.key,
        phase=phase,
        source_name=source.get("name"),
        source_url=article.get("url"),
        title=article.get("title"),
        metadata={
            "published_at": article.get("publishedAt"),
            "home_team": fixture.home_team.name,
            "away_team": fixture.away_team.name,
            **dict(metadata or {}),
        },
    )


def _rejection_count(rows: list[dict[str, Any]]) -> int:
    return sum(1 for row in rows if row.get("status") == "rejected")
