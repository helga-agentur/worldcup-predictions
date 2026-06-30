"""Open-Meteo match-window weather plugin."""

from __future__ import annotations

import datetime as dt
import json
import urllib.error
from typing import Any, Iterable

from worldcup_predictions.core.constants import (
    ENDPOINT_OPEN_METEO_FORECAST,
    SIGNAL_CONFIDENCE_WEATHER,
    SIGNAL_WEIGHT_WEATHER,
    SOURCE_OPEN_METEO,
    VENUE_COORDINATES,
    WEATHER_GOAL_FACTOR_MAX,
    WEATHER_GOAL_FACTOR_MIN,
)
from worldcup_predictions.core.contracts import Diagnostic, Signal, parse_utc_datetime
from worldcup_predictions.core.datasets import WEATHER_OBSERVATIONS as WEATHER_DATASET
from worldcup_predictions.core.events import EventName, event_value
from worldcup_predictions.core.metadata import PluginKind, PluginMetadata, QuotaPolicy
from worldcup_predictions.core.plugin import BasePlugin, PluginResult
from worldcup_predictions.core.signals import TOTAL_GOALS_FACTOR
from worldcup_predictions.plugins.source_runtime import SourceRuntime
from worldcup_predictions.plugins.source_utils import date_range_for_window, match_window_hours, optional_float
from worldcup_predictions.storage.ledger import SourceRequest, normalize_datetime
from worldcup_predictions.tournament.contracts import FixtureRecord


HOURLY_FIELDS = ",".join(
    [
        "temperature_2m",
        "apparent_temperature",
        "relative_humidity_2m",
        "precipitation",
        "rain",
        "showers",
        "precipitation_probability",
        "weather_code",
        "wind_speed_10m",
        "wind_gusts_10m",
    ]
)


class WeatherPlugin(BasePlugin):
    """Fetch weather forecasts and emit conservative total-goal signals."""

    id = "weather"
    version = "0.1.0"
    priority = 260
    subscribed_events = (EventName.FEATURE_SIGNALS_REQUESTED.value,)
    metadata = PluginMetadata(
        plugin_id=id,
        kind=PluginKind.SOURCE,
        description="Fetch Open-Meteo match-window weather and emit total-goal factors.",
        datasets_read=(WEATHER_DATASET,),
        datasets_written=(WEATHER_DATASET,),
        signals_emitted=(TOTAL_GOALS_FACTOR,),
        quota_policy=QuotaPolicy(
            quota_limited=False,
            ledger_required=True,
            description="Ledger avoids unnecessary repeated weather requests even though Open-Meteo has no project API key.",
        ),
        confidence_policy="Weather signals are capped and require material heat, wind, rain, or storm risk.",
    )

    def handle(self, event, context, payload):
        runtime = SourceRuntime(self, event, context)
        if context.storage is None:
            return runtime.storage_unavailable_result("weather")

        state = runtime.tournament_state()

        diagnostics: list[Diagnostic] = []
        written = 0
        for fixture in state.open_fixtures():
            result = self._fetch_fixture_weather(runtime, fixture)
            diagnostics.extend(result.diagnostics)
            written += int(result.metadata.get("written_rows") or 0)

        rows = runtime.read_latest(WEATHER_DATASET)
        signals = weather_signals_from_rows(rows)
        return PluginResult(
            plugin_id=self.id,
            event=event_value(event),
            signals=signals,
            artifacts=[runtime.structured_artifact(WEATHER_DATASET, rows_written=written, signals=len(signals))],
            diagnostics=diagnostics,
            metadata={"written_rows": written, "signals": len(signals)},
        )

    def _fetch_fixture_weather(self, runtime: SourceRuntime, fixture: FixtureRecord) -> PluginResult:
        coordinates = fixture_coordinates(fixture)
        if coordinates is None:
            return runtime.result(
                diagnostics=[
                    runtime.diagnostic(
                        level="info",
                        message="No venue coordinates available; weather source skipped for fixture.",
                        fixture_key=fixture.key,
                    )
                ],
            )

        latitude, longitude = coordinates
        try:
            start, end = match_window_hours(fixture.event_date)
        except ValueError as exc:
            return runtime.result(diagnostics=[runtime.diagnostic("warning", str(exc), fixture_key=fixture.key)])
        start_date, end_date = date_range_for_window(start, end)
        request = SourceRequest(
            source=SOURCE_OPEN_METEO,
            endpoint=ENDPOINT_OPEN_METEO_FORECAST,
            purpose="match_window_weather",
            params={
                "latitude": round(latitude, 4),
                "longitude": round(longitude, 4),
                "start_date": start_date,
                "end_date": end_date,
                "hourly": HOURLY_FIELDS,
                "timezone": "UTC",
            },
            fixture_key=fixture.key,
            quota_cost=0,
            min_refresh_interval=dt.timedelta(minutes=runtime.context.config.source_defaults.weather_refresh_minutes),
        )
        decision = runtime.should_fetch(request)
        if not decision.should_fetch:
            return runtime.skipped_fetch_result("Weather", decision.reason, fixture_key=fixture.key, metadata=decision.metadata)

        try:
            payload, _headers = runtime.fetch_json(
                ENDPOINT_OPEN_METEO_FORECAST,
                {
                    "latitude": latitude,
                    "longitude": longitude,
                    "start_date": start_date,
                    "end_date": end_date,
                    "hourly": HOURLY_FIELDS,
                    "timezone": "UTC",
                },
            )
        except (OSError, TimeoutError, urllib.error.HTTPError, json.JSONDecodeError) as exc:
            runtime.record_error(request, exc)
            return runtime.result(
                diagnostics=[
                    runtime.diagnostic(
                        level="warning",
                        message="Weather fetch failed; stored weather rows will be used.",
                        fixture_key=fixture.key,
                        metadata={"error": str(exc)},
                    )
                ],
            )

        row = weather_row_from_open_meteo(payload, fixture, latitude=latitude, longitude=longitude, window_start=start, window_end=end)
        runtime.record_success(
            request,
            message="Fetched Open-Meteo match-window forecast.",
            metadata={"rows": 1 if row else 0},
        )
        count = 0
        if row is not None:
            count = runtime.write_records(WEATHER_DATASET, [row])
        return runtime.result(metadata={"written_rows": count})


