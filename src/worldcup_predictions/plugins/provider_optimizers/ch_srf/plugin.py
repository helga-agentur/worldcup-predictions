"""srf.ch provider optimizer plugin."""

from __future__ import annotations

from typing import Any

from worldcup_predictions.core.contracts import Diagnostic, Prediction
from worldcup_predictions.core.events import EventName, event_value
from worldcup_predictions.core.metadata import PluginKind, PluginMetadata
from worldcup_predictions.core.plugin import BasePlugin, PluginResult
from worldcup_predictions.plugins.provider_optimizers.common import ScoreMatrixOptimizer
from worldcup_predictions.plugins.provider_optimizers.ch_srf.rules import srf_rules_for_fixture


class SrfChProviderOptimizerPlugin(BasePlugin):
    """Optimize exact-score tips for SRF's public scoring rules."""

    id = "provider_optimizer_srf_ch"
    version = "0.1.0"
    priority = 700
    subscribed_events = (EventName.PROVIDER_OPTIMIZATION_REQUESTED.value,)
    metadata = PluginMetadata(
        plugin_id=id,
        kind=PluginKind.PROVIDER_OPTIMIZER,
        description="Optimize exact-score tips for srf.ch scoring rules.",
        confidence_policy="Uses provider expected points over the neutral score matrix without changing model probabilities.",
    )

    def __init__(self, optimizer: ScoreMatrixOptimizer | None = None) -> None:
        self.optimizer = optimizer or ScoreMatrixOptimizer()

    def handle(self, event, context: Any, payload: dict[str, Any]) -> PluginResult:
        prediction = payload.get("prediction")
        if not isinstance(prediction, Prediction):
            return PluginResult.empty(self.id, event)

        rules = srf_rules_for_fixture(prediction.fixture)
        optimized_tip = self.optimizer.optimize(prediction, rules, optimizer_id=self.id)
        diagnostics = []
        if optimized_tip.metadata.get("fallback") == "missing_score_matrix":
            diagnostics.append(
                Diagnostic(
                    level="warning",
                    message=(
                        "SRF tip optimization used the neutral most-likely result because no score matrix "
                        "was available."
                    ),
                    source=self.id,
                    fixture_key=prediction.fixture.key,
                )
            )
        return PluginResult(
            plugin_id=self.id,
            event=event_value(event),
            optimized_tips=[optimized_tip],
            diagnostics=diagnostics,
        )
