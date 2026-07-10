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


def unstored_extraction_diagnostics(storage: Any, rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Drop diagnostic rows that already exist verbatim in structured storage.

    Diagnostic record keys are stable hashes of the full payload, so plugins
    that re-derive diagnostics from unchanged evidence every run (match notes,
    postmatch stats) would append byte-identical rows forever: 97.5% of all
    extraction_diagnostics rows on live were such repeats (2026-07-10).
    Stored rows keep serving latest_only reads, so no information is lost.
    """

    if not rows:
        return rows
    checker = getattr(storage, "existing_record_keys", None)
    if not callable(checker):
        return rows
    existing = checker("extraction_diagnostics", [str(row.get("record_key") or "") for row in rows])
    return [row for row in rows if str(row.get("record_key") or "") not in existing]


def _truncate(value: Any, limit: int) -> str:
    text = " ".join(str(value or "").split())
    return text[:limit]
