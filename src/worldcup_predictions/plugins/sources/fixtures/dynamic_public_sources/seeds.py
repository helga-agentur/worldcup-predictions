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

    return [
        *_core_dynamic_public_seeds(),
        *_espn_scoreboard_seeds(state),
        *trusted_public_source_seeds(),
    ]


def dynamic_public_seeds_for_run(
    state: TournamentState,
    *,
    now: dt.datetime | None = None,
) -> list[DynamicPublicSeed]:
    """Return a bounded per-run seed batch.

    Core score/fixture pages are always included. The broader curated source
    registry rotates in batches so the half-hourly cron gathers broad evidence
    without spending one run on 100+ sequential public-page requests.
    """

    return [
        *_core_dynamic_public_seeds(),
        *_espn_scoreboard_seeds(state),
        *_trusted_public_source_batch(now=now),
    ]


def trusted_public_source_seeds() -> list[DynamicPublicSeed]:
    """Return the curated public-source registry."""

    return [
        _trusted_seed(label, url, category=category)
        for label, url, category in TRUSTED_PUBLIC_SOURCE_URLS
    ]


def _core_dynamic_public_seeds() -> list[DynamicPublicSeed]:
    return [
        DynamicPublicSeed(
            url=ENDPOINT_FIFA_WORLDCUP_2026_SCORES,
            label="FIFA public scores and fixtures",
            purpose="dynamic_fifa_scores_fixtures_page",
            min_refresh=dt.timedelta(minutes=30),
            max_discovered_links=6,
            category="core",
        ),
        DynamicPublicSeed(
            url=ENDPOINT_FOTMOB_MATCH_SITEMAP,
            label="FotMob public match sitemap",
            purpose="dynamic_fotmob_match_sitemap",
            min_refresh=dt.timedelta(hours=6),
            max_discovered_links=8,
            category="core",
        ),
        DynamicPublicSeed(
            url=ENDPOINT_SOFASCORE_FOOTBALL,
            label="SofaScore public football page",
            purpose="dynamic_sofascore_football_page",
            min_refresh=dt.timedelta(hours=6),
            max_discovered_links=4,
            category="core",
        ),
        DynamicPublicSeed(
            url=ENDPOINT_TWENTY_MIN_TIPPSPIEL_DETAILS,
            label="20min public tippspiel page",
            purpose="dynamic_twenty_min_tippspiel_page",
            min_refresh=dt.timedelta(minutes=30),
            max_discovered_links=4,
            category="core",
        ),
    ]


def _espn_scoreboard_seeds(state: TournamentState) -> list[DynamicPublicSeed]:
    seeds = []
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
                category="scoreboard",
            )
        )
    return seeds


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


