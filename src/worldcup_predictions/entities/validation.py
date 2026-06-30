"""Validate stored team labels against the canonical country registry."""

from __future__ import annotations

from typing import Any

from worldcup_predictions.core.datasets import (
    ENTITY_VALIDATION,
    FOOTBALL_DATA_MATCH_DETAILS,
    FOOTBALL_DATA_TEAMS,
    LINEUP_AVAILABILITY,
    MARKET_ODDS,
    PUBLIC_MATCH_ANALYSIS,
    SQUAD_PLAYERS,
    TOURNAMENT_FIXTURES,
    TOURNAMENT_RESULTS,
    WIKIPEDIA_SQUADS,
)
from worldcup_predictions.storage.ledger import stable_hash
from worldcup_predictions.tournament import TeamResolver


TEAM_FIELD_DATASETS: dict[str, tuple[str, ...]] = {
    TOURNAMENT_FIXTURES: ("home_team", "away_team"),
    TOURNAMENT_RESULTS: ("home_team", "away_team"),
    MARKET_ODDS: ("home_team", "away_team"),
    PUBLIC_MATCH_ANALYSIS: ("home_team", "away_team"),
    LINEUP_AVAILABILITY: ("home_team", "away_team", "affected_team"),
    FOOTBALL_DATA_TEAMS: ("team",),
    FOOTBALL_DATA_MATCH_DETAILS: ("home_team", "away_team"),
    SQUAD_PLAYERS: ("team", "nationality"),
    WIKIPEDIA_SQUADS: ("team",),
}


def build_entity_validation_rows(storage, *, run_id: str | None = None) -> list[dict[str, Any]]:
    resolver = TeamResolver.default()
    rows: list[dict[str, Any]] = []
    for dataset, fields in TEAM_FIELD_DATASETS.items():
        for record in storage.read_records(dataset, latest_only=True):
            for field in fields:
                value = record.get(field)
                if not value:
                    continue
                resolved = resolver.resolve(str(value))
                status = "resolved" if resolved.fifa_code else "unresolved"
                rows.append(
                    {
                        "record_key": stable_hash({"dataset": dataset, "field": field, "value": value}),
                        "entity_type": "country",
                        "dataset": dataset,
                        "field": field,
                        "raw_value": value,
                        "status": status,
                        "canonical_name": resolved.name,
                        "fifa_code": resolved.fifa_code,
                        "metadata": {
                            "source": record.get("_record", {}).get("source"),
                            "fixture_key": record.get("fixture_key") or record.get("_record", {}).get("fixture_key"),
                        },
                    }
                )
    storage.write_records(ENTITY_VALIDATION, rows, source="entity_validation", run_id=run_id)
    return rows
