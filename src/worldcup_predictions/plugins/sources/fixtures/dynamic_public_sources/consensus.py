"""Consensus helpers for dynamic public-source claims."""

from __future__ import annotations

from collections import defaultdict
from typing import Any, Iterable

from worldcup_predictions.core.constants import (
    DYNAMIC_SOURCE_RESULT_MIN_DOMAINS,
    DYNAMIC_SOURCE_RESULT_MIN_WEIGHTED_SUPPORT,
    SOURCE_DYNAMIC_PUBLIC,
)
from worldcup_predictions.core.contracts import ScoreTip
from worldcup_predictions.storage.ledger import stable_hash
from worldcup_predictions.tournament import ResultRecord, TeamRef


def build_claim_consensus_rows(claims: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    """Group atomic claims and calculate weighted support."""

    grouped: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for claim in claims:
        claim_type = str(claim.get("claim_type") or "")
        consensus_key = str(claim.get("consensus_key") or "")
        if claim_type and consensus_key:
            grouped[(claim_type, consensus_key)].append(claim)

    rows: list[dict[str, Any]] = []
    for (claim_type, consensus_key), claim_rows in sorted(grouped.items()):
        domains = sorted({str(row.get("domain") or "") for row in claim_rows if row.get("domain")})
        source_urls = sorted({str(row.get("source_url") or "") for row in claim_rows if row.get("source_url")})
        weighted_support = sum(_optional_float(row.get("claim_weight")) or 0.0 for row in claim_rows)
        avg_confidence = _average(_optional_float(row.get("extraction_confidence")) for row in claim_rows)
        avg_reputation = _average(_optional_float(row.get("source_reputation")) for row in claim_rows)
        status = _consensus_status(
            claim_type=claim_type,
            domain_count=len(domains),
            weighted_support=weighted_support,
        )
        primary = _primary_claim(claim_rows)
        rows.append(
            {
                "record_key": stable_hash({"claim_type": claim_type, "consensus_key": consensus_key}),
                "claim_type": claim_type,
                "consensus_key": consensus_key,
                "fixture_key": primary.get("fixture_key"),
                "event_date": primary.get("event_date"),
                "home_team": primary.get("home_team"),
                "away_team": primary.get("away_team"),
                "home_fifa_code": primary.get("home_fifa_code"),
                "away_fifa_code": primary.get("away_fifa_code"),
                "value_signature": primary.get("value_signature"),
                "value": primary.get("value"),
                "status": status,
                "claim_count": len(claim_rows),
                "domain_count": len(domains),
                "source_url_count": len(source_urls),
                "weighted_support": round(weighted_support, 6),
                "avg_extraction_confidence": avg_confidence,
                "avg_source_reputation": avg_reputation,
                "domains": domains,
                "claim_ids": sorted(str(row.get("claim_id") or "") for row in claim_rows if row.get("claim_id")),
                "metadata": {
                    "policy": {
                        "result_min_domains": DYNAMIC_SOURCE_RESULT_MIN_DOMAINS,
                        "result_min_weighted_support": DYNAMIC_SOURCE_RESULT_MIN_WEIGHTED_SUPPORT,
                    }
                },
            }
        )
    return rows


def result_records_from_consensus(
    consensus_rows: Iterable[dict[str, Any]],
    claims: Iterable[dict[str, Any]],
) -> list[ResultRecord]:
    """Convert only strong result consensus groups into source observations."""

    claims_by_key: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for claim in claims:
        if str(claim.get("claim_type") or "") == "result":
            claims_by_key[str(claim.get("consensus_key") or "")].append(claim)

    results: list[ResultRecord] = []
    for row in consensus_rows:
        if str(row.get("claim_type") or "") != "result" or str(row.get("status") or "") != "strong":
            continue
        for claim in _one_claim_per_domain(claims_by_key.get(str(row.get("consensus_key") or ""), [])):
            value = dict(claim.get("value") or {})
            home_score = _optional_int(value.get("home_score"))
            away_score = _optional_int(value.get("away_score"))
            if home_score is None or away_score is None:
                continue
            domain = str(claim.get("domain") or "unknown")
            results.append(
                ResultRecord(
                    event_date=str(claim.get("event_date") or ""),
                    home_team=TeamRef(str(claim.get("home_team") or ""), claim.get("home_fifa_code")),
                    away_team=TeamRef(str(claim.get("away_team") or ""), claim.get("away_fifa_code")),
                    score=ScoreTip(home_score, away_score),
                    source=f"{SOURCE_DYNAMIC_PUBLIC}:{domain}",
                    notes="Dynamic public-source result claim promoted after domain consensus.",
                    metadata={
                        "dynamic_public_consensus": {
                            "consensus_key": row.get("consensus_key"),
                            "claim_id": claim.get("claim_id"),
                            "source_url": claim.get("source_url"),
                            "domain": domain,
                            "weighted_support": row.get("weighted_support"),
                            "domain_count": row.get("domain_count"),
                            "status": row.get("status"),
                        }
                    },
                )
            )
    return results


def _consensus_status(*, claim_type: str, domain_count: int, weighted_support: float) -> str:
    if (
        claim_type == "result"
        and domain_count >= DYNAMIC_SOURCE_RESULT_MIN_DOMAINS
        and weighted_support >= DYNAMIC_SOURCE_RESULT_MIN_WEIGHTED_SUPPORT
    ):
        return "strong"
    return "candidate"


def _primary_claim(claims: list[dict[str, Any]]) -> dict[str, Any]:
    if not claims:
        return {}
    return sorted(
        claims,
        key=lambda row: (
            -(_optional_float(row.get("claim_weight")) or 0.0),
            str(row.get("domain") or ""),
            str(row.get("claim_id") or ""),
        ),
    )[0]


def _one_claim_per_domain(claims: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_domain: dict[str, dict[str, Any]] = {}
    for claim in sorted(claims, key=lambda row: -(_optional_float(row.get("claim_weight")) or 0.0)):
        domain = str(claim.get("domain") or "")
        if domain and domain not in by_domain:
            by_domain[domain] = claim
    return list(by_domain.values())


def _average(values: Iterable[float | None]) -> float | None:
    samples = [value for value in values if value is not None]
    if not samples:
        return None
    return sum(samples) / len(samples)


def _optional_float(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _optional_int(value: Any) -> int | None:
    if value in (None, ""):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None
