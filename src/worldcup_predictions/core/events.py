"""Stable workflow events that plugins may subscribe to."""

from __future__ import annotations

from enum import StrEnum


class EventName(StrEnum):
    """Named extension points in the prediction workflow."""

    WORKFLOW_STARTED = "workflow_started"
    FIXTURES_REQUESTED = "fixtures_requested"
    RAW_SOURCE_FETCHED = "raw_source_fetched"
    PRE_GAME_ANALYSIS_AVAILABLE = "pre_game_analysis_available"
    FIXTURE_CONTEXT_REQUESTED = "fixture_context_requested"
    FEATURE_SIGNALS_REQUESTED = "feature_signals_requested"
    SCORE_MATRIX_CREATED = "score_matrix_created"
    PREDICTIONS_REQUESTED = "predictions_requested"
    PREDICTION_READY = "prediction_ready"
    PROVIDER_OPTIMIZATION_REQUESTED = "provider_optimization_requested"
    PROVIDER_TIP_READY = "provider_tip_ready"
    SIMULATION_REQUESTED = "simulation_requested"
    SIMULATION_COMPLETED = "simulation_completed"
    BONUS_EVALUATION_REQUESTED = "bonus_evaluation_requested"
    RESULTS_UPDATED = "results_updated"
    POSTMATCH_ANALYSIS_AVAILABLE = "postmatch_analysis_available"
    CALIBRATION_REQUESTED = "calibration_requested"
    DEBUG_REPORT_REQUESTED = "debug_report_requested"


def event_value(event: EventName | str) -> str:
    """Return the canonical string value for an event name."""

    if isinstance(event, EventName):
        return event.value
    return str(event)
