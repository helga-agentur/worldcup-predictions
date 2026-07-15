"""Polymarket prediction-market plugin.

Polymarket's public Gamma API (keyless, no quota) carries real-money markets
for the World Cup: a three-way moneyline per match (millions in volume by the
knockout rounds) and an outright winner event. Match prices feed the existing
``market_hda_probabilities`` signal under their own source, so the skill loop
scores Polymarket separately from bookmaker odds, and the outright prices
refresh the tournament prior that went stale when the Odds API quota died.
"""

from __future__ import annotations

import datetime as dt
import json
import urllib.error
import urllib.parse
from typing import Any

from worldcup_predictions.core.constants import SIGNAL_WEIGHT_MARKET_HDA
from worldcup_predictions.core.contracts import Diagnostic, Signal, parse_utc_datetime
from worldcup_predictions.core.datasets import MARKET_OUTRIGHTS
from worldcup_predictions.core.events import EventName, event_value
from worldcup_predictions.core.metadata import PluginKind, PluginMetadata, QuotaPolicy
from worldcup_predictions.core.plugin import BasePlugin, PluginResult
from worldcup_predictions.core.signals import MARKET_HDA_PROBABILITIES
from worldcup_predictions.plugins.source_runtime import SourceRuntime
from worldcup_predictions.storage.ledger import SourceRequest, normalize_datetime, utc_now
from worldcup_predictions.tournament import TeamResolver
from worldcup_predictions.tournament.contracts import FixtureRecord


SOURCE_POLYMARKET = "polymarket"
GAMMA_EVENTS_ENDPOINT = "https://gamma-api.polymarket.com/events"
GAMMA_SEARCH_ENDPOINT = "https://gamma-api.polymarket.com/public-search"
OUTRIGHT_EVENT_SLUG = "world-cup-winner"
OUTRIGHT_SPORT_KEY = "polymarket_worldcup_winner"

# Prediction-market prices move continuously; poll every run while matches
# are open (the API is keyless and unmetered for this volume).
MATCH_REFRESH = dt.timedelta(minutes=25)
OUTRIGHT_REFRESH = dt.timedelta(minutes=25)

# Confidence scales with traded volume: a multi-million-dollar book is a
# sharper aggregator than a thin novelty market.
HIGH_VOLUME_USD = 250_000
CONFIDENCE_HIGH_VOLUME = 0.85
CONFIDENCE_LOW_VOLUME = 0.55


