"""Per-fixture prediction debug report plugin."""

from __future__ import annotations

from collections import defaultdict

from worldcup_predictions.core.constants import EXPECTED_DEBUG_SIGNAL_SOURCES
from worldcup_predictions.core.contracts import Artifact, Diagnostic, OptimizedTip, Prediction, Signal
from worldcup_predictions.core.datasets import PREDICTION_DEBUG_REPORT as DEBUG_REPORT_DATASET
from worldcup_predictions.core.datasets import PREDICTION_SIGNAL_IMPACTS
from worldcup_predictions.core.events import EventName, event_value
from worldcup_predictions.core.metadata import PluginKind, PluginMetadata
from worldcup_predictions.core.plugin import BasePlugin, PluginResult


EXPECTED_SIGNAL_SOURCES = set(EXPECTED_DEBUG_SIGNAL_SOURCES)


class DebugReportPlugin(BasePlugin):
    """Persist compact, comparable prediction debug rows."""

    id = "debug_report"
    version = "0.1.0"
    priority = 950
    subscribed_events = (EventName.DEBUG_REPORT_REQUESTED.value,)
    metadata = PluginMetadata(
        plugin_id=id,
        kind=PluginKind.OUTPUT,
        description="Persist per-fixture source coverage, model adjustments, and provider tips.",
        datasets_written=(DEBUG_REPORT_DATASET, PREDICTION_SIGNAL_IMPACTS),
        confidence_policy="Debug rows are audit artifacts and do not affect predictions.",
    )

    def handle(self, event, context, payload):
        if context.storage is None:
            return PluginResult(
                plugin_id=self.id,
                event=event_value(event),
                diagnostics=[Diagnostic(level="warning", message="Structured storage is unavailable; debug report was skipped.", source=self.id)],
            )
        predictions = [item for item in payload.get("predictions") or [] if isinstance(item, Prediction)]
        optimized_tips = [item for item in payload.get("optimized_tips") or [] if isinstance(item, OptimizedTip)]
        signals = workflow_signals(context)
        rows = debug_rows(predictions, optimized_tips, signals)
        impact_rows = signal_impact_rows(predictions, signals)
        writer = getattr(context.storage, "replace_records", context.storage.write_records)
        count = writer(DEBUG_REPORT_DATASET, rows, source=self.id, run_id=context.run_id)
        impact_count = writer(PREDICTION_SIGNAL_IMPACTS, impact_rows, source=self.id, run_id=context.run_id)
        return PluginResult(
            plugin_id=self.id,
            event=event_value(event),
            artifacts=[
                Artifact(
                    name=DEBUG_REPORT_DATASET,
                    kind="structured_dataset",
                    source=self.id,
                    data={"rows": count},
                ),
                Artifact(
                    name=PREDICTION_SIGNAL_IMPACTS,
                    kind="structured_dataset",
                    source=self.id,
                    data={"rows": impact_count},
                ),
            ],
            metadata={"rows": count, "signal_impact_rows": impact_count},
        )


def workflow_signals(context) -> list[Signal]:
    signals: list[Signal] = []
    for result in context.event_results:
        signals.extend(result.signals)
    return signals


