"""Public market-odds source plugin.

This plugin supports structured API odds only.
"""

from __future__ import annotations

import datetime as dt
import json
import urllib.error
from typing import Any, Iterable

from worldcup_predictions.core.constants import (
    ENDPOINT_THE_ODDS_API_SPORTS,
    ENDPOINT_THE_ODDS_API_WORLD_CUP_EVENTS,
    ENDPOINT_THE_ODDS_API_WORLD_CUP_ODDS,
    ENV_ENVIRONMENT,
    ENV_ODDS_API_KEY,
    ENVIRONMENT_LIVE,
    THE_ODDS_API_FINAL_RESERVE_CREDITS,
    SIGNAL_WEIGHT_MARKET_GOAL_DIFF,
    SIGNAL_WEIGHT_MARKET_HDA,
    SIGNAL_WEIGHT_MARKET_TOTAL_GOALS,
    SOURCE_MARKET_ODDS,
    SOURCE_THE_ODDS_API,
    THE_ODDS_API_EVENT_MARKET_FIXTURE_LIMIT,
    THE_ODDS_API_EVENT_MARKET_WINDOW_HOURS,
    THE_ODDS_API_EVENT_MARKETS,
    THE_ODDS_API_MARKETS,
    THE_ODDS_API_REGIONS,
    THE_ODDS_API_WORLD_CUP_SPORT,
)
from worldcup_predictions.core.contracts import Diagnostic, Signal
from worldcup_predictions.core.datasets import MARKET_ODDS as MARKET_ODDS_DATASET
from worldcup_predictions.core.datasets import MARKET_OUTRIGHTS
from worldcup_predictions.core.events import EventName, event_value
from worldcup_predictions.core.metadata import EnvVar, PluginKind, PluginMetadata, QuotaPolicy
from worldcup_predictions.core.plugin import BasePlugin, PluginResult
from worldcup_predictions.core.signals import MARKET_GOAL_DIFF, MARKET_HDA_PROBABILITIES, MARKET_TOTAL_GOALS
from worldcup_predictions.plugins.source_runtime import SourceRuntime
from worldcup_predictions.storage.ledger import SourceRequest, normalize_datetime, parse_datetime, stable_hash, utc_now
from worldcup_predictions.tournament import TeamResolver, TournamentState
from worldcup_predictions.tournament.contracts import FixtureRecord, TeamRef


