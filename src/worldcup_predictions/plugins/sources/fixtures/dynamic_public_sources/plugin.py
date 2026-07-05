"""Dynamic public-source crawling and claim consensus."""

from __future__ import annotations

import datetime as dt
import hashlib
import urllib.error
from dataclasses import dataclass, field
from typing import Any

from worldcup_predictions.core.constants import SOURCE_DYNAMIC_PUBLIC
from worldcup_predictions.core.contracts import Artifact, Diagnostic
from worldcup_predictions.core.datasets import (
    EXTRACTION_DIAGNOSTICS,
    PUBLIC_CLAIM_CONSENSUS,
    PUBLIC_MARKET_OBSERVATIONS,
    PUBLIC_MATCH_ANALYSIS,
    PUBLIC_SOURCE_CLAIMS,
    PUBLIC_SOURCE_PAGES,
    PUBLIC_SOURCE_REPUTATION,
    TOURNAMENT_RESULTS,
)
from worldcup_predictions.core.events import EventName, event_value
from worldcup_predictions.core.extraction import extraction_diagnostic_row
from worldcup_predictions.core.metadata import PluginKind, PluginMetadata, QuotaPolicy
from worldcup_predictions.core.plugin import BasePlugin, PluginResult
from worldcup_predictions.plugins.source_runtime import SourceRuntime
from worldcup_predictions.plugins.sources.fixtures.dynamic_public_sources.consensus import (
    build_claim_consensus_rows,
    result_records_from_consensus,
)
from worldcup_predictions.plugins.sources.fixtures.dynamic_public_sources.crawler import (
    DiscoveredLink,
    discover_candidate_links,
    page_description,
    page_title,
    scrapy_selector_available,
)
from worldcup_predictions.plugins.sources.fixtures.dynamic_public_sources.extractors import extract_claims_from_page
from worldcup_predictions.plugins.sources.fixtures.dynamic_public_sources.reputation import build_reputation_rows
from worldcup_predictions.plugins.sources.fixtures.dynamic_public_sources.seeds import (
    domain_from_url,
    dynamic_public_seeds,
    split_url_params,
)
from worldcup_predictions.plugins.sources.fixtures.public_score_sources.plugin import public_page_analysis_rows, robots_allows
from worldcup_predictions.storage.ledger import SourceRequest, normalize_datetime, stable_hash, utc_now
from worldcup_predictions.tournament import ResultRecord, TournamentState
from worldcup_predictions.tournament.repository import load_tournament_state, write_derived_state, write_results


@dataclass
class PageExtractionResult:
    """Structured output from one dynamic public page attempt."""

    page_rows: list[dict[str, Any]] = field(default_factory=list)
    claims: list[dict[str, Any]] = field(default_factory=list)
    market_rows: list[dict[str, Any]] = field(default_factory=list)
    analysis_rows: list[dict[str, Any]] = field(default_factory=list)
    extraction_rows: list[dict[str, Any]] = field(default_factory=list)
    diagnostics: list[Diagnostic] = field(default_factory=list)
    discovered_links: list[DiscoveredLink] = field(default_factory=list)


