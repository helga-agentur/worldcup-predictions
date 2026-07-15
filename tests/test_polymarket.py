from __future__ import annotations

import unittest

from worldcup_predictions.plugins.sources.markets.polymarket.plugin import (
    polymarket_match_signal,
    polymarket_outright_rows,
)
from worldcup_predictions.market_prior import team_strengths_from_outrights
from worldcup_predictions.tournament import FixtureRecord, TeamResolver


def _fixture() -> FixtureRecord:
    resolver = TeamResolver.default()
    return FixtureRecord(
        event_date="2026-07-15T19:00:00Z",
        home_team=resolver.resolve("England"),
        away_team=resolver.resolve("Argentina"),
        stage="Semi-final",
    )


SEARCH_PAYLOAD = {
    "events": [
        {
            "title": "England vs. Argentina",
            "slug": "fifwc-eng-arg-2026-07-15",
            "endDate": "2026-07-15T19:00:00Z",
            "markets": [
                {
                    "question": "Will England win on 2026-07-15?",
                    "outcomes": '["Yes", "No"]',
                    "outcomePrices": '["0.35375", "0.64625"]',
                    "volumeNum": 2358030.4,
                },
                {
                    "question": "Will England vs. Argentina end in a draw?",
                    "outcomes": '["Yes", "No"]',
                    "outcomePrices": '["0.33375", "0.66625"]',
                    "volumeNum": 630900.6,
                },
                {
                    "question": "Will Argentina win on 2026-07-15?",
                    "outcomes": '["Yes", "No"]',
                    "outcomePrices": '["0.31625", "0.68375"]',
                    "volumeNum": 1693491.9,
                },
            ],
        }
    ]
}


class PolymarketMatchSignalTest(unittest.TestCase):
    def test_moneyline_becomes_normalized_hda_signal(self) -> None:
        signal = polymarket_match_signal(SEARCH_PAYLOAD, _fixture())

        self.assertIsNotNone(signal)
        assert signal is not None
        m = signal.metadata
        total = m["prob_home"] + m["prob_draw"] + m["prob_away"]
        self.assertAlmostEqual(total, 1.0, places=9)
        self.assertGreater(m["prob_home"], m["prob_away"], "England priced above Argentina")
        self.assertEqual(signal.source, "polymarket")
        self.assertEqual(signal.confidence, 0.85, "multi-million volume earns high confidence")

    def test_wrong_pairing_or_date_is_rejected(self) -> None:
        resolver = TeamResolver.default()
        other = FixtureRecord(
            event_date="2026-07-18T21:00:00Z",
            home_team=resolver.resolve("France"),
            away_team=resolver.resolve("Argentina"),
            stage="Third place",
        )
        self.assertIsNone(polymarket_match_signal(SEARCH_PAYLOAD, other))

        stale = {"events": [dict(SEARCH_PAYLOAD["events"][0], endDate="2026-06-20T19:00:00Z")]}
        self.assertIsNone(polymarket_match_signal(stale, _fixture()))

    def test_incomplete_moneyline_is_rejected(self) -> None:
        partial = {"events": [dict(SEARCH_PAYLOAD["events"][0], markets=SEARCH_PAYLOAD["events"][0]["markets"][:2])]}
        self.assertIsNone(polymarket_match_signal(partial, _fixture()))


class PolymarketOutrightTest(unittest.TestCase):
    PAYLOAD = [
        {
            "title": "World Cup Winner",
            "markets": [
                {"groupItemTitle": "Spain", "outcomes": '["Yes","No"]', "outcomePrices": '["0.5815","0.4185"]'},
                {"groupItemTitle": "England", "outcomes": '["Yes","No"]', "outcomePrices": '["0.2265","0.7735"]'},
                {"groupItemTitle": "Argentina", "outcomes": '["Yes","No"]', "outcomePrices": '["0.1965","0.8035"]'},
                {"groupItemTitle": "France", "outcomes": '["Yes","No"]', "outcomePrices": '["0","1"]'},
            ],
        }
    ]

    def test_outrights_normalize_and_keep_eliminated_teams(self) -> None:
        rows = polymarket_outright_rows(self.PAYLOAD)

        by_team = {row["team"]: row for row in rows}
        self.assertAlmostEqual(sum(r["fair_probability"] for r in rows), 1.0, places=9)
        self.assertGreater(by_team["Spain"]["fair_probability"], 0.5)
        # Eliminated teams stay as fresh zero rows so stale bookmaker prices
        # cannot survive through the freshest-wins strengths builder.
        self.assertEqual(by_team["France"]["fair_probability"], 0.0)

    def test_freshest_observation_wins_in_strengths(self) -> None:
        stale = {
            "record_key": "odds:FRA",
            "sport_key": "odds",
            "team": "France",
            "fifa_code": "FRA",
            "observed_at_utc": "2026-07-01T00:00:00Z",
            "fair_probability": 0.295,
        }
        fresh_rows = polymarket_outright_rows(self.PAYLOAD)
        strengths = team_strengths_from_outrights([stale, *fresh_rows])
        # France's fresh zero supersedes the stale 0.295 (floored to 0.01).
        self.assertLessEqual(strengths["FRA"], 0.01)
        self.assertGreater(strengths["ESP"], 0.5)


if __name__ == "__main__":
    unittest.main()