class PolymarketPlugin(BasePlugin):
    """Fetch Polymarket match and outright prices as market signals."""

    id = "polymarket"
    version = "0.1.0"
    priority = 252
    subscribed_events = (EventName.FEATURE_SIGNALS_REQUESTED.value,)
    metadata = PluginMetadata(
        plugin_id=id,
        kind=PluginKind.SOURCE,
        description="Fetch Polymarket real-money match and outright prices as market probability signals.",
        datasets_written=(MARKET_OUTRIGHTS,),
        signals_emitted=(MARKET_HDA_PROBABILITIES,),
        quota_policy=QuotaPolicy(
            quota_limited=False,
            ledger_required=True,
            description="Keyless public Gamma API polled every scheduled run while fixtures are open.",
        ),
        confidence_policy="Signal confidence scales with traded volume; prices are normalized to a fair book.",
    )

    def handle(self, event, context, payload):
        runtime = SourceRuntime(self, event, context)
        if context.storage is None:
            return runtime.storage_unavailable_result("Polymarket data")

        state = runtime.tournament_state()
        diagnostics: list[Diagnostic] = []
        signals: list[Signal] = []
        now = dt.datetime.now(dt.timezone.utc)

        outright_result = self._fetch_outrights(runtime)
        diagnostics.extend(outright_result.diagnostics)
        outright_written = int(outright_result.metadata.get("written_rows") or 0)

        for fixture in state.open_fixtures():
            if not fixture.home_team.fifa_code or not fixture.away_team.fifa_code:
                continue
            kickoff = fixture.kickoff_at
            if kickoff is not None and kickoff <= now:
                continue
            result = self._fetch_match(runtime, fixture)
            diagnostics.extend(result.diagnostics)
            signal = result.metadata.get("signal")
            if signal is not None:
                signals.append(signal)

        return PluginResult(
            plugin_id=self.id,
            event=event_value(event),
            signals=signals,
            artifacts=[runtime.structured_artifact(MARKET_OUTRIGHTS, rows_written=outright_written, signals=len(signals))],
            diagnostics=diagnostics,
            metadata={"written_rows": outright_written, "signals": len(signals)},
        )

    def _fetch_outrights(self, runtime: SourceRuntime) -> PluginResult:
        request = SourceRequest(
            source=SOURCE_POLYMARKET,
            endpoint=GAMMA_EVENTS_ENDPOINT,
            purpose="worldcup_outright_winner",
            params={"slug": OUTRIGHT_EVENT_SLUG},
            quota_cost=0,
            min_refresh_interval=OUTRIGHT_REFRESH,
            quota_scope=SOURCE_POLYMARKET,
            rate_limit_backoff=dt.timedelta(hours=6),
        )
        decision = runtime.should_fetch(request)
        if not decision.should_fetch:
            return runtime.skipped_fetch_result("Polymarket outrights", decision.reason, metadata=decision.metadata)
        try:
            payload, _headers = runtime.fetch_json(GAMMA_EVENTS_ENDPOINT, {"slug": OUTRIGHT_EVENT_SLUG})
        except (OSError, TimeoutError, urllib.error.HTTPError, json.JSONDecodeError) as exc:
            runtime.record_error(request, exc)
            return runtime.result(
                diagnostics=[
                    runtime.diagnostic(
                        "warning",
                        "Polymarket outright fetch failed; stored outright rows will be used.",
                        metadata={"error": str(exc)},
                    )
                ]
            )
        rows = polymarket_outright_rows(payload)
        runtime.record_success(request, message="Fetched Polymarket outright winner prices.", metadata={"rows": len(rows)})
        count = runtime.write_records(MARKET_OUTRIGHTS, rows) if rows else 0
        return runtime.result(metadata={"written_rows": count})

    def _fetch_match(self, runtime: SourceRuntime, fixture: FixtureRecord) -> PluginResult:
        query = f"{fixture.home_team.name} {fixture.away_team.name}"
        params = {"q": query, "limit_per_type": 10, "events_status": "active"}
        request = SourceRequest(
            source=SOURCE_POLYMARKET,
            endpoint=GAMMA_SEARCH_ENDPOINT,
            purpose="match_moneyline",
            params={"q": query},
            fixture_key=fixture.key,
            quota_cost=0,
            min_refresh_interval=MATCH_REFRESH,
            quota_scope=SOURCE_POLYMARKET,
            rate_limit_backoff=dt.timedelta(hours=6),
        )
        decision = runtime.should_fetch(request)
        if not decision.should_fetch:
            return runtime.skipped_fetch_result("Polymarket match", decision.reason, fixture_key=fixture.key, metadata=decision.metadata)
        try:
            payload, _headers = runtime.fetch_json(GAMMA_SEARCH_ENDPOINT, params)
        except (OSError, TimeoutError, urllib.error.HTTPError, json.JSONDecodeError) as exc:
            runtime.record_error(request, exc)
            return runtime.result(
                diagnostics=[
                    runtime.diagnostic(
                        "warning",
                        "Polymarket match search failed; stored signals will be used.",
                        fixture_key=fixture.key,
                        metadata={"error": str(exc)},
                    )
                ]
            )
        signal = polymarket_match_signal(payload, fixture)
        runtime.record_success(
            request,
            message="Fetched Polymarket match moneyline.",
            metadata={"signal": signal is not None},
        )
        if signal is None:
            return runtime.result(
                diagnostics=[
                    runtime.diagnostic(
                        "info",
                        "No Polymarket moneyline found for fixture.",
                        fixture_key=fixture.key,
                    )
                ],
                metadata={"signal": None},
            )
        return runtime.result(metadata={"signal": signal})


