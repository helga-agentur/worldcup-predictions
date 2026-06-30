from __future__ import annotations

import datetime as dt
import json
import unittest

from worldcup_predictions.core.contracts import ScoreTip
from worldcup_predictions.plugins.public_score_sources.plugin import _espn_dates_to_fetch, parse_espn_scoreboard_results, public_page_analysis_rows
from worldcup_predictions.tournament import FixtureRecord, TeamResolver, build_tournament_state


class PublicScoreSourcesTest(unittest.TestCase):
    def test_espn_dates_include_due_utc_and_us_local_date_but_skip_future(self) -> None:
        resolver = TeamResolver.default()
        due_fixture = FixtureRecord(
            event_date="2026-06-30T01:00:00Z",
            home_team=resolver.resolve("USA"),
            away_team=resolver.resolve("Bosnia and Herzegovina"),
            stage="Group Stage",
        )
        future_fixture = FixtureRecord(
            event_date="2026-07-01T17:00:00Z",
            home_team=resolver.resolve("Brazil"),
            away_team=resolver.resolve("Japan"),
            stage="Group Stage",
        )
        state = build_tournament_state([due_fixture, future_fixture], [])

        dates = _espn_dates_to_fetch(state, now=dt.datetime(2026, 6, 30, 2, tzinfo=dt.UTC))

        self.assertEqual(dates, ["20260629", "20260630"])

    def test_public_page_analysis_rows_extract_supported_pregame_note(self) -> None:
        resolver = TeamResolver.default()
        fixture = FixtureRecord(
            event_date="2026-07-10T18:00:00Z",
            home_team=resolver.resolve("Brazil"),
            away_team=resolver.resolve("Japan"),
            stage="Group Stage",
        )
        html = """
        <html>
          <head><title>Brazil vs Japan preview</title><meta name="description" content="Brazil Japan tactical preview"></head>
          <body>Brazil and Japan meet in an attacking open game, with over 2.5 goals expected.</body>
        </html>
        """

        rows, diagnostics = public_page_analysis_rows(
            html,
            state=build_tournament_state([fixture], []),
            source="test_public_source",
            source_name="Test public source",
            source_url="https://example.test/match",
        )

        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["phase"], "pregame")
        self.assertEqual(rows[0]["fixture_key"], fixture.key)
        self.assertEqual(rows[0]["signal_type"], "high_tempo_or_attacking")
        self.assertEqual(diagnostics[0]["status"], "accepted")

    def test_espn_scoreboard_parser_aligns_result_to_canonical_fixture_order(self) -> None:
        resolver = TeamResolver.default()
        fixture = FixtureRecord(
            event_date="2026-06-29T17:00:00Z",
            home_team=resolver.resolve("Brazil"),
            away_team=resolver.resolve("Japan"),
            stage="Round of 32",
        )
        payload = {
            "page": {
                "content": {
                    "scoreboard": {
                        "gmsByLeague": [
                            {
                                "evts": [
                                    {
                                        "id": "760487",
                                        "completed": True,
                                        "date": "2026-06-29T17:00Z",
                                        "status": {"state": "post", "description": "Full Time", "detail": "FT"},
                                        "link": "/soccer/match/_/gameId/760487/japan-brazil",
                                        "competitors": [
                                            {"displayName": "Japan", "abbrev": "JPN", "isHome": False, "score": 1},
                                            {"displayName": "Brazil", "abbrev": "BRA", "isHome": True, "score": 2},
                                        ],
                                    }
                                ]
                            }
                        ]
                    }
                }
            }
        }
        html = "<script>window['__espnfitt__']=" + json.dumps(payload) + ";</script>"

        rows = parse_espn_scoreboard_results(html, state=build_tournament_state([fixture], []))

        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].fixture_key, fixture.key)
        self.assertEqual(rows[0].score, ScoreTip(2, 1))
        self.assertEqual(rows[0].source, "espn_scoreboard")

    def test_espn_scoreboard_parser_swaps_score_when_external_home_away_is_reversed(self) -> None:
        resolver = TeamResolver.default()
        fixture = FixtureRecord(
            event_date="2026-06-29T17:00:00Z",
            home_team=resolver.resolve("Brazil"),
            away_team=resolver.resolve("Japan"),
            stage="Round of 32",
        )
        payload = {
            "event": {
                "competitors": [
                    {"displayName": "Brazil", "abbrev": "BRA", "isHome": False, "score": 2},
                    {"displayName": "Japan", "abbrev": "JPN", "isHome": True, "score": 1},
                ],
                "completed": True,
                "date": "2026-06-29T17:00Z",
                "status": {"state": "post", "description": "Full Time", "detail": "FT"},
            }
        }
        html = "<script>window['__espnfitt__']=" + json.dumps(payload) + ";</script>"

        rows = parse_espn_scoreboard_results(html, state=build_tournament_state([fixture], []))

        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].fixture_key, fixture.key)
        self.assertEqual(rows[0].score, ScoreTip(2, 1))


if __name__ == "__main__":
    unittest.main()
