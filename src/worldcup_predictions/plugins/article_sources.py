"""Shared article-source helpers for public news/analysis plugins."""

from __future__ import annotations

import datetime as dt
import os
import re
from functools import lru_cache
from typing import Any, Callable

from worldcup_predictions.core.constants import (
    ENDPOINT_NEWS_API_EVERYTHING,
    ENV_NEWS_API_KEY,
    NEWS_API_DEFAULT_PAGE_SIZE,
    NEWS_API_RELIABLE_DOMAINS,
)
from worldcup_predictions.core.http import HttpClient
from worldcup_predictions.core.source_reliability import assess_source_reliability
from worldcup_predictions.core.source_reliability import source_reliability_bucket
from worldcup_predictions.entities import load_country_registry, normalize_entity_text
from worldcup_predictions.entities.countries import CountryRegistry
from worldcup_predictions.plugins.source_utils import fetch_json
from worldcup_predictions.tournament.contracts import FixtureRecord, TeamRef


def fetch_news_api(
    *,
    query: str,
    page_size: int = NEWS_API_DEFAULT_PAGE_SIZE,
    domains: tuple[str, ...] | None = NEWS_API_RELIABLE_DOMAINS,
    http_client: HttpClient | None = None,
    fetcher: Callable[[str, dict[str, Any]], tuple[Any, dict[str, str]]] | None = None,
) -> tuple[list[dict[str, Any]], dict[str, str]]:
    api_key = os.environ.get(ENV_NEWS_API_KEY)
    if not api_key:
        raise OSError(f"{ENV_NEWS_API_KEY} is not configured.")
    fetch = fetcher or (http_client.get_json if http_client is not None else fetch_json)
    params = {
        "apiKey": api_key,
        "q": query,
        "language": "en",
        "searchIn": "title,description",
        "sortBy": "publishedAt",
        "pageSize": page_size,
    }
    if domains:
        params["domains"] = ",".join(domains)
    payload, headers = fetch(ENDPOINT_NEWS_API_EVERYTHING, params)
    articles = payload.get("articles") if isinstance(payload, dict) else []
    return list(articles or []), headers


def source_reliability(url: str | None, source_name: str | None = None) -> float:
    return assess_source_reliability(url, source_name).score


def article_base_row(article: dict[str, Any], fixture: FixtureRecord, *, phase: str, observed_at: str) -> dict[str, Any]:
    source = article.get("source") or {}
    url = article.get("url")
    reliability_profile = assess_source_reliability(
        url,
        source.get("name"),
        published_at=article.get("publishedAt"),
        kickoff_at=fixture.kickoff_at,
    )
    reliability = reliability_profile.score
    return {
        "fixture_key": fixture.key,
        "event_date": fixture.event_date,
        "phase": phase,
        "home_team": fixture.home_team.name,
        "away_team": fixture.away_team.name,
        "home_fifa_code": fixture.home_team.fifa_code,
        "away_fifa_code": fixture.away_team.fifa_code,
        "published_at": article.get("publishedAt"),
        "observed_at_utc": observed_at,
        "source_name": source.get("name"),
        "source_url": url,
        "title": _truncate(article.get("title"), 240),
        "description": _truncate(article.get("description"), 400),
        "reliability": reliability,
        "reliability_bucket": reliability_profile.bucket,
        "source_tier": reliability_profile.tier,
        "source_strength": reliability_profile.strength,
        "reliability_reasons": list(reliability_profile.reasons),
    }


def article_text(article: dict[str, Any]) -> str:
    return " ".join(str(article.get(name) or "") for name in ("title", "description", "content")).casefold()


def mentioned_side(text: str, fixture: FixtureRecord) -> str | None:
    home_mentions = _mentions_team(text, fixture.home_team)
    away_mentions = _mentions_team(text, fixture.away_team)
    if home_mentions and not away_mentions:
        return "home"
    if away_mentions and not home_mentions:
        return "away"
    return None


def article_mentions_fixture(article: dict[str, Any], fixture: FixtureRecord) -> bool:
    text = article_text(article)
    return _mentions_team(text, fixture.home_team) and _mentions_team(text, fixture.away_team)


def analysis_query(fixture: FixtureRecord, *, phase: str) -> str:
    stage_terms = _stage_query_terms(fixture)
    terms = (
        "preview OR prediction OR tactical OR analysis OR team news OR injuries OR suspended OR lineups OR odds"
        if phase == "pregame"
        else "report OR reaction OR analysis OR tactical OR xG OR shots OR red card OR injury OR weather"
    )
    if _is_knockout_fixture(fixture):
        if phase == "pregame":
            terms = f"{terms} OR knockout OR \"extra time\" OR penalties OR suspension OR suspended OR \"yellow cards\""
        else:
            terms = f"{terms} OR knockout OR \"extra time\" OR penalties OR shootout"
    if stage_terms:
        terms = f"{terms} OR {stage_terms}"
    home = _team_query(fixture.home_team)
    away = _team_query(fixture.away_team)
    return f"({home}) ({away}) ({terms})"


