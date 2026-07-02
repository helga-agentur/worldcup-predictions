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
        request = self._matching_request(endpoint, params)
        response = self.http_client().get_text(
            endpoint,
            params,
            headers={"Accept": "application/json", **self._conditional_headers(request), **(headers or {})},
        )
        self._remember_response(request, response.headers, status_code=response.status_code)
        if response.status_code == 304:
            return {}, response.headers
        return response.json(), response.headers

    def fetch_text(self, endpoint: str, params: dict[str, Any] | None = None, *, headers: dict[str, str] | None = None):
        request = self._matching_request(endpoint, params)
        response = self.http_client().get_text(
            endpoint,
            params,
            headers={**self._conditional_headers(request), **(headers or {})},
        )
        self._remember_response(request, response.headers, status_code=response.status_code)
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
        self.context.state["_source_runtime_last_request"] = request
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
        response_info = self._response_info(request)
        not_modified = bool(response_info.get("not_modified"))
        response_headers = response_info.get("response_headers") if isinstance(response_info.get("response_headers"), dict) else {}
        cache_validators = _cache_validators_from_headers(response_headers)
        status = "not_modified" if not_modified else "success"
        self.storage.record_fetch(
            SourceLedgerRecord(
                request=request,
                status=status,
                run_id=self.context.run_id,
                quota_remaining=quota_remaining,
                message=message or ("Not modified." if not_modified else None),
                metadata={
                    **dict(metadata or {}),
                    "not_modified": not_modified,
                    "response_headers": response_headers,
                    "cache_validators": cache_validators,
                },
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
        response_headers = _sanitize_response_headers(getattr(error, "headers", None))
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
                    "response_headers": response_headers,
                    "cache_validators": _cache_validators_from_headers(response_headers),
                },
            )
        )

    def _matching_request(self, endpoint: str, params: Mapping[str, Any] | None) -> SourceRequest | None:
        request = self.context.state.get("_source_runtime_last_request")
        if not isinstance(request, SourceRequest):
            return None
        if request.endpoint != endpoint:
            return None
        request_params = {key: value for key, value in dict(request.params).items() if value is not None}
        fetch_params = {key: value for key, value in dict(params or {}).items() if value is not None}
        if not _params_match(request_params, fetch_params):
            return None
        return request

    def _conditional_headers(self, request: SourceRequest | None) -> dict[str, str]:
        if request is None:
            return {}
        reader = getattr(self.storage, "cache_validators", None)
        validators = reader(request) if callable(reader) else {}
        headers = {}
        etag = str(validators.get("etag") or "").strip() if isinstance(validators, dict) else ""
        last_modified = str(validators.get("last_modified") or "").strip() if isinstance(validators, dict) else ""
        if etag:
            headers["If-None-Match"] = etag
        if last_modified:
            headers["If-Modified-Since"] = last_modified
        return headers

    def _remember_response(self, request: SourceRequest | None, headers: Mapping[str, Any], *, status_code: int) -> None:
        if request is None:
            return
        responses = self.context.state.setdefault("_source_runtime_responses", {})
        responses[request.request_key] = {
            "not_modified": status_code == 304,
            "response_headers": _sanitize_response_headers(headers),
        }

    def _response_info(self, request: SourceRequest) -> dict[str, Any]:
        responses = self.context.state.get("_source_runtime_responses")
        if not isinstance(responses, dict):
            return {}
        response = responses.pop(request.request_key, {})
        return response if isinstance(response, dict) else {}


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


def _params_match(request_params: Mapping[str, Any], fetch_params: Mapping[str, Any]) -> bool:
    for key, value in request_params.items():
        if key not in fetch_params or str(fetch_params[key]) != str(value):
            return False
    return True


def _sanitize_response_headers(headers: Mapping[str, Any] | None) -> dict[str, str]:
    sanitized = {}
    for key, value in dict(headers or {}).items():
        header = str(key)
        if header.casefold() == "set-cookie":
            sanitized[header] = "[redacted]"
        else:
            sanitized[header] = str(value)
    return sanitized


def _cache_validators_from_headers(headers: Mapping[str, Any]) -> dict[str, str]:
    normalized = {str(key).casefold(): str(value) for key, value in dict(headers or {}).items()}
    validators = {}
    etag = normalized.get("etag", "").strip()
    last_modified = normalized.get("last-modified", "").strip()
    if etag:
        validators["etag"] = etag
    if last_modified:
        validators["last_modified"] = last_modified
    return validators