class DynamicPublicSourcesPlugin(BasePlugin):
    """Fetch bounded public pages, extract claims, and promote only consensus facts."""

    id = "dynamic_public_sources"
    version = "0.1.0"
    priority = 132
    subscribed_events = (EventName.FIXTURES_REQUESTED.value,)
    metadata = PluginMetadata(
        plugin_id=id,
        kind=PluginKind.SOURCE,
        description="Discover robots-aware public pages, extract fixture/result/market claims, and score domain reputation over time.",
        datasets_read=(PUBLIC_SOURCE_CLAIMS, PUBLIC_SOURCE_REPUTATION, TOURNAMENT_RESULTS),
        datasets_written=(
            TOURNAMENT_RESULTS,
            PUBLIC_SOURCE_PAGES,
            PUBLIC_SOURCE_CLAIMS,
            PUBLIC_CLAIM_CONSENSUS,
            PUBLIC_SOURCE_REPUTATION,
            PUBLIC_MARKET_OBSERVATIONS,
            PUBLIC_MATCH_ANALYSIS,
            EXTRACTION_DIAGNOSTICS,
        ),
        quota_policy=QuotaPolicy(
            quota_limited=False,
            ledger_required=True,
            description="Every page fetch is robots-gated, source-ledgered, cache-validator aware, and rate-limit backed off per domain.",
        ),
        confidence_policy="Dynamic public results require multi-domain weighted consensus before becoming raw result observations; market rows need a confidence floor before trend use.",
    )

    def handle(self, event, context, payload):
        runtime = SourceRuntime(self, event, context)
        if context.storage is None:
            return runtime.storage_unavailable_result("dynamic public sources")

        state = runtime.tournament_state()
        reputation_by_type = _reputation_map(runtime.read_latest(PUBLIC_SOURCE_REPUTATION))
        robots_cache: dict[str, tuple[bool, str]] = {}
        seen_urls: set[str] = set()
        collected = PageExtractionResult()

        for seed in dynamic_public_seeds(state):
            seed_result = self._fetch_and_extract(
                runtime,
                state,
                url=seed.url,
                label=seed.label,
                purpose=seed.purpose,
                min_refresh=seed.min_refresh,
                max_discovered_links=seed.max_discovered_links,
                reputation_by_type=reputation_by_type,
                robots_cache=robots_cache,
                seen_urls=seen_urls,
            )
            _extend(collected, seed_result)
            for link in seed_result.discovered_links:
                link_result = self._fetch_and_extract(
                    runtime,
                    state,
                    url=link.url,
                    label=link.label or domain_from_url(link.url),
                    purpose="dynamic_public_discovered_page",
                    min_refresh=dt.timedelta(hours=6),
                    max_discovered_links=0,
                    reputation_by_type=reputation_by_type,
                    robots_cache=robots_cache,
                    seen_urls=seen_urls,
                )
                _extend(collected, link_result)

        all_claims = _dedupe_claims([*runtime.read_latest(PUBLIC_SOURCE_CLAIMS), *collected.claims])
        consensus_rows = build_claim_consensus_rows(all_claims)
        reputation_rows = build_reputation_rows(all_claims, state.results)
        promoted_results = _dedupe_results(result_records_from_consensus(consensus_rows, all_claims))

        page_count = runtime.write_records(PUBLIC_SOURCE_PAGES, collected.page_rows)
        claim_count = runtime.write_records(PUBLIC_SOURCE_CLAIMS, collected.claims)
        consensus_count = runtime.write_records(PUBLIC_CLAIM_CONSENSUS, consensus_rows)
        reputation_count = runtime.write_records(PUBLIC_SOURCE_REPUTATION, reputation_rows)
        market_count = runtime.write_records(PUBLIC_MARKET_OBSERVATIONS, collected.market_rows)
        analysis_count = runtime.write_records(PUBLIC_MATCH_ANALYSIS, collected.analysis_rows)
        extraction_count = runtime.write_records(EXTRACTION_DIAGNOSTICS, collected.extraction_rows)
        result_count = write_results(runtime.storage, promoted_results, source=self.id, run_id=runtime.context.run_id)

        if result_count:
            refreshed_state = load_tournament_state(runtime.storage)
            write_derived_state(runtime.storage, refreshed_state, run_id=runtime.context.run_id)
            runtime.context.state["tournament_state"] = refreshed_state

        return PluginResult(
            plugin_id=self.id,
            event=event_value(event),
            artifacts=[
                Artifact(TOURNAMENT_RESULTS, "structured_dataset", self.id, data={"rows_written": result_count}),
                Artifact(PUBLIC_SOURCE_PAGES, "structured_dataset", self.id, data={"rows_written": page_count}),
                Artifact(PUBLIC_SOURCE_CLAIMS, "structured_dataset", self.id, data={"rows_written": claim_count}),
                Artifact(PUBLIC_CLAIM_CONSENSUS, "structured_dataset", self.id, data={"rows_written": consensus_count}),
                Artifact(PUBLIC_SOURCE_REPUTATION, "structured_dataset", self.id, data={"rows_written": reputation_count}),
                Artifact(PUBLIC_MARKET_OBSERVATIONS, "structured_dataset", self.id, data={"rows_written": market_count}),
                Artifact(PUBLIC_MATCH_ANALYSIS, "structured_dataset", self.id, data={"rows_written": analysis_count}),
                Artifact(EXTRACTION_DIAGNOSTICS, "structured_dataset", self.id, data={"rows_written": extraction_count}),
            ],
            diagnostics=collected.diagnostics,
            metadata={
                "pages": page_count,
                "claims": claim_count,
                "consensus_rows": consensus_count,
                "reputation_rows": reputation_count,
                "market_rows": market_count,
                "analysis_rows": analysis_count,
                "extraction_rows": extraction_count,
                "promoted_results": result_count,
                "scrapy_selector_available": scrapy_selector_available(),
            },
        )

    def _fetch_and_extract(
        self,
        runtime: SourceRuntime,
        state: TournamentState,
        *,
        url: str,
        label: str,
        purpose: str,
        min_refresh: dt.timedelta,
        max_discovered_links: int,
        reputation_by_type: dict[tuple[str, str], float],
        robots_cache: dict[str, tuple[bool, str]],
        seen_urls: set[str],
    ) -> PageExtractionResult:
        if url in seen_urls:
            return PageExtractionResult()
        seen_urls.add(url)
        domain = domain_from_url(url)
        endpoint, params = split_url_params(url)
        source = f"{SOURCE_DYNAMIC_PUBLIC}:{domain}"
        result = PageExtractionResult()

        allowed, robots_message = robots_allows(runtime, endpoint, cache=robots_cache)
        if not allowed:
            result.page_rows.append(_page_row(url=url, domain=domain, status="robots_disallowed", metadata={"robots": robots_message}))
            result.diagnostics.append(
                runtime.diagnostic(
                    "info",
                    "Dynamic public page skipped because robots.txt does not allow or could not confirm this path.",
                    metadata={"url": url, "robots": robots_message},
                )
            )
            result.extraction_rows.append(
                extraction_diagnostic_row(
                    source=SOURCE_DYNAMIC_PUBLIC,
                    extractor="dynamic_public_fetch_v1",
                    status="rejected",
                    reason="robots_disallowed_or_unconfirmed",
                    source_name=label,
                    source_url=url,
                    metadata={"domain": domain, "robots": robots_message},
                )
            )
            return result

        request = SourceRequest(
            source=source,
            endpoint=endpoint,
            purpose=purpose,
            params=params,
            quota_cost=0,
            min_refresh_interval=min_refresh,
            quota_scope=source,
            rate_limit_backoff=dt.timedelta(hours=6),
        )
        decision = runtime.should_fetch(request)
        if not decision.should_fetch:
            result.page_rows.append(
                _page_row(
                    url=url,
                    domain=domain,
                    status="skipped",
                    metadata={"reason": decision.reason, "decision_metadata": decision.metadata},
                )
            )
            result.diagnostics.extend(runtime.skipped_fetch_result(label or url, decision.reason, metadata=decision.metadata).diagnostics)
            return result

        try:
            page, headers = runtime.fetch_text(endpoint, params)
        except (OSError, TimeoutError, urllib.error.HTTPError) as exc:
            runtime.record_error(request, exc)
            result.page_rows.append(
                _page_row(
                    url=url,
                    domain=domain,
                    status="error",
                    metadata={"error": str(exc)},
                )
            )
            result.diagnostics.append(runtime.diagnostic("warning", "Dynamic public page fetch failed.", metadata={"url": url, "error": str(exc)}))
            return result

        if _request_not_modified(runtime, request):
            runtime.record_success(
                request,
                message="Dynamic public page was not modified.",
                metadata={"url": url, "claims": 0, "market_rows": 0, "analysis_rows": 0},
            )
            result.page_rows.append(
                _page_row(
                    url=url,
                    domain=domain,
                    status="not_modified",
                    metadata={"headers": _header_summary(headers)},
                )
            )
            return result

        discovered_links = discover_candidate_links(page, base_url=url, state=state, limit=max_discovered_links)
        claims, market_rows, extraction_rows = extract_claims_from_page(
            page,
            state=state,
            source_url=url,
            domain=domain,
            source_name=label or domain,
            reputation_by_type=reputation_by_type,
        )
        analysis_rows, analysis_diagnostics = public_page_analysis_rows(
            page,
            state=state,
            source=SOURCE_DYNAMIC_PUBLIC,
            source_name=label or domain,
            source_url=url,
        )
        result.claims.extend(claims)
        result.market_rows.extend(market_rows)
        result.analysis_rows.extend(analysis_rows)
        result.extraction_rows.extend(extraction_rows)
        result.extraction_rows.extend(analysis_diagnostics)
        result.discovered_links.extend(discovered_links)
        result.page_rows.append(
            _page_row(
                url=url,
                domain=domain,
                status="success",
                title=page_title(page),
                description=page_description(page),
                content_hash=hashlib.sha256(page.encode("utf-8", errors="replace")).hexdigest(),
                metadata={
                    "headers": _header_summary(headers),
                    "content_length": len(page),
                    "claims": len(claims),
                    "market_rows": len(market_rows),
                    "analysis_rows": len(analysis_rows),
                    "discovered_link_count": len(discovered_links),
                    "discovered_links": [{"url": link.url, "reason": link.reason} for link in discovered_links],
                    "scrapy_selector_available": scrapy_selector_available(),
                },
            )
        )
        runtime.record_success(
            request,
            message="Fetched dynamic public page.",
            metadata={
                "url": url,
                "claims": len(claims),
                "market_rows": len(market_rows),
                "analysis_rows": len(analysis_rows),
                "extraction_rows": len(result.extraction_rows),
                "discovered_links": len(discovered_links),
            },
        )
        return result


