from __future__ import annotations

import datetime as dt
import unittest

from worldcup_predictions.plugins.weather.plugin import (
    weather_goal_factor,
    weather_row_from_open_meteo,
    weather_signals_from_rows,
)
from worldcup_predictions.tournament import FixtureRecord, TeamResolver


def fixture() -> FixtureRecord:
    resolver = TeamResolver.default()
    return FixtureRecord(
        event_date="2026-07-10T18:00:00Z",
        home_team=resolver.resolve("France"),
        away_team=resolver.resolve("Iraq"),
        group="Group I",
        stage="Group Stage",
        venue="Test Stadium",
        metadata={"latitude": 47.0, "longitude": 8.0},
    )


class WeatherPluginTest(unittest.TestCase):
    def test_weather_row_uses_full_match_window(self) -> None:
        match = fixture()
        payload = {
            "hourly": {
                "time": [
                    "2026-07-10T17:00",
                    "2026-07-10T18:00",
                    "2026-07-10T19:00",
                    "2026-07-10T20:00",
                    "2026-07-10T21:00",
                ],
                "temperature_2m": [28, 30, 32, 31, 28],
                "apparent_temperature": [31, 35, 36, 34, 30],
                "relative_humidity_2m": [60, 70, 80, 78, 65],
                "precipitation": [0, 2, 4, 1, 0],
                "rain": [0, 2, 4, 1, 0],
                "showers": [0, 0, 1, 0, 0],
                "precipitation_probability": [10, 40, 70, 55, 20],
                "weather_code": [2, 95, 95, 61, 1],
                "wind_speed_10m": [18, 25, 28, 20, 15],
                "wind_gusts_10m": [30, 48, 52, 35, 25],
            }
        }

        row = weather_row_from_open_meteo(
            payload,
            match,
            latitude=47.0,
            longitude=8.0,
            window_start=dt.datetime(2026, 7, 10, 17, 45, tzinfo=dt.timezone.utc),
            window_end=dt.datetime(2026, 7, 10, 20, 15, tzinfo=dt.timezone.utc),
        )

        self.assertIsNotNone(row)
        assert row is not None
        self.assertEqual(row["hours_covered"], 4)
        self.assertTrue(row["storm_risk"])
        self.assertLess(row["goal_factor"], 1.0)
        self.assertIn("storm", row["caveat"])

    def test_weather_signal_emits_only_material_adjustments(self) -> None:
        match = fixture()
        row = {
            "fixture_key": match.key,
            "goal_factor": 0.90,
            "caveat": "Weather adjustment for storm risk.",
            "storm_risk": True,
        }

        signals = weather_signals_from_rows([row])

        self.assertEqual(len(signals), 1)
        self.assertEqual(signals[0].name, "total_goals_factor")
        self.assertEqual(signals[0].fixture_key, match.key)

    def test_weather_goal_factor_is_capped(self) -> None:
        factor = weather_goal_factor(
            {
                "apparent_temperature_max_c": 41,
                "precipitation_sum_mm": 30,
                "wind_gusts_max_kmh": 80,
                "precipitation_probability_max_pct": 95,
                "storm_risk": True,
            }
        )

        self.assertEqual(factor, 0.82)


if __name__ == "__main__":
    unittest.main()
