"""Persist workflow outputs as structured records."""

from __future__ import annotations

from typing import Any

from worldcup_predictions.core.contracts import Diagnostic, OptimizedTip, Prediction
from worldcup_predictions.core.datasets import OPTIMIZED_TIPS, PREDICTIONS
from worldcup_predictions.core.events import EventName, event_value
from worldcup_predictions.core.metadata import PluginKind, PluginMetadata
from worldcup_predictions.core.plugin import BasePlugin, PluginResult


class StructuredOutputPlugin(BasePlugin):
    """Store extracted prediction facts without raw source payloads."""

    id = "structured_output"
    version = "0.1.0"
    priority = 900
    subscribed_events = (EventName.PREDICTION_READY.value, EventName.PROVIDER_TIP_READY.value)
    metadata = PluginMetadata(
        plugin_id=id,
        kind=PluginKind.OUTPUT,
        description="Persist provider-neutral predictions and provider-specific optimized tips.",
        datasets_written=(PREDICTIONS, OPTIMIZED_TIPS),
        confidence_policy="Output-only plugin; does not affect predictions.",
    )

    def handle(self, event, context: Any, payload: dict[str, Any]) -> PluginResult:
        if event_value(event) == EventName.PROVIDER_TIP_READY.value:
            return self._persist_optimized_tip(event, context, payload)
        return self._persist_prediction(event, context, payload)

    def _persist_prediction(self, event, context: Any, payload: dict[str, Any]) -> PluginResult:
        prediction = payload.get("prediction")
        if not isinstance(prediction, Prediction):
            return PluginResult.empty(self.id, event)

        storage = getattr(context, "storage", None)
        if storage is None:
            return PluginResult(
                plugin_id=self.id,
                event=event_value(event),
                diagnostics=[
                    Diagnostic(
                        level="warning",
                        message="Structured storage is unavailable, so prediction facts were not persisted.",
                        source=self.id,
                        fixture_key=prediction.fixture.key,
                    )
                ],
            )

        row = {
            "record_key": prediction.fixture.key,
            "fixture_key": prediction.fixture.key,
            "event_date": prediction.fixture.event_date,
            "home_team": prediction.fixture.home_team,
            "away_team": prediction.fixture.away_team,
            "stage": prediction.fixture.stage,
            "group": prediction.fixture.group,
            "matchday": prediction.fixture.matchday,
            "most_likely_home": prediction.most_likely.home,
            "most_likely_away": prediction.most_likely.away,
            "expected_home_goals": prediction.expected_home_goals,
            "expected_away_goals": prediction.expected_away_goals,
            "prob_home": prediction.outcome_probabilities.home,
            "prob_draw": prediction.outcome_probabilities.draw,
            "prob_away": prediction.outcome_probabilities.away,
            "confidence_label": prediction.confidence_label,
            "confidence_percent": prediction.confidence_percent,
            "score_matrix_entries": len(prediction.score_matrix),
            "score_matrix": [entry.to_dict() for entry in prediction.score_matrix],
            "prediction_source": prediction.source,
            "metadata": prediction.metadata,
        }
        written = storage.write_records(
            PREDICTIONS,
            [row],
            source=self.id,
            run_id=context.run_id,
            fixture_key=prediction.fixture.key,
            metadata={"source_prediction_plugin": prediction.source},
        )
        return PluginResult(
            plugin_id=self.id,
            event=event_value(event),
            diagnostics=[
                Diagnostic(
                    level="info",
                    message=f"Persisted {written} structured prediction row.",
                    source=self.id,
                    fixture_key=prediction.fixture.key,
                    metadata={"dataset": PREDICTIONS},
                )
            ],
        )

    def _persist_optimized_tip(self, event, context: Any, payload: dict[str, Any]) -> PluginResult:
        prediction = payload.get("prediction")
        optimized_tip = payload.get("optimized_tip")
        if not isinstance(optimized_tip, OptimizedTip):
            return PluginResult.empty(self.id, event)

        storage = getattr(context, "storage", None)
        if storage is None:
            return PluginResult(
                plugin_id=self.id,
                event=event_value(event),
                diagnostics=[
                    Diagnostic(
                        level="warning",
                        message="Structured storage is unavailable, so optimized provider facts were not persisted.",
                        source=self.id,
                        fixture_key=optimized_tip.fixture_key,
                    )
                ],
            )

        row = optimized_tip.to_dict()
        row["record_key"] = f"{optimized_tip.ruleset.key}|{optimized_tip.fixture_key}"
        if isinstance(prediction, Prediction):
            row.update(
                {
                    "event_date": prediction.fixture.event_date,
                    "home_team": prediction.fixture.home_team,
                    "away_team": prediction.fixture.away_team,
                    "stage": prediction.fixture.stage,
                    "group": prediction.fixture.group,
                    "matchday": prediction.fixture.matchday,
                    "most_likely_home": prediction.most_likely.home,
                    "most_likely_away": prediction.most_likely.away,
                    "expected_home_goals": prediction.expected_home_goals,
                    "expected_away_goals": prediction.expected_away_goals,
                    "prediction_source": prediction.source,
                }
            )
        written = storage.write_records(
            OPTIMIZED_TIPS,
            [row],
            source=self.id,
            run_id=context.run_id,
            fixture_key=optimized_tip.fixture_key,
            metadata={"optimizer_id": optimized_tip.optimizer_id, "ruleset": optimized_tip.ruleset.key},
        )
        return PluginResult(
            plugin_id=self.id,
            event=event_value(event),
            diagnostics=[
                Diagnostic(
                    level="info",
                    message=f"Persisted {written} structured optimized-tip row.",
                    source=self.id,
                    fixture_key=optimized_tip.fixture_key,
                    metadata={"dataset": OPTIMIZED_TIPS, "provider": optimized_tip.ruleset.provider},
                )
            ],
        )
