from __future__ import annotations

import datetime as dt

import json
import unittest

from worldcup_predictions.core.contracts import ScoreTip
from worldcup_predictions.plugins.sources.fixtures.public_score_sources.plugin import public_page_analysis_rows
from worldcup_predictions.tournament import FixtureRecord, TeamResolver, build_tournament_state


class PublicScoreSourcesTest(unittest.TestCase):
    def test_public_page_analysis_rows_extract_supported_pregame_note(self) -> None:
        resolver = TeamResolver.default()
        fixture = FixtureRecord(
            event_date=(dt.datetime.now(dt.timezone.utc) + dt.timedelta(days=2)).strftime("%Y-%m-%dT18:00:00Z"),
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

if __name__ == "__main__":
    unittest.main()