def fixture_coordinates(fixture: FixtureRecord) -> tuple[float, float] | None:
    metadata = fixture.metadata or {}
    latitude = (
        optional_float(metadata.get("venue_latitude"))
        or optional_float(metadata.get("latitude"))
        or optional_float(metadata.get("lat"))
    )
    longitude = (
        optional_float(metadata.get("venue_longitude"))
        or optional_float(metadata.get("longitude"))
        or optional_float(metadata.get("lon"))
    )
    if latitude is None or longitude is None:
        venue = _normalize_venue_label(fixture.venue or metadata.get("location") or metadata.get("venue") or "")
        return VENUE_COORDINATES.get(venue)
    return latitude, longitude


def _normalize_venue_label(value: Any) -> str:
    return " ".join(str(value or "").casefold().replace("-", " ").split())


def weather_row_from_open_meteo(
    payload: dict[str, Any],
    fixture: FixtureRecord,
    *,
    latitude: float,
    longitude: float,
    window_start: dt.datetime,
    window_end: dt.datetime,
) -> dict[str, Any] | None:
    hourly = payload.get("hourly") or {}
    times = hourly.get("time") or []
    selected_indexes = [
        index
        for index, value in enumerate(times)
        if _hour_overlaps_window(str(value), window_start, window_end)
    ]
    if not selected_indexes:
        return None

    def series_max(name: str) -> float | None:
        return _series_max(hourly.get(name), selected_indexes)

    def series_sum(name: str) -> float:
        return _series_sum(hourly.get(name), selected_indexes)

    weather_codes = [_optional_int_at(hourly.get("weather_code"), index) for index in selected_indexes]
    weather_codes = [code for code in weather_codes if code is not None]
    row = {
        "record_key": f"{fixture.key}:{SOURCE_OPEN_METEO}:{normalize_datetime(window_start)}",
        "fixture_key": fixture.key,
        "event_date": fixture.event_date,
        "home_team": fixture.home_team.name,
        "away_team": fixture.away_team.name,
        "home_fifa_code": fixture.home_team.fifa_code,
        "away_fifa_code": fixture.away_team.fifa_code,
        "venue": fixture.venue,
        "latitude": latitude,
        "longitude": longitude,
        "window_start_utc": normalize_datetime(window_start),
        "window_end_utc": normalize_datetime(window_end),
        "hours_covered": len(selected_indexes),
        "temperature_max_c": series_max("temperature_2m"),
        "apparent_temperature_max_c": series_max("apparent_temperature"),
        "humidity_max_pct": series_max("relative_humidity_2m"),
        "precipitation_sum_mm": series_sum("precipitation"),
        "rain_sum_mm": series_sum("rain"),
        "showers_sum_mm": series_sum("showers"),
        "precipitation_probability_max_pct": series_max("precipitation_probability"),
        "wind_speed_max_kmh": series_max("wind_speed_10m"),
        "wind_gusts_max_kmh": series_max("wind_gusts_10m"),
        "weather_code_max": max(weather_codes) if weather_codes else None,
        "storm_risk": any(code in {95, 96, 99} for code in weather_codes),
    }
    row["goal_factor"] = weather_goal_factor(row)
    row["caveat"] = weather_caveat(row)
    return row