def polymarket_outright_rows(payload: Any) -> list[dict[str, Any]]:
    """Normalize the outright winner event into market_outrights rows."""

    events = payload if isinstance(payload, list) else []
    if not events:
        return []
    markets = events[0].get("markets") or []
    resolver = TeamResolver.default(source=SOURCE_POLYMARKET)
    observed_at = normalize_datetime(utc_now()) or ""
    prices: dict[str, tuple[str, str | None, float]] = {}
    for market in markets:
        label = str(market.get("groupItemTitle") or market.get("question") or "").strip()
        price = _yes_price(market)
        # Zero-priced (eliminated) teams are written too: their fresh zero
        # must supersede any stale nonzero strength from a dead odds source.
        if not label or price is None or price < 0:
            continue
        team = resolver.resolve(label)
        key = team.fifa_code or team.name
        if key:
            prices[key] = (team.name, team.fifa_code, price)
    total = sum(price for _name, _code, price in prices.values())
    if total <= 0:
        return []
    # fair_probability keeps zero for eliminated teams; the strengths builder
    # treats missing/zero as "no market support" rather than inheriting stale
    # pre-elimination prices.
    rows = []
    for key, (name, code, price) in sorted(prices.items()):
        rows.append(
            {
                "record_key": f"{OUTRIGHT_SPORT_KEY}:{key}",
                "sport_key": OUTRIGHT_SPORT_KEY,
                "team": name,
                "fifa_code": code,
                "observed_at_utc": observed_at,
                "bookmaker_count": 1,
                "avg_implied_probability": price,
                "fair_probability": price / total,
                "metadata": {"source": SOURCE_POLYMARKET, "market": "outright_winner"},
            }
        )
    return rows


def polymarket_match_signal(payload: Any, fixture: FixtureRecord) -> Signal | None:
    """Build a market H/D/A signal from a public-search result, if it matches."""

    event = _matching_match_event(payload, fixture)
    if event is None:
        return None
    home = draw = away = None
    volume = 0.0
    resolver = TeamResolver.default(source=SOURCE_POLYMARKET)
    home_key = fixture.home_team.key
    away_key = fixture.away_team.key
    for market in event.get("markets") or []:
        question = str(market.get("question") or "")
        price = _yes_price(market)
        if price is None:
            continue
        volume += float(market.get("volumeNum") or 0)
        lowered = question.casefold()
        if "draw" in lowered:
            draw = price
            continue
        if not lowered.startswith("will ") or " win" not in lowered:
            continue
        subject = question[5 : lowered.index(" win")].strip()
        team = resolver.resolve(subject)
        if team.key == home_key:
            home = price
        elif team.key == away_key:
            away = price
    if home is None or draw is None or away is None:
        return None
    total = home + draw + away
    if total <= 0:
        return None
    prob_home, prob_draw, prob_away = home / total, draw / total, away / total
    confidence = CONFIDENCE_HIGH_VOLUME if volume >= HIGH_VOLUME_USD else CONFIDENCE_LOW_VOLUME
    return Signal(
        name=MARKET_HDA_PROBABILITIES,
        source=SOURCE_POLYMARKET,
        fixture_key=fixture.key,
        value=prob_home,
        weight=SIGNAL_WEIGHT_MARKET_HDA,
        confidence=confidence,
        rationale="Polymarket real-money three-way moneyline, normalized to a fair book.",
        metadata={
            "prob_home": prob_home,
            "prob_draw": prob_draw,
            "prob_away": prob_away,
            "market_volume_usd": round(volume, 0),
            "event_slug": event.get("slug"),
            "raw_prices": {"home": home, "draw": draw, "away": away},
        },
    )


def _matching_match_event(payload: Any, fixture: FixtureRecord) -> dict[str, Any] | None:
    events = (payload or {}).get("events") if isinstance(payload, dict) else None
    if not isinstance(events, list):
        return None
    resolver = TeamResolver.default(source=SOURCE_POLYMARKET)
    kickoff = parse_utc_datetime(fixture.event_date)
    for event in events:
        title = str(event.get("title") or "")
        if " vs" not in title:
            continue
        left, _, right = title.partition(" vs")
        right = right.lstrip(". ").strip()
        first = resolver.resolve(left.strip())
        second = resolver.resolve(right)
        pair = {first.key, second.key}
        if pair != {fixture.home_team.key, fixture.away_team.key}:
            continue
        end_date = parse_utc_datetime(str(event.get("endDate") or ""))
        if kickoff is not None and end_date is not None and abs(end_date - kickoff) > dt.timedelta(hours=36):
            continue
        return event
    return None


def _yes_price(market: dict[str, Any]) -> float | None:
    outcomes = market.get("outcomes")
    prices = market.get("outcomePrices")
    if isinstance(outcomes, str):
        try:
            outcomes = json.loads(outcomes)
        except ValueError:
            outcomes = None
    if isinstance(prices, str):
        try:
            prices = json.loads(prices)
        except ValueError:
            prices = None
    if not isinstance(prices, list) or not prices:
        return None
    index = 0
    if isinstance(outcomes, list):
        for position, outcome in enumerate(outcomes):
            if str(outcome).casefold() == "yes":
                index = position
                break
    try:
        return float(prices[index])
    except (TypeError, ValueError, IndexError):
        return None
