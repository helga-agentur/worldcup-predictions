"""Persistence helpers for tournament records."""

from __future__ import annotations

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
        for row in storage.read_records(FIXTURES_DATASET, latest_only=True)
    ]


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