def _extend(target: PageExtractionResult, source: PageExtractionResult) -> None:
    target.page_rows.extend(source.page_rows)
    target.claims.extend(source.claims)
    target.market_rows.extend(source.market_rows)
    target.analysis_rows.extend(source.analysis_rows)
    target.extraction_rows.extend(source.extraction_rows)
    target.diagnostics.extend(source.diagnostics)
    target.discovered_links.extend(source.discovered_links)


def _page_row(
    *,
    url: str,
    domain: str,
    status: str,
    title: str = "",
    description: str = "",
    content_hash: str = "",
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "record_key": stable_hash({"url": url}),
        "url": url,
        "domain": domain,
        "status": status,
        "title": title[:240],
        "description": description[:400],
        "content_sha256": content_hash,
        "observed_at_utc": normalize_datetime(utc_now()) or "",
        "metadata": dict(metadata or {}),
    }


def _request_not_modified(runtime: SourceRuntime, request: SourceRequest) -> bool:
    responses = runtime.context.state.get("_source_runtime_responses")
    if not isinstance(responses, dict):
        return False
    response_info = responses.get(request.request_key)
    return bool(isinstance(response_info, dict) and response_info.get("not_modified"))


def _header_summary(headers: dict[str, Any]) -> dict[str, Any]:
    summary: dict[str, Any] = {}
    for key, value in headers.items():
        normalized = str(key).casefold()
        if normalized in {"etag", "last-modified", "cache-control", "expires", "content-type"}:
            summary[str(key)] = value
    return summary


def _reputation_map(rows: list[dict[str, Any]]) -> dict[tuple[str, str], float]:
    mapping: dict[tuple[str, str], float] = {}
    for row in rows:
        domain = str(row.get("domain") or "")
        claim_type = str(row.get("claim_type") or "")
        score = _optional_float(row.get("source_score"))
        if domain and claim_type and score is not None:
            mapping[(domain, claim_type)] = score
    return mapping


def _dedupe_claims(claims: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_key: dict[str, dict[str, Any]] = {}
    for claim in claims:
        key = str(claim.get("claim_id") or claim.get("record_key") or "")
        if key:
            by_key[key] = claim
    return list(by_key.values())


def _dedupe_results(results: list[ResultRecord]) -> list[ResultRecord]:
    by_key: dict[str, ResultRecord] = {}
    for result in results:
        by_key[result.record_key] = result
    return list(by_key.values())


def _optional_float(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
