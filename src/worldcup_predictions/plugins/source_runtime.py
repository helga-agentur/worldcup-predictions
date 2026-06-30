"""Shared runtime helpers for source-style plugins."""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from typing import Any, Iterable, Mapping

from worldcup_predictions.core.contracts import Artifact, Diagnostic
from worldcup_predictions.core.events import EventName, event_value
from worldcup_predictions.core.http import HttpClient
from worldcup_predictions.core.plugin import BasePlugin, PluginResult
from worldcup_predictions.plugins.source_utils import load_env_value
from worldcup_predictions.storage.ledger import SourceLedgerRecord, SourceRequest, normalize_datetime, utc_now
from worldcup_predictions.tournament import TournamentState
from worldcup_predictions.tournament.repository import load_tournament_state


@dataclass(frozen=True)
class SourceRuntime:
    """Convenience wrapper around common source-plugin plumbing."""

    plugin: BasePlugin
    event: EventName | str
    context: Any

    @property
    def plugin_id(self) -> str:
        return self.plugin.id

    @property
    def event_name(self) -> str:
        return event_value(self.event)

    @property
    def storage(self) -> Any:
        return self.context.storage

    def storage_unavailable_result(self, label: str) -> PluginResult:
        return PluginResult(
            plugin_id=self.plugin_id,
            event=self.event_name,
            diagnostics=[
                Diagnostic(
                    level="warning",
                    message=f"Structured storage is unavailable; {label} was skipped.",
                    source=self.plugin_id,
                )
            ],
        )

    def tournament_state(self) -> TournamentState:
        state = self.context.state.get("tournament_state")
        if not isinstance(state, TournamentState):
            state = load_tournament_state(self.storage)
            self.context.state["tournament_state"] = state
        return state

    def env_value(self, name: str) -> str | None:
        return load_env_value(self.context.project_root, name)

    def http_client(self) -> HttpClient:
        return HttpClient(
            timeout_seconds=self.context.config.source_defaults.timeout_seconds,
            user_agent=self.context.config.user_agent,
        )

    def fetch_json(self, endpoint: str, params: dict[str, Any] | None = None, *, headers: dict[str, str] | None = None):
        return self.http_client().get_json(endpoint, params, headers=headers)

    def fetch_text(self, endpoint: str, params: dict[str, Any] | None = None, *, headers: dict[str, str] | None = None):
        response = self.http_client().get_text(endpoint, params, headers=headers)
        return response.body, response.headers

    def result(
        self,
        *,
        signals=None,
        artifacts=None,
        diagnostics=None,
        metadata=None,
    ) -> PluginResult:
        return PluginResult(
            plugin_id=self.plugin_id,
            event=self.event_name,
            signals=list(signals or []),
            artifacts=list(artifacts or []),
            diagnostics=list(diagnostics or []),
            metadata=dict(metadata or {}),
        )

    def diagnostic(
        self,
        level: str,
        message: str,
        *,
        fixture_key: str | None = None,
        metadata: Mapping[str, Any] | None = None,
    ) -> Diagnostic:
        return Diagnostic(
            level=level,
            message=message,
            source=self.plugin_id,
            fixture_key=fixture_key,
            metadata=dict(metadata or {}),
        )

    def skipped_fetch_result(
        self,
        label: str,
        reason: str,
        *,
        fixture_key: str | None = None,
        metadata: Mapping[str, Any] | None = None,
    ) -> PluginResult:
        return self.result(
            diagnostics=[
                self.diagnostic(
                    "info",
                    f"{label} fetch skipped: {reason}.",
                    fixture_key=fixture_key,
                    metadata=metadata,
                )
            ]
        )

    def structured_artifact(self, dataset: str, *, rows_written: int = 0, signals: int = 0) -> Artifact:
        return Artifact(
            name=dataset,
            kind="structured_dataset",
            source=self.plugin_id,
            data={"rows_written": rows_written, "signals": signals},
        )

    def write_records(self, dataset: str, rows: Iterable[Mapping[str, Any]]) -> int:
        return self.storage.write_records(dataset, rows, source=self.plugin_id, run_id=self.context.run_id)

    def read_latest(self, dataset: str) -> list[dict[str, Any]]:
        return self.storage.read_records(dataset, latest_only=True)

    def should_fetch(self, request: SourceRequest):
        decision = self.storage.should_fetch(request)
        if not decision.should_fetch:
            self.storage.record_fetch(
                SourceLedgerRecord(
                    request=request,
                    status="skipped",
                    run_id=self.context.run_id,
                    quota_remaining=_optional_int(decision.metadata.get("quota_remaining")),
                    next_safe_fetch_at=decision.next_safe_fetch_at,
                    message=decision.reason,
                    metadata={
                        "decision_reason": decision.reason,
                        "decision_metadata": dict(decision.metadata or {}),
                    },
                )
            )
        return decision

    def record_success(
        self,
        request: SourceRequest,
        *,
        message: str | None = None,
        metadata: Mapping[str, Any] | None = None,
        quota_remaining: int | None = None,
    ) -> None:
        self.storage.record_fetch(
            SourceLedgerRecord(
                request=request,
                status="success",
                run_id=self.context.run_id,
                quota_remaining=quota_remaining,
                message=message,
                metadata=dict(metadata or {}),
            )
        )

    def record_error(
        self,
        request: SourceRequest,
        error: Exception | str,
        *,
        metadata: Mapping[str, Any] | None = None,
        quota_remaining: int | None = None,
    ) -> None:
        status = _error_status(error)
        next_safe_fetch_at = _retry_after_next_safe_fetch_at(error) if status == "rate_limited" else None
        self.storage.record_fetch(
            SourceLedgerRecord(
                request=request,
                status=status,
                run_id=self.context.run_id,
                quota_remaining=quota_remaining,
                next_safe_fetch_at=next_safe_fetch_at,
                message=str(error),
                metadata={
                    **dict(metadata or {}),
                    "error_status": status,
                    "http_status": _optional_int(getattr(error, "code", None)),
                },
            )
        )


def _optional_int(value: Any) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _error_status(error: Exception | str) -> str:
    code = _optional_int(getattr(error, "code", None))
    if code == 429 or "429" in str(error):
        return "rate_limited"
    return "error"


def _retry_after_next_safe_fetch_at(error: Exception | str) -> str | None:
    headers = getattr(error, "headers", None)
    if headers is None:
        return None
    retry_after = _optional_int(headers.get("Retry-After") or headers.get("retry-after"))
    if retry_after is None:
        return None
    return normalize_datetime(utc_now() + dt.timedelta(seconds=max(0, retry_after)))
