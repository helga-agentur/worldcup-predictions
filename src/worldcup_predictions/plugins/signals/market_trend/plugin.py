"""Market movement signals derived from the stored odds snapshot history.

Each hourly run appends a timestamped ``market_odds`` row per fixture, so the
append-only store holds a time series. This plugin reads that history (not just the
latest snapshot), measures totals-line drift, cross-snapshot disagreement, and the
favorite-probability move, and emits a small total-goals trend factor. It replaces the
legacy ``analyze_bookmaker_trends`` script, sourced from public Odds API history rather
than the removed manual bookmaker snapshots.
"""

from __future__ import annotations

import statistics
from collections import defaultdict
from typing import Any

from worldcup_predictions.core.constants import DYNAMIC_SOURCE_MARKET_MIN_CONFIDENCE, SOURCE_DYNAMIC_PUBLIC, SOURCE_MARKET_TREND
from worldcup_predictions.core.contracts import Artifact, Diagnostic, Signal
from worldcup_predictions.core.datasets import MARKET_ODDS, MARKET_TRENDS, PUBLIC_MARKET_OBSERVATIONS
from worldcup_predictions.core.events import EventName, event_value
from worldcup_predictions.core.metadata import PluginKind, PluginMetadata
from worldcup_predictions.core.plugin import BasePlugin, PluginResult
from worldcup_predictions.core.signals import TOTAL_GOALS_FACTOR
from worldcup_predictions.tournament import TournamentState
from worldcup_predictions.tournament.repository import load_tournament_state


MIN_SNAPSHOTS = 3
MIN_DRIFT = 0.10
MAX_TREND_FACTOR = 0.05
TREND_WEIGHT = 0.30


class MarketTrendPlugin(BasePlugin):
    """Emit a conservative total-goals trend factor from market movement over time."""

    id = "market_trend"
    version = "0.1.0"
    priority = 255
    subscribed_events = (EventName.FEATURE_SIGNALS_REQUESTED.value,)
    metadata = PluginMetadata(
        plugin_id=id,
        kind=PluginKind.SIGNAL,
        description="Derive market movement (totals-line drift, disagreement, favorite move) from the odds snapshot history.",
        datasets_read=(MARKET_ODDS, PUBLIC_MARKET_OBSERVATIONS),
        datasets_written=(MARKET_TRENDS,),
        signals_emitted=(TOTAL_GOALS_FACTOR,),
        confidence_policy="Trends need multiple snapshots; confidence rises with snapshot count and falls with cross-snapshot disagreement.",
    )

    def handle(self, event, context, payload):
        if context.storage is None:
            return PluginResult(
                plugin_id=self.id,
                event=event_value(event),
                diagnostics=[Diagnostic("warning", "Structured storage is unavailable; market trends were skipped.", self.id)],
            )
        history = context.storage.read_records(MARKET_ODDS, latest_only=False)
        public_history = public_market_history_rows(context.storage.read_records(PUBLIC_MARKET_OBSERVATIONS, latest_only=False))
        state = context.state.get("tournament_state")
        if not isinstance(state, TournamentState):
            state = load_tournament_state(context.storage)
            context.state["tournament_state"] = state
        open_keys = {fixture.key for fixture in state.open_fixtures()}
        rows = market_trend_rows([*history, *public_history], open_keys=open_keys)
        count = context.storage.write_records(MARKET_TRENDS, rows, source=self.id, run_id=context.run_id)
        signals = market_trend_signals(rows)
        diagnostics = []
        if not rows:
            diagnostics.append(
                Diagnostic("info", "Not enough market snapshots yet to measure movement.", self.id)
            )
        return PluginResult(
            plugin_id=self.id,
            event=event_value(event),
            signals=signals,
            artifacts=[Artifact(MARKET_TRENDS, "structured_dataset", self.id, data={"rows": count, "signals": len(signals)})],
            diagnostics=diagnostics,
            metadata={"rows": count, "signals": len(signals)},
        )


