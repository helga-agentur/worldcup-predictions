"""Typed workflow event payloads."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from worldcup_predictions.core.contracts import OptimizedTip, Prediction


class PayloadMixin:
    """Compatibility helpers for existing dict-style plugin access."""

    def to_dict(self) -> dict[str, Any]:
        return dict(self.__dict__)

    def get(self, key: str, default: Any = None) -> Any:
        return self.to_dict().get(key, default)


@dataclass(frozen=True)
class WorkflowStartedPayload(PayloadMixin):
    limit: int


@dataclass(frozen=True)
class FixturesRequestedPayload(PayloadMixin):
    limit: int


@dataclass(frozen=True)
class ResultsUpdatedPayload(PayloadMixin):
    previous_results: list[dict[str, Any]]
    current_results: list[dict[str, Any]]
    new_results: list[dict[str, Any]] = field(default_factory=list)
    changed_results: list[dict[str, Any]] = field(default_factory=list)
    source_event: str = ""


@dataclass(frozen=True)
class FeatureSignalsRequestedPayload(PayloadMixin):
    limit: int


@dataclass(frozen=True)
class PredictionsRequestedPayload(PayloadMixin):
    limit: int
    include_closed: bool = False


@dataclass(frozen=True)
class PredictionReadyPayload(PayloadMixin):
    prediction: Prediction


@dataclass(frozen=True)
class ProviderOptimizationRequestedPayload(PayloadMixin):
    prediction: Prediction


@dataclass(frozen=True)
class ProviderTipReadyPayload(PayloadMixin):
    prediction: Prediction
    optimized_tip: OptimizedTip


@dataclass(frozen=True)
class DebugReportRequestedPayload(PayloadMixin):
    predictions: list[Prediction] = field(default_factory=list)
    optimized_tips: list[OptimizedTip] = field(default_factory=list)
