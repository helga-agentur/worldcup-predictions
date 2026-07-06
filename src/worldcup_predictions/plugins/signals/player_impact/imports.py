"""Kaggle player-value extraction helpers."""

from __future__ import annotations

import csv
import io
import zipfile
from collections import defaultdict
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any

from worldcup_predictions.core.football import clean_person_name, normalize_position, optional_float
from worldcup_predictions.storage.ledger import stable_hash


def match_transfermarkt_zip_to_squad_rows(zip_path: Path, squad_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Join Kaggle player-scores data to already-known squad rows.

    The caller downloads a selected dataset into a temporary file and deletes it
    after extracted structured facts are written.
    """

    players = _load_transfermarkt_players(zip_path)
    by_country, by_last_token = _index_players(players)
    matched = []
    for squad in squad_rows:
        team = str(squad.get("team") or "")
        player_name = str(squad.get("player_name") or "")
        if not team or not player_name:
            continue
        match, score = _match_player(squad, by_country, by_last_token, players)
        row = dict(squad)
        row["match_score"] = round(score, 3)
        if match:
            row.update(
                {
                    "transfermarkt_player_id": match["player_id"],
                    "transfermarkt_name": match["name"],
                    "transfermarkt_country": match["country"],
                    "transfermarkt_position": match["position"],
                    "current_club_name": match["current_club_name"],
                    "market_value_in_eur": match["market_value_in_eur"],
                    "valuation_date": match["valuation_date"],
                    "transfermarkt_url": match["url"],
                }
            )
        matched.append(_squad_player_row(row, team=team, player_name=player_name, source="kaggle:davidcariboo/player-scores"))
    return matched


def _squad_player_row(row: dict[str, Any], *, team: str, player_name: str, source: str) -> dict[str, Any]:
    return {
        "record_key": row.get("record_key") or stable_hash({"team": team, "player": player_name, "source": source}),
        "team": team,
        "fifa_code": row.get("fifa_code") or None,
        "player_name": player_name,
        "source_player_id": row.get("source_player_id") or row.get("transfermarkt_player_id"),
        "position": normalize_position(row.get("position") or row.get("transfermarkt_position")),
        "date_of_birth": row.get("date_of_birth"),
        "nationality": row.get("nationality") or row.get("transfermarkt_country"),
        "current_club_name": row.get("current_club_name"),
        "market_value_in_eur": optional_float(row.get("market_value_in_eur")),
        "match_score": optional_float(row.get("match_score")) or 0.0,
        "metadata": {
            "source": source,
            "transfermarkt_url": row.get("transfermarkt_url"),
            "valuation_date": row.get("valuation_date"),
        },
    }


def _read_zip_csv(zip_path: Path, filename: str):
    with zipfile.ZipFile(zip_path) as archive:
        with archive.open(filename) as handle:
            yield from csv.DictReader(io.TextIOWrapper(handle, encoding="utf-8"))


def _latest_valuations(zip_path: Path) -> dict[str, dict[str, Any]]:
    latest: dict[str, dict[str, Any]] = {}
    for row in _read_zip_csv(zip_path, "player_valuations.csv"):
        player_id = row.get("player_id")
        value = optional_float(row.get("market_value_in_eur")) or 0
        if not player_id or value <= 0:
            continue
        current = latest.get(player_id)
        if current is None or str(row.get("date") or "") > str(current.get("date") or ""):
            latest[player_id] = {
                "date": row.get("date") or "",
                "market_value_in_eur": value,
                "current_club_name": row.get("current_club_name") or "",
            }
    return latest


def _load_transfermarkt_players(zip_path: Path) -> list[dict[str, Any]]:
    valuations = _latest_valuations(zip_path)
    rows = []
    for row in _read_zip_csv(zip_path, "players.csv"):
        player_id = row.get("player_id")
        value = optional_float(row.get("market_value_in_eur")) or 0
        valuation = valuations.get(str(player_id), {})
        value = valuation.get("market_value_in_eur") or value
        if not player_id or value <= 0:
            continue
        rows.append(
            {
                "player_id": player_id,
                "name": row.get("name") or "",
                "clean_name": clean_person_name(row.get("name")),
                "country": row.get("country_of_citizenship") or "",
                "position": normalize_position(row.get("position")),
                "current_club_name": valuation.get("current_club_name") or row.get("current_club_name") or "",
                "market_value_in_eur": value,
                "valuation_date": valuation.get("date") or "",
                "url": row.get("url") or "",
            }
        )
    return rows


def _index_players(players: list[dict[str, Any]]):
    by_country = defaultdict(list)
    by_last_token = defaultdict(list)
    for player in players:
        country_key = clean_person_name(player.get("country"))
        if country_key:
            by_country[country_key].append(player)
        tokens = str(player.get("clean_name") or "").split()
        if tokens:
            by_last_token[tokens[-1]].append(player)
    return by_country, by_last_token


def _match_player(squad_player: dict[str, Any], by_country, by_last_token, all_players):
    player_name = str(squad_player.get("player_name") or "")
    country_key = clean_person_name(squad_player.get("nationality") or squad_player.get("team"))
    candidates = by_country.get(country_key, [])
    if not candidates:
        tokens = clean_person_name(player_name).split()
        candidates = by_last_token.get(tokens[-1], []) if tokens else []
    if not candidates:
        candidates = all_players
    best = None
    best_score = 0.0
    for candidate in candidates:
        score = SequenceMatcher(None, clean_person_name(player_name), candidate["clean_name"]).ratio()
        if clean_person_name(candidate.get("country")) == country_key:
            score += 0.08
        if score > best_score:
            best = candidate
            best_score = score
    if best_score < 0.78:
        return None, best_score
    return best, min(1.0, best_score)
