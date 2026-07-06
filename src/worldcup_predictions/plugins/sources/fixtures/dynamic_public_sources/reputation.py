"""Domain reputation scoring for dynamic public-source claims."""

from __future__ import annotations

from collections import defaultdict
from typing import Any, Iterable

from worldcup_predictions.core.constants import (
    DYNAMIC_SOURCE_INITIAL_REPUTATION,
    DYNAMIC_SOURCE_REPUTATION_PRIOR_WEIGHT,
    SOURCE_DYNAMIC_PUBLIC,
)
from worldcup_predictions.storage.ledger import stable_hash
from worldcup_predictions.tournament import ResultRecord


def build_reputation_rows(
    claims: Iterable[dict[str, Any]],
    confirmed_results: Iterable[ResultRecord],
) -> list[dict[str, Any]]:
    """Score domains by claim type with Bayesian shrinkage."""

    reference_results = {
        result.fixture_key: result
        for result in confirmed_results
        if _is_independent_reference_result(result)
    }
    stats: dict[tuple[str, str], dict[str, Any]] = defaultdict(lambda: {"correct": 0, "incorrect": 0, "pending": 0, "claim_count": 0})
    for claim in claims:
        domain = str(claim.get("domain") or "")
        claim_type = str(claim.get("claim_type") or "")
        if not domain or not claim_type:
            continue
        row = stats[(domain, claim_type)]
        row["claim_count"] += 1
        if claim_type != "result":
            row["pending"] += 1
            continue
        reference = reference_results.get(str(claim.get("fixture_key") or ""))
        if reference is None:
            row["pending"] += 1
            continue
        expected = reference.score.as_text()
        observed = str(claim.get("value_signature") or "")
        if observed == expected:
            row["correct"] += 1
        else:
            row["incorrect"] += 1

    rows: list[dict[str, Any]] = []
    for (domain, claim_type), row in sorted(stats.items()):
        correct = int(row["correct"])
        incorrect = int(row["incorrect"])
        pending = int(row["pending"])
        observed = correct + incorrect
        source_score = _shrunken_score(correct, incorrect)
        rows.append(
            {
                "record_key": stable_hash({"domain": domain, "claim_type": claim_type}),
                "domain": domain,
                "claim_type": claim_type,
                "source_score": source_score,
                "claim_count": int(row["claim_count"]),
                "correct_count": correct,
                "incorrect_count": incorrect,
                "pending_count": pending,
                "observed_result_count": observed,
                "metadata": {
                    "prior_score": DYNAMIC_SOURCE_INITIAL_REPUTATION,
                    "prior_weight": DYNAMIC_SOURCE_REPUTATION_PRIOR_WEIGHT,
                    "independent_reference_results": len(reference_results),
                },
            }
        )
    return rows


def _shrunken_score(correct: int, incorrect: int) -> float:
    observed = correct + incorrect
    if observed <= 0:
        return DYNAMIC_SOURCE_INITIAL_REPUTATION
    prior = DYNAMIC_SOURCE_INITIAL_REPUTATION
    prior_weight = DYNAMIC_SOURCE_REPUTATION_PRIOR_WEIGHT
    raw = (prior * prior_weight + correct) / (prior_weight + observed)
    sample_confidence = min(1.0, observed / (prior_weight + observed))
    return round(_clamp(prior * (1 - sample_confidence) + raw * sample_confidence, 0.10, 0.95), 6)


def _is_independent_reference_result(result: ResultRecord) -> bool:
    confirmation = dict(result.metadata.get("confirmation") or {})
    sources = [str(source) for source in confirmation.get("sources") or [result.source]]
    return any(not source.startswith(f"{SOURCE_DYNAMIC_PUBLIC}:") for source in sources)


def _clamp(value: float, lower: float, upper: float) -> float:
    return max(lower, min(upper, value))