class MarketOddsPlugin(BasePlugin):
    """Fetch and emit provider-neutral market signals."""

    id = "market_odds"
    version = "0.1.0"
    priority = 250
    subscribed_events = (EventName.FEATURE_SIGNALS_REQUESTED.value,)
    metadata = PluginMetadata(
        plugin_id=id,
        kind=PluginKind.SOURCE,
        description="Fetch public Odds API markets and emit provider-neutral market signals.",
        datasets_read=(MARKET_ODDS_DATASET, MARKET_OUTRIGHTS),
        datasets_written=(MARKET_ODDS_DATASET, MARKET_OUTRIGHTS),
        signals_emitted=(MARKET_HDA_PROBABILITIES, MARKET_TOTAL_GOALS, MARKET_GOAL_DIFF),
        env_vars=(EnvVar(ENV_ODDS_API_KEY, required=False, description="The Odds API key for public market prices."),),
        quota_policy=QuotaPolicy(
            quota_limited=True,
            ledger_required=True,
            description="Fetches are skipped while fresh enough or when quota floor is reached.",
        ),
        confidence_policy="Confidence rises with bookmaker count and is capped before blending into the score matrix.",
    )

    def handle(self, event, context, payload):
        runtime = SourceRuntime(self, event, context)
        if context.storage is None:
            return runtime.storage_unavailable_result("market odds")

        state = runtime.tournament_state()

        diagnostics: list[Diagnostic] = []
        written = 0
        outright_written = 0
        fetch_result = self._maybe_fetch(runtime, state)
        diagnostics.extend(fetch_result.diagnostics)
        written += int(fetch_result.metadata.get("written_rows") or 0)
        outright_result = self._maybe_fetch_outrights(runtime, state)
        diagnostics.extend(outright_result.diagnostics)
        outright_written += int(outright_result.metadata.get("written_rows") or 0)

        rows = runtime.read_latest(MARKET_ODDS_DATASET)
        signals = market_signals_from_rows(rows)
        return PluginResult(
            plugin_id=self.id,
            event=event_value(event),
            signals=signals,
            artifacts=[
                runtime.structured_artifact(MARKET_ODDS_DATASET, rows_written=written, signals=len(signals)),
                runtime.structured_artifact(MARKET_OUTRIGHTS, rows_written=outright_written),
            ],
            diagnostics=diagnostics,
            metadata={"signals": len(signals), "written_rows": written, "outright_rows": outright_written},
        )

    def _live_api_key(self, runtime: SourceRuntime) -> tuple[str | None, Diagnostic | None]:
        """API key when fetching is allowed: configured key + live environment.

        Local and CI runs share the same monthly credit pool as the live
        server, so only the live scheduler may spend it; everything else
        reads the stored odds the live runs already synced.
        """

        api_key = runtime.env_value(ENV_ODDS_API_KEY)
        if not api_key:
            return None, runtime.diagnostic(
                "info",
                f"{ENV_ODDS_API_KEY} is not configured; using stored market odds only.",
            )
        environment = str(runtime.env_value(ENV_ENVIRONMENT) or "").strip().casefold()
        if environment != ENVIRONMENT_LIVE:
            return None, runtime.diagnostic(
                "info",
                f"Odds API fetches only run where {ENV_ENVIRONMENT}={ENVIRONMENT_LIVE}; "
                "using stored market odds only.",
            )
        return api_key, None

    def _quota_floor(self, runtime: SourceRuntime, open_fixtures: list[FixtureRecord]) -> int:
        base_floor = int(runtime.context.config.source_defaults.odds_quota_remaining_floor)
        return quota_floor_for_fixtures(open_fixtures, base_floor)

    def _maybe_fetch(self, runtime: SourceRuntime, state: TournamentState) -> PluginResult:
        open_fixtures = state.open_fixtures()
        if not open_fixtures:
            return runtime.result(
                diagnostics=[
                    runtime.diagnostic("info", "No open fixtures are available; market odds fetch was skipped.")
                ]
            )
        api_key, gate_diagnostic = self._live_api_key(runtime)
        if not api_key:
            return runtime.result(diagnostics=[gate_diagnostic] if gate_diagnostic else [])

        request = SourceRequest(
            source=SOURCE_THE_ODDS_API,
            endpoint=ENDPOINT_THE_ODDS_API_WORLD_CUP_ODDS,
            purpose="upcoming_match_market_odds",
            params={
                "sport": THE_ODDS_API_WORLD_CUP_SPORT,
                "regions": THE_ODDS_API_REGIONS,
                "markets": THE_ODDS_API_MARKETS,
                "odds_format": "decimal",
            },
            quota_cost=_credit_cost(THE_ODDS_API_MARKETS, THE_ODDS_API_REGIONS),
            min_refresh_interval=main_odds_refresh_interval(open_fixtures),
            quota_remaining_floor=self._quota_floor(runtime, open_fixtures),
            quota_scope=SOURCE_THE_ODDS_API,
            rate_limit_backoff=dt.timedelta(hours=6),
        )
        decision = runtime.should_fetch(request)
        if not decision.should_fetch:
            return runtime.skipped_fetch_result("Market odds", decision.reason, metadata=decision.metadata)

        try:
            payload, headers = runtime.fetch_json(
                ENDPOINT_THE_ODDS_API_WORLD_CUP_ODDS,
                {
                    "apiKey": api_key,
                    "regions": THE_ODDS_API_REGIONS,
                    "markets": THE_ODDS_API_MARKETS,
                    "oddsFormat": "decimal",
                    "dateFormat": "iso",
                },
            )
        except urllib.error.HTTPError as exc:
            message = exc.read().decode("utf-8", errors="replace")[:500]
            runtime.record_error(
                request,
                exc,
                quota_remaining=_optional_int(exc.headers.get("x-requests-remaining")),
                metadata={"reason": exc.reason, "response_body": message},
            )
            return runtime.result(
                diagnostics=[
                    runtime.diagnostic(
                        level="warning",
                        message=f"Market odds fetch failed with HTTP {exc.code}; stored odds will be used.",
                    )
                ],
            )
        except (TimeoutError, OSError, json.JSONDecodeError) as exc:
            runtime.record_error(request, exc)
            return runtime.result(
                diagnostics=[
                    runtime.diagnostic(
                        level="warning",
                        message="Market odds fetch failed; stored odds will be used.",
                        metadata={"error": str(exc)},
                    )
                ],
            )

        odds_payload = payload if isinstance(payload, list) else []
        rows = odds_api_rows(odds_payload, open_fixtures)
        event_result = self._maybe_fetch_event_markets(runtime, open_fixtures, odds_payload)
        diagnostics = list(event_result.diagnostics)
        rows.extend(event_result.metadata.get("rows") or [])
        runtime.record_success(
            request,
            quota_remaining=_optional_int(headers.get("x-requests-remaining")),
            message="Fetched odds from The Odds API.",
            metadata={"requests_used": _optional_int(headers.get("x-requests-used")), "events": len(odds_payload), "rows": len(rows)},
        )
        count = runtime.write_records(MARKET_ODDS_DATASET, rows)
        return runtime.result(diagnostics=diagnostics, metadata={"written_rows": count})

    def _maybe_fetch_event_markets(
        self,
        runtime: SourceRuntime,
        open_fixtures: list[FixtureRecord],
        odds_payload: list[dict[str, Any]],
    ) -> PluginResult:
        if not THE_ODDS_API_EVENT_MARKETS:
            return runtime.result(metadata={"rows": []})
        api_key, _gate = self._live_api_key(runtime)
        if not api_key:
            return runtime.result(metadata={"rows": []})
        near_term_fixtures = _near_term_fixtures(open_fixtures)
        if not near_term_fixtures:
            return runtime.result(metadata={"rows": []})

        event_ids = odds_api_event_ids(odds_payload, near_term_fixtures)
        diagnostics: list[Diagnostic] = []
        if len(event_ids) < len(near_term_fixtures):
            events_result = self._maybe_fetch_events(runtime)
            diagnostics.extend(events_result.diagnostics)
            event_ids.update(events_result.metadata.get("event_ids") or {})

        rows = []
        for fixture in near_term_fixtures:
            event_id = event_ids.get(fixture.key)
            if not event_id:
                diagnostics.append(
                    runtime.diagnostic(
                        "info",
                        "Odds API event-market fetch skipped because no event id was available.",
                        fixture_key=fixture.key,
                    )
                )
                continue
            endpoint = f"{ENDPOINT_THE_ODDS_API_WORLD_CUP_EVENTS}/{event_id}/odds"
            request = SourceRequest(
                source=SOURCE_THE_ODDS_API,
                endpoint=endpoint,
                purpose="near_term_event_markets",
                fixture_key=fixture.key,
                params={
                    "sport": THE_ODDS_API_WORLD_CUP_SPORT,
                    "event_id": event_id,
                    "regions": THE_ODDS_API_REGIONS,
                    "markets": THE_ODDS_API_EVENT_MARKETS,
                    "odds_format": "decimal",
                },
                quota_cost=_credit_cost(THE_ODDS_API_EVENT_MARKETS, THE_ODDS_API_REGIONS),
                min_refresh_interval=dt.timedelta(minutes=30),
                quota_remaining_floor=self._quota_floor(runtime, open_fixtures),
                quota_scope=SOURCE_THE_ODDS_API,
                rate_limit_backoff=dt.timedelta(hours=6),
            )
            decision = runtime.should_fetch(request)
            if not decision.should_fetch:
                diagnostics.extend(
                    runtime.skipped_fetch_result(
                        "Odds API event markets",
                        decision.reason,
                        fixture_key=fixture.key,
                        metadata=decision.metadata,
                    ).diagnostics
                )
                continue
            try:
                payload, headers = runtime.fetch_json(
                    endpoint,
                    {
                        "apiKey": api_key,
                        "regions": THE_ODDS_API_REGIONS,
                        "markets": THE_ODDS_API_EVENT_MARKETS,
                        "oddsFormat": "decimal",
                        "dateFormat": "iso",
                    },
                )
            except urllib.error.HTTPError as exc:
                message = exc.read().decode("utf-8", errors="replace")[:500]
                runtime.record_error(
                    request,
                    exc,
                    quota_remaining=_optional_int(exc.headers.get("x-requests-remaining")),
                    metadata={"reason": exc.reason, "response_body": message},
                )
                diagnostics.append(
                    runtime.diagnostic(
                        "warning",
                        f"Odds API event-market fetch failed with HTTP {exc.code}.",
                        fixture_key=fixture.key,
                    )
                )
                continue
            except (OSError, TimeoutError, json.JSONDecodeError) as exc:
                runtime.record_error(request, exc)
                diagnostics.append(
                    runtime.diagnostic(
                        "warning",
                        "Odds API event-market fetch failed.",
                        fixture_key=fixture.key,
                        metadata={"error": str(exc)},
                    )
                )
                continue
            row = _event_market_row(payload if isinstance(payload, dict) else {}, fixture, fixture.home_team, fixture.away_team, normalize_datetime(utc_now()) or "")
            runtime.record_success(
                request,
                quota_remaining=_optional_int(headers.get("x-requests-remaining")),
                message="Fetched Odds API near-term event markets.",
                metadata={"event_id": event_id, "row": bool(row)},
            )
            if row is not None:
                row["record_key"] = f"{fixture.key}:odds_api_event:{event_id}"
                row["metadata"] = {
                    **dict(row.get("metadata") or {}),
                    "source": SOURCE_THE_ODDS_API,
                    "markets": THE_ODDS_API_EVENT_MARKETS,
                    "event_market_fetch": True,
                }
                rows.append(row)
        return runtime.result(diagnostics=diagnostics, metadata={"rows": rows})

    def _maybe_fetch_events(self, runtime: SourceRuntime) -> PluginResult:
        api_key, _gate = self._live_api_key(runtime)
        if not api_key:
            return runtime.result(metadata={"event_ids": {}})
        request = SourceRequest(
            source=SOURCE_THE_ODDS_API,
            endpoint=ENDPOINT_THE_ODDS_API_WORLD_CUP_EVENTS,
            purpose="world_cup_event_discovery",
            params={"sport": THE_ODDS_API_WORLD_CUP_SPORT},
            quota_cost=1,
            min_refresh_interval=dt.timedelta(hours=6),
            quota_remaining_floor=self._quota_floor(runtime, runtime.tournament_state().open_fixtures()),
            quota_scope=SOURCE_THE_ODDS_API,
            rate_limit_backoff=dt.timedelta(hours=6),
        )
        decision = runtime.should_fetch(request)
        if not decision.should_fetch:
            return runtime.skipped_fetch_result("Odds API event discovery", decision.reason, metadata=decision.metadata)
        try:
            payload, headers = runtime.fetch_json(ENDPOINT_THE_ODDS_API_WORLD_CUP_EVENTS, {"apiKey": api_key, "dateFormat": "iso"})
        except (OSError, TimeoutError, urllib.error.HTTPError, json.JSONDecodeError) as exc:
            runtime.record_error(request, exc)
            return runtime.result(diagnostics=[runtime.diagnostic("warning", "Odds API event discovery failed.", metadata={"error": str(exc)})])
        state = runtime.tournament_state()
        event_ids = odds_api_event_ids(payload if isinstance(payload, list) else [], state.open_fixtures())
        runtime.record_success(
            request,
            quota_remaining=_optional_int(headers.get("x-requests-remaining")),
            message="Fetched Odds API event discovery.",
            metadata={"events": len(payload) if isinstance(payload, list) else 0, "matched_events": len(event_ids)},
        )
        return runtime.result(metadata={"event_ids": event_ids})

    def _maybe_fetch_outrights(self, runtime: SourceRuntime, state: TournamentState) -> PluginResult:
        api_key, _gate = self._live_api_key(runtime)
        if not api_key:
            return runtime.result()
        quota_floor = self._quota_floor(runtime, state.open_fixtures())
        sports_request = SourceRequest(
            source=SOURCE_THE_ODDS_API,
            endpoint=ENDPOINT_THE_ODDS_API_SPORTS,
            purpose="world_cup_sports_discovery",
            params={},
            quota_cost=1,
            min_refresh_interval=dt.timedelta(hours=12),
            quota_remaining_floor=quota_floor,
            quota_scope=SOURCE_THE_ODDS_API,
            rate_limit_backoff=dt.timedelta(hours=6),
        )
        decision = runtime.should_fetch(sports_request)
        diagnostics: list[Diagnostic] = []
        if not decision.should_fetch:
            diagnostics.extend(runtime.skipped_fetch_result("Odds API sports discovery", decision.reason, metadata=decision.metadata).diagnostics)
            sports_rows = runtime.read_latest(MARKET_OUTRIGHTS)
            return runtime.result(diagnostics=diagnostics, metadata={"written_rows": 0, "stored_outrights": len(sports_rows)})
        try:
            sports_payload, sports_headers = runtime.fetch_json(ENDPOINT_THE_ODDS_API_SPORTS, {"apiKey": api_key})
        except (OSError, TimeoutError, urllib.error.HTTPError, json.JSONDecodeError) as exc:
            runtime.record_error(sports_request, exc)
            return runtime.result(diagnostics=[runtime.diagnostic("warning", "Odds API sports discovery failed.", metadata={"error": str(exc)})])
        runtime.record_success(
            sports_request,
            message="Fetched Odds API sports discovery.",
            quota_remaining=_optional_int(sports_headers.get("x-requests-remaining")),
            metadata={"sports": len(sports_payload) if isinstance(sports_payload, list) else 0},
        )
        sports = sports_payload if isinstance(sports_payload, list) else []
        outright_sports = [
            str(item.get("key"))
            for item in sports
            if item.get("key")
            and "world cup" in str(item.get("title") or item.get("description") or "").casefold()
            and ("winner" in str(item.get("description") or item.get("key") or "").casefold() or "outright" in str(item.get("description") or "").casefold())
        ]
        written = 0
        for sport_key in sorted(set(outright_sports)):
            endpoint = f"https://api.the-odds-api.com/v4/sports/{sport_key}/odds"
            request = SourceRequest(
                source=SOURCE_THE_ODDS_API,
                endpoint=endpoint,
                purpose="world_cup_outrights",
                params={"sport": sport_key, "regions": THE_ODDS_API_REGIONS, "markets": "outrights"},
                quota_cost=_credit_cost("outrights", THE_ODDS_API_REGIONS),
                min_refresh_interval=dt.timedelta(hours=6),
                quota_remaining_floor=quota_floor,
                quota_scope=SOURCE_THE_ODDS_API,
                rate_limit_backoff=dt.timedelta(hours=6),
            )
            outright_decision = runtime.should_fetch(request)
            if not outright_decision.should_fetch:
                diagnostics.extend(runtime.skipped_fetch_result("Odds API outrights", outright_decision.reason, metadata=outright_decision.metadata).diagnostics)
                continue
            try:
                payload, headers = runtime.fetch_json(
                    endpoint,
                    {
                        "apiKey": api_key,
                        "regions": THE_ODDS_API_REGIONS,
                        "markets": "outrights",
                        "oddsFormat": "decimal",
                        "dateFormat": "iso",
                    },
                )
            except (OSError, TimeoutError, urllib.error.HTTPError, json.JSONDecodeError) as exc:
                runtime.record_error(request, exc)
                diagnostics.append(runtime.diagnostic("warning", "Odds API outright fetch failed.", metadata={"sport": sport_key, "error": str(exc)}))
                continue
            rows = outright_rows(payload if isinstance(payload, list) else [], sport_key)
            count = runtime.write_records(MARKET_OUTRIGHTS, rows)
            runtime.record_success(
                request,
                message="Fetched Odds API outrights.",
                quota_remaining=_optional_int(headers.get("x-requests-remaining")),
                metadata={"sport": sport_key, "rows": count},
            )
            written += count
        return runtime.result(diagnostics=diagnostics, metadata={"written_rows": written})


