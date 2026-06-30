"""Postmatch xG/stat processing plugin."""

from __future__ import annotations

from typing import Any

from worldcup_predictions.core.contracts import Artifact, Diagnostic
from worldcup_predictions.core.datasets import EXTRACTION_DIAGNOSTICS
from worldcup_predictions.core.datasets import PUBLIC_MATCH_ANALYSIS as PUBLIC_ANALYSIS_DATASET
from worldcup_predictions.core.datasets import POSTMATCH_STATS as POSTMATCH_STATS_DATASET
from worldcup_predictions.core.datasets import POSTMATCH_TEAM_PERFORMANCE as POSTMATCH_TEAM_PERFORMANCE_DATASET
from worldcup_predictions.core.events import EventName, event_value
from worldcup_predictions.core.extraction import extraction_diagnostic_row
from worldcup_predictions.core.metadata import PluginKind, PluginMetadata
from worldcup_predictions.core.plugin import BasePlugin, PluginResult
from worldcup_predictions.plugins.article_sources import stat_row_from_public_analysis
from worldcup_predictions.plugins.source_utils import optional_float, optional_int


class PostmatchStatsPlugin(BasePlugin):
    """Convert postmatch stat rows into team-level performance rows."""

    id = "postmatch_stats"
    version = "0.1.0"
    priority = 300
    subscribed_events = (EventName.FEATURE_SIGNALS_REQUESTED.value,)
    metadata = PluginMetadata(
        plugin_id=id,
        kind=PluginKind.SIGNAL,
        description="Normalize postmatch xG/stats into team chance-quality performance rows.",
        datasets_read=(POSTMATCH_STATS_DATASET, PUBLIC_ANALYSIS_DATASET),
        datasets_written=(POSTMATCH_STATS_DATASET, POSTMATCH_TEAM_PERFORMANCE_DATASET, EXTRACTION_DIAGNOSTICS),
        confidence_policy="xG is preferred; shots/on-target/corners provide a conservative proxy; red-card matches are down-weighted.",
    )

    def handle(self, event, context, payload):
        if context.storage is None:
            return PluginResult(
                plugin_id=self.id,
                event=event_value(event),
                diagnostics=[Diagnostic(level="warning", message="Structured storage is unavailable; postmatch stats were skipped.", source=self.id)],
            )
        public_analysis_rows = context.storage.read_records(PUBLIC_ANALYSIS_DATASET, latest_only=True)
        derived_stat_rows, extraction_diagnostics = postmatch_stat_rows_with_diagnostics(public_analysis_rows)
        derived_count = context.storage.write_records(
            POSTMATCH_STATS_DATASET,
            derived_stat_rows,
            source=f"{self.id}:public_analysis",
            run_id=context.run_id,
        )
        diagnostic_count = context.storage.write_records(
            EXTRACTION_DIAGNOSTICS,
            extraction_diagnostics,
            source=self.id,
            run_id=context.run_id,
        )
        stat_rows = context.storage.read_records(POSTMATCH_STATS_DATASET, latest_only=True)
        performance_rows = team_performance_rows(stat_rows)
        count = context.storage.write_records(
            POSTMATCH_TEAM_PERFORMANCE_DATASET,
            performance_rows,
            source=self.id,
            run_id=context.run_id,
        )
        diagnostics = []
        if not stat_rows:
            diagnostics.append(
                Diagnostic(
                    level="info",
                    message="No postmatch stat rows are available; calibration can fall back to scores only.",
                    source=self.id,
                )
            )
        elif public_analysis_rows and not derived_stat_rows:
            diagnostics.append(
                Diagnostic(
                    level="info",
                    message="Public postmatch analysis exists, but no xG/shots/cards/possession/corners were parseable.",
                    source=self.id,
                    metadata={"public_analysis_rows": len(public_analysis_rows)},
                )
            )
        return PluginResult(
            plugin_id=self.id,
            event=event_value(event),
            artifacts=[
                Artifact(
                    name=POSTMATCH_STATS_DATASET,
                    kind="structured_dataset",
                    source=self.id,
                    data={"rows": derived_count},
                ),
                Artifact(
                    name=POSTMATCH_TEAM_PERFORMANCE_DATASET,
                    kind="structured_dataset",
                    source=self.id,
                    data={"rows": count},
                ),
                Artifact(
                    name=EXTRACTION_DIAGNOSTICS,
                    kind="structured_dataset",
                    source=self.id,
                    data={"rows": diagnostic_count},
                )
            ],
            diagnostics=diagnostics,
            metadata={
                "public_analysis_rows": len(public_analysis_rows),
                "derived_stat_rows": derived_count,
                "stat_rows": len(stat_rows),
                "performance_rows": count,
                "extraction_diagnostics": diagnostic_count,
            },
        )


