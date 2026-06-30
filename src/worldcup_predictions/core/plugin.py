"""Small hook-style plugin runtime for workflow modules."""

from __future__ import annotations

import importlib
import time
from dataclasses import dataclass, field
from typing import Any, Iterable, Protocol

from worldcup_predictions.core.contracts import Artifact, Diagnostic, OptimizedTip, Prediction, Signal
from worldcup_predictions.core.datasets import DATASET_CONTRACTS, PLUGIN_EVENT_OUTPUTS, PLUGIN_RUN_DIAGNOSTICS
from worldcup_predictions.core.events import EventName, event_value
from worldcup_predictions.core.metadata import PluginKind, PluginMetadata
from worldcup_predictions.core.payloads import PayloadMixin
from worldcup_predictions.core.signals import SIGNAL_CONTRACTS
from worldcup_predictions.storage.ledger import stable_hash


@dataclass(frozen=True)
class PluginResult:
    """Result returned by a plugin for one event."""

    plugin_id: str
    event: str
    signals: list[Signal] = field(default_factory=list)
    artifacts: list[Artifact] = field(default_factory=list)
    predictions: list[Prediction] = field(default_factory=list)
    optimized_tips: list[OptimizedTip] = field(default_factory=list)
    diagnostics: list[Diagnostic] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def empty(cls, plugin_id: str, event: EventName | str) -> "PluginResult":
        return cls(plugin_id=plugin_id, event=event_value(event))


class Plugin(Protocol):
    id: str
    version: str
    priority: int
    subscribed_events: tuple[str, ...]
    metadata: PluginMetadata

    def supports(self, event: EventName | str) -> bool:
        ...

    def handle(self, event: EventName | str, context: Any, payload: dict[str, Any] | PayloadMixin) -> PluginResult:
        ...


class BasePlugin:
    """Base class for deterministic workflow plugins."""

    id = "base"
    version = "0.1.0"
    priority = 100
    subscribed_events: tuple[str, ...] = ()
    metadata = PluginMetadata(
        plugin_id="base",
        kind=PluginKind.WORKFLOW,
        description="Base workflow plugin.",
    )

    def supports(self, event: EventName | str) -> bool:
        return event_value(event) in self.subscribed_events

    def handle(self, event: EventName | str, context: Any, payload: dict[str, Any] | PayloadMixin) -> PluginResult:
        return PluginResult.empty(self.id, event)