def lineup_query(fixture: FixtureRecord) -> str:
    terms = (
        "lineup OR line-up OR starting XI OR starting lineup OR team news OR injury OR injured "
        "OR suspension OR suspended OR ban OR banned OR yellow card OR cards OR doubtful OR fitness OR rotation"
    )
    if _is_knockout_fixture(fixture):
        terms = f"{terms} OR knockout OR penalties OR \"extra time\""
    return f"({_team_query(fixture.home_team)}) ({_team_query(fixture.away_team)}) ({terms})"


def extract_postmatch_stats_from_text(text: str, fixture: FixtureRecord) -> dict[str, Any]:
    """Extract conservative public stat facts from article text.

    The patterns intentionally handle only common compact forms such as
    "xG 1.8-0.7" or "shots on target 5-2". Ambiguous prose is kept as notes
    rather than guessed into numeric match stats.
    """

    normalized = str(text or "").casefold().replace("–", "-").replace("—", "-")
    stats: dict[str, Any] = {}
    for field, patterns in {
        "xg": (r"\bxg\s*(?:[:=]|was|were)?\s*(\d+(?:\.\d+)?)\s*[-:]\s*(\d+(?:\.\d+)?)",),
        "shots_on_target": (
            r"\bshots?\s+on\s+target\s*(?:[:=]|were)?\s*(\d+)\s*[-:]\s*(\d+)",
            r"\bsot\s*(?:[:=]|were)?\s*(\d+)\s*[-:]\s*(\d+)",
        ),
        "shots": (r"\bshots?\s*(?:[:=]|were)?\s*(\d+)\s*[-:]\s*(\d+)",),
        "corners": (r"\bcorners?\s*(?:[:=]|were)?\s*(\d+)\s*[-:]\s*(\d+)",),
        "possession": (r"\bpossession\s*(?:[:=]|was)?\s*(\d{1,2})\s*%?\s*[-:]\s*(\d{1,2})\s*%?",),
    }.items():
        values = _first_stat_pair(normalized, patterns)
        if values is None:
            continue
        home_value, away_value = values
        stats[f"home_{field}"] = home_value
        stats[f"away_{field}"] = away_value

    home_red, away_red = _red_card_counts(normalized, fixture)
    if home_red:
        stats["home_red_cards"] = home_red
    if away_red:
        stats["away_red_cards"] = away_red
    return stats


def classify_public_note(text: str) -> tuple[str | None, dict[str, Any]]:
    notes: dict[str, Any] = {}
    categories: list[str] = []
    for category, terms in {
        "red_card_context": ("red card", "sent off", "dismissed"),
        "weather_context": ("storm", "thunderstorm", "rain delay", "heavy rain", "weather delay"),
        "injury_context": ("injury", "injured", "limped off", "fitness"),
        "suspension_context": ("suspended", "suspension", "ban", "banned", "yellow card", "cards"),
        "lineup_context": ("lineup", "line-up", "starting xi", "rotation", "rested"),
        "finishing_context": ("wasteful", "clinical", "big chances", "missed chances"),
        "set_piece_context": ("corner", "free kick", "set piece", "penalty"),
        "extra_time_context": ("extra time", "aet", "after extra time"),
        "penalty_shootout_context": ("shootout", "penalties", "penalty shootout"),
    }.items():
        if any(term in text for term in terms):
            categories.append(category)
    if not categories:
        return None, {}
    notes["categories"] = categories
    notes["extractor"] = "public_note_keywords_v1"
    return categories[0], notes


def classify_tempo_signal(text: str) -> tuple[str | None, float | None]:
    low_terms = (
        "low scoring",
        "tight game",
        "cagey",
        "defensive",
        "under 2.5",
        "compact",
        "shutout",
        "clean sheet",
    )
    high_terms = (
        "high scoring",
        "open game",
        "attacking",
        "over 2.5",
        "end-to-end",
        "goals expected",
        "vulnerable defence",
    )
    if any(term in text for term in low_terms):
        return "low_tempo_or_defensive", 0.96
    if any(term in text for term in high_terms):
        return "high_tempo_or_attacking", 1.04
    return None, None


