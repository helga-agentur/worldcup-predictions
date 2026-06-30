"""Persistence helpers for tournament records."""

from __future__ import annotations

from typing import Any

from worldcup_predictions.storage.contracts import StructuredStorage
from worldcup_predictions.tournament.contracts import FixtureRecord, ResultRecord, TournamentState
from worldcup_predictions.tournament.state import build_tournament_state, standing_records

FIXTURES_DATASET = "tournament_fixtures"
RESULTS_DATASET = "tournament_results"
RESULT_CHECKS_DATASET = "tournament_result_checks"
STANDINGS_DATASET = "tournament_standings"
GROUP_STATE_DATASET = "group_state_signals"


def load_fixtures(storage: StructuredStorage) -> list[FixtureRecord]:
    return [
        FixtureRecord.from_record(row)
        for row in load_active_fixture_rows(storage)
    ]


def load_active_fixture_rows(storage: StructuredStorage) -> list[dict[str, Any]]:
    """Return the latest active fixture batch for each source.

    Fixture datasets are append-only for auditability. When an upstream source
    renames or removes future placeholders, older rows must not remain active
    just because their old fixture key differs from the current one.
    """

    rows = storage.read_records(FIXTURES_DATASET)
    latest_observed_by_source: dict[str, str] = {}
    for row in rows:
        record = row.get("_record") or {}
        source = str(record.get("source") or "")
        observed_at = str(record.get("observed_at_utc") or "")
        if not source or not observed_at:
            continue
        if observed_at >= latest_observed_by_source.get(source, ""):
            latest_observed_by_source[source] = observed_at

    latest_by_key: dict[str, dict[str, Any]] = {}
    for row in rows:
        record = row.get("_record") or {}
        source = str(record.get("source") or "")
        observed_at = str(record.get("observed_at_utc") or "")
        if source and latest_observed_by_source.get(source) != observed_at:
            continue
        key = str(row.get("record_key") or record.get("record_key") or "")
        if key:
            latest_by_key[key] = row
    return list(latest_by_key.values())


def load_results(storage: StructuredStorage) -> list[ResultRecord]:
    return [
        ResultRecord.from_record(row)
        for row in storage.read_records(RESULTS_DATASET, latest_only=True)
    ]


def load_tournament_state(storage: StructuredStorage) -> TournamentState:
    return build_tournament_state(load_fixtures(storage), load_results(storage))


def write_fixtures(storage: StructuredStorage, fixtures: list[FixtureRecord], *, source: str, run_id: str | None = None) -> int:
    return storage.write_records(
        FIXTURES_DATASET,
        [fixture.to_record() for fixture in fixtures],
        source=source,
        run_id=run_id,
    )


def write_results(storage: StructuredStorage, results: list[ResultRecord], *, source: str, run_id: str | None = None) -> int:
    return storage.write_records(
        RESULTS_DATASET,
        [result.to_record() for result in results],
        source=source,
        run_id=run_id,
    )


def write_derived_state(storage: StructuredStorage, state: TournamentState, *, run_id: str | None = None) -> dict[str, int]:
    return {
        STANDINGS_DATASET: storage.write_records(
            STANDINGS_DATASET,
            standing_records(state),
            source="tournament_state",
            run_id=run_id,
        ),
        RESULT_CHECKS_DATASET: storage.write_records(
            RESULT_CHECKS_DATASET,
            state.result_checks,
            source="tournament_state",
            run_id=run_id,
        ),
    }