class PluginManager:
    """Register plugins and emit events in deterministic order."""

    def __init__(self, plugins: Iterable[Plugin] | None = None, *, fail_fast: bool = False) -> None:
        self.fail_fast = fail_fast
        self._plugins: list[Plugin] = []
        for plugin in plugins or []:
            self.register(plugin)

    @property
    def plugins(self) -> tuple[Plugin, ...]:
        return tuple(self._plugins)

    def register(self, plugin: Plugin) -> None:
        if any(existing.id == plugin.id for existing in self._plugins):
            raise ValueError(f"Plugin already registered: {plugin.id}")
        metadata = plugin_metadata(plugin)
        if metadata.plugin_id != plugin.id:
            raise ValueError(f"Plugin metadata id mismatch: {plugin.id} != {metadata.plugin_id}")
        self._plugins.append(plugin)
        self._plugins.sort(key=lambda item: (item.priority, item.id))

    def emit(self, event: EventName | str, context: Any, payload: dict[str, Any] | PayloadMixin | None = None) -> list[PluginResult]:
        payload = payload or {}
        results: list[PluginResult] = []
        for plugin in self._plugins:
            if not plugin.supports(event):
                continue
            started = time.perf_counter()
            try:
                result = plugin.handle(event, context, payload)
            except Exception as exc:  # noqa: BLE001 - plugins should not block unrelated sources.
                if self.fail_fast:
                    raise
                result = PluginResult(
                    plugin_id=plugin.id,
                    event=event_value(event),
                    diagnostics=[
                        Diagnostic(
                            level="error",
                            message=f"{plugin.id} failed while handling {event_value(event)}: {exc}",
                            source=plugin.id,
                        )
                    ],
                )
            duration_ms = round((time.perf_counter() - started) * 1000, 3)
            result = _with_metadata(result, {"duration_ms": duration_ms})
            result = self._validate_result(plugin, result)
            result = self._record_core_event_outputs(plugin, result, context)
            result = self._record_core_diagnostics(plugin, result, context, payload, duration_ms)
            results.append(result)
            if hasattr(context, "record_result"):
                context.record_result(result)
        return results

    def _validate_result(self, plugin: Plugin, result: PluginResult) -> PluginResult:
        diagnostics = list(result.diagnostics)
        metadata = plugin_metadata(plugin)
        declared_signals = set(metadata.signals_emitted)
        for signal in result.signals:
            if signal.name not in SIGNAL_CONTRACTS:
                diagnostics.append(
                    Diagnostic(
                        level="warning",
                        message=f"{plugin.id} emitted unknown signal '{signal.name}'.",
                        source="plugin_manager",
                        fixture_key=signal.fixture_key,
                    )
                )
            elif declared_signals and signal.name not in declared_signals:
                diagnostics.append(
                    Diagnostic(
                        level="warning",
                        message=f"{plugin.id} emitted undeclared signal '{signal.name}'.",
                        source="plugin_manager",
                        fixture_key=signal.fixture_key,
                    )
                )
        declared_datasets = set(metadata.datasets_written)
        for artifact in result.artifacts:
            if artifact.kind != "structured_dataset":
                continue
            if artifact.name not in DATASET_CONTRACTS:
                diagnostics.append(
                    Diagnostic(
                        level="warning",
                        message=f"{plugin.id} wrote unknown dataset '{artifact.name}'.",
                        source="plugin_manager",
                    )
                )
            elif declared_datasets and artifact.name not in declared_datasets:
                diagnostics.append(
                    Diagnostic(
                        level="warning",
                        message=f"{plugin.id} wrote undeclared dataset '{artifact.name}'.",
                        source="plugin_manager",
                    )
                )
        if diagnostics == result.diagnostics:
            return result
        return PluginResult(
            plugin_id=result.plugin_id,
            event=result.event,
            signals=result.signals,
            artifacts=result.artifacts,
            predictions=result.predictions,
            optimized_tips=result.optimized_tips,
            diagnostics=diagnostics,
            metadata=result.metadata,
        )

    def list_plugins(self) -> list[dict[str, Any]]:
        return [
            {
                "id": plugin.id,
                "version": plugin.version,
                "priority": plugin.priority,
                "events": list(plugin.subscribed_events),
                "metadata": plugin_metadata(plugin).to_dict(),
            }
            for plugin in self._plugins
        ]

    def _record_core_event_outputs(
        self,
        plugin: Plugin,
        result: PluginResult,
        context: Any,
    ) -> PluginResult:
        storage = getattr(context, "storage", None)
        run_id = str(getattr(context, "run_id", "") or "")
        if storage is None or not run_id:
            return result
        sequence = len(getattr(context, "event_results", []))
        rows = _event_output_rows(result, run_id=run_id, sequence=sequence)
        if not rows:
            return result
        try:
            storage.write_records(
                PLUGIN_EVENT_OUTPUTS,
                rows,
                source="core_plugin_manager",
                run_id=run_id,
                metadata={"plugin_id": plugin.id, "event": result.event, "sequence": sequence},
            )
        except Exception as exc:  # noqa: BLE001 - output history must never block workflow execution.
            return _with_diagnostic(
                result,
                Diagnostic(
                    level="warning",
                    message=f"Core plugin event outputs could not be persisted: {exc}",
                    source="core_plugin_manager",
                ),
            )
        return result

    def _record_core_diagnostics(
        self,
        plugin: Plugin,
        result: PluginResult,
        context: Any,
        payload: dict[str, Any] | PayloadMixin,
        duration_ms: float,
    ) -> PluginResult:
        storage = getattr(context, "storage", None)
        run_id = str(getattr(context, "run_id", "") or "")
        if storage is None or not run_id:
            return result
        metadata = plugin_metadata(plugin)
        row = {
            "record_key": stable_hash(
                {
                    "run_id": run_id,
                    "plugin": plugin.id,
                    "event": result.event,
                    "sequence": len(getattr(context, "event_results", [])),
                }
            ),
            "run_id": run_id,
            "plugin_id": plugin.id,
            "event": result.event,
            "plugin_kind": metadata.kind.value,
            "priority": getattr(plugin, "priority", None),
            "duration_ms": duration_ms,
            "payload": _payload_summary(payload),
            "output_counts": {
                "signals": len(result.signals),
                "artifacts": len(result.artifacts),
                "predictions": len(result.predictions),
                "optimized_tips": len(result.optimized_tips),
                "diagnostics": len(result.diagnostics),
            },
            "signal_names": sorted({signal.name for signal in result.signals}),
            "signal_sources": sorted({signal.source for signal in result.signals}),
            "fixture_keys": sorted(
                {
                    key
                    for key in [
                        *(signal.fixture_key for signal in result.signals),
                        *(prediction.fixture.key for prediction in result.predictions),
                        *(tip.fixture_key for tip in result.optimized_tips),
                    ]
                    if key
                }
            ),
            "artifact_names": [artifact.name for artifact in result.artifacts],
            "diagnostic_levels": _diagnostic_levels(result.diagnostics),
            "metadata": result.metadata,
        }
        try:
            storage.write_records(
                PLUGIN_RUN_DIAGNOSTICS,
                [row],
                source="core_plugin_manager",
                run_id=run_id,
            )
        except Exception as exc:  # noqa: BLE001 - diagnostics must never block workflow execution.
            return _with_diagnostic(
                result,
                Diagnostic(
                    level="warning",
                    message=f"Core plugin diagnostics could not be persisted: {exc}",
                    source="core_plugin_manager",
                ),
            )
        return result


