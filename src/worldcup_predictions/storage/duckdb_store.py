"""DuckDB-backed structured storage for the refactored workflow."""

from __future__ import annotations

import contextlib
import datetime as dt
import re
from pathlib import Path
from typing import Any, Iterable, Mapping

from worldcup_predictions.core.datasets import DATASET_CONTRACTS
from worldcup_predictions.storage.ledger import (
    FetchDecision,
    SourceLedgerRecord,
    SourceRequest,
    canonical_json,
    normalize_datetime,
    parse_datetime,
    stable_hash,
    utc_now,
)


def _load_duckdb():
    try:
        import duckdb  # type: ignore
    except ModuleNotFoundError as exc:
        raise RuntimeError("DuckDB is required for structured storage. Install project dependencies first.") from exc
    return duckdb


def _safe_dataset_name(value: str) -> str:
    safe = re.sub(r"[^A-Za-z0-9_.-]+", "_", value.strip())
    return safe.strip("._-") or "records"


# A quota observation can only justify skipping for so long: providers reset
# monthly (Odds API) or per minute (football-data.org), and an unbounded block
# once parked football-data for days on a per-minute counter. After this
# horizon the floor is ignored and one probe request re-measures the quota.
# Only rows from actual attempts count as observations: skip rows echo the
# stale quota_remaining with a fresh timestamp on every run, which kept the
# horizon from ever being reached on live (2026-07-10).
QUOTA_FLOOR_MAX_AGE = dt.timedelta(hours=24)


