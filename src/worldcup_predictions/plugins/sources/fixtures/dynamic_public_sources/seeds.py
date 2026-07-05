"""Seed pages for dynamic public-source discovery."""

from __future__ import annotations

import datetime as dt
import urllib.parse
from dataclasses import dataclass
from typing import Any

from worldcup_predictions.core.constants import (
    ENDPOINT_ESPN_SOCCER_SCOREBOARD,
    ENDPOINT_FIFA_WORLDCUP_2026_SCORES,
    ENDPOINT_FOTMOB_MATCH_SITEMAP,
    ENDPOINT_SOFASCORE_FOOTBALL,
    ENDPOINT_TWENTY_MIN_TIPPSPIEL_DETAILS,
)
from worldcup_predictions.plugins.sources.fixtures.public_score_sources.plugin import _espn_dates_to_fetch
from worldcup_predictions.tournament import TournamentState


@dataclass(frozen=True)
class DynamicPublicSeed:
    """A robots-gated public page that can lead to fixture facts."""

    url: str
    label: str
    purpose: str
    min_refresh: dt.timedelta
    max_discovered_links: int = 4


def dynamic_public_seeds(state: TournamentState) -> list[DynamicPublicSeed]:
    """Return bounded public seed pages for the current tournament state."""

    seeds = [
        DynamicPublicSeed(
            url=ENDPOINT_FIFA_WORLDCUP_2026_SCORES,
            label="FIFA public scores and fixtures",
            purpose="dynamic_fifa_scores_fixtures_page",
            min_refresh=dt.timedelta(minutes=30),
            max_discovered_links=6,
        ),
        DynamicPublicSeed(
            url=ENDPOINT_FOTMOB_MATCH_SITEMAP,
            label="FotMob public match sitemap",
            purpose="dynamic_fotmob_match_sitemap",
            min_refresh=dt.timedelta(hours=6),
            max_discovered_links=8,
        ),
        DynamicPublicSeed(
            url=ENDPOINT_SOFASCORE_FOOTBALL,
            label="SofaScore public football page",
            purpose="dynamic_sofascore_football_page",
            min_refresh=dt.timedelta(hours=6),
            max_discovered_links=4,
        ),
        DynamicPublicSeed(
            url=ENDPOINT_TWENTY_MIN_TIPPSPIEL_DETAILS,
            label="20min public tippspiel page",
            purpose="dynamic_twenty_min_tippspiel_page",
            min_refresh=dt.timedelta(minutes=30),
            max_discovered_links=4,
        ),
    ]
    for date_value in _espn_dates_to_fetch(state):
        seeds.append(
            DynamicPublicSeed(
                url=url_with_params(
                    ENDPOINT_ESPN_SOCCER_SCOREBOARD,
                    {"league": "fifa.world", "dates": date_value},
                ),
                label=f"ESPN World Cup scoreboard {date_value}",
                purpose="dynamic_espn_scoreboard",
                min_refresh=dt.timedelta(minutes=30),
                max_discovered_links=3,
            )
        )
    return seeds


def split_url_params(url: str) -> tuple[str, dict[str, Any]]:
    parsed = urllib.parse.urlparse(url)
    endpoint = urllib.parse.urlunparse((parsed.scheme, parsed.netloc, parsed.path, "", "", ""))
    return endpoint, dict(urllib.parse.parse_qsl(parsed.query, keep_blank_values=True))


def url_with_params(endpoint: str, params: dict[str, Any]) -> str:
    if not params:
        return endpoint
    return f"{endpoint}?{urllib.parse.urlencode(params)}"


def domain_from_url(url: str) -> str:
    return urllib.parse.urlparse(url).netloc.casefold().removeprefix("www.")