def plugin_metadata(plugin: Plugin) -> PluginMetadata:
    metadata = getattr(plugin, "metadata", BasePlugin.metadata)
    if metadata.plugin_id != BasePlugin.metadata.plugin_id:
        return metadata
    return PluginMetadata(
        plugin_id=plugin.id,
        kind=PluginKind.WORKFLOW,
        description=f"{plugin.id} plugin.",
    )


def _with_metadata(result: PluginResult, metadata: dict[str, Any]) -> PluginResult:
    return PluginResult(
        plugin_id=result.plugin_id,
        event=result.event,
        signals=result.signals,
        artifacts=result.artifacts,
        predictions=result.predictions,
        optimized_tips=result.optimized_tips,
        diagnostics=result.diagnostics,
        metadata={**result.metadata, **metadata},
    )


def _with_diagnostic(result: PluginResult, diagnostic: Diagnostic) -> PluginResult:
    return PluginResult(
        plugin_id=result.plugin_id,
        event=result.event,
        signals=result.signals,
        artifacts=result.artifacts,
        predictions=result.predictions,
        optimized_tips=result.optimized_tips,
        diagnostics=[*result.diagnostics, diagnostic],
        metadata=result.metadata,
    )


def _payload_summary(payload: dict[str, Any] | PayloadMixin) -> dict[str, Any]:
    data = payload.to_dict() if isinstance(payload, PayloadMixin) else dict(payload or {})
    summary: dict[str, Any] = {
        "type": payload.__class__.__name__,
        "keys": sorted(data),
    }
    if "limit" in data:
        summary["limit"] = data["limit"]
    if "include_closed" in data:
        summary["include_closed"] = data["include_closed"]
    prediction = data.get("prediction")
    if isinstance(prediction, Prediction):
        summary["fixture_key"] = prediction.fixture.key
        summary["prediction_source"] = prediction.source
    optimized_tip = data.get("optimized_tip")
    if isinstance(optimized_tip, OptimizedTip):
        summary["optimized_tip_provider"] = optimized_tip.ruleset.provider
        summary["optimized_tip_fixture_key"] = optimized_tip.fixture_key
    if isinstance(data.get("predictions"), list):
        summary["prediction_count"] = len(data["predictions"])
    if isinstance(data.get("optimized_tips"), list):
        summary["optimized_tip_count"] = len(data["optimized_tips"])
    return summary