def odds_api_rows(payload: Iterable[dict[str, Any]], fixtures: list[FixtureRecord]) -> list[dict[str, Any]]:
    resolver = TeamResolver.default(source=SOURCE_THE_ODDS_API)
    fixture_by_pair = {
        (fixture.home_team.key, fixture.away_team.key): fixture
        for fixture in fixtures
    }
    rows = []
    observed_at = normalize_datetime(utc_now()) or ""
    for event in payload:
        home_team = resolver.resolve(str(event.get("home_team") or ""))
        away_team = resolver.resolve(str(event.get("away_team") or ""))
        fixture = fixture_by_pair.get((home_team.key, away_team.key))
        if fixture is None:
            continue
        row = _event_market_row(event, fixture, home_team, away_team, observed_at)
        if row is not None:
            rows.append(row)
    return rows


def odds_api_event_ids(payload: Iterable[dict[str, Any]], fixtures: list[FixtureRecord]) -> dict[str, str]:
    resolver = TeamResolver.default(source=SOURCE_THE_ODDS_API)
    fixture_by_pair = {
        (fixture.home_team.key, fixture.away_team.key): fixture
        for fixture in fixtures
    }
    event_ids: dict[str, str] = {}
    for event in payload:
        home_team = resolver.resolve(str(event.get("home_team") or ""))
        away_team = resolver.resolve(str(event.get("away_team") or ""))
        fixture = fixture_by_pair.get((home_team.key, away_team.key))
        event_id = str(event.get("id") or "")
        if fixture is not None and event_id:
            event_ids[fixture.key] = event_id
    return event_ids


