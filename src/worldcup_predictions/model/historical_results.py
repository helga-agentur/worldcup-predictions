"""Historical international result parsing and loading."""

from __future__ import annotations

import csv
import io
from typing import Any

from worldcup_predictions.core.contracts import ScoreTip
from worldcup_predictions.model.contracts import HistoricalResult
from worldcup_predictions.storage.contracts import StructuredStorage
from worldcup_predictions.tournament.teams import TeamResolver

HISTORICAL_RESULTS_DATASET = "historical_results"


def parse_historical_results_text(
    text: str,
    *,
    source: str = "martj42/international_results",
    resolver: TeamResolver | None = None,
) -> list[HistoricalResult]:
    resolver = resolver or TeamResolver.default(source="historical_results")
    rows: list[HistoricalResult] = []
    for row in csv.DictReader(io.StringIO(text)):
        result = _row_to_result(dict(row), source=source, resolver=resolver)
        if result is not None:
            rows.append(result)
    rows.sort(key=lambda item: (item.date, item.home_team.name, item.away_team.name))
    return rows


def parse_shootouts_text(
    text: str,
    *,
    source: str = "martj42/international_results",
    resolver: TeamResolver | None = None,
) -> list[dict[str, Any]]:
    resolver = resolver or TeamResolver.default(source="historical_shootouts")
    rows = []
    for row in csv.DictReader(io.StringIO(text)):
        parsed = _shootout_row(dict(row), source=source, resolver=resolver)
        if parsed is not None:
            rows.append(parsed)
    return rows


def load_historical_results(storage: StructuredStorage) -> list[HistoricalResult]:
    return [
        HistoricalResult.from_record(row)
        for row in storage.read_records(HISTORICAL_RESULTS_DATASET, latest_only=True)
    ]


def _row_to_result(row: dict[str, str], *, source: str, resolver: TeamResolver) -> HistoricalResult | None:
    try:
        home_score = int(row.get("home_score", ""))
        away_score = int(row.get("away_score", ""))
    except ValueError:
        return None
    date = str(row.get("date") or "").strip()
    home_team = str(row.get("home_team") or "").strip()
    away_team = str(row.get("away_team") or "").strip()
    if not date or not home_team or not away_team:
        return None
    return HistoricalResult(
        date=date[:10],
        home_team=resolver.resolve(home_team),
        away_team=resolver.resolve(away_team),
        score=ScoreTip(home_score, away_score),
        tournament=str(row.get("tournament") or "").strip() or None,
        neutral=_truthy(row.get("neutral"), default=True),
        source=source,
        metadata={
            key: value
            for key, value in row.items()
            if key not in {"date", "home_team", "away_team", "home_score", "away_score", "tournament", "neutral"}
        },
    )


def _shootout_row(row: dict[str, str], *, source: str, resolver: TeamResolver) -> dict[str, Any] | None:
    date = str(row.get("date") or "").strip()
    home_label = str(row.get("home_team") or "").strip()
    away_label = str(row.get("away_team") or "").strip()
    winner_label = str(row.get("winner") or "").strip()
    if not date or not home_label or not away_label or not winner_label:
        return None
    home = resolver.resolve(home_label)
    away = resolver.resolve(away_label)
    winner = resolver.resolve(winner_label)
    return {
        "record_key": f"{date[:10]}:{home.key}:{away.key}:shootout",
        "date": date[:10],
        "home_team": home.name,
        "away_team": away.name,
        "home_fifa_code": home.fifa_code,
        "away_fifa_code": away.fifa_code,
        "winner": winner.name,
        "winner_fifa_code": winner.fifa_code,
        "first_shooter": row.get("first_shooter"),
        "metadata": {"source": source},
    }


def _truthy(value: str | None, *, default: bool) -> bool:
    if value is None or value == "":
        return default
    return str(value).strip().casefold() in {"1", "true", "yes", "y"}