def _diagnostic_levels(diagnostics: list[Diagnostic]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for diagnostic in diagnostics:
        counts[diagnostic.level] = counts.get(diagnostic.level, 0) + 1
    return counts


def _event_output_rows(result: PluginResult, *, run_id: str, sequence: int) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for output_type, values in (
        ("signal", result.signals),
        ("artifact", result.artifacts),
        ("prediction", result.predictions),
        ("optimized_tip", result.optimized_tips),
        ("diagnostic", result.diagnostics),
    ):
        for output_index, value in enumerate(values):
            payload = _output_payload(output_type, value)
            rows.append(
                {
                    "record_key": stable_hash(
                        {
                            "run_id": run_id,
                            "plugin_id": result.plugin_id,
                            "event": result.event,
                            "sequence": sequence,
                            "output_type": output_type,
                            "output_index": output_index,
                            "payload": payload,
                        }
                    ),
                    "run_id": run_id,
                    "plugin_id": result.plugin_id,
                    "event": result.event,
                    "sequence": sequence,
                    "output_type": output_type,
                    "output_index": output_index,
                    "fixture_key": _output_fixture_key(output_type, value),
                    "payload": payload,
                    "result_metadata": result.metadata,
                }
            )
    return rows


def _output_payload(output_type: str, value: Any) -> dict[str, Any]:
    if isinstance(value, Signal):
        return {
            "name": value.name,
            "source": value.source,
            "fixture_key": value.fixture_key,
            "value": value.value,
            "weight": value.weight,
            "confidence": value.confidence,
            "rationale": value.rationale,
            "metadata": value.metadata,
        }
    if isinstance(value, Artifact):
        return {
            "name": value.name,
            "kind": value.kind,
            "source": value.source,
            "path": value.path,
            "data": value.data,
            "metadata": value.metadata,
        }
    if isinstance(value, Prediction):
        return value.to_dict()
    if isinstance(value, OptimizedTip):
        return value.to_dict()
    if isinstance(value, Diagnostic):
        return {
            "level": value.level,
            "message": value.message,
            "source": value.source,
            "fixture_key": value.fixture_key,
            "metadata": value.metadata,
        }
    return {"type": output_type, "value": value}


def _output_fixture_key(output_type: str, value: Any) -> str | None:
    if isinstance(value, Signal | Diagnostic):
        return value.fixture_key
    if isinstance(value, Prediction):
        return value.fixture.key
    if isinstance(value, OptimizedTip):
        return value.fixture_key
    if isinstance(value, Artifact):
        if isinstance(value.data, dict):
            fixture_key = value.data.get("fixture_key")
            if fixture_key:
                return str(fixture_key)
        fixture_key = value.metadata.get("fixture_key")
        if fixture_key:
            return str(fixture_key)
    return None


def load_plugins_from_modules(module_names: Iterable[str]) -> list[Plugin]:
    """Load plugins from modules exposing register_plugins()."""

    loaded: list[Plugin] = []
    for module_name in module_names:
        module = importlib.import_module(module_name)
        register = getattr(module, "register_plugins", None)
        if register is None:
            raise AttributeError(f"{module_name} does not expose register_plugins()")
        loaded.extend(register())
    return loaded