def market_signals_from_rows(rows: list[dict[str, Any]]) -> list[Signal]:
    latest_by_fixture_source: dict[tuple[str, str], dict[str, Any]] = {}
    for row in rows:
        fixture_key = str(row.get("fixture_key") or "")
        if not fixture_key:
            continue
        source_key = _market_row_source_key(row)
        current = latest_by_fixture_source.get((fixture_key, source_key))
        observed_at = _observed_at(row)
        current_observed_at = ""
        if current:
            current_observed_at = _observed_at(current)
        if current is None or observed_at >= current_observed_at:
            latest_by_fixture_source[(fixture_key, source_key)] = row

    rows_by_fixture: dict[str, list[dict[str, Any]]] = {}
    for (fixture_key, _source_key), row in latest_by_fixture_source.items():
        rows_by_fixture.setdefault(fixture_key, []).append(row)

    signals: list[Signal] = []
    for fixture_key, fixture_rows in rows_by_fixture.items():
        h2h_rows = [
            row
            for row in fixture_rows
            if int(row.get("h2h_bookmaker_count") or 0)
            and all(row.get(name) is not None for name in ("prob_home", "prob_draw", "prob_away"))
        ]
        totals_rows = [row for row in fixture_rows if int(row.get("totals_bookmaker_count") or 0) and row.get("total_goals") is not None]
        spreads_rows = [row for row in fixture_rows if int(row.get("spreads_bookmaker_count") or 0) and row.get("goal_diff") is not None]
        h2h_count = sum(int(row.get("h2h_bookmaker_count") or 0) for row in h2h_rows)
        totals_count = sum(int(row.get("totals_bookmaker_count") or 0) for row in totals_rows)
        spreads_count = sum(int(row.get("spreads_bookmaker_count") or 0) for row in spreads_rows)
        sources = _market_sources(fixture_rows)
        if h2h_count:
            confidence = _market_confidence(h2h_count)
            prob_home = _weighted_average(h2h_rows, "prob_home", "h2h_bookmaker_count")
            prob_draw = _weighted_average(h2h_rows, "prob_draw", "h2h_bookmaker_count")
            prob_away = _weighted_average(h2h_rows, "prob_away", "h2h_bookmaker_count")
            if prob_home is None or prob_draw is None or prob_away is None:
                continue
            total_probability = prob_home + prob_draw + prob_away
            if total_probability > 0:
                prob_home /= total_probability
                prob_draw /= total_probability
                prob_away /= total_probability
            signals.append(
                Signal(
                    name="market_hda_probabilities",
                    source=SOURCE_MARKET_ODDS,
                    fixture_key=fixture_key,
                    value=prob_home,
                    weight=SIGNAL_WEIGHT_MARKET_HDA,
                    confidence=confidence,
                    rationale=f"No-vig market H/D/A probabilities from {h2h_count} bookmaker sample(s).",
                    metadata={
                        "prob_home": prob_home,
                        "prob_draw": prob_draw,
                        "prob_away": prob_away,
                        "bookmaker_count": h2h_count,
                        "market_margin": _weighted_average(h2h_rows, "h2h_margin", "h2h_bookmaker_count"),
                        "sources": sources,
                    },
                )
            )
        if totals_count:
            total_goals = _weighted_average(totals_rows, "total_goals", "totals_bookmaker_count")
            if total_goals is None:
                continue
            over_probability = _weighted_average(totals_rows, "total_over_probability", "totals_bookmaker_count")
            btts_yes_probability = _weighted_average(totals_rows, "btts_yes_probability", "btts_bookmaker_count")
            target_total = total_goals
            if over_probability is not None:
                # Shift the posted line toward the side the market leans: in an efficient
                # market the line sits where over/under is ~50/50, so a lean above 0.5
                # implies a higher expected total.
                target_total = total_goals + (over_probability - 0.5) * 0.8
            if btts_yes_probability is not None:
                target_total += (btts_yes_probability - 0.5) * 0.35
            signals.append(
                Signal(
                    name="market_total_goals",
                    source=SOURCE_MARKET_ODDS,
                    fixture_key=fixture_key,
                    value=target_total,
                    weight=SIGNAL_WEIGHT_MARKET_TOTAL_GOALS,
                    confidence=_market_confidence(totals_count),
                    rationale="Public totals market goal line, shifted by the over/under lean.",
                    metadata={
                        "bookmaker_count": totals_count,
                        "total_line": total_goals,
                        "total_over_probability": over_probability,
                        "btts_yes_probability": btts_yes_probability,
                        "sources": sources,
                    },
                )
            )
        if spreads_count:
            goal_diff = _weighted_average(spreads_rows, "goal_diff", "spreads_bookmaker_count")
            if goal_diff is None:
                continue
            signals.append(
                Signal(
                    name="market_goal_diff",
                    source=SOURCE_MARKET_ODDS,
                    fixture_key=fixture_key,
                    value=goal_diff,
                    weight=SIGNAL_WEIGHT_MARKET_GOAL_DIFF,
                    confidence=_market_confidence(spreads_count),
                    rationale="Public spread market goal-difference line.",
                    metadata={"bookmaker_count": spreads_count, "sources": sources},
                )
            )
    return signals


