"""Automatic lineup, injury, and availability signal plugin."""

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
    SOURCE_LINEUP_AVAILABILITY,
    SOURCE_NEWS_API,
)
from worldcup_predictions.core.contracts import Diagnostic, Signal
from worldcup_predictions.core.datasets import EXTRACTION_DIAGNOSTICS
from worldcup_predictions.core.datasets import FIFA_MATCH_DETAILS
from worldcup_predictions.core.datasets import FOOTBALL_DATA_MATCH_DETAILS
from worldcup_predictions.core.datasets import LINEUP_CONSENSUS as LINEUP_CONSENSUS_DATASET
from worldcup_predictions.core.datasets import LINEUP_AVAILABILITY as LINEUP_AVAILABILITY_DATASET
from worldcup_predictions.core.events import EventName, event_value
from worldcup_predictions.core.extraction import extraction_diagnostic_row
from worldcup_predictions.core.metadata import EnvVar, PluginKind, PluginMetadata, QuotaPolicy
from worldcup_predictions.core.plugin import BasePlugin, PluginResult
from worldcup_predictions.core.signals import TEAM_EXPECTED_GOALS_FACTOR
from worldcup_predictions.plugins.article_sources import (
    article_base_row,
    article_is_pregame_for_fixture,
    article_text,
    fetch_news_api,
    lineup_query,
    mentioned_side,
)
from worldcup_predictions.plugins.source_runtime import SourceRuntime
from worldcup_predictions.storage.ledger import SourceRequest, normalize_datetime, stable_hash, utc_now
from worldcup_predictions.tournament.contracts import FixtureRecord


