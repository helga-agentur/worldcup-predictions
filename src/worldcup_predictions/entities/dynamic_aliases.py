"""Generate deterministic alias candidates from structured project data."""

from __future__ import annotations

from typing import Any, Iterable

from worldcup_predictions.core.datasets import (
    ENTITY_ALIASES_GENERATED,
    MARKET_ODDS,
    PUBLIC_MATCH_ANALYSIS,
    SQUAD_PLAYERS,
    TOURNAMENT_FIXTURES,
)
from worldcup_predictions.entities.registry import EntityAlias, GenericEntityRegistry, load_entity_registry
from worldcup_predictions.storage.ledger import stable_hash


def build_generated_alias_rows(storage, *, run_id: str | None = None) -> list[dict[str, Any]]:
    """Write generated alias candidates and return them.

    These rows are candidates, not a replacement for the country registry. A
    caller may construct a generic registry from them, but ambiguous aliases are
    explicitly flagged and should not auto-attach data.
    """

    rows: list[dict[str, Any]] = []
    rows.extend(_player_aliases(storage.read_records(SQUAD_PLAYERS, latest_only=True)))
    rows.extend(_fixture_aliases(storage.read_records(TOURNAMENT_FIXTURES, latest_only=True)))
    rows.extend(_market_aliases(storage.read_records(MARKET_ODDS, latest_only=True)))
    rows.extend(_source_aliases(storage.read_records(PUBLIC_MATCH_ANALYSIS, latest_only=True)))
    rows = _mark_ambiguous(rows)
    storage.write_records(ENTITY_ALIASES_GENERATED, rows, source="entity_alias_generation", run_id=run_id)
    return rows


def registry_from_generated_alias_rows(rows: Iterable[dict[str, Any]], *, include_static: bool = True) -> GenericEntityRegistry:
    entities: dict[tuple[str, str], dict[str, Any]] = {}
    for row in rows:
        entity_type = str(row.get("entity_type") or "")
        canonical_id = str(row.get("canonical_id") or "")
        alias = str(row.get("alias") or "")
        if not entity_type or not canonical_id or not alias:
            continue
        target = entities.setdefault(
            (entity_type, canonical_id),
            {
                "entity_type": entity_type,
                "canonical_id": canonical_id,
                "names": {"en": str(row.get("canonical_name") or alias)},
                "aliases": {"en": []},
                "ambiguous_aliases": {"en": []},
            },
        )
        bucket = "ambiguous_aliases" if row.get("ambiguous") else "aliases"
        target[bucket].setdefault("en", []).append(alias)
    generated = [
        EntityAlias.from_dict(
            {
                **value,
                "aliases": {locale: sorted(set(aliases)) for locale, aliases in value.get("aliases", {}).items()},
                "ambiguous_aliases": {locale: sorted(set(aliases)) for locale, aliases in value.get("ambiguous_aliases", {}).items()},
            }
        )
        for value in entities.values()
    ]
    if not include_static:
        return GenericEntityRegistry(generated)
    static = load_entity_registry()
    return GenericEntityRegistry([*static.entities.values(), *generated])


def _player_aliases(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    aliases = []
    for row in rows:
        player = str(row.get("player_name") or "").strip()
        team_key = str(row.get("fifa_code") or row.get("team") or "").strip()
        if not player or not team_key:
            continue
        canonical_id = f"{team_key}:{player.casefold()}"
        aliases.append(_alias_row("player", canonical_id, player, player, source_dataset=SQUAD_PLAYERS, metadata={"team": row.get("team"), "fifa_code": row.get("fifa_code")}))
        short = _short_player_alias(player)
        if short and short != player:
            aliases.append(_alias_row("player", canonical_id, player, short, source_dataset=SQUAD_PLAYERS, metadata={"team": row.get("team"), "fifa_code": row.get("fifa_code")}))
    return aliases


def _fixture_aliases(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    aliases = []
    for row in rows:
        venue = str(row.get("venue") or "").strip()
        if venue:
            aliases.append(_alias_row("venue", venue.casefold(), venue, venue, source_dataset=TOURNAMENT_FIXTURES))
        stage = str(row.get("stage") or "").strip()
        if stage:
            aliases.append(_alias_row("stage", stage.casefold(), stage, stage, source_dataset=TOURNAMENT_FIXTURES))
        group = str(row.get("group") or "").strip()
        if group:
            aliases.append(_alias_row("group", group.casefold(), group, group, source_dataset=TOURNAMENT_FIXTURES))
    return aliases


def _market_aliases(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    aliases = []
    for row in rows:
        market = str(row.get("market") or row.get("market_key") or "").strip()
        if market:
            aliases.append(_alias_row("market", market.casefold(), market, market, source_dataset=MARKET_ODDS))
        bookmaker = str(row.get("bookmaker") or row.get("bookmaker_key") or "").strip()
        if bookmaker:
            aliases.append(_alias_row("bookmaker", bookmaker.casefold(), bookmaker, bookmaker, source_dataset=MARKET_ODDS))
    return aliases


def _source_aliases(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    aliases = []
    for row in rows:
        source = str(row.get("source_name") or "").strip()
        if source:
            aliases.append(_alias_row("publisher", source.casefold(), source, source, source_dataset=PUBLIC_MATCH_ANALYSIS))
    return aliases


def _alias_row(
    entity_type: str,
    canonical_id: str,
    canonical_name: str,
    alias: str,
    *,
    source_dataset: str,
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "record_key": stable_hash({"entity_type": entity_type, "canonical_id": canonical_id, "alias": alias, "source_dataset": source_dataset}),
        "entity_type": entity_type,
        "canonical_id": canonical_id,
        "canonical_name": canonical_name,
        "alias": alias,
        "locale": "en",
        "source_dataset": source_dataset,
        "ambiguous": False,
        "metadata": dict(metadata or {}),
    }


def _mark_ambiguous(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    ids_by_alias: dict[tuple[str, str], set[str]] = {}
    for row in rows:
        ids_by_alias.setdefault((str(row["entity_type"]), str(row["alias"]).casefold()), set()).add(str(row["canonical_id"]))
    return [
        {
            **row,
            "ambiguous": len(ids_by_alias[(str(row["entity_type"]), str(row["alias"]).casefold())]) > 1,
        }
        for row in rows
    ]


def _short_player_alias(player: str) -> str:
    parts = player.split()
    if len(parts) < 2:
        return ""
    return parts[-1]
