"""20min.ch provider optimizer plugin."""

from __future__ import annotations

from typing import Any

from worldcup_predictions.core.contracts import Diagnostic, Prediction
from worldcup_predictions.core.events import EventName, event_value
from worldcup_predictions.core.metadata import PluginKind, PluginMetadata
from worldcup_predictions.core.plugin import BasePlugin, PluginResult
from .rules import optimize_twenty_min_tip


class TwentyMinChProviderOptimizerPlugin(BasePlugin):
    """Optimize selections for 20min.ch's World Cup prediction rules."""

    id = "provider_optimizer_20min_ch"
    version = "0.1.0"
    priority = 710
    subscribed_events = (EventName.PROVIDER_OPTIMIZATION_REQUESTED.value,)
    metadata = PluginMetadata(
        plugin_id=id,
        kind=PluginKind.PROVIDER_OPTIMIZER,
        description="Optimize selections for 20min.ch scoring rules.",
        confidence_policy="Uses provider expected points or advancement probabilities without changing model probabilities.",
    )

    def handle(self, event, context: Any, payload: dict[str, Any]) -> PluginResult:
        prediction = payload.get("prediction")
        if not isinstance(prediction, Prediction):
            return PluginResult.empty(self.id, event)

        optimized_tip = optimize_twenty_min_tip(prediction, optimizer_id=self.id)
        diagnostics = []
        if optimized_tip.metadata.get("probability_source") == "derived_from_outcome_probabilities":
            diagnostics.append(
                Diagnostic(
                    level="info",
                    message=(
                        "20min knockout advancement probability was derived from outcome probabilities because "
                        "no explicit advancement probabilities were available."
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