def public_market_history_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Normalize high-confidence public-page market observations for trend analysis."""

    normalized: list[dict[str, Any]] = []
    for row in rows:
        confidence = _optional_float(row.get("confidence"))
        if confidence is None or confidence < DYNAMIC_SOURCE_MARKET_MIN_CONFIDENCE:
            continue
        fixture_key = str(row.get("fixture_key") or "")
        if not fixture_key:
            continue
        normalized.append(
            {
                **row,
                "metadata": {
                    **dict(row.get("metadata") or {}),
                    "source": SOURCE_DYNAMIC_PUBLIC,
                    "bookmaker": row.get("domain") or (row.get("metadata") or {}).get("bookmaker") or "",
                    "public_market_confidence": confidence,
                },
            }
        )
    return normalized


def market_trend_rows(history: list[dict[str, Any]], *, open_keys: set[str] | None = None) -> list[dict[str, Any]]:
    by_fixture: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in history:
        fixture_key = str(row.get("fixture_key") or "")
        if not fixture_key:
            continue
        if open_keys is not None and fixture_key not in open_keys:
            continue
        by_fixture[fixture_key].append(row)

    rows: list[dict[str, Any]] = []
    for fixture_key, snapshots in sorted(by_fixture.items()):
        ordered = sorted(snapshots, key=_observed_at)
        total_values = [value for value in (_optional_float(row.get("total_goals")) for row in ordered) if value is not None]
        if len(total_values) < MIN_SNAPSHOTS:
            continue
        total_open = total_values[0]
        total_latest = total_values[-1]
        total_drift = total_latest - total_open
        total_volatility = statistics.pstdev(total_values) if len(total_values) > 1 else 0.0
        favorite_series = [
            max(home, away)
            for home, away in (
                (_optional_float(row.get("prob_home")), _optional_float(row.get("prob_away")))
                for row in ordered
            )
            if home is not None and away is not None
        ]
        favorite_prob_drift = favorite_series[-1] - favorite_series[0] if len(favorite_series) >= 2 else None
        factor = 1 + _clamp(total_drift * 0.10, -MAX_TREND_FACTOR, MAX_TREND_FACTOR)
        rows.append(
            {
                "record_key": fixture_key,
                "fixture_key": fixture_key,
                "snapshot_count": len(total_values),
                "total_open": total_open,
                "total_latest": total_latest,
                "total_line_drift": total_drift,
                "total_volatility": total_volatility,
                "favorite_prob_drift": favorite_prob_drift,
                "trend_total_goals_factor": factor,
                "metadata": {"source": SOURCE_MARKET_TREND},
            }
        )
    return rows


def market_trend_signals(rows: list[dict[str, Any]]) -> list[Signal]:
    signals: list[Signal] = []
    for row in rows:
        drift = float(row.get("total_line_drift") or 0.0)
        if abs(drift) < MIN_DRIFT:
            continue
        count = int(row.get("snapshot_count") or 0)
        volatility = float(row.get("total_volatility") or 0.0)
        # More snapshots raise confidence; cross-snapshot disagreement lowers it.
        confidence = _clamp(0.20 + count * 0.05, 0.20, 0.70) / (1 + volatility * 2)
        signals.append(
            Signal(
                name=TOTAL_GOALS_FACTOR,
                source=SOURCE_MARKET_TREND,
                fixture_key=str(row.get("fixture_key")),
                value=float(row.get("trend_total_goals_factor") or 1.0),
                weight=TREND_WEIGHT,
                confidence=_clamp(confidence, 0.0, 0.70),
                rationale="Totals-line drift across the stored market snapshot history.",
                metadata={
                    "total_line_drift": drift,
                    "snapshot_count": count,
                    "total_volatility": volatility,
                    "favorite_prob_drift": row.get("favorite_prob_drift"),
                },
            )
        )
    return signals


def _observed_at(row: dict[str, Any]) -> str:
    record = row.get("_record") or {}
    return str(row.get("observed_at_utc") or record.get("observed_at_utc") or "")


def _optional_float(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _clamp(value: float, lower: float, upper: float) -> float:
    return max(lower, min(upper, value))
