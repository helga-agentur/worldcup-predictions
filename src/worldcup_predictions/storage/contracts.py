"""Storage protocols used by repositories and plugins."""

from __future__ import annotations

from typing import Any, Iterable, Mapping, Protocol

from worldcup_predictions.storage.ledger import FetchDecision, SourceLedgerRecord, SourceRequest


class StructuredStorage(Protocol):
    """Protocol implemented by structured storage backends."""

    def should_fetch(self, request: SourceRequest, *, now=None) -> FetchDecision:
        ...

    def record_fetch(self, record: SourceLedgerRecord) -> None:
        ...

    def read_source_ledger(
        self,
        *,
        run_id: str | None = None,
        source: str | None = None,
        status: str | None = None,
    ) -> list[dict[str, Any]]:
        ...

    def write_records(
        self,
        dataset: str,
        rows: Iterable[Mapping[str, Any]],
        *,
        source: str,
        run_id: str | None = None,
        fixture_key: str | None = None,
        metadata: Mapping[str, Any] | None = None,
    ) -> int:
        ...

    def replace_records(
        self,
        dataset: str,
        rows: Iterable[Mapping[str, Any]],
        *,
        source: str,
        run_id: str | None = None,
        fixture_key: str | None = None,
        metadata: Mapping[str, Any] | None = None,
    ) -> int:
        ...

    def read_records(
        self,
        dataset: str,
        *,
        source: str | None = None,
        fixture_key: str | None = None,
        latest_only: bool = False,
    ) -> list[dict[str, Any]]:
        ...