def postmatch_stat_rows_with_diagnostics(rows: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    derived = []
    diagnostics = []
    for row in rows:
        if row.get("phase") != "postgame":
            continue
        stat_row = stat_row_from_public_analysis(row)
        if stat_row is None:
            diagnostics.append(
                _postmatch_stat_diagnostic(row, status="rejected", reason="no_parseable_xg_or_stat_fields")
            )
            continue
        derived.append(stat_row)
        diagnostics.append(
            _postmatch_stat_diagnostic(
                row,
                status="accepted",
                reason="stats_extracted",
                metadata={"fields": sorted(key for key, value in stat_row.items() if key.startswith(("home_", "away_")) and value not in (None, ""))},
            )
        )
    return derived, diagnostics


def team_performance_rows(stat_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows = []
    for row in stat_rows:
        fixture_key = str(row.get("fixture_key") or "")
        if not fixture_key:
            continue
        home_quality = chance_quality(row, "home")
        away_quality = chance_quality(row, "away")
        if home_quality is None and away_quality is None:
            continue
        red_cards_total = (optional_int(row.get("home_red_cards")) or 0) + (optional_int(row.get("away_red_cards")) or 0)
        match_weight = 0.50 if red_cards_total else 1.0
        rows.extend(
            [
                _team_row(row, side="home", quality=home_quality, opponent_quality=away_quality, match_weight=match_weight),
                _team_row(row, side="away", quality=away_quality, opponent_quality=home_quality, match_weight=match_weight),
            ]
        )
    return [row for row in rows if row is not None]


def chance_quality(row: dict[str, Any], side: str) -> float | None:
    xg = optional_float(row.get(f"{side}_xg"))
    if xg is not None:
        return max(0.0, min(5.5, xg))
    shots = optional_float(row.get(f"{side}_shots"))
    shots_on_target = optional_float(row.get(f"{side}_shots_on_target"))
    corners = optional_float(row.get(f"{side}_corners"))
    if shots is None and shots_on_target is None and corners is None:
        return None
    proxy = (shots or 0.0) * 0.045 + (shots_on_target or 0.0) * 0.22 + (corners or 0.0) * 0.035
    return max(0.0, min(4.5, proxy))


def _team_row(
    row: dict[str, Any],
    *,
    side: str,
    quality: float | None,
    opponent_quality: float | None,
    match_weight: float,
) -> dict[str, Any] | None:
    if quality is None:
        return None
    opponent_side = "away" if side == "home" else "home"
    return {
        "record_key": f"{row.get('fixture_key')}:{side}",
        "fixture_key": row.get("fixture_key"),
        "event_date": row.get("event_date"),
        "team": row.get(f"{side}_team"),
        "fifa_code": row.get(f"{side}_fifa_code"),
        "side": side,
        "opponent": row.get(f"{opponent_side}_team"),
        "opponent_fifa_code": row.get(f"{opponent_side}_fifa_code"),
        "goals_for": optional_int(row.get(f"{side}_score")),
        "goals_against": optional_int(row.get(f"{opponent_side}_score")),
        "chance_quality_for": quality,
        "chance_quality_against": opponent_quality,
        "red_cards_for": optional_int(row.get(f"{side}_red_cards")) or 0,
        "red_cards_against": optional_int(row.get(f"{opponent_side}_red_cards")) or 0,
        "match_weight": match_weight,
        "metadata": {
            "used_xg": optional_float(row.get(f"{side}_xg")) is not None,
            "red_card_downweighted": match_weight < 1.0,
        },
    }


def _postmatch_stat_diagnostic(
    row: dict[str, Any],
    *,
    status: str,
    reason: str,
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return extraction_diagnostic_row(
        source="postmatch_stats",
        extractor="postmatch_stats_public_analysis_v1",
        status=status,
        reason=reason,
        fixture_key=row.get("fixture_key"),
        phase=row.get("phase"),
        source_name=row.get("source_name"),
        source_url=row.get("source_url"),
        title=row.get("title"),
        metadata={
            "signal_type": row.get("signal_type"),
            "reliability": row.get("reliability"),
            **dict(metadata or {}),
        },
    )