def debug_rows(predictions: list[Prediction], optimized_tips: list[OptimizedTip], signals: list[Signal]) -> list[dict]:
    signals_by_fixture: dict[str, list[Signal]] = defaultdict(list)
    global_signals: list[Signal] = []
    for signal in signals:
        if signal.fixture_key:
            signals_by_fixture[signal.fixture_key].append(signal)
        else:
            global_signals.append(signal)
    tips_by_fixture: dict[str, list[OptimizedTip]] = defaultdict(list)
    for tip in optimized_tips:
        tips_by_fixture[tip.fixture_key].append(tip)
    rows = []
    for prediction in predictions:
        fixture_key = prediction.fixture.key
        fixture_signals = [*signals_by_fixture.get(fixture_key, []), *global_signals]
        present_sources = {signal.source for signal in fixture_signals}
        provider_tips = {
            tip.ruleset.provider: {
                "tip": tip.display_text(),
                "expected_points": tip.expected_points,
                "selection_type": tip.selection_type,
            }
            for tip in tips_by_fixture.get(fixture_key, [])
        }
        rows.append(
            {
                "record_key": fixture_key,
                "fixture_key": fixture_key,
                "event_date": prediction.fixture.event_date,
                "home_team": prediction.fixture.home_team,
                "away_team": prediction.fixture.away_team,
                "most_likely_result": prediction.most_likely.as_text(),
                "expected_home_goals": prediction.expected_home_goals,
                "expected_away_goals": prediction.expected_away_goals,
                "prob_home": prediction.outcome_probabilities.home,
                "prob_draw": prediction.outcome_probabilities.draw,
                "prob_away": prediction.outcome_probabilities.away,
                "confidence_label": prediction.confidence_label,
                "confidence_percent": prediction.confidence_percent,
                "signal_count": len(fixture_signals),
                "signal_sources": sorted(present_sources),
                "signal_names": sorted({signal.name for signal in fixture_signals}),
                "missing_signal_sources": sorted(EXPECTED_SIGNAL_SOURCES - present_sources),
                "signal_adjustments": prediction.metadata.get("signal_adjustments", []),
                "provider_tips": provider_tips,
                "metadata": {
                    "model": prediction.metadata.get("model"),
                    "draw_adjustment": prediction.metadata.get("draw_adjustment"),
                },
            }
        )
    return rows


def signal_impact_rows(predictions: list[Prediction], signals: list[Signal]) -> list[dict]:
    signals_by_fixture: dict[str, list[Signal]] = defaultdict(list)
    global_signals: list[Signal] = []
    for signal in signals:
        if signal.fixture_key:
            signals_by_fixture[signal.fixture_key].append(signal)
        else:
            global_signals.append(signal)
    rows = []
    for prediction in predictions:
        fixture_key = prediction.fixture.key
        adjustments = prediction.metadata.get("signal_adjustments", [])
        adjustments_by_name: dict[str, list[dict]] = defaultdict(list)
        for adjustment in adjustments:
            if isinstance(adjustment, dict) and adjustment.get("signal"):
                adjustments_by_name[str(adjustment["signal"])].append(adjustment)
        relevant_signals = [*signals_by_fixture.get(fixture_key, []), *global_signals]
        for index, signal in enumerate(relevant_signals):
            scope = "fixture" if signal.fixture_key else "global"
            rows.append(
                {
                    "record_key": f"{fixture_key}:{scope}:{index}:{signal.source}:{signal.name}",
                    "fixture_key": fixture_key,
                    "event_date": prediction.fixture.event_date,
                    "home_team": prediction.fixture.home_team,
                    "away_team": prediction.fixture.away_team,
                    "signal_name": signal.name,
                    "signal_source": signal.source,
                    "signal_value": signal_impact_value(signal),
                    "signal_weight": signal.weight,
                    "signal_confidence": signal.confidence,
                    "signal_scope": scope,
                    "rationale": signal.rationale,
                    "applied": signal.name in adjustments_by_name,
                    "adjustments": adjustments_by_name.get(signal.name, []),
                    "prediction_source": prediction.source,
                    "prob_home": prediction.outcome_probabilities.home,
                    "prob_draw": prediction.outcome_probabilities.draw,
                    "prob_away": prediction.outcome_probabilities.away,
                    "expected_home_goals": prediction.expected_home_goals,
                    "expected_away_goals": prediction.expected_away_goals,
                    "metadata": signal.metadata,
                }
            )
    return rows


def signal_impact_value(signal: Signal):
    """Return a compact value for diagnostics, including metadata-only probability signals."""

    if signal.value is not None:
        return signal.value
    metadata = signal.metadata or {}
    probability_keys = ("prob_home", "prob_draw", "prob_away")
    if all(key in metadata for key in probability_keys):
        return {key: metadata.get(key) for key in probability_keys}
    return None