def outright_rows(payload: list[dict[str, Any]], sport_key: str) -> list[dict[str, Any]]:
    resolver = TeamResolver.default(source=SOURCE_THE_ODDS_API)
    observed_at = normalize_datetime(utc_now()) or ""
    samples: dict[str, list[float]] = {}
    bookmaker_counts: dict[str, int] = {}
    for event in payload:
        for bookmaker in event.get("bookmakers") or []:
            for market in bookmaker.get("markets") or []:
                if str(market.get("key") or "") != "outrights":
                    continue
                for outcome in market.get("outcomes") or []:
                    price = _optional_float(outcome.get("price"))
                    if not price or price <= 1:
                        continue
                    team = resolver.resolve(str(outcome.get("name") or ""))
                    key = team.fifa_code or team.name
                    if not key:
                        continue
                    samples.setdefault(key, []).append(1 / price)
                    bookmaker_counts[key] = bookmaker_counts.get(key, 0) + 1
    total = sum(sum(values) / len(values) for values in samples.values())
    rows = []
    for key, values in sorted(samples.items()):
        implied = sum(values) / len(values)
        fair_probability = implied / total if total > 0 else None
        team = resolver.resolve(key)
        rows.append(
            {
                "record_key": f"{sport_key}:{key}",
                "sport_key": sport_key,
                "team": team.name,
                "fifa_code": team.fifa_code,
                "observed_at_utc": observed_at,
                "bookmaker_count": bookmaker_counts.get(key, 0),
                "avg_implied_probability": implied,
                "fair_probability": fair_probability,
                "metadata": {"source": SOURCE_THE_ODDS_API, "market": "outrights"},
            }
        )
    return rows