class LineupAvailabilityPlugin(BasePlugin):
    """Build automatic availability signals from reliable public sources."""

    id = "lineup_availability"
    version = "0.1.0"
    # Runs before public_analysis (270) so prediction-impacting lineup queries
    # spend the shared NewsAPI daily quota before postgame article backfill.
    priority = 265
    subscribed_events = (EventName.FEATURE_SIGNALS_REQUESTED.value,)
    metadata = PluginMetadata(
        plugin_id=id,
        kind=PluginKind.SOURCE,
        description="Fetch reliable public lineup/availability reports and emit side-specific xG factors.",
        datasets_read=(LINEUP_AVAILABILITY_DATASET, FOOTBALL_DATA_MATCH_DETAILS),
        datasets_written=(LINEUP_AVAILABILITY_DATASET, LINEUP_CONSENSUS_DATASET, EXTRACTION_DIAGNOSTICS),
        signals_emitted=(TEAM_EXPECTED_GOALS_FACTOR,),
        env_vars=(EnvVar(ENV_NEWS_API_KEY, required=False, description="NewsAPI key for bounded availability discovery."),),
        quota_policy=QuotaPolicy(
            quota_limited=True,
            ledger_required=True,
            description="Fixture-specific NewsAPI calls are skipped while fresh or near the quota floor.",
        ),
        confidence_policy="Reliable articles are aggregated per fixture/side and capped before model use.",
    )

    def handle(self, event, context, payload):
        runtime = SourceRuntime(self, event, context)
        if context.storage is None:
            return runtime.storage_unavailable_result("lineup availability")
        state = runtime.tournament_state()

        diagnostics: list[Diagnostic] = []
        written = 0
        extraction_written = 0
        api_key = runtime.env_value(ENV_NEWS_API_KEY)
        if api_key:
            for fixture in state.open_fixtures():
                result = self._fetch_fixture_lineup(runtime, fixture)
                diagnostics.extend(result.diagnostics)
                written += int(result.metadata.get("written_rows") or 0)
                extraction_written += int(result.metadata.get("extraction_diagnostics") or 0)
        else:
            diagnostics.append(
                runtime.diagnostic(
                    level="info",
                    message=f"{ENV_NEWS_API_KEY} is not configured; using stored lineup availability only.",
                )
            )

        rows = runtime.read_latest(LINEUP_AVAILABILITY_DATASET)
        detail_rows = [
            *football_data_detail_lineup_rows(context.storage.read_records(FOOTBALL_DATA_MATCH_DETAILS, latest_only=True)),
            *fifa_match_detail_formation_rows(context.storage.read_records(FIFA_MATCH_DETAILS, latest_only=True)),
        ]
        detail_count = context.storage.write_records(
            LINEUP_AVAILABILITY_DATASET,
            detail_rows,
            source=f"{self.id}:football_data_details",
            run_id=context.run_id,
        )
        written += detail_count
        rows = runtime.read_latest(LINEUP_AVAILABILITY_DATASET)
        consensus_rows = lineup_consensus_rows(rows)
        consensus_count = context.storage.write_records(
            LINEUP_CONSENSUS_DATASET,
            consensus_rows,
            source=self.id,
            run_id=context.run_id,
        )
        signals = lineup_availability_signals_from_rows(rows)
        return PluginResult(
            plugin_id=self.id,
            event=event_value(event),
            signals=signals,
            artifacts=[
                runtime.structured_artifact(LINEUP_AVAILABILITY_DATASET, rows_written=written, signals=len(signals)),
                runtime.structured_artifact(LINEUP_CONSENSUS_DATASET, rows_written=consensus_count),
                runtime.structured_artifact(EXTRACTION_DIAGNOSTICS, rows_written=extraction_written),
            ],
            diagnostics=diagnostics,
            metadata={
                "written_rows": written,
                "consensus_rows": consensus_count,
                "extraction_diagnostics": extraction_written,
                "signals": len(signals),
            },
        )

    def _fetch_fixture_lineup(self, runtime: SourceRuntime, fixture: FixtureRecord) -> PluginResult:
        api_key = runtime.env_value(ENV_NEWS_API_KEY)
        if not api_key:
            return runtime.result()
        query = lineup_query(fixture)
        request = SourceRequest(
            source=SOURCE_NEWS_API,
            endpoint=ENDPOINT_NEWS_API_EVERYTHING,
            purpose="lineup_availability",
            params={"q": query, "language": "en", "pageSize": NEWS_API_DEFAULT_PAGE_SIZE},
            fixture_key=fixture.key,
            quota_cost=1,
            min_refresh_interval=dt.timedelta(minutes=runtime.context.config.source_defaults.default_refresh_minutes),
            quota_remaining_floor=runtime.context.config.source_defaults.news_quota_remaining_floor,
        )
        decision = runtime.should_fetch(request)
        if not decision.should_fetch:
            return runtime.skipped_fetch_result("Lineup availability", decision.reason, fixture_key=fixture.key, metadata=decision.metadata)
        try:
            articles, _headers = fetch_news_api(
                query=query,
                page_size=NEWS_API_DEFAULT_PAGE_SIZE,
                http_client=runtime.http_client(),
            )
        except (OSError, TimeoutError, urllib.error.HTTPError, json.JSONDecodeError) as exc:
            runtime.record_error(request, exc)
            return runtime.result(
                diagnostics=[
                    runtime.diagnostic(
                        level="warning",
                        message="Lineup availability fetch failed; stored rows will be used.",
                        fixture_key=fixture.key,
                        metadata={"error": str(exc)},
                    )
                ],
            )
        rows, extraction_diagnostics = lineup_availability_rows_with_diagnostics(articles, fixture)
        runtime.record_success(
            request,
            message="Fetched lineup availability articles.",
            metadata={"articles": len(articles), "rows": len(rows), "rejections": _rejection_count(extraction_diagnostics)},
        )
        count = runtime.write_records(LINEUP_AVAILABILITY_DATASET, rows)
        diagnostic_count = runtime.write_records(EXTRACTION_DIAGNOSTICS, extraction_diagnostics)
        diagnostics = []
        if not rows:
            diagnostics.append(
                runtime.diagnostic(
                    "info",
                    "Lineup availability query returned no usable rows after reliability, timing, side, and keyword filters.",
                    fixture_key=fixture.key,
                    metadata={"articles": len(articles), "query": query},
                )
            )
        return runtime.result(diagnostics=diagnostics, metadata={"written_rows": count, "extraction_diagnostics": diagnostic_count})