def stat_row_from_public_analysis(row: dict[str, Any]) -> dict[str, Any] | None:
    stats = dict(row.get("postmatch_stats") or (row.get("metadata") or {}).get("postmatch_stats") or {})
    if not stats:
        return None
    return {
        "record_key": f"{row.get('fixture_key')}:{row.get('source_url') or row.get('record_key')}:public_analysis_stats",
        "fixture_key": row.get("fixture_key"),
        "event_date": row.get("event_date"),
        "home_team": row.get("home_team"),
        "away_team": row.get("away_team"),
        "home_fifa_code": row.get("home_fifa_code"),
        "away_fifa_code": row.get("away_fifa_code"),
        "home_xg": stats.get("home_xg"),
        "away_xg": stats.get("away_xg"),
        "home_shots": stats.get("home_shots"),
        "away_shots": stats.get("away_shots"),
        "home_shots_on_target": stats.get("home_shots_on_target"),
        "away_shots_on_target": stats.get("away_shots_on_target"),
        "home_corners": stats.get("home_corners"),
        "away_corners": stats.get("away_corners"),
        "home_possession": stats.get("home_possession"),
        "away_possession": stats.get("away_possession"),
        "home_red_cards": stats.get("home_red_cards"),
        "away_red_cards": stats.get("away_red_cards"),
        "metadata": {
            "source": "public_analysis",
            "source_url": row.get("source_url"),
            "source_name": row.get("source_name"),
            "reliability": row.get("reliability"),
            "signal_type": row.get("signal_type"),
        },
    }


def article_is_pregame_for_fixture(article: dict[str, Any], fixture: FixtureRecord) -> bool:
    published = _parse_date(article.get("publishedAt"))
    kickoff = fixture.kickoff_at
    if published is None or kickoff is None:
        return True
    return published < kickoff


def _mentions_team(text: str, team: TeamRef) -> bool:
    normalized_text = normalize_entity_text(text)
    candidates = {normalize_entity_text(team.name)}
    if team.fifa_code:
        candidates.add(normalize_entity_text(team.fifa_code))
        try:
            country = _country_registry().get(team.fifa_code)
        except KeyError:
            country = None
        if country is not None:
            for name in country.names.values():
                candidates.add(normalize_entity_text(name))
            for aliases in country.aliases.values():
                candidates.update(normalize_entity_text(alias) for alias in aliases)
    return any(candidate and candidate in normalized_text for candidate in candidates)


def _team_query(team: TeamRef) -> str:
    choices = [f'"{team.name}"']
    if team.fifa_code:
        choices.append(team.fifa_code)
    return " OR ".join(choices)


def _is_knockout_fixture(fixture: FixtureRecord) -> bool:
    stage = str(fixture.stage or "").casefold()
    if fixture.group or "group" in stage or "gruppe" in stage:
        return False
    return bool(stage)


def _stage_query_terms(fixture: FixtureRecord) -> str:
    stage = str(fixture.stage or "").casefold()
    if not stage:
        return ""
    if "round of 32" in stage or "runde der 32" in stage or "last 32" in stage:
        return '"round of 32" OR knockout'
    if "round of 16" in stage or "achtel" in stage or "last 16" in stage:
        return '"round of 16" OR knockout'
    if "quarter" in stage or "viertel" in stage:
        return '"quarter-final" OR quarterfinal'
    if "semi" in stage or "halbfinal" in stage:
        return '"semi-final" OR semifinal'
    if "final" in stage:
        return "final"
    return ""


def _first_stat_pair(text: str, patterns: tuple[str, ...]) -> tuple[float, float] | None:
    for pattern in patterns:
        match = re.search(pattern, text)
        if match:
            left = float(match.group(1))
            right = float(match.group(2))
            return left, right
    return None


def _red_card_counts(text: str, fixture: FixtureRecord) -> tuple[int, int]:
    if not any(term in text for term in ("red card", "sent off", "dismissed")):
        return 0, 0
    home = 1 if _mentions_team(text, fixture.home_team) and re.search(r"(red card|sent off|dismissed)", text) else 0
    away = 1 if _mentions_team(text, fixture.away_team) and re.search(r"(red card|sent off|dismissed)", text) else 0
    if home and away:
        return 0, 0
    return home, away


@lru_cache(maxsize=1)
def _country_registry() -> CountryRegistry:
    return load_country_registry()


def _parse_date(value: Any) -> dt.datetime | None:
    if not value:
        return None
    try:
        parsed = dt.datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=dt.timezone.utc)
    return parsed.astimezone(dt.timezone.utc)


def _truncate(value: Any, limit: int) -> str:
    text = " ".join(str(value or "").split())
    return text[:limit]
