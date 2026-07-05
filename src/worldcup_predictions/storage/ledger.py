"""Quota-aware source ledger contracts.

The ledger records fetch attempts and their rate-limit/quota state. It does not
store raw API responses and is intentionally separate from structured data.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
from dataclasses import dataclass, field
from typing import Any, Mapping


def utc_now() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0)


def normalize_datetime(value: dt.datetime | str | None) -> str | None:
    if value is None:
        return None
    if isinstance(value, str):
        parsed = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
    else:
        parsed = value
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=dt.timezone.utc)
    return parsed.astimezone(dt.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def parse_datetime(value: str | None) -> dt.datetime | None:
    if not value:
        return None
    parsed = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=dt.timezone.utc)
    return parsed.astimezone(dt.timezone.utc)


def canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True, default=str)


def stable_hash(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class SourceRequest:
    """A fetch intent before spending API quota."""

    source: str
    endpoint: str
    purpose: str
    params: Mapping[str, Any] = field(default_factory=dict)
    fixture_key: str | None = None
    quota_cost: int = 1
    min_refresh_interval: dt.timedelta | None = None
    quota_remaining_floor: int = 0
    quota_scope: str | None = None
    rate_limit_backoff: dt.timedelta | None = None

    @property
    def request_key(self) -> str:
        return stable_hash(
            {
                "source": self.source,
                "endpoint": self.endpoint,
                "purpose": self.purpose,
                "params": dict(self.params),
                "fixture_key": self.fixture_key,
            }
        )


@dataclass(frozen=True)
class FetchDecision:
    """Decision made before a source request is executed."""

    should_fetch: bool
    reason: str
    request_key: str
    next_safe_fetch_at: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class SourceLedgerRecord:
    """One source request attempt and the quota state observed around it."""

    request: SourceRequest
    status: str
    run_id: str | None = None
    fetched_at_utc: str = field(default_factory=lambda: normalize_datetime(utc_now()) or "")
    quota_remaining: int | None = None
    rate_limit_reset_at: str | None = None
    next_safe_fetch_at: str | None = None
    message: str | None = None
    metadata: Mapping[str, Any] = field(default_factory=dict)
