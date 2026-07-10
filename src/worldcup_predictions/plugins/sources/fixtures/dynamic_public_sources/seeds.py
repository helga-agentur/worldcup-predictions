"""Seed pages for dynamic public-source discovery."""

from __future__ import annotations

import datetime as dt
import urllib.parse
from dataclasses import dataclass
from typing import Any

from worldcup_predictions.storage.ledger import utc_now
from worldcup_predictions.tournament import TournamentState


DYNAMIC_PUBLIC_TRUSTED_SOURCE_BATCH_SIZE = 24


@dataclass(frozen=True)
class DynamicPublicSeed:
    """A robots-gated public page that can lead to fixture facts."""

    url: str
    label: str
    purpose: str
    min_refresh: dt.timedelta
    max_discovered_links: int = 4
    category: str = "core"


def dynamic_public_seeds(state: TournamentState) -> list[DynamicPublicSeed]:
    """Return all registered public seed pages for the current tournament state."""

    return trusted_public_source_seeds()


def dynamic_public_seeds_for_run(
    state: TournamentState,
    *,
    now: dt.datetime | None = None,
) -> list[DynamicPublicSeed]:
    """Return a bounded per-run seed batch.

    The curated source registry rotates in batches so the half-hourly cron
    gathers broad evidence without spending one run on many sequential
    public-page requests. Core score/fixture pages are no longer duplicated
    here; dedicated plugins fetch and parse them directly.
    """

    return _trusted_public_source_batch(now=now)


def trusted_public_source_seeds() -> list[DynamicPublicSeed]:
    """Return the curated public-source registry."""

    return [
        _trusted_seed(label, url, category=category)
        for label, url, category in TRUSTED_PUBLIC_SOURCE_URLS
    ]


