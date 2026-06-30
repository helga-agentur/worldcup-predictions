"""Tournament state workflow plugin."""

from __future__ import annotations

from worldcup_predictions.core.contracts import Artifact, Diagnostic
from worldcup_predictions.core.datasets import GROUP_STATE_SIGNALS, TOURNAMENT_RESULT_CHECKS, TOURNAMENT_RESULTS, TOURNAMENT_STANDINGS
from worldcup_predictions.core.events import EventName, event_value
from worldcup_predictions.core.metadata import PluginKind, PluginMetadata
from worldcup_predictions.core.plugin import BasePlugin, PluginResult
from worldcup_predictions.core.signals import GROUP_DRAW_PRESSURE, GROUP_ELIMINATION_RISK, GROUP_MOTIVATION, GROUP_ROTATION_RISK
from worldcup_predictions.tournament import (
    TournamentState,
    build_group_state_rows,
    build_group_state_signals,
)
from worldcup_predictions.tournament.repository import (
    GROUP_STATE_DATASET,
    load_tournament_state,
    write_derived_state,
)


class TournamentStatePlugin(BasePlugin):
    """Load/persist tournament state and derived group-context signals."""

    id = "tournament_state"
    version = "0.1.0"
    priority = 100
    subscribed_events = (
        EventName.WORKFLOW_STARTED.value,
        EventName.FIXTURES_REQUESTED.value,
        EventName.FEATURE_SIGNALS_REQUESTED.value,
    )
    metadata = PluginMetadata(
        plugin_id=id,
        kind=PluginKind.WORKFLOW,
        description="Load tournament state, apply result consensus, derive standings, and emit group-state signals.",
        datasets_read=(TOURNAMENT_RESULTS,),
        datasets_written=(TOURNAMENT_STANDINGS, TOURNAMENT_RESULT_CHECKS, GROUP_STATE_SIGNALS),
        signals_emitted=(GROUP_MOTIVATION, GROUP_DRAW_PRESSURE, GROUP_ROTATION_RISK, GROUP_ELIMINATION_RISK),
        confidence_policy="Group-state signals are deterministic from fixtures and consensus-confirmed results.",
    )

    def handle(self, event, context, payload):
        if context.storage is None:
            return PluginResult(
                plugin_id=self.id,
                event=event_value(event),
                diagnostics=[
                    Diagnostic(
                        level="warning",
                        message="Structured storage is unavailable; tournament state was skipped.",
                        source=self.id,
                    )
                ],
            )

        event_name = event_value(event)
        if event_name == EventName.FEATURE_SIGNALS_REQUESTED.value:
            return self._emit_group_state(context, event_name)
        return self._load_state(context, event_name)

    def _load_state(self, context, event_name: str) -> PluginResult:
        state = load_tournament_state(context.storage)
        context.state["tournament_state"] = state
        write_counts = write_derived_state(context.storage, state, run_id=context.run_id)
        return PluginResult(
            plugin_id=self.id,
            event=event_name,
            artifacts=[_state_artifact(state, write_counts)],
            metadata={
                "fixtures": len(state.fixtures),
                "results": len(state.results),
                "groups": len(state.standings),
            },
        )

    def _emit_group_state(self, context, event_name: str) -> PluginResult:
        state = context.state.get("tournament_state")
        if not isinstance(state, TournamentState):
            state = load_tournament_state(context.storage)
            context.state["tournament_state"] = state
        rows = build_group_state_rows(state)
        count = context.storage.write_records(
            GROUP_STATE_DATASET,
            rows,
            source=self.id,
            run_id=context.run_id,
        )
        return PluginResult(
            plugin_id=self.id,
            event=event_name,
            signals=build_group_state_signals(state),
            artifacts=[
                Artifact(
                    name=GROUP_STATE_DATASET,
                    kind="structured_dataset",
                    source=self.id,
                    data={"rows": count},
                )
            ],
            metadata={"group_state_rows": count},
        )


def _state_artifact(state: TournamentState, write_counts: dict[str, int]) -> Artifact:
    return Artifact(
        name="tournament_state",
        kind="tournament_state",
        source="tournament_state",
        data={
            "fixtures": len(state.fixtures),
            "results": len(state.results),
            "groups": len(state.standings),
            "result_checks": len(state.result_checks),
            "write_counts": write_counts,
        },
    )