def _sql_literal(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


class DuckDBStorage:
    """Single-file analytical store plus Parquet exports.

    This store persists only normalized structured rows and source-ledger facts.
    Raw source responses are intentionally not part of the storage contract.
    """

    def __init__(self, db_path: Path, structured_root: Path) -> None:
        self.db_path = Path(db_path)
        self.structured_root = Path(structured_root)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.structured_root.mkdir(parents=True, exist_ok=True)
        self._deferred_export_datasets: set[str] | None = None
        self._ensure_schema()

    @classmethod
    def at_data_root(cls, data_root: Path) -> "DuckDBStorage":
        return cls(db_path=Path(data_root) / "worldcup_predictions.duckdb", structured_root=Path(data_root) / "structured")

    @contextlib.contextmanager
    def deferred_dataset_exports(self):
        """Batch per-write Parquet exports and flush them once on exit.

        Long commands write the same large datasets dozens of times per run;
        exporting the full history on every write dominated scheduled-update
        wall time. While deferred, the Parquet files lag the database exactly
        as they already did between consecutive writes.
        """

        already_deferred = self._deferred_export_datasets is not None
        if not already_deferred:
            self._deferred_export_datasets = set()
        try:
            yield self
        finally:
            if not already_deferred:
                self.flush_dataset_exports()
                self._deferred_export_datasets = None

    def flush_dataset_exports(self) -> list[str]:
        """Export every dataset written while exports were deferred."""

        datasets = sorted(self._deferred_export_datasets or ())
        if not datasets:
            return []
        con = self._connect()
        try:
            for dataset in datasets:
                self._export_dataset(con, dataset)
        finally:
            con.close()
        if self._deferred_export_datasets is not None:
            self._deferred_export_datasets.clear()
        return datasets

    def _export_or_defer(self, con, dataset: str) -> None:
        if self._deferred_export_datasets is not None:
            self._deferred_export_datasets.add(dataset)
            return
        self._export_dataset(con, dataset)

    def _connect(self):
        duckdb = _load_duckdb()
        return duckdb.connect(str(self.db_path))

    def _ensure_schema(self) -> None:
        con = self._connect()
        try:
            con.execute(
                """
                CREATE TABLE IF NOT EXISTS source_ledger (
                    request_key TEXT NOT NULL,
                    run_id TEXT,
                    source TEXT NOT NULL,
                    endpoint TEXT NOT NULL,
                    purpose TEXT NOT NULL,
                    fixture_key TEXT,
                    quota_scope TEXT,
                    params_json TEXT NOT NULL,
                    quota_cost INTEGER NOT NULL,
                    status TEXT NOT NULL,
                    fetched_at_utc TEXT NOT NULL,
                    quota_remaining INTEGER,
                    rate_limit_reset_at TEXT,
                    next_safe_fetch_at TEXT,
                    message TEXT,
                    metadata_json TEXT NOT NULL
                )
                """
            )
            self._ensure_column(con, "source_ledger", "run_id", "TEXT")
            self._ensure_column(con, "source_ledger", "quota_scope", "TEXT")
            con.execute(
                """
                CREATE TABLE IF NOT EXISTS structured_records (
                    dataset TEXT NOT NULL,
                    record_key TEXT NOT NULL,
                    source TEXT NOT NULL,
                    fixture_key TEXT,
                    run_id TEXT,
                    observed_at_utc TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    metadata_json TEXT NOT NULL
                )
                """
            )
        finally:
            con.close()

    def should_fetch(self, request: SourceRequest, *, now=None) -> FetchDecision:
        """Return whether a fetch should be attempted for this request."""

        now = now or utc_now()
        now_iso = normalize_datetime(now) or ""
        scoped_decision = self._quota_scope_decision(request, now=now, now_iso=now_iso)
        if scoped_decision is not None:
            return scoped_decision

        con = self._connect()
        try:
            row = con.execute(
                """
                SELECT status, fetched_at_utc, quota_remaining, next_safe_fetch_at
                FROM source_ledger
                WHERE request_key = ?
                  AND status != 'skipped'
                ORDER BY fetched_at_utc DESC, rowid DESC
                LIMIT 1
                """,
                [request.request_key],
            ).fetchone()
        finally:
            con.close()

        if not row:
            return FetchDecision(True, "no_previous_fetch", request.request_key)

        status, fetched_at_utc, quota_remaining, next_safe_fetch_at = row
        next_safe_dt = parse_datetime(next_safe_fetch_at)
        if next_safe_dt and next_safe_dt > now:
            return FetchDecision(
                False,
                "next_safe_fetch_at_not_reached",
                request.request_key,
                next_safe_fetch_at=next_safe_fetch_at,
                metadata={"now": now_iso},
            )

        if quota_remaining is not None and quota_remaining <= request.quota_remaining_floor:
            observed_at = parse_datetime(fetched_at_utc)
            if observed_at and now - observed_at < QUOTA_FLOOR_MAX_AGE:
                return FetchDecision(
                    False,
                    "quota_floor_reached",
                    request.request_key,
                    metadata={"quota_remaining": quota_remaining, "quota_remaining_floor": request.quota_remaining_floor},
                )

        if status == "success" and request.min_refresh_interval:
            fetched_at = parse_datetime(fetched_at_utc)
            if fetched_at and fetched_at + request.min_refresh_interval > now:
                next_allowed = fetched_at + request.min_refresh_interval
                return FetchDecision(
                    False,
                    "fresh_enough",
                    request.request_key,
                    next_safe_fetch_at=normalize_datetime(next_allowed),
                    metadata={"last_success_at": fetched_at_utc, "now": now_iso},
                )

        return FetchDecision(True, "stale_or_retry_allowed", request.request_key)

    def _quota_scope_decision(self, request: SourceRequest, *, now, now_iso: str) -> FetchDecision | None:
        """Block sibling request keys when a shared provider quota is exhausted."""

        quota_scope = str(request.quota_scope or "").strip()
        if not quota_scope:
            return None

        con = self._connect()
        try:
            blocked_row = con.execute(
                """
                SELECT request_key, status, fetched_at_utc, next_safe_fetch_at
                FROM source_ledger
                WHERE source = ?
                  AND quota_scope = ?
                  AND (
                    status = 'rate_limited'
                    OR (status = 'error' AND metadata_json LIKE '%"http_status":403%')
                  )
                  AND next_safe_fetch_at IS NOT NULL
                ORDER BY fetched_at_utc DESC
                LIMIT 1
                """,
                [request.source, quota_scope],
            ).fetchone()
            quota_row = con.execute(
                """
                SELECT request_key, status, fetched_at_utc, quota_remaining
                FROM source_ledger
                WHERE source = ?
                  AND quota_scope = ?
                  AND quota_remaining IS NOT NULL
                  AND status != 'skipped'
                ORDER BY fetched_at_utc DESC, rowid DESC
                LIMIT 1
                """,
                [request.source, quota_scope],
            ).fetchone()
        finally:
            con.close()

        if blocked_row:
            blocked_key, blocked_status, blocked_at, next_safe_fetch_at = blocked_row
            next_safe_dt = parse_datetime(next_safe_fetch_at)
            if next_safe_dt and next_safe_dt > now:
                return FetchDecision(
                    False,
                    "quota_scope_next_safe_fetch_at_not_reached",
                    request.request_key,
                    next_safe_fetch_at=next_safe_fetch_at,
                    metadata={
                        "now": now_iso,
                        "quota_scope": quota_scope,
                        "blocked_request_key": blocked_key,
                        "blocked_status": blocked_status,
                        "blocked_at": blocked_at,
                    },
                )

        if quota_row:
            quota_key, quota_status, quota_at, quota_remaining = quota_row
            observed_at = parse_datetime(quota_at)
            quota_is_fresh = observed_at is not None and now - observed_at < QUOTA_FLOOR_MAX_AGE
            if quota_is_fresh and quota_remaining is not None and quota_remaining <= request.quota_remaining_floor:
                return FetchDecision(
                    False,
                    "quota_scope_quota_floor_reached",
                    request.request_key,
                    metadata={
                        "quota_scope": quota_scope,
                        "quota_remaining": quota_remaining,
                        "quota_remaining_floor": request.quota_remaining_floor,
                        "quota_request_key": quota_key,
                        "quota_status": quota_status,
                        "quota_observed_at": quota_at,
                    },
                )
        return None

    def existing_record_keys(self, dataset: str, record_keys: list[str]) -> set[str]:
        """Return which of the given record keys already exist in a dataset.

        Used to avoid re-appending byte-identical rows (extraction diagnostics
        re-derived from unchanged evidence every run) to append-only datasets.
        """

        keys = [str(key) for key in record_keys if key]
        if not keys:
            return set()
        con = self._connect()
        try:
            rows = con.execute(
                """
                SELECT DISTINCT record_key FROM structured_records
                WHERE dataset = ? AND record_key IN (SELECT unnest(?::VARCHAR[]))
                """,
                [dataset, keys],
            ).fetchall()
        finally:
            con.close()
        return {row[0] for row in rows}

    def consecutive_request_failures(self, request_key: str) -> int:
        """Consecutive failed attempts for this request key since its last success.

        Skipped rows are decisions, not attempts, so they do not interrupt or
        extend a failure streak.
        """

        con = self._connect()
        try:
            rows = con.execute(
                """
                SELECT status FROM source_ledger
                WHERE request_key = ? AND status != 'skipped'
                ORDER BY fetched_at_utc DESC, rowid DESC
                LIMIT 16
                """,
                [request_key],
            ).fetchall()
        finally:
            con.close()
        failures = 0
        for (status,) in rows:
            if status in ("error", "rate_limited"):
                failures += 1
            else:
                break
        return failures

    def cache_validators(self, request: SourceRequest) -> dict[str, str]:
        """Return the latest HTTP cache validators stored for this request."""

        con = self._connect()
        try:
            rows = con.execute(
                """
                SELECT metadata_json
                FROM source_ledger
                WHERE request_key = ? AND status IN ('success', 'not_modified')
                ORDER BY fetched_at_utc DESC
                LIMIT 10
                """,
                [request.request_key],
            ).fetchall()
        finally:
            con.close()
        for (metadata_json,) in rows:
            metadata = self._loads_json(metadata_json)
            validators = metadata.get("cache_validators") if isinstance(metadata, dict) else None
            if not isinstance(validators, dict):
                continue
            etag = str(validators.get("etag") or "").strip()
            last_modified = str(validators.get("last_modified") or "").strip()
            result = {}
            if etag:
                result["etag"] = etag
            if last_modified:
                result["last_modified"] = last_modified
            if result:
                return result
        return {}

    def record_fetch(self, record: SourceLedgerRecord) -> None:
        """Persist fetch metadata without storing the raw response body."""

        con = self._connect()
        try:
            con.execute(
                """
                INSERT INTO source_ledger (
                    request_key,
                    run_id,
                    source,
                    endpoint,
                    purpose,
                    fixture_key,
                    quota_scope,
                    params_json,
                    quota_cost,
                    status,
                    fetched_at_utc,
                    quota_remaining,
                    rate_limit_reset_at,
                    next_safe_fetch_at,
                    message,
                    metadata_json
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    record.request.request_key,
                    record.run_id,
                    record.request.source,
                    record.request.endpoint,
                    record.request.purpose,
                    record.request.fixture_key,
                    record.request.quota_scope,
                    canonical_json(dict(record.request.params)),
                    record.request.quota_cost,
                    record.status,
                    normalize_datetime(record.fetched_at_utc) or "",
                    record.quota_remaining,
                    normalize_datetime(record.rate_limit_reset_at),
                    normalize_datetime(record.next_safe_fetch_at),
                    record.message,
                    canonical_json(dict(record.metadata)),
                ],
            )
        finally:
            con.close()

    def read_source_ledger(
        self,
        *,
        run_id: str | None = None,
        source: str | None = None,
        status: str | None = None,
    ) -> list[dict[str, Any]]:
        """Read source-ledger rows for audits and scheduled-run health summaries."""

        where = []
        params: list[Any] = []
        if run_id is not None:
            where.append("run_id = ?")
            params.append(run_id)
        if source is not None:
            where.append("source = ?")
            params.append(source)
        if status is not None:
            where.append("status = ?")
            params.append(status)
        where_sql = f"WHERE {' AND '.join(where)}" if where else ""

        con = self._connect()
        try:
            rows = con.execute(
                f"""
                SELECT
                    request_key,
                    run_id,
                    source,
                    endpoint,
                    purpose,
                    fixture_key,
                    quota_scope,
                    params_json,
                    quota_cost,
                    status,
                    fetched_at_utc,
                    quota_remaining,
                    rate_limit_reset_at,
                    next_safe_fetch_at,
                    message,
                    metadata_json
                FROM source_ledger
                {where_sql}
                ORDER BY fetched_at_utc, source, endpoint, request_key
                """,
                params,
            ).fetchall()
        finally:
            con.close()

        return [
            {
                "request_key": request_key,
                "run_id": row_run_id,
                "source": row_source,
                "endpoint": endpoint,
                "purpose": purpose,
                "fixture_key": fixture_key,
                "quota_scope": quota_scope,
                "params": self._loads_json(params_json),
                "quota_cost": quota_cost,
                "status": row_status,
                "fetched_at_utc": fetched_at_utc,
                "quota_remaining": quota_remaining,
                "rate_limit_reset_at": rate_limit_reset_at,
                "next_safe_fetch_at": next_safe_fetch_at,
                "message": message,
                "metadata": self._loads_json(metadata_json),
            }
            for (
                request_key,
                row_run_id,
                row_source,
                endpoint,
                purpose,
                fixture_key,
                quota_scope,
                params_json,
                quota_cost,
                row_status,
                fetched_at_utc,
                quota_remaining,
                rate_limit_reset_at,
                next_safe_fetch_at,
                message,
                metadata_json,
            ) in rows
        ]

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
        """Append extracted structured rows and export the dataset to Parquet."""

        observed_at = normalize_datetime(utc_now()) or ""
        metadata_json = canonical_json(dict(metadata or {}))
        prepared = []
        for row in rows:
            payload = dict(row)
            _validate_dataset_payload(dataset, payload)
            row_fixture_key = str(payload.get("fixture_key") or fixture_key or "") or None
            record_key = str(payload.get("record_key") or stable_hash({"dataset": dataset, "source": source, "payload": payload}))
            prepared.append(
                [
                    dataset,
                    record_key,
                    source,
                    row_fixture_key,
                    run_id,
                    observed_at,
                    canonical_json(payload),
                    metadata_json,
                ]
            )
        if not prepared:
            return 0

        con = self._connect()
        try:
            con.executemany(
                """
                INSERT INTO structured_records VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                prepared,
            )
            self._export_or_defer(con, dataset)
        finally:
            con.close()
        return len(prepared)

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
        """Replace one generated structured dataset and export a fresh Parquet file."""

        con = self._connect()
        try:
            con.execute("DELETE FROM structured_records WHERE dataset = ?", [dataset])
            self._export_or_defer(con, dataset)
        finally:
            con.close()
        return self.write_records(
            dataset,
            rows,
            source=source,
            run_id=run_id,
            fixture_key=fixture_key,
            metadata=metadata,
        )

    def read_records(
        self,
        dataset: str,
        *,
        source: str | None = None,
        fixture_key: str | None = None,
        latest_only: bool = False,
    ) -> list[dict[str, Any]]:
        """Read structured payloads back from storage."""

        where = ["dataset = ?"]
        params: list[Any] = [dataset]
        if source is not None:
            where.append("source = ?")
            params.append(source)
        if fixture_key is not None:
            where.append("fixture_key = ?")
            params.append(fixture_key)

        # latest_only collapses to the newest row per record key inside DuckDB
        # so full dataset histories are not parsed from JSON on every read.
        # The rowid tie-breaker preserves the previous Python collapse's
        # last-inserted-wins behavior for writes within the same second.
        latest_filter = (
            """
                QUALIFY ROW_NUMBER() OVER (
                    PARTITION BY record_key
                    ORDER BY observed_at_utc DESC, source DESC, rowid DESC
                ) = 1
            """
            if latest_only
            else ""
        )
        con = self._connect()
        try:
            rows = con.execute(
                f"""
                SELECT record_key, source, fixture_key, run_id, observed_at_utc, payload_json, metadata_json
                FROM structured_records
                WHERE {" AND ".join(where)}
                {latest_filter}
                ORDER BY observed_at_utc, source, record_key
                """,
                params,
            ).fetchall()
        finally:
            con.close()

        records: list[dict[str, Any]] = []
        for record_key, row_source, row_fixture_key, run_id, observed_at_utc, payload_json, metadata_json in rows:
            payload = self._loads_json(payload_json)
            metadata = self._loads_json(metadata_json)
            payload["_record"] = {
                "record_key": record_key,
                "source": row_source,
                "fixture_key": row_fixture_key,
                "run_id": run_id,
                "observed_at_utc": observed_at_utc,
                "metadata": metadata,
            }
            records.append(payload)
        return records

    @staticmethod
    def _loads_json(value: str | None) -> Any:
        if not value:
            return {}
        import json

        return json.loads(value)

    @staticmethod
    def _ensure_column(con, table: str, column: str, column_type: str) -> None:
        columns = con.execute(f"PRAGMA table_info({_sql_literal(table)})").fetchall()
        existing = {str(row[1]) for row in columns}
        if column not in existing:
            con.execute(f"ALTER TABLE {table} ADD COLUMN {column} {column_type}")

    def _export_dataset(self, con, dataset: str) -> Path:
        path = self.structured_root / f"{_safe_dataset_name(dataset)}.parquet"
        query = (
            "COPY ("
            "SELECT dataset, record_key, source, fixture_key, run_id, observed_at_utc, payload_json, metadata_json "
            f"FROM structured_records WHERE dataset = {_sql_literal(dataset)} "
            "ORDER BY observed_at_utc, source, record_key"
            f") TO {_sql_literal(str(path))} (FORMAT PARQUET)"
        )
        con.execute(query)
        return path

    def export_source_ledger(self) -> Path:
        path = self.structured_root / "source_ledger.parquet"
        con = self._connect()
        try:
            con.execute(
                "COPY (SELECT * FROM source_ledger ORDER BY fetched_at_utc, source, endpoint) "
                f"TO {_sql_literal(str(path))} (FORMAT PARQUET)"
            )
        finally:
            con.close()
        return path


def _validate_dataset_payload(dataset: str, payload: Mapping[str, Any]) -> None:
    contract = DATASET_CONTRACTS.get(dataset)
    if contract is None:
        return
    missing = [field for field in contract.required_fields if payload.get(field) in (None, "")]
    if missing:
        fields = ", ".join(missing)
        raise ValueError(f"{dataset} row missing required fields: {fields}")