def _event_market_row(
    event: dict[str, Any],
    fixture: FixtureRecord,
    home_team: TeamRef,
    away_team: TeamRef,
    observed_at: str,
) -> dict[str, Any] | None:
    h2h_samples: list[tuple[float, float, float, float]] = []
    total_samples: list[tuple[float, float | None]] = []
    spread_lines: list[float] = []
    dnb_samples: list[tuple[float, float, float]] = []
    btts_samples: list[tuple[float, float]] = []
    team_total_home_lines: list[float] = []
    team_total_away_lines: list[float] = []
    alternate_total_samples: list[tuple[float, float | None]] = []
    alternate_spread_lines: list[float] = []
    bookmakers = event.get("bookmakers") or []
    for bookmaker in bookmakers:
        markets = {str(market.get("key") or ""): market for market in bookmaker.get("markets") or []}
        h2h = _h2h_sample(markets.get("h2h"), home_team, away_team)
        if h2h:
            h2h_samples.append(h2h)
        total = _total_sample(markets.get("totals"))
        if total is not None:
            total_samples.append(total)
        spread = _spread_line(markets.get("spreads"), home_team, away_team)
        if spread is not None:
            spread_lines.append(spread)
        dnb = _draw_no_bet_sample(markets.get("draw_no_bet"), home_team, away_team)
        if dnb is not None:
            dnb_samples.append(dnb)
        btts = _btts_sample(markets.get("btts"))
        if btts is not None:
            btts_samples.append(btts)
        team_total_home, team_total_away = _team_totals_sample(markets.get("team_totals"), home_team, away_team)
        if team_total_home is not None:
            team_total_home_lines.append(team_total_home)
        if team_total_away is not None:
            team_total_away_lines.append(team_total_away)
        alternate_total = _total_sample(markets.get("alternate_totals"))
        if alternate_total is not None:
            alternate_total_samples.append(alternate_total)
        alternate_spread = _spread_line(markets.get("alternate_spreads"), home_team, away_team)
        if alternate_spread is not None:
            alternate_spread_lines.append(alternate_spread)

    all_total_samples = total_samples + alternate_total_samples
    total_lines = [line for line, _ in all_total_samples]
    if not total_lines and team_total_home_lines and team_total_away_lines:
        total_lines = [
            (sum(team_total_home_lines) / len(team_total_home_lines))
            + (sum(team_total_away_lines) / len(team_total_away_lines))
        ]
    over_probabilities = [prob for _, prob in all_total_samples if prob is not None]
    all_spread_lines = spread_lines + alternate_spread_lines
    btts_yes_probabilities = [yes for yes, _margin in btts_samples]
    dnb_avg = _average_tuple(dnb_samples)
    if not h2h_samples and not total_lines and not all_spread_lines and not dnb_samples and not btts_samples:
        return None
    event_id = str(event.get("id") or stable_hash({"event": event, "fixture": fixture.key}))
    h2h_avg = _average_tuple(h2h_samples)
    row = {
        "record_key": f"{fixture.key}:odds_api:{event_id}",
        "fixture_key": fixture.key,
        "event_id": event_id,
        "event_date": fixture.event_date,
        "commence_time": event.get("commence_time"),
        "home_team": fixture.home_team.name,
        "away_team": fixture.away_team.name,
        "home_fifa_code": fixture.home_team.fifa_code,
        "away_fifa_code": fixture.away_team.fifa_code,
        "observed_at_utc": observed_at,
        "bookmaker_count": len(bookmakers),
        "h2h_bookmaker_count": len(h2h_samples),
        "totals_bookmaker_count": len(total_lines),
        "spreads_bookmaker_count": len(all_spread_lines),
        "draw_no_bet_bookmaker_count": len(dnb_samples),
        "btts_bookmaker_count": len(btts_samples),
        "team_totals_bookmaker_count": max(len(team_total_home_lines), len(team_total_away_lines)),
        "alternate_totals_bookmaker_count": len(alternate_total_samples),
        "alternate_spreads_bookmaker_count": len(alternate_spread_lines),
        "prob_home": h2h_avg[0] if h2h_avg else None,
        "prob_draw": h2h_avg[1] if h2h_avg else None,
        "prob_away": h2h_avg[2] if h2h_avg else None,
        "h2h_margin": h2h_avg[3] if h2h_avg else None,
        "draw_no_bet_home_probability": dnb_avg[0] if dnb_avg else None,
        "draw_no_bet_away_probability": dnb_avg[1] if dnb_avg else None,
        "draw_no_bet_margin": dnb_avg[2] if dnb_avg else None,
        "btts_yes_probability": sum(btts_yes_probabilities) / len(btts_yes_probabilities) if btts_yes_probabilities else None,
        "btts_margin": sum(margin for _yes, margin in btts_samples) / len(btts_samples) if btts_samples else None,
        "team_total_home": sum(team_total_home_lines) / len(team_total_home_lines) if team_total_home_lines else None,
        "team_total_away": sum(team_total_away_lines) / len(team_total_away_lines) if team_total_away_lines else None,
        "total_goals": sum(total_lines) / len(total_lines) if total_lines else None,
        "total_over_probability": sum(over_probabilities) / len(over_probabilities) if over_probabilities else None,
        "goal_diff": sum(all_spread_lines) / len(all_spread_lines) if all_spread_lines else None,
        "metadata": {"source": SOURCE_THE_ODDS_API, "markets": _event_market_keys(event)},
    }
    return row


