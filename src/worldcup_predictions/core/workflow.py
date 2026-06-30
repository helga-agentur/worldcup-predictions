"""Composable prediction workflow orchestration."""

from __future__ import annotations

import datetime as dt
import json
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from worldcup_predictions.core.config import DEFAULT_CONFIG, ProjectConfig, load_project_config
from worldcup_predictions.core.contracts import Diagnostic, OptimizedTip, Prediction
from worldcup_predictions.core.env import load_project_env
from worldcup_predictions.core.events import EventName
from worldcup_predictions.core.payloads import (
    DebugReportRequestedPayload,
    FeatureSignalsRequestedPayload,
    FixturesRequestedPayload,
    PredictionsRequestedPayload,
    PredictionReadyPayload,
    ProviderOptimizationRequestedPayload,
    ProviderTipReadyPayload,
    ResultsUpdatedPayload,
    WorkflowStartedPayload,
)
from worldcup_predictions.core.plugin import PluginManager, PluginResult
from worldcup_predictions.tournament.repository import load_tournament_state


@dataclass
class WorkflowContext:
    """Mutable run context shared across plugins."""

    project_root: Path
    data_root: Path
    storage: Any | None = None
    config: ProjectConfig = DEFAULT_CONFIG
    run_id: str = field(default_factory=lambda: uuid.uuid4().hex)
    started_at: dt.datetime = field(default_factory=lambda: dt.datetime.now(dt.timezone.utc))
    settings: dict[str, Any] = field(default_factory=dict)
    state: dict[str, Any] = field(default_factory=dict)
    event_results: list[PluginResult] = field(default_factory=list)

    def record_result(self, result: PluginResult) -> None:
        self.event_results.append(result)


@dataclass(frozen=True)
class WorkflowRun:
    """A completed workflow run."""

    context: WorkflowContext
    predictions: list[Prediction]
    optimized_tips: list[OptimizedTip]
    diagnostics: list[Diagnostic]

    def to_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.context.run_id,
            "started_at": self.context.started_at.isoformat(),
            "predictions": [prediction.to_dict() for prediction in self.predictions],
            "optimized_tips": [tip.to_dict() for tip in self.optimized_tips],
            "diagnostics": [
                {
                    "level": diagnostic.level,
                    "message": diagnostic.message,
                    "source": diagnostic.source,
                    "fixture_key": diagnostic.fixture_key,
                    "metadata": diagnostic.metadata,
                }
                for diagnostic in self.diagnostics
            ],
            "events": [
                {
                    "plugin_id": result.plugin_id,
                    "event": result.event,
                    "signals": len(result.signals),
                    "artifacts": len(result.artifacts),
                    "predictions": len(result.predictions),
                    "optimized_tips": len(result.optimized_tips),
                    "diagnostics": len(result.diagnostics),
                    "metadata": result.metadata,
                }
                for result in self.context.event_results
            ],
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), indent=2, sort_keys=True)