def weather_signals_from_rows(rows: list[dict[str, Any]]) -> list[Signal]:
    latest_by_fixture: dict[str, dict[str, Any]] = {}
    for row in rows:
        fixture_key = str(row.get("fixture_key") or "")
        observed_at = str(row.get("_record", {}).get("observed_at_utc") or row.get("window_start_utc") or "")
        current = latest_by_fixture.get(fixture_key)
        current_observed_at = str(current.get("_record", {}).get("observed_at_utc") or current.get("window_start_utc") or "") if current else ""
        if fixture_key and (current is None or observed_at >= current_observed_at):
            latest_by_fixture[fixture_key] = row
    signals = []
    for fixture_key, row in latest_by_fixture.items():
        factor = optional_float(row.get("goal_factor"))
        if factor is None or abs(factor - 1.0) < 0.005:
            continue
        signals.append(
            Signal(
                name="total_goals_factor",
                source=SOURCE_OPEN_METEO,
                fixture_key=fixture_key,
                value=factor,
                weight=SIGNAL_WEIGHT_WEATHER,
                confidence=SIGNAL_CONFIDENCE_WEATHER,
                rationale=str(row.get("caveat") or "Weather match-window goal adjustment."),
                metadata={
                    "temperature_max_c": row.get("temperature_max_c"),
                    "precipitation_sum_mm": row.get("precipitation_sum_mm"),
                    "wind_gusts_max_kmh": row.get("wind_gusts_max_kmh"),
                    "storm_risk": row.get("storm_risk"),
                },
            )
        )
    return signals


def weather_goal_factor(row: dict[str, Any]) -> float:
    factor = 1.0
    temperature = optional_float(row.get("apparent_temperature_max_c")) or optional_float(row.get("temperature_max_c")) or 0.0
    precipitation = optional_float(row.get("precipitation_sum_mm")) or 0.0
    rain = optional_float(row.get("rain_sum_mm")) or 0.0
    gusts = optional_float(row.get("wind_gusts_max_kmh")) or 0.0
    probability = optional_float(row.get("precipitation_probability_max_pct")) or 0.0
    storm = bool(row.get("storm_risk"))
    if temperature >= 32:
        factor -= 0.04
    if temperature >= 36:
        factor -= 0.03
    if gusts >= 45:
        factor -= 0.03
    if precipitation >= 5 or rain >= 5:
        factor -= 0.04
    if storm and probability >= 35:
        factor -= 0.05
    elif storm:
        factor -= 0.02
    return max(WEATHER_GOAL_FACTOR_MIN, min(WEATHER_GOAL_FACTOR_MAX, factor))


def weather_caveat(row: dict[str, Any]) -> str:
    reasons = []
    if row.get("storm_risk"):
        reasons.append("storm risk")
    if (optional_float(row.get("precipitation_sum_mm")) or 0.0) >= 5:
        reasons.append("heavy precipitation")
    if (optional_float(row.get("wind_gusts_max_kmh")) or 0.0) >= 45:
        reasons.append("high wind gusts")
    if (optional_float(row.get("apparent_temperature_max_c")) or optional_float(row.get("temperature_max_c")) or 0.0) >= 32:
        reasons.append("heat")
    if not reasons:
        return "Weather conditions are not expected to materially move the score model."
    return "Weather adjustment for " + ", ".join(reasons) + "."


def _hour_overlaps_window(value: str, start: dt.datetime, end: dt.datetime) -> bool:
    parsed = parse_utc_datetime(value if "T" in value else value.replace(" ", "T"))
    if parsed is None:
        return False
    hour_end = parsed + dt.timedelta(hours=1)
    return parsed < end and hour_end > start


def _series_max(values: Iterable[Any] | None, indexes: list[int]) -> float | None:
    numbers = [_optional_float_at(values, index) for index in indexes]
    numbers = [number for number in numbers if number is not None]
    return max(numbers) if numbers else None


def _series_sum(values: Iterable[Any] | None, indexes: list[int]) -> float:
    numbers = [_optional_float_at(values, index) for index in indexes]
    return sum(number for number in numbers if number is not None)


def _optional_float_at(values: Iterable[Any] | None, index: int) -> float | None:
    if values is None:
        return None
    sequence = list(values)
    if index >= len(sequence):
        return None
    return optional_float(sequence[index])


def _optional_int_at(values: Iterable[Any] | None, index: int) -> int | None:
    value = _optional_float_at(values, index)
    return int(value) if value is not None else None