def _h2h_sample(market: dict[str, Any] | None, home_team: TeamRef, away_team: TeamRef) -> tuple[float, float, float, float] | None:
    if not market:
        return None
    raw: dict[str, float] = {}
    for outcome in market.get("outcomes") or []:
        side = _outcome_side(str(outcome.get("name") or ""), home_team, away_team)
        price = _optional_float(outcome.get("price"))
        if side and price and price > 1:
            raw[side] = 1 / price
    if not all(side in raw for side in ("home", "draw", "away")):
        return None
    total = raw["home"] + raw["draw"] + raw["away"]
    if total <= 0:
        return None
    return raw["home"] / total, raw["draw"] / total, raw["away"] / total, total - 1


def _total_sample(market: dict[str, Any] | None) -> tuple[float, float | None] | None:
    """Return (totals line, no-vig over-probability) for one bookmaker's totals market."""

    if not market:
        return None
    points: dict[float, dict[str, float]] = {}
    for outcome in market.get("outcomes") or []:
        name = str(outcome.get("name") or "")
        point = _optional_float(outcome.get("point"))
        price = _optional_float(outcome.get("price"))
        if point is None or not price or price <= 1:
            continue
        side = "over" if _is_over(name) else "under" if _is_under(name) else ""
        if side:
            points.setdefault(point, {})[side] = 1 / price
    if not points:
        return None
    candidates = []
    for point, sides in points.items():
        over = sides.get("over")
        under = sides.get("under")
        over_probability = None
        balance_distance = 1.0
        if over is not None and under is not None and over + under > 0:
            over_probability = over / (over + under)
            balance_distance = abs(over_probability - 0.5)
        candidates.append((balance_distance, point, over_probability))
    candidates.sort(key=lambda item: (item[0], item[1]))
    _distance, line, over_probability = candidates[0]
    return line, over_probability


def _spread_line(market: dict[str, Any] | None, home_team: TeamRef, away_team: TeamRef) -> float | None:
    if not market:
        return None
    points: dict[float, dict[str, float]] = {}
    for outcome in market.get("outcomes") or []:
        side = _outcome_side(str(outcome.get("name") or ""), home_team, away_team)
        point = _optional_float(outcome.get("point"))
        price = _optional_float(outcome.get("price"))
        if point is None or not price or price <= 1 or side not in {"home", "away"}:
            continue
        goal_diff = -point if side == "home" else point
        points.setdefault(goal_diff, {})[side] = 1 / price
    if not points:
        return None
    candidates = []
    for goal_diff, sides in points.items():
        home = sides.get("home")
        away = sides.get("away")
        balance_distance = 1.0
        if home is not None and away is not None and home + away > 0:
            balance_distance = abs(home / (home + away) - 0.5)
        candidates.append((balance_distance, abs(goal_diff), goal_diff))
    candidates.sort(key=lambda item: (item[0], item[1]))
    return candidates[0][2]


def _draw_no_bet_sample(market: dict[str, Any] | None, home_team: TeamRef, away_team: TeamRef) -> tuple[float, float, float] | None:
    if not market:
        return None
    raw: dict[str, float] = {}
    for outcome in market.get("outcomes") or []:
        side = _outcome_side(str(outcome.get("name") or ""), home_team, away_team)
        price = _optional_float(outcome.get("price"))
        if side in {"home", "away"} and price and price > 1:
            raw[side] = 1 / price
    if not all(side in raw for side in ("home", "away")):
        return None
    total = raw["home"] + raw["away"]
    if total <= 0:
        return None
    return raw["home"] / total, raw["away"] / total, total - 1


def _btts_sample(market: dict[str, Any] | None) -> tuple[float, float] | None:
    if not market:
        return None
    raw: dict[str, float] = {}
    for outcome in market.get("outcomes") or []:
        name = _normalize_label(str(outcome.get("name") or ""))
        price = _optional_float(outcome.get("price"))
        if not price or price <= 1:
            continue
        if name in {"yes", "ja", "both teams to score"}:
            raw["yes"] = 1 / price
        elif name in {"no", "nein"}:
            raw["no"] = 1 / price
    if not all(side in raw for side in ("yes", "no")):
        return None
    total = raw["yes"] + raw["no"]
    if total <= 0:
        return None
    return raw["yes"] / total, total - 1


def _team_totals_sample(market: dict[str, Any] | None, home_team: TeamRef, away_team: TeamRef) -> tuple[float | None, float | None]:
    if not market:
        return None, None
    team_points: dict[str, dict[float, dict[str, float]]] = {"home": {}, "away": {}}
    for outcome in market.get("outcomes") or []:
        side = _outcome_side(str(outcome.get("description") or outcome.get("name") or ""), home_team, away_team)
        name = str(outcome.get("name") or "")
        point = _optional_float(outcome.get("point"))
        price = _optional_float(outcome.get("price"))
        if side not in {"home", "away"} or point is None or not price or price <= 1:
            continue
        total_side = "over" if _is_over(name) else "under" if _is_under(name) else ""
        if total_side:
            team_points[side].setdefault(point, {})[total_side] = 1 / price
    return _balanced_team_total(team_points["home"]), _balanced_team_total(team_points["away"])


def _balanced_team_total(points: dict[float, dict[str, float]]) -> float | None:
    if not points:
        return None
    candidates = []
    for point, sides in points.items():
        over = sides.get("over")
        under = sides.get("under")
        distance = 1.0
        if over is not None and under is not None and over + under > 0:
            distance = abs(over / (over + under) - 0.5)
        candidates.append((distance, point))
    candidates.sort(key=lambda item: (item[0], item[1]))
    return candidates[0][1]


def _outcome_side(name: str, home_team: TeamRef, away_team: TeamRef) -> str | None:
    normalized = _normalize_label(name)
    if normalized in {"draw", "tie", "x", "unentschieden", "remis"}:
        return "draw"
    if normalized == _normalize_label(home_team.name) or name.upper() == (home_team.fifa_code or ""):
        return "home"
    if normalized == _normalize_label(away_team.name) or name.upper() == (away_team.fifa_code or ""):
        return "away"
    resolver = TeamResolver.default(source=SOURCE_THE_ODDS_API)
    resolved = resolver.resolve(name)
    if resolved.key == home_team.key:
        return "home"
    if resolved.key == away_team.key:
        return "away"
    return None


