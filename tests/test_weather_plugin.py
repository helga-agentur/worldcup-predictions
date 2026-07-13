from __future__ import annotations

import datetime as dt
import unittest

from worldcup_predictions.plugins.sources.enrichment.weather.plugin import (
    fixture_teams_resolved,
    next_weather_fetch_at,
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


class WeatherFetchScheduleTest(unittest.TestCase):
    KICKOFF = dt.datetime(2026, 7, 14, 19, 0, tzinfo=dt.timezone.utc)

    def test_daily_cadence_beyond_24_hours(self) -> None:
        now = self.KICKOFF - dt.timedelta(days=5)
        self.assertEqual(next_weather_fetch_at(self.KICKOFF, now), now + dt.timedelta(hours=24))

    def test_daily_cadence_lands_on_24_hour_checkpoint(self) -> None:
        now = self.KICKOFF - dt.timedelta(hours=30)
        self.assertEqual(next_weather_fetch_at(self.KICKOFF, now), self.KICKOFF - dt.timedelta(hours=24))

    def test_checkpoints_inside_24_hours(self) -> None:
        cases = [
            (dt.timedelta(hours=20), dt.timedelta(hours=18)),
            (dt.timedelta(hours=13), dt.timedelta(hours=12)),
            (dt.timedelta(hours=7), dt.timedelta(hours=6)),
            (dt.timedelta(hours=4), dt.timedelta(hours=3)),
            (dt.timedelta(hours=1, minutes=30), dt.timedelta(hours=1)),
        ]
        for before, checkpoint in cases:
            with self.subTest(before=before):
                now = self.KICKOFF - before
                self.assertEqual(next_weather_fetch_at(self.KICKOFF, now), self.KICKOFF - checkpoint)

    def test_no_more_prematch_fetches_inside_the_final_hour(self) -> None:
        now = self.KICKOFF - dt.timedelta(minutes=30)
        self.assertEqual(next_weather_fetch_at(self.KICKOFF, now), self.KICKOFF)
        self.assertEqual(next_weather_fetch_at(self.KICKOFF, self.KICKOFF + dt.timedelta(hours=1)), self.KICKOFF)

    def test_placeholder_pairings_are_not_fetched(self) -> None:
        resolver = TeamResolver.default()
        real = FixtureRecord(
            event_date="2026-07-14T19:00:00Z",
            home_team=resolver.resolve("France"),
            away_team=resolver.resolve("Spain"),
        )
        placeholder = FixtureRecord(
            event_date="2026-07-15T19:00:00Z",
            home_team=resolver.resolve("Sieger Viertelfinal 3"),
            away_team=resolver.resolve("Sieger Viertelfinal 4"),
        )
        self.assertTrue(fixture_teams_resolved(real))
        self.assertFalse(fixture_teams_resolved(placeholder))

    def test_actual_rows_are_flagged_and_excluded_from_signals(self) -> None:
        match = fixture()
        payload = {
            "hourly": {
                "time": ["2026-07-10T18:00", "2026-07-10T19:00"],
                "temperature_2m": [28, 30],
                "precipitation": [0, 0],
            }
        }
        row = weather_row_from_open_meteo(
            payload,
            match,
            latitude=47.0,
            longitude=8.0,
            window_start=dt.datetime(2026, 7, 10, 17, 45, tzinfo=dt.timezone.utc),
            window_end=dt.datetime(2026, 7, 10, 20, 15, tzinfo=dt.timezone.utc),
            data_kind="actual",
        )
        assert row is not None
        self.assertEqual(row["data_kind"], "actual")
        self.assertTrue(row["record_key"].endswith(":actual"))
        self.assertEqual(weather_signals_from_rows([{**row, "goal_factor": 0.9}]), [])


if __name__ == "__main__":
    unittest.main()
