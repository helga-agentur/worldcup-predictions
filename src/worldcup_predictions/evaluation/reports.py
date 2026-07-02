"""Generate human-readable Markdown reports from structured workflow rows."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from worldcup_predictions.core.datasets import (
    DIAGNOSTICS_COMPLETENESS_AUDIT,
    EXTRACTION_DIAGNOSTICS,
    PREDICTION_AUDIT,
    PREDICTION_DEBUG_REPORT,
    PREDICTION_REPORTS,
    PROVIDER_BONUS_TRACKER,
    PROVIDER_KNOCKOUT_AUDIT,
    PROVIDER_POINTS,
)
from worldcup_predictions.storage.ledger import stable_hash, utc_now


def write_standard_reports(storage, project_root: Path, *, run_id: str | None = None) -> list[dict[str, Any]]:
    """Write standard Markdown reports under `reports/` and persist manifests."""

    reports_root = Path(project_root) / "reports"
    reports_root.mkdir(parents=True, exist_ok=True)
    manifests = [
        _write_report(
            reports_root / "prediction-debug.md",
            "Prediction Debug",
            _debug_report_markdown(storage.read_records(PREDICTION_DEBUG_REPORT, latest_only=True)),
        ),
        _write_report(
            reports_root / "prediction-audit.md",
            "Prediction Audit",
            _audit_report_markdown(storage.read_records(PREDICTION_AUDIT, latest_only=True)),
        ),
        _write_report(
            reports_root / "provider-points.md",
            "Provider Points",
            _provider_points_markdown(storage.read_records(PROVIDER_POINTS, latest_only=True)),
        ),
        _write_report(
            reports_root / "provider-knockout-audit.md",
            "Provider Knockout Audit",
            _provider_knockout_audit_markdown(storage.read_records(PROVIDER_KNOCKOUT_AUDIT, latest_only=True)),
        ),
        _write_report(
            reports_root / "bonus-tracker.md",
            "Bonus Tracker",
            _bonus_tracker_markdown(storage.read_records(PROVIDER_BONUS_TRACKER, latest_only=True)),
        ),
        _write_report(
            reports_root / "extraction-diagnostics.md",
            "Extraction Diagnostics",
            _extraction_diagnostics_markdown(storage.read_records(EXTRACTION_DIAGNOSTICS, latest_only=True)),
        ),
        _write_report(
            reports_root / "diagnostics-completeness.md",
            "Diagnostics Completeness",
            _diagnostics_completeness_markdown(storage.read_records(DIAGNOSTICS_COMPLETENESS_AUDIT, latest_only=True)),
        ),
        _write_report(
            reports_root / "source-ledger.md",
            "Source Ledger",
            _source_ledger_markdown(_read_source_ledger(storage, run_id=run_id)),
        ),
    ]
    storage.write_records(PREDICTION_REPORTS, manifests, source="reports", run_id=run_id)
    return manifests


def _write_report(path: Path, title: str, body: str) -> dict[str, Any]:
    text = f"# {title}\n\n{body.strip()}\n"
    path.write_text(text, encoding="utf-8")
    return {
        "record_key": stable_hash({"report": path.name, "path": str(path)}),
        "report_key": path.stem,
        "title": title,
        "path": str(path),
        "generated_at_utc": utc_now().isoformat(),
        "bytes": len(text.encode("utf-8")),
    }


def _debug_report_markdown(rows: list[dict[str, Any]]) -> str:
    if not rows:
        return "No prediction debug rows are available yet."
    lines = [
        "| Match | Most likely | H/D/A | Signals | Missing sources |",
        "| --- | ---: | ---: | ---: | --- |",
    ]
    for row in sorted(rows, key=lambda item: str(item.get("event_date") or "")):
        hda = f"{_pct(row.get('prob_home'))} / {_pct(row.get('prob_draw'))} / {_pct(row.get('prob_away'))}"
        missing = ", ".join(row.get("missing_signal_sources") or []) or "-"
        lines.append(
            "| {match} | {most_likely} | {hda} | {signals} | {missing} |".format(
                match=f"{row.get('home_team')} - {row.get('away_team')}",
                most_likely=row.get("most_likely_result") or "-",
                hda=hda,
                signals=row.get("signal_count") or 0,
                missing=missing,
            )
        )
    return "\n".join(lines)


def _audit_report_markdown(rows: list[dict[str, Any]]) -> str:
    if not rows:
        return "No prediction audit rows are available yet."
    by_provider: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        provider = str(row.get("provider") or "missing")
        by_provider.setdefault(provider, []).append(row)
    lines = []
    for provider, provider_rows in sorted(by_provider.items()):
        total = sum(float(row.get("points") or 0.0) for row in provider_rows)
        exact = sum(1 for row in provider_rows if row.get("correct_exact"))
        outcome = sum(1 for row in provider_rows if row.get("correct_outcome"))
        lines.append(f"## {provider}\n")
        lines.append(f"{total:.0f} points, {exact} exact scores, {outcome} correct outcomes.\n")
        lines.append("| Match | Tip | Actual | Points | Snapshot |")
        lines.append("| --- | ---: | ---: | ---: | --- |")
        for row in sorted(provider_rows, key=lambda item: str(item.get("event_date") or "")):
            lines.append(
                "| {match} | {tip} | {actual} | {points:.0f} | {snapshot} |".format(
                    match=f"{row.get('home_team')} - {row.get('away_team')}",
                    tip=row.get("tip") or "-",
                    actual=row.get("actual") or "-",
                    points=float(row.get("points") or 0.0),
                    snapshot=row.get("snapshot_id") or "-",
                )
            )
        lines.append("")
    return "\n".join(lines)


def _provider_points_markdown(rows: list[dict[str, Any]]) -> str:
    if not rows:
        return "No provider point rows are available yet."
    lines = [
        "| Provider | Match | Tip | Actual | Points | Cumulative |",
        "| --- | --- | ---: | ---: | ---: | ---: |",
    ]
    for row in sorted(rows, key=lambda item: (str(item.get("provider") or ""), str(item.get("event_date") or ""))):
        lines.append(
            "| {provider} | {match} | {tip} | {actual} | {points:.0f} | {cumulative:.0f} |".format(
                provider=row.get("provider"),
                match=f"{row.get('home_team')} - {row.get('away_team')}",
                tip=row.get("tip") or "-",
                actual=row.get("actual") or "-",
                points=float(row.get("points") or 0.0),
                cumulative=float(row.get("cumulative_points") or 0.0),
            )
        )
    return "\n".join(lines)


def _provider_knockout_audit_markdown(rows: list[dict[str, Any]]) -> str:
    if not rows:
        return "No knockout provider audit rows are available yet."
    lines = [
        "| Provider | Match | Stage | Selection | Type | Expected points | Divergence |",
        "| --- | --- | --- | --- | --- | ---: | --- |",
    ]
    for row in sorted(rows, key=lambda item: (str(item.get("event_date") or ""), str(item.get("provider") or ""))):
        lines.append(
            "| {provider} | {match} | {stage} | {selection} | {selection_type} | {expected_points:.2f} | {divergence} |".format(
                provider=_escape(row.get("provider")),
                match=_escape(f"{row.get('home_team')} - {row.get('away_team')}"),
                stage=_escape(row.get("stage")),
                selection=_escape(row.get("selection") or row.get("tip")),
                selection_type=_escape(row.get("selection_type")),
                expected_points=float(row.get("expected_points") or 0.0),
                divergence=_escape(row.get("optimizer_divergence")),
            )
        )
    return "\n".join(lines)


def _bonus_tracker_markdown(rows: list[dict[str, Any]]) -> str:
    if not rows:
        return "No bonus tracker rows are available yet."
    lines = [
        "| Provider | Question | Answer | Status | Current state |",
        "| --- | --- | --- | --- | --- |",
    ]
    for row in sorted(rows, key=lambda item: (str(item.get("provider") or ""), str(item.get("question_key") or ""))):
        lines.append(
            "| {provider} | {question} | {answer} | {status} | {state} |".format(
                provider=row.get("provider"),
                question=_escape(row.get("question")),
                answer=_escape(row.get("submitted_answer")),
                status=row.get("status") or "-",
                state=_escape(row.get("current_state")),
            )
        )
    return "\n".join(lines)


def _extraction_diagnostics_markdown(rows: list[dict[str, Any]]) -> str:
    if not rows:
        return "No extraction diagnostics are available yet."
    reason_counts: dict[tuple[str, str, str], int] = {}
    for row in rows:
        key = (
            str(row.get("source") or "-"),
            str(row.get("extractor") or "-"),
            str(row.get("reason") or "-"),
        )
        reason_counts[key] = reason_counts.get(key, 0) + 1
    lines = [
        "## Rejection Summary",
        "",
        "| Source | Extractor | Reason | Rows |",
        "| --- | --- | --- | ---: |",
    ]
    for (source, extractor, reason), count in sorted(reason_counts.items(), key=lambda item: (-item[1], item[0])):
        lines.append(f"| {_escape(source)} | {_escape(extractor)} | `{_escape(reason)}` | {count} |")
    lines.extend(
        [
            "",
            "## Fixture Details",
            "",
            "| Fixture | Phase | Source | Status | Reason | Title |",
            "| --- | --- | --- | --- | --- | --- |",
        ]
    )
    for row in sorted(rows, key=lambda item: (str(item.get("fixture_key") or ""), str(item.get("reason") or ""))):
        source = row.get("source_name") or row.get("source") or "-"
        title = row.get("title") or row.get("source_url") or "-"
        lines.append(
            "| {fixture} | {phase} | {source} | {status} | `{reason}` | {title} |".format(
                fixture=_escape(row.get("fixture_key")),
                phase=_escape(row.get("phase")),
                source=_escape(source),
                status=_escape(row.get("status")),
                reason=_escape(row.get("reason")),
                title=_escape(title),
            )
        )
    return "\n".join(lines)


def _diagnostics_completeness_markdown(rows: list[dict[str, Any]]) -> str:
    if not rows:
        return "No diagnostics completeness audit rows are available yet."
    status_counts: dict[str, int] = {}
    scope_counts: dict[tuple[str, str], int] = {}
    for row in rows:
        status = str(row.get("status") or "unknown")
        scope = str(row.get("scope") or "unknown")
        status_counts[status] = status_counts.get(status, 0) + 1
        scope_counts[(scope, status)] = scope_counts.get((scope, status), 0) + 1
    lines = [
        "## Summary",
        "",
        "| Status | Rows |",
        "| --- | ---: |",
    ]
    for status, count in sorted(status_counts.items()):
        lines.append(f"| {_escape(status)} | {count} |")
    lines.extend(
        [
            "",
            "## By Scope",
            "",
            "| Scope | Status | Rows |",
            "| --- | --- | ---: |",
        ]
    )
    for (scope, status), count in sorted(scope_counts.items()):
        lines.append(f"| {_escape(scope)} | {_escape(status)} | {count} |")
    problem_rows = [row for row in rows if row.get("status") != "ok"]
    if not problem_rows:
        lines.extend(["", "All audited diagnostic surfaces are complete."])
        return "\n".join(lines)
    lines.extend(
        [
            "",
            "## Findings",
            "",
            "| Scope | Subject | Severity | Reason | Missing fields |",
            "| --- | --- | --- | --- | --- |",
        ]
    )
    for row in sorted(problem_rows, key=lambda item: (str(item.get("scope") or ""), str(item.get("subject") or ""))):
        missing = ", ".join(str(item) for item in row.get("missing_fields") or []) or "-"
        lines.append(
            "| {scope} | {subject} | {severity} | {reason} | {missing} |".format(
                scope=_escape(row.get("scope")),
                subject=_escape(row.get("subject")),
                severity=_escape(row.get("severity")),
                reason=_escape(row.get("reason")),
                missing=_escape(missing),
            )
        )
    return "\n".join(lines)


def _source_ledger_markdown(rows: list[dict[str, Any]]) -> str:
    if not rows:
        return "No source-ledger rows are available for this run yet."
    status_counts: dict[str, int] = {}
    source_status_counts: dict[tuple[str, str], int] = {}
    quota_cost_by_status: dict[str, int] = {}
    item_count_by_source: dict[str, int] = {}
    cache_skipped_by_source: dict[str, int] = {}
    calls_made = 0
    calls_avoided = 0
    for row in rows:
        source = str(row.get("source") or "unknown")
        status = str(row.get("status") or "unknown")
        quota_cost = int(row.get("quota_cost") or 0)
        metadata = row.get("metadata") if isinstance(row.get("metadata"), dict) else {}
        status_counts[status] = status_counts.get(status, 0) + 1
        source_status_counts[(source, status)] = source_status_counts.get((source, status), 0) + 1
        quota_cost_by_status[status] = quota_cost_by_status.get(status, 0) + quota_cost
        item_count_by_source[source] = item_count_by_source.get(source, 0) + _source_metadata_item_count(metadata)
        if status == "skipped":
            calls_avoided += 1
            decision_reason = str(metadata.get("decision_reason") or "")
            if decision_reason in {"fresh_enough", "next_safe_fetch_at_not_reached"}:
                cache_skipped_by_source[source] = cache_skipped_by_source.get(source, 0) + 1
        else:
            calls_made += 1
        if status == "not_modified":
            cache_skipped_by_source[source] = cache_skipped_by_source.get(source, 0) + 1
    lines = [
        "## Summary",
        "",
        f"- Calls made: {calls_made}",
        f"- Calls avoided: {calls_avoided}",
        f"- Quota cost made: {sum(cost for status, cost in quota_cost_by_status.items() if status != 'skipped')}",
        f"- Quota cost avoided: {quota_cost_by_status.get('skipped', 0)}",
        "",
        "| Status | Rows | Quota cost |",
        "| --- | ---: | ---: |",
    ]
    for status, count in sorted(status_counts.items()):
        lines.append(f"| {_escape(status)} | {count} | {quota_cost_by_status.get(status, 0)} |")
    lines.extend(
        [
            "",
            "## By Source",
            "",
            "| Source | Status | Rows |",
            "| --- | --- | ---: |",
        ]
    )
    for (source, status), count in sorted(source_status_counts.items()):
        lines.append(f"| {_escape(source)} | {_escape(status)} | {count} |")
    lines.extend(
        [
            "",
            "## Source Volume",
            "",
            "| Source | Items recorded | Cache skips / not modified |",
            "| --- | ---: | ---: |",
        ]
    )
    for source in sorted(set(item_count_by_source) | set(cache_skipped_by_source)):
        lines.append(f"| {_escape(source)} | {item_count_by_source.get(source, 0)} | {cache_skipped_by_source.get(source, 0)} |")
    notable = [row for row in rows if row.get("status") in {"skipped", "not_modified", "rate_limited", "error"}]
    if notable:
        lines.extend(
            [
                "",
                "## Notable Rows",
                "",
                "| Source | Purpose | Status | Reason | Next safe fetch |",
                "| --- | --- | --- | --- | --- |",
            ]
        )
        for row in notable:
            lines.append(
                "| {source} | {purpose} | {status} | {message} | {next_safe} |".format(
                    source=_escape(row.get("source")),
                    purpose=_escape(row.get("purpose")),
                    status=_escape(row.get("status")),
                    message=_escape(row.get("message")),
                    next_safe=_escape(row.get("next_safe_fetch_at")),
                )
            )
    return "\n".join(lines)


def _source_metadata_item_count(metadata: dict[str, Any]) -> int:
    count = 0
    for key in ("rows", "rows_written", "fixtures", "results", "details", "matches", "events", "articles", "signals", "teams", "players", "sports"):
        value = metadata.get(key)
        if isinstance(value, bool):
            continue
        if isinstance(value, int | float):
            count += int(value)
    return count


def _read_source_ledger(storage, *, run_id: str | None) -> list[dict[str, Any]]:
    reader = getattr(storage, "read_source_ledger", None)
    if reader is None:
        return []
    try:
        return reader(run_id=run_id)
    except Exception:  # noqa: BLE001 - report generation should not block prediction runs.
        return []


def _pct(value: Any) -> str:
    try:
        return f"{float(value):.0%}"
    except (TypeError, ValueError):
        return "-"


def _escape(value: Any) -> str:
    return " ".join(str(value or "-").replace("|", "\\|").split())