class PredictionWorkflow:
    """High-level workflow that delegates data and model work to plugins."""

    def __init__(self, manager: PluginManager, context: WorkflowContext) -> None:
        self.manager = manager
        self.context = context

    @classmethod
    def from_project_root(
        cls,
        project_root: Path,
        manager: PluginManager,
        *,
        data_root: Path | None = None,
        settings: dict[str, Any] | None = None,
    ) -> "PredictionWorkflow":
        resolved_data_root = data_root or project_root / "data"
        resolved_settings = dict(settings or {})
        load_project_env(project_root)
        config = load_project_config(project_root)
        storage = None
        try:
            from worldcup_predictions.storage import DuckDBStorage

            storage = DuckDBStorage.at_data_root(resolved_data_root)
        except RuntimeError as exc:
            resolved_settings["storage_unavailable"] = str(exc)
        context = WorkflowContext(
            project_root=project_root,
            data_root=resolved_data_root,
            storage=storage,
            config=config,
            settings=resolved_settings,
        )
        return cls(manager=manager, context=context)

    def next_predictions(self, *, limit: int, include_closed: bool = False, include_all_fixtures: bool = False) -> WorkflowRun:
        previous_results = self.latest_result_rows()
        self.manager.emit(EventName.WORKFLOW_STARTED, self.context, WorkflowStartedPayload(limit=limit))
        self.manager.emit(EventName.FIXTURES_REQUESTED, self.context, FixturesRequestedPayload(limit=limit))
        self.emit_results_updated_if_changed(previous_results, source_event=EventName.FIXTURES_REQUESTED.value)
        self.manager.emit(EventName.FEATURE_SIGNALS_REQUESTED, self.context, FeatureSignalsRequestedPayload(limit=limit))
        results = self.manager.emit(
            EventName.PREDICTIONS_REQUESTED,
            self.context,
            {
                **PredictionsRequestedPayload(limit=limit, include_closed=include_closed).to_dict(),
                "include_all_fixtures": include_all_fixtures,
            },
        )
        predictions = [prediction for result in results for prediction in result.predictions]
        predictions.sort(key=lambda item: (item.fixture.event_date, item.fixture.home_team, item.fixture.away_team))
        if limit > 0:
            predictions = predictions[:limit]
        optimized_tips: list[OptimizedTip] = []
        for prediction in predictions:
            self.manager.emit(EventName.PREDICTION_READY, self.context, PredictionReadyPayload(prediction=prediction))
            optimization_results = self.manager.emit(
                EventName.PROVIDER_OPTIMIZATION_REQUESTED,
                self.context,
                ProviderOptimizationRequestedPayload(prediction=prediction),
            )
            prediction_tips: list[OptimizedTip] = []
            for result in optimization_results:
                prediction_tips.extend(result.optimized_tips)
            optimized_tips.extend(prediction_tips)
            for optimized_tip in prediction_tips:
                self.manager.emit(
                    EventName.PROVIDER_TIP_READY,
                    self.context,
                    ProviderTipReadyPayload(prediction=prediction, optimized_tip=optimized_tip),
                )
        self.manager.emit(
            EventName.DEBUG_REPORT_REQUESTED,
            self.context,
            DebugReportRequestedPayload(predictions=predictions, optimized_tips=optimized_tips),
        )
        diagnostics = [diagnostic for result in self.context.event_results for diagnostic in result.diagnostics]
        return WorkflowRun(
            context=self.context,
            predictions=predictions,
            optimized_tips=optimized_tips,
            diagnostics=diagnostics,
        )

    def latest_result_rows(self) -> list[dict[str, Any]]:
        """Return consensus-confirmed final-score rows, if storage is available."""

        if self.context.storage is None:
            return []
        return [
            result.to_record()
            for result in load_tournament_state(self.context.storage).results
        ]

    def emit_results_updated_if_changed(
        self,
        previous_results: list[dict[str, Any]],
        *,
        source_event: str,
    ) -> list[PluginResult]:
        """Emit RESULTS_UPDATED when consensus-confirmed final scores changed."""

        current_results = self.latest_result_rows()
        new_results, changed_results = result_row_changes(previous_results, current_results)
        if not new_results and not changed_results:
            return []
        return self.manager.emit(
            EventName.RESULTS_UPDATED,
            self.context,
            ResultsUpdatedPayload(
                previous_results=previous_results,
                current_results=current_results,
                new_results=new_results,
                changed_results=changed_results,
                source_event=source_event,
            ),
        )


def result_row_changes(
    previous_results: list[dict[str, Any]],
    current_results: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Return newly observed and changed confirmed result rows by stable result record key."""

    previous_by_key = {_result_record_key(row): row for row in previous_results if _result_record_key(row)}
    new_results: list[dict[str, Any]] = []
    changed_results: list[dict[str, Any]] = []
    for current in current_results:
        key = _result_record_key(current)
        if not key:
            continue
        previous = previous_by_key.get(key)
        if previous is None:
            new_results.append({"update_type": "new", "current": current})
            continue
        if _result_signature(previous) != _result_signature(current):
            changed_results.append({"update_type": "changed", "previous": previous, "current": current})
    return new_results, changed_results


def _result_record_key(row: dict[str, Any]) -> str:
    return str(row.get("record_key") or row.get("_record", {}).get("record_key") or "")


def _result_signature(row: dict[str, Any]) -> tuple[Any, ...]:
    return (
        row.get("fixture_key"),
        row.get("event_date"),
        row.get("home_fifa_code") or row.get("home_team"),
        row.get("away_fifa_code") or row.get("away_team"),
        row.get("home_score"),
        row.get("away_score"),
        row.get("status"),
    )