def _trusted_public_source_batch(*, now: dt.datetime | None = None) -> list[DynamicPublicSeed]:
    seeds = trusted_public_source_seeds()
    if len(seeds) <= DYNAMIC_PUBLIC_TRUSTED_SOURCE_BATCH_SIZE:
        return seeds
    current = now or utc_now()
    bucket = int(current.timestamp() // (30 * 60))
    start = (bucket * DYNAMIC_PUBLIC_TRUSTED_SOURCE_BATCH_SIZE) % len(seeds)
    selected = []
    for index in range(DYNAMIC_PUBLIC_TRUSTED_SOURCE_BATCH_SIZE):
        selected.append(seeds[(start + index) % len(seeds)])
    return selected


def _trusted_seed(label: str, url: str, *, category: str) -> DynamicPublicSeed:
    return DynamicPublicSeed(
        url=url,
        label=label,
        purpose=f"dynamic_trusted_{_slug(label)}",
        min_refresh=dt.timedelta(hours=24),
        max_discovered_links=2,
        category=category,
    )


def _slug(value: str) -> str:
    return "".join(character if character.isalnum() else "_" for character in value.casefold()).strip("_")


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


# Trimmed 2026-07-10: domains with zero successful fetches all tournament
# (bot protection/paywalls) and domains that fetched fine but never yielded a
# single extracted claim or data row were removed after a live-ledger audit.
TRUSTED_PUBLIC_SOURCE_URLS: tuple[tuple[str, str, str], ...] = (
    ("Associated Press Soccer", "https://apnews.com/hub/soccer", "wire"),
    ("BBC Football", "https://www.bbc.com/sport/football", "major_media"),
    ("The Guardian Football", "https://www.theguardian.com/football", "major_media"),
    ("The Athletic Football", "https://www.nytimes.com/athletic/football/", "specialist_media"),
    ("NBC Sports Soccer", "https://www.nbcsports.com/soccer", "major_media"),
    ("FOX Sports Soccer", "https://www.foxsports.com/soccer", "major_media"),
    ("Sky Sports Football", "https://www.skysports.com/football", "major_media"),
    ("FourFourTwo", "https://www.fourfourtwo.com/", "specialist_media"),
    ("GOAL", "https://www.goal.com/en", "specialist_media"),
    ("The Analyst Football", "https://theanalyst.com/eu/sport/football", "specialist_media"),
    ("StatsBomb", "https://statsbomb.com/news/", "specialist_media"),
    ("Transfermarkt", "https://www.transfermarkt.com/", "data_app"),
    ("Soccerway", "https://int.soccerway.com/", "data_app"),
    ("LiveScore Football", "https://www.livescore.com/en/football/", "data_app"),
    ("Flashscore Football", "https://www.flashscore.com/football/", "data_app"),
    ("OneFootball", "https://onefootball.com/en/home", "specialist_media"),
    ("90min", "https://www.90min.com/", "specialist_media"),
    ("Football365", "https://www.football365.com/", "specialist_media"),
    ("World Soccer", "https://www.worldsoccer.com/", "specialist_media"),
    ("Inside World Football", "https://www.insideworldfootball.com/", "specialist_media"),
    ("Sports Mole Football", "https://www.sportsmole.co.uk/football/", "specialist_media"),
    ("Sporting News Soccer", "https://www.sportingnews.com/us/soccer", "major_media"),
    ("Yahoo Sports Soccer", "https://sports.yahoo.com/soccer/", "major_media"),
    ("Bleacher Report World Football", "https://bleacherreport.com/world-football", "major_media"),
    ("UEFA", "https://www.uefa.com/", "official"),
    ("CONCACAF", "https://www.concacaf.com/", "official"),
    ("The FA England", "https://www.thefa.com/england", "official_federation"),
    ("DFB", "https://www.dfb.de/news/", "official_federation"),
    ("FFF", "https://www.fff.fr/selection/", "official_federation"),
    ("AUF Uruguay", "https://www.auf.org.uy/seleccion-mayor/", "official_federation"),
    ("JFA Japan", "https://www.jfa.jp/eng/samuraiblue/news/", "official_federation"),
    ("SAFA South Africa", "https://www.safa.net/category/bafana-bafana/", "official_federation"),
    ("Ghana FA", "https://www.ghanafa.org/category/black-stars", "official_federation"),
    ("FECAFOOT Cameroon", "https://fecafoot-officiel.com/", "official_federation"),
    ("Senegal FA", "https://www.fsfoot.sn/", "official_federation"),
    ("Scottish FA", "https://www.scottishfa.co.uk/news/", "official_federation"),
    ("L Equipe Football", "https://www.lequipe.fr/Football/", "major_media"),
    ("RMC Sport Football", "https://rmcsport.bfmtv.com/football/", "major_media"),
    ("Eurosport Football", "https://www.eurosport.com/football/", "major_media"),
    ("La Gazzetta dello Sport", "https://www.gazzetta.it/Calcio/", "major_media"),
    ("Football Italia", "https://football-italia.net/", "specialist_media"),
    ("Marca Football", "https://www.marca.com/futbol.html", "major_media"),
    ("Mundo Deportivo Football", "https://www.mundodeportivo.com/futbol", "major_media"),
    ("Sport ES Football", "https://www.sport.es/es/futbol/", "major_media"),
    ("Record Portugal Football", "https://www.record.pt/futebol", "major_media"),
    ("Ole Football", "https://www.ole.com.ar/futbol-internacional/", "major_media"),
    ("TyC Sports Football", "https://www.tycsports.com/futbol-internacional.html", "major_media"),
    ("Clarin Deportes Football", "https://www.clarin.com/deportes/futbol/", "major_media"),
    ("El Grafico", "https://www.elgrafico.com.ar/", "specialist_media"),
    ("TUDN Football", "https://www.tudn.com/futbol", "major_media"),
    ("TSN Soccer", "https://www.tsn.ca/soccer", "major_media"),
    ("CBC Soccer", "https://www.cbc.ca/sports/soccer", "major_media"),
    ("Japan Times Soccer", "https://www.japantimes.co.jp/sports/soccer/", "major_media"),
    ("Korea Herald Sports", "https://www.koreaherald.com/Sports", "major_media"),
    ("Korea Times Sports", "https://www.koreatimes.co.kr/sports", "major_media"),
    ("The National Football", "https://www.thenationalnews.com/sport/football/", "major_media"),
    ("SuperSport Football", "https://supersport.com/football", "major_media"),
    ("News24 Soccer", "https://www.news24.com/sport/soccer", "major_media"),
    ("SRF Fussball", "https://www.srf.ch/sport/fussball", "local_media"),
    ("RTS Football", "https://www.rts.ch/sport/football/", "local_media"),
    ("RSI Calcio", "https://www.rsi.ch/sport/calcio/", "local_media"),
    ("Blick Fussball", "https://www.blick.ch/sport/fussball/", "local_media"),
    ("NZZ Sport", "https://www.nzz.ch/sport", "local_media"),
    ("20 Minuten Fussball", "https://www.20min.ch/sport/fussball", "local_media"),
)