def _is_over(name: str) -> bool:
    return _normalize_label(name) in {"over", "o", "ueber", "uber"}


def _is_under(name: str) -> bool:
    return _normalize_label(name) in {"under", "u", "unter"}


def _normalize_label(value: str) -> str:
    return " ".join(str(value or "").casefold().replace(".", " ").split())


def _average_tuple(samples: list[tuple[float, float, float, float]]) -> tuple[float, float, float, float] | None:
    if not samples:
        return None
    width = len(samples[0])
    return tuple(sum(sample[index] for sample in samples) / len(samples) for index in range(width))  # type: ignore[return-value]


def _market_row_source_key(row: dict[str, Any]) -> str:
    metadata = dict(row.get("metadata") or {})
    source = str(metadata.get("source") or row.get("_record", {}).get("source") or "")
    bookmaker = str(metadata.get("bookmaker") or "")
    event_id = str(row.get("event_id") or row.get("record_key") or row.get("_record", {}).get("record_key") or "")
    return "|".join(part for part in (source, bookmaker, event_id) if part)


def _observed_at(row: dict[str, Any]) -> str:
    return str(row.get("observed_at_utc") or row.get("_record", {}).get("observed_at_utc") or "")


def _weighted_average(rows: list[dict[str, Any]], value_key: str, weight_key: str) -> float | None:
    total = 0.0
    weight_total = 0.0
    for row in rows:
        value = _optional_float(row.get(value_key))
        if value is None:
            continue
        weight = max(1, int(row.get(weight_key) or 0))
        total += value * weight
        weight_total += weight
    if weight_total <= 0:
        return None
    return total / weight_total


def _market_sources(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    sources = []
    seen = set()
    for row in rows:
        metadata = dict(row.get("metadata") or {})
        source = str(metadata.get("source") or row.get("_record", {}).get("source") or "")
        bookmaker = str(metadata.get("bookmaker") or "")
        key = (source, bookmaker)
        if key in seen:
            continue
        seen.add(key)
        sources.append({"source": source, "bookmaker": bookmaker})
    return sources


def _market_confidence(bookmaker_count: int) -> float:
    if bookmaker_count >= 8:
        return 0.95
    if bookmaker_count >= 4:
        return 0.85
    if bookmaker_count >= 2:
        return 0.72
    return 0.55


def _credit_cost(markets: str, regions: str) -> int:
    """Odds API pricing: one credit per market per region per call."""

    market_count = len([market for market in str(markets or "").split(",") if market]) or 1
    region_count = len([region for region in str(regions or "").split(",") if region]) or 1
    return market_count * region_count


def main_odds_refresh_interval(fixtures: list[FixtureRecord]) -> dt.timedelta:
    """Kickoff-aware cadence: fetch often only when odds actually move.

    Checkpoint-style spacing like the weather schedule: twice daily until a
    day before the nearest kickoff, roughly 24/18/12/9/6/3h before it, and
    hourly only inside the final three hours (team news lands ~1h before
    kickoff). One fetch covers every upcoming match, so a defined final does
    not add calls of its own while an earlier match is still ahead of it.
    """

    now = utc_now()
    upcoming = [
        kickoff
        for kickoff in (parse_datetime(fixture.event_date) for fixture in fixtures)
        if kickoff is not None and kickoff > now
    ]
    if not upcoming:
        return dt.timedelta(hours=12)
    hours_to_kickoff = (min(upcoming) - now).total_seconds() / 3600.0
    if hours_to_kickoff <= 3:
        return dt.timedelta(hours=1)
    if hours_to_kickoff <= 12:
        return dt.timedelta(hours=3)
    if hours_to_kickoff <= 24:
        return dt.timedelta(hours=6)
    return dt.timedelta(hours=12)


def quota_floor_for_fixtures(fixtures: list[FixtureRecord], base_floor: int) -> int:
    """Reserve credits for the final while earlier fixtures are still open."""

    finals = [fixture for fixture in fixtures if _is_final_stage(fixture.stage)]
    if not finals:
        return base_floor
    dated = [fixture for fixture in fixtures if fixture.event_date]
    if dated and min(dated, key=lambda fixture: fixture.event_date) in finals:
        return base_floor
    return max(base_floor, THE_ODDS_API_FINAL_RESERVE_CREDITS)


def _is_final_stage(stage: str | None) -> bool:
    normalized = str(stage or "").casefold()
    if "final" not in normalized:
        return False
    return not any(token in normalized for token in ("semi", "third", "bronze", "quarter", "1/2", "1/4"))


def _near_term_fixtures(fixtures: list[FixtureRecord]) -> list[FixtureRecord]:
    now = utc_now()
    window_end = now + dt.timedelta(hours=THE_ODDS_API_EVENT_MARKET_WINDOW_HOURS)
    candidates = []
    for fixture in fixtures:
        kickoff = parse_datetime(fixture.event_date)
        if kickoff is None:
            continue
        if now <= kickoff <= window_end:
            candidates.append(fixture)
    candidates.sort(key=lambda fixture: fixture.event_date)
    return candidates[:THE_ODDS_API_EVENT_MARKET_FIXTURE_LIMIT]


def _event_market_keys(event: dict[str, Any]) -> str:
    keys = sorted(
        {
            str(market.get("key") or "")
            for bookmaker in (event.get("bookmakers") or [])
            for market in (bookmaker.get("markets") or [])
            if market.get("key")
        }
    )
    return ",".join(keys) if keys else THE_ODDS_API_MARKETS


def _optional_float(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _optional_int(value: Any) -> int | None:
    if value in (None, ""):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None
