"""Structured extraction diagnostics for source and signal plugins."""

from __future__ import annotations

from typing import Any, Mapping

from worldcup_predictions.storage.ledger import stable_hash


def extraction_diagnostic_row(
    *,
    source: str,
    extractor: str,
    status: str,
    reason: str,
    fixture_key: str | None = None,
    phase: str | None = None,
    source_name: str | None = None,
    source_url: str | None = None,
    title: str | None = None,
    severity: str = "info",
    metadata: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Return one normalized extraction diagnostic row.

    These rows are intentionally structured so rejections can be grouped across
    plugins without parsing human diagnostic text.
    """

    payload = {
        "source": source,
        "extractor": extractor,
        "status": status,
        "reason": reason,
        "fixture_key": fixture_key,
        "phase": phase,
        "source_name": source_name,
        "source_url": source_url,
        "title": _truncate(title, 240),
        "severity": severity,
        "metadata": dict(metadata or {}),
    }
    payload["record_key"] = stable_hash(payload)
    return payload


def _truncate(value: Any, limit: int) -> str:
    text = " ".join(str(value or "").split())
    return text[:limit]
