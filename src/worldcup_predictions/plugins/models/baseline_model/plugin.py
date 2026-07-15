"""Baseline prediction model plugin."""

from __future__ import annotations

from worldcup_predictions.core.contracts import Diagnostic, ScoreTip, Signal
from worldcup_predictions.core.datasets import HISTORICAL_RESULTS, MARKET_OUTRIGHTS, TOURNAMENT_RESULTS
from worldcup_predictions.core.events import EventName, event_value
from worldcup_predictions.core.metadata import PluginKind, PluginMetadata
from worldcup_predictions.core.plugin import BasePlugin, PluginResult
from worldcup_predictions.evaluation.signal_skill import signal_skill_multipliers
from worldcup_predictions.model.contracts import ModelSignalPolicy
from worldcup_predictions.model.signal_application import SignalApplierRegistry
from worldcup_predictions.market_prior import adjust_prediction_for_outrights, team_strengths_from_outrights
from worldcup_predictions.model import BaselineModel, HistoricalResult, load_historical_results
from worldcup_predictions.tournament import TournamentState
from worldcup_predictions.tournament.repository import load_tournament_state


class BaselineModelPlugin(BasePlugin):
    """Emit neutral score predictions from tournament state and historical results."""

    id = "baseline_model"
    version = "0.1.0"
    priority = 400
    subscribed_events = (EventName.PREDICTIONS_REQUESTED.value,)
    metadata = PluginMetadata(
        plugin_id=id,
        kind=PluginKind.MODEL,
        description="Transparent Elo/goal-profile model with capped typed-signal blending.",
        datasets_read=(HISTORICAL_RESULTS, MARKET_OUTRIGHTS, TOURNAMENT_RESULTS),
        confidence_policy="Confidence is the strongest H/D/A probability after exact-score matrix adjustments.",
    )

    def handle(self, event, context, payload):
        if context.storage is None:
            return PluginResult(
                plugin_id=self.id,
                event=event_value(event),
                diagnostics=[
                    Diagnostic(
                        level="warning",
                        message="Structured storage is unavailable; baseline predictions were skipped.",
                        source=self.id,
                    )
                ],
            )

        state = context.state.get("tournament_state")
        if not isinstance(state, TournamentState):
            state = load_tournament_state(context.storage)
            context.state["tournament_state"] = state

        include_closed = bool(payload.get("include_closed"))
        include_all_fixtures = bool(payload.get("include_all_fixtures"))
        limit = int(payload.get("limit") or 0)
        if include_all_fixtures:
            fixtures = sorted(state.fixtures, key=lambda item: (item.event_date, item.home_team.name, item.away_team.name))
        else:
            fixtures = state.fixtures_without_results() if include_closed else state.open_fixtures()
        fixtures = fixtures[:limit] if limit > 0 else fixtures

        historical_results = load_historical_results(context.storage)
        if not context.settings.get("ignore_tournament_results_for_model"):
            historical_results.extend(_historical_from_tournament_results(state))
        signals = _workflow_signals(context)
        policy = ModelSignalPolicy(signal_skill_multipliers=signal_skill_multipliers(context.storage, state))
        model = BaselineModel(historical_results, signal_appliers=SignalApplierRegistry.default(policy))
        team_strengths = team_strengths_from_outrights(context.storage.read_records(MARKET_OUTRIGHTS, latest_only=True))
        predictions = [
            adjust_prediction_for_outrights(model.predict_fixture(fixture, signals=signals), team_strengths)
            for fixture in fixtures
        ]
        diagnostics = []
        if not historical_results:
            diagnostics.append(
                Diagnostic(
                    level="warning",
                    message="No historical results are available; baseline model used neutral fallback ratings.",
                    source=self.id,
                )
            )
        return PluginResult(
            plugin_id=self.id,
            event=event_value(event),
            predictions=predictions,
            diagnostics=diagnostics,
            metadata={
                "fixtures": len(fixtures),
                "historical_results": len(historical_results),
                "outright_strengths": len(team_strengths),
            },
        )


def _historical_from_tournament_results(state: TournamentState) -> list[HistoricalResult]:
    rows = []
    for result in state.results:
        rows.append(
            HistoricalResult(
                date=result.event_date[:10],
                home_team=result.home_team,
                away_team=result.away_team,
                score=ScoreTip(result.score.home, result.score.away),
                tournament="FIFA World Cup",
                neutral=True,
                source=result.source,
                metadata={"fixture_key": result.fixture_key},
            )
        )
    return rows


def _workflow_signals(context) -> list[Signal]:
    signals: list[Signal] = []
    for result in context.event_results:
        signals.extend(result.signals)
    return signals