TRUSTED_PUBLIC_SOURCE_URLS: tuple[tuple[str, str, str], ...] = (
    ("Reuters Soccer", "https://www.reuters.com/sports/soccer/", "wire"),
    ("Associated Press Soccer", "https://apnews.com/hub/soccer", "wire"),
    ("BBC Football", "https://www.bbc.com/sport/football", "major_media"),
    ("The Guardian Football", "https://www.theguardian.com/football", "major_media"),
    ("The Athletic Football", "https://www.nytimes.com/athletic/football/", "specialist_media"),
    ("ESPN Soccer", "https://www.espn.com/soccer/", "major_media"),
    ("CBS Sports Soccer", "https://www.cbssports.com/soccer/", "major_media"),
    ("NBC Sports Soccer", "https://www.nbcsports.com/soccer", "major_media"),
    ("FOX Sports Soccer", "https://www.foxsports.com/soccer", "major_media"),
    ("Sky Sports Football", "https://www.skysports.com/football", "major_media"),
    ("FourFourTwo", "https://www.fourfourtwo.com/", "specialist_media"),
    ("GOAL", "https://www.goal.com/en", "specialist_media"),
    ("The Analyst Football", "https://theanalyst.com/eu/sport/football", "specialist_media"),
    ("StatsBomb", "https://statsbomb.com/news/", "specialist_media"),
    ("WhoScored", "https://www.whoscored.com/News", "data_app"),
    ("Transfermarkt", "https://www.transfermarkt.com/", "data_app"),
    ("Soccerway", "https://int.soccerway.com/", "data_app"),
    ("LiveScore Football", "https://www.livescore.com/en/football/", "data_app"),
    ("Flashscore Football", "https://www.flashscore.com/football/", "data_app"),
    ("BeSoccer", "https://www.besoccer.com/", "data_app"),
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
    ("CONMEBOL", "https://www.conmebol.com/en/", "official"),
    ("AFC", "https://www.the-afc.com/en/", "official"),
    ("CAF", "https://www.cafonline.com/", "official"),
    ("OFC", "https://www.oceaniafootball.com/", "official"),
    ("US Soccer", "https://www.ussoccer.com/stories", "official_federation"),
    ("Canada Soccer", "https://canadasoccer.com/news/", "official_federation"),
    ("Mexico National Team", "https://miseleccion.mx/noticias", "official_federation"),
    ("The FA England", "https://www.thefa.com/england", "official_federation"),
    ("DFB", "https://www.dfb.de/news/", "official_federation"),
    ("FFF", "https://www.fff.fr/selection/", "official_federation"),
    ("RFEF Spain", "https://rfef.es/es/selecciones", "official_federation"),
    ("FIGC Italy", "https://www.figc.it/en/national-teams/news/", "official_federation"),
    ("CBF Brazil", "https://www.cbf.com.br/selecao-brasileira/noticias", "official_federation"),
    ("AFA Argentina", "https://www.afa.com.ar/es/seleccion/mayor", "official_federation"),
    ("AUF Uruguay", "https://www.auf.org.uy/seleccion-mayor/", "official_federation"),
    ("FPF Portugal", "https://www.fpf.pt/News", "official_federation"),
    ("KNVB Netherlands", "https://www.onsoranje.nl/nieuws/nederlands-elftal-mannen", "official_federation"),
    ("RBFA Belgium", "https://www.rbfa.be/en/national-teams/red-devils", "official_federation"),
    ("Football Australia", "https://www.footballaustralia.com.au/news", "official_federation"),
    ("JFA Japan", "https://www.jfa.jp/eng/samuraiblue/news/", "official_federation"),
    ("KFA Korea", "https://www.kfa.or.kr/", "official_federation"),
    ("New Zealand Football", "https://www.nzfootball.co.nz/news", "official_federation"),
    ("SAFA South Africa", "https://www.safa.net/category/bafana-bafana/", "official_federation"),
    ("Ghana FA", "https://www.ghanafa.org/category/black-stars", "official_federation"),
    ("Nigeria FA", "https://thenff.com/category/super-eagles/", "official_federation"),
    ("Egypt FA", "https://www.efa.com.eg/en/", "official_federation"),
    ("FRMF Morocco", "https://frmf.ma/", "official_federation"),
    ("FECAFOOT Cameroon", "https://fecafoot-officiel.com/", "official_federation"),
    ("Senegal FA", "https://www.fsfoot.sn/", "official_federation"),
    ("Tunisia FTF", "https://www.ftf.org.tn/", "official_federation"),
    ("Swiss Football Association", "https://www.football.ch/sfv/nationalteams/a-team.aspx", "official_federation"),
    ("Austria OFB", "https://www.oefb.at/oefb/News", "official_federation"),
    ("Croatia HNS", "https://hns.team/en/news/", "official_federation"),
    ("Denmark DBU", "https://www.dbu.dk/nyheder/", "official_federation"),
    ("Sweden SvFF", "https://www.svenskfotboll.se/nyheter/", "official_federation"),
    ("Norway NFF", "https://www.fotball.no/landslag/", "official_federation"),
    ("Finland FA", "https://www.palloliitto.fi/ajankohtaista", "official_federation"),
    ("Poland PZPN", "https://www.pzpn.pl/reprezentacje/reprezentacja-a/aktualnosci", "official_federation"),
    ("Czech FA", "https://www.fotbal.cz/repre/", "official_federation"),
    ("Turkey TFF", "https://www.tff.org/default.aspx?pageID=471", "official_federation"),
    ("Scottish FA", "https://www.scottishfa.co.uk/news/", "official_federation"),
    ("FA Wales", "https://faw.cymru/news/", "official_federation"),
    ("FA Ireland", "https://www.fai.ie/latest/", "official_federation"),
    ("L Equipe Football", "https://www.lequipe.fr/Football/", "major_media"),
    ("RMC Sport Football", "https://rmcsport.bfmtv.com/football/", "major_media"),
    ("Eurosport Football", "https://www.eurosport.com/football/", "major_media"),
    ("Kicker", "https://www.kicker.de/fussball", "major_media"),
    ("Sport Bild Football", "https://sportbild.bild.de/fussball", "major_media"),
    ("La Gazzetta dello Sport", "https://www.gazzetta.it/Calcio/", "major_media"),
    ("Football Italia", "https://football-italia.net/", "specialist_media"),
    ("Marca Football", "https://www.marca.com/futbol.html", "major_media"),
    ("AS Football", "https://as.com/futbol/", "major_media"),
    ("Mundo Deportivo Football", "https://www.mundodeportivo.com/futbol", "major_media"),
    ("Sport ES Football", "https://www.sport.es/es/futbol/", "major_media"),
    ("A Bola Football", "https://www.abola.pt/futebol", "major_media"),
    ("Record Portugal Football", "https://www.record.pt/futebol", "major_media"),
    ("O Jogo Football", "https://www.ojogo.pt/futebol", "major_media"),
    ("Globo Esporte Football", "https://ge.globo.com/futebol/", "major_media"),
    ("UOL Esporte Football", "https://www.uol.com.br/esporte/futebol/", "major_media"),
    ("Lance Football", "https://www.lance.com.br/futebol-internacional", "major_media"),
    ("Ole Football", "https://www.ole.com.ar/futbol-internacional/", "major_media"),
    ("TyC Sports Football", "https://www.tycsports.com/futbol-internacional.html", "major_media"),
    ("Clarin Deportes Football", "https://www.clarin.com/deportes/futbol/", "major_media"),
    ("El Grafico", "https://www.elgrafico.com.ar/", "specialist_media"),
    ("Mediotiempo Football", "https://www.mediotiempo.com/futbol", "major_media"),
    ("TUDN Football", "https://www.tudn.com/futbol", "major_media"),
    ("TV Azteca Deportes Football", "https://www.tvazteca.com/aztecadeportes/futbol", "major_media"),
    ("TSN Soccer", "https://www.tsn.ca/soccer", "major_media"),
    ("Sportsnet Soccer", "https://www.sportsnet.ca/soccer/", "major_media"),
    ("CBC Soccer", "https://www.cbc.ca/sports/soccer", "major_media"),
    ("Japan Times Soccer", "https://www.japantimes.co.jp/sports/soccer/", "major_media"),
    ("Kyodo Sports", "https://english.kyodonews.net/news/sports/", "wire"),
    ("Korea Herald Sports", "https://www.koreaherald.com/Sports", "major_media"),
    ("Korea Times Sports", "https://www.koreatimes.co.kr/sports", "major_media"),
    ("Al Jazeera Football", "https://www.aljazeera.com/sports/football/", "major_media"),
    ("The National Football", "https://www.thenationalnews.com/sport/football/", "major_media"),
    ("Arab News Sport", "https://www.arabnews.com/sport", "major_media"),
    ("SuperSport Football", "https://supersport.com/football", "major_media"),
    ("News24 Soccer", "https://www.news24.com/sport/soccer", "major_media"),
    ("SRF Fussball", "https://www.srf.ch/sport/fussball", "local_media"),
    ("RTS Football", "https://www.rts.ch/sport/football/", "local_media"),
    ("RSI Calcio", "https://www.rsi.ch/sport/calcio/", "local_media"),
    ("Blick Fussball", "https://www.blick.ch/sport/fussball/", "local_media"),
    ("Watson Fussball", "https://www.watson.ch/sport/fussball", "local_media"),
    ("NZZ Sport", "https://www.nzz.ch/sport", "local_media"),
    ("Tages-Anzeiger Fussball", "https://www.tagesanzeiger.ch/sport/fussball", "local_media"),
    ("20 Minuten Fussball", "https://www.20min.ch/sport/fussball", "local_media"),
)