def lineup_availability_rows_from_articles(articles: list[dict[str, Any]], fixture: FixtureRecord) -> list[dict[str, Any]]:
    rows, _diagnostics = lineup_availability_rows_with_diagnostics(articles, fixture)
    return rows


def lineup_availability_rows_with_diagnostics(
    articles: list[dict[str, Any]],
    fixture: FixtureRecord,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    observed_at = normalize_datetime(utc_now()) or ""
    rows = []
    diagnostics = []
    for article in articles:
        if not article_is_pregame_for_fixture(article, fixture):
            diagnostics.append(_article_diagnostic(article, fixture, status="rejected", reason="published_after_kickoff"))
            continue
        text = article_text(article)
        side = mentioned_side(text, fixture)
        if side is None:
            diagnostics.append(_article_diagnostic(article, fixture, status="rejected", reason="no_unambiguous_team_side"))
            continue
        signal_type, factor = classify_availability_signal(text)
        if signal_type is None:
            diagnostics.append(_article_diagnostic(article, fixture, status="rejected", reason="no_availability_signal"))
            continue
        row = article_base_row(article, fixture, phase="pregame", observed_at=observed_at)
        team = fixture.home_team if side == "home" else fixture.away_team
        row.update(
            {
                "record_key": stable_hash({"fixture_key": fixture.key, "url": row.get("source_url"), "side": side, "signal": signal_type}),
                "affected_side": side,
                "affected_team": team.name,
                "affected_fifa_code": team.fifa_code,
                "signal_type": signal_type,
                "expected_goals_factor": factor,
                "metadata": {"extractor": "availability_keywords_v1"},
            }
        )
        rows.append(row)
        diagnostics.append(
            _article_diagnostic(
                article,
                fixture,
                status="accepted",
                reason="accepted",
                metadata={"side": side, "signal_type": signal_type, "factor": factor, "reliability": row.get("reliability")},
            )
        )
    return rows, diagnostics


def lineup_availability_signals_from_rows(rows: list[dict[str, Any]]) -> list[Signal]:
    grouped: dict[tuple[str, str], list[dict[str, Any]]] = {}
    confirmed_sides: set[tuple[str, str]] = set()
    for row in rows:
        side = str(row.get("affected_side") or "")
        fixture_key = str(row.get("fixture_key") or "")
        # A confirmed/official lineup is a high-confidence marker, not an effect: it
        # carries a neutral factor, so keep it out of the weighted factor (it would
        # dilute genuine injury/rotation signals) and instead use it to raise confidence.
        if str(row.get("signal_type") or "") == "official_lineup_available":
            if fixture_key and side in {"home", "away"}:
                confirmed_sides.add((fixture_key, side))
            continue
        reliability = float(row.get("reliability") or 0.0)
        if reliability < RELIABILITY_SIGNAL_FLOOR:
            continue
        factor = row.get("expected_goals_factor")
        if factor is None or side not in {"home", "away"} or not fixture_key:
            continue
        grouped.setdefault((fixture_key, side), []).append(row)

    signals = []
    for (fixture_key, side), fixture_rows in grouped.items():
        weighted_factor = sum(float(row["expected_goals_factor"]) * float(row.get("reliability") or 0.0) for row in fixture_rows)
        total_weight = sum(float(row.get("reliability") or 0.0) for row in fixture_rows)
        if total_weight <= 0:
            continue
        factor = weighted_factor / total_weight
        reliability = min(0.90, total_weight / len(fixture_rows))
        confirmed_lineup = (fixture_key, side) in confirmed_sides
        if confirmed_lineup:
            # A confirmed XI removes guesswork about who is available.
            reliability = min(0.95, reliability + 0.05)
        signals.append(
            Signal(
                name="team_expected_goals_factor",
                source=SOURCE_LINEUP_AVAILABILITY,
                fixture_key=fixture_key,
                value=factor,
                weight=0.45,
                confidence=reliability,
                rationale=f"Availability consensus from {len(fixture_rows)} article(s)" + (" with a confirmed lineup." if confirmed_lineup else "."),
                metadata={
                    "side": side,
                    "signal_types": sorted({str(row.get("signal_type")) for row in fixture_rows}),
                    "affected_team": fixture_rows[0].get("affected_team"),
                    "source_urls": [row.get("source_url") for row in fixture_rows[:5]],
                    "reliability": reliability,
                    "confirmed_lineup": confirmed_lineup,
                },
            )
        )
    return signals


def lineup_consensus_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for row in rows:
        fixture_key = str(row.get("fixture_key") or "")
        side = str(row.get("affected_side") or "")
        if fixture_key and side in {"home", "away"}:
            grouped.setdefault((fixture_key, side), []).append(row)

    consensus = []
    for (fixture_key, side), side_rows in sorted(grouped.items()):
        reliable_rows = [row for row in side_rows if float(row.get("reliability") or 0.0) >= 0.70]
        factors = [
            float(row["expected_goals_factor"])
            for row in reliable_rows
            if row.get("expected_goals_factor") not in (None, "")
        ]
        consensus_factor = sum(factors) / len(factors) if factors else None
        first = side_rows[0]
        official_rows = [
            row
            for row in side_rows
            if str(row.get("signal_type") or "") == "official_lineup_available"
        ]
        consensus.append(
            {
                "record_key": f"{fixture_key}:{side}",
                "fixture_key": fixture_key,
                "event_date": first.get("event_date"),
                "home_team": first.get("home_team"),
                "away_team": first.get("away_team"),
                "home_fifa_code": first.get("home_fifa_code"),
                "away_fifa_code": first.get("away_fifa_code"),
                "affected_side": side,
                "affected_team": first.get("affected_team"),
                "affected_fifa_code": first.get("affected_fifa_code"),
                "evidence_count": len(side_rows),
                "reliable_evidence_count": len(reliable_rows),
                "official_lineup_count": len(official_rows),
                "consensus_expected_goals_factor": consensus_factor,
                "signal_types": sorted({str(row.get("signal_type")) for row in side_rows if row.get("signal_type")}),
                "source_urls": [row.get("source_url") for row in side_rows if row.get("source_url")][:8],
                "status": _lineup_consensus_status(len(reliable_rows), len(official_rows), consensus_factor),
                "metadata": {
                    "source_names": sorted({str(row.get("source_name")) for row in side_rows if row.get("source_name")}),
                    "avg_reliability": (
                        sum(float(row.get("reliability") or 0.0) for row in side_rows) / len(side_rows)
                        if side_rows
                        else None
                    ),
                },
            }
        )
    return consensus


def football_data_detail_lineup_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    derived = []
    for row in rows:
        fixture_key = str(row.get("fixture_key") or "")
        if not fixture_key:
            continue
        for side in ("home", "away"):
            lineup_count = int(row.get(f"{side}_lineup_count") or 0)
            if lineup_count <= 0:
                continue
            derived.append(
                {
                    "record_key": f"{fixture_key}:{side}:football_data_lineup",
                    "fixture_key": fixture_key,
                    "event_date": row.get("event_date"),
                    "phase": "pregame" if str(row.get("status") or "").casefold() not in {"finished", "awarded"} else "postgame",
                    "home_team": row.get("home_team"),
                    "away_team": row.get("away_team"),
                    "home_fifa_code": row.get("home_fifa_code"),
                    "away_fifa_code": row.get("away_fifa_code"),
                    "published_at": row.get("_record", {}).get("observed_at_utc"),
                    "observed_at_utc": row.get("_record", {}).get("observed_at_utc"),
                    "source_name": "football-data.org",
                    "source_url": "",
                    "title": "football-data.org lineup detail",
                    "description": f"{side} lineup has {lineup_count} player row(s)",
                    "reliability": 0.90,
                    "reliability_bucket": "official_or_wire",
                    "affected_side": side,
                    "affected_team": row.get(f"{side}_team"),
                    "affected_fifa_code": row.get(f"{side}_fifa_code"),
                    "signal_type": "official_lineup_available",
                    "expected_goals_factor": 1.0,
                    "metadata": {
                        "extractor": "football_data_match_detail_lineup_v1",
                        "lineup_count": lineup_count,
                        "lineup": row.get(f"{side}_lineup"),
                    },
                }
            )
    return derived


def fifa_match_detail_formation_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Convert FIFA official formation fields into neutral lineup-context evidence."""

    derived = []
    for row in rows:
        fixture_key = str(row.get("fixture_key") or "")
        if not fixture_key:
            continue
        for side in ("home", "away"):
            tactics = str(row.get(f"{side}_tactics") or "").strip()
            if not tactics:
                continue
            derived.append(
                {
                    "record_key": f"{fixture_key}:{side}:fifa_formation",
                    "fixture_key": fixture_key,
                    "event_date": row.get("event_date"),
                    "phase": "postgame" if row.get(f"{side}_score") is not None else "pregame",
                    "home_team": row.get("home_team"),
                    "away_team": row.get("away_team"),
                    "home_fifa_code": row.get("home_fifa_code"),
                    "away_fifa_code": row.get("away_fifa_code"),
                    "published_at": row.get("_record", {}).get("observed_at_utc"),
                    "observed_at_utc": row.get("_record", {}).get("observed_at_utc"),
                    "source_name": "FIFA match centre",
                    "source_url": (row.get("metadata") or {}).get("source_url") or "",
                    "title": "FIFA official formation detail",
                    "description": f"{side} formation listed as {tactics}",
                    "reliability": 0.95,
                    "reliability_bucket": "official_or_wire",
                    "affected_side": side,
                    "affected_team": row.get(f"{side}_team"),
                    "affected_fifa_code": row.get(f"{side}_fifa_code"),
                    "signal_type": "official_formation_available",
                    "expected_goals_factor": 1.0,
                    "metadata": {
                        "extractor": "fifa_match_centre_formation_v1",
                        "formation": tactics,
                        "fifa_match_id": row.get("fifa_match_id"),
                    },
                }
            )
    return derived


def classify_availability_signal(text: str) -> tuple[str | None, float | None]:
    positive_terms = ("returns", "returned", "fit", "available", "back in training", "cleared to play")
    suspension_terms = ("suspended", "suspension", "ban", "banned", "sent off")
    card_risk_terms = ("yellow card", "cards", "card accumulation", "accumulation", "booking")
    injury_terms = ("injured", "injury", "doubtful", "fitness test", "ruled out", "misses out")
    rotation_terms = ("rotation", "rested", "rests", "bench", "second string")
    if any(term in text for term in suspension_terms):
        return "suspension_risk", 0.93
    if any(term in text for term in card_risk_terms):
        return "card_accumulation_risk", 0.97
    if any(term in text for term in injury_terms):
        return "injury_or_fitness_risk", 0.96
    if any(term in text for term in rotation_terms):
        return "rotation_risk", 0.97
    if any(term in text for term in positive_terms):
        return "positive_availability", 1.03
    return None, None


def _lineup_consensus_status(reliable_count: int, official_count: int, factor: float | None) -> str:
    if official_count:
        return "official_lineup_available"
    if reliable_count >= 2 and factor is not None and abs(factor - 1.0) >= 0.02:
        return "actionable_consensus"
    if reliable_count:
        return "weak_consensus"
    return "unreliable_only"


def _article_diagnostic(
    article: dict[str, Any],
    fixture: FixtureRecord,
    *,
    status: str,
    reason: str,
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    source = article.get("source") or {}
    return extraction_diagnostic_row(
        source=SOURCE_LINEUP_AVAILABILITY,
        extractor="lineup_availability_v1",
        status=status,
        reason=reason,
        fixture_key=fixture.key,
        phase="pregame",
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
