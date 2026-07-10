from __future__ import annotations

import tempfile
import unittest
import unittest.mock
from pathlib import Path

from worldcup_predictions.core.datasets import EXTRACTION_DIAGNOSTICS, TOURNAMENT_RESULTS
from worldcup_predictions.core.events import EventName
from worldcup_predictions.core.workflow import WorkflowContext
from worldcup_predictions.plugins.source_runtime import SourceRuntime
from worldcup_predictions.plugins.sources.fixtures.fixturedownload_source import FixtureDownloadSourcePlugin
from worldcup_predictions.plugins.sources.fixtures.fixturedownload_source.plugin import parse_fixturedownload_matches


def fixturedownload_match(**overrides):
    match = {
        "MatchNumber": 1,
        "RoundNumber": 1,
        "DateUtc": "2026-06-11 19:00:00Z",
        "Location": "Mexico City Stadium",
        "HomeTeam": "Mexico",
        "AwayTeam": "South Africa",
        "Group": "Group A",
        "HomeTeamScore": 2,
        "AwayTeamScore": 0,
        "Winner": "Mexico",
    }
    match.update(overrides)
    return match


class FixtureDownloadSourceTest(unittest.TestCase):
    def _run_plugin(self, payload):
        with tempfile.TemporaryDirectory() as tmp:
            from worldcup_predictions.storage import DuckDBStorage

            storage = DuckDBStorage.at_data_root(Path(tmp) / "data")
            context = WorkflowContext(project_root=Path(tmp), data_root=Path(tmp) / "data", storage=storage, run_id="run-a")
            plugin = FixtureDownloadSourcePlugin()

            def fake_fetch_json(self, endpoint, params=None, *, headers=None):
                return payload, {}

            with unittest.mock.patch.object(SourceRuntime, "fetch_json", fake_fetch_json):
                result = plugin.handle(EventName.FIXTURES_REQUESTED, context, {})

            results = storage.read_records(TOURNAMENT_RESULTS, latest_only=True)
            diagnostics = storage.read_records(EXTRACTION_DIAGNOSTICS, latest_only=True)
            return result, results, diagnostics

    def test_scored_match_writes_tournament_result_row(self) -> None:
        result, rows, diagnostics = self._run_plugin([fixturedownload_match()])

        self.assertEqual(result.metadata["results"], 1)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["fixture_key"], "2026-06-11T19:00:00Z|MEX|RSA")
        self.assertEqual(rows[0]["event_date"], "2026-06-11T19:00:00Z")
        self.assertEqual(rows[0]["home_team"], "Mexico")
        self.assertEqual(rows[0]["away_team"], "South Africa")
        self.assertEqual(rows[0]["home_fifa_code"], "MEX")
        self.assertEqual(rows[0]["away_fifa_code"], "RSA")
        self.assertEqual(rows[0]["home_score"], 2)
        self.assertEqual(rows[0]["away_score"], 0)
        self.assertEqual(rows[0]["source"], "fixturedownload")
        self.assertEqual(rows[0]["metadata"]["match_number"], 1)
        self.assertNotIn("winner", rows[0]["metadata"])
        self.assertEqual(diagnostics, [])

    def test_null_score_and_tbd_matches_are_skipped(self) -> None:
        result, rows, diagnostics = self._run_plugin(
            [
                fixturedownload_match(HomeTeamScore=None, AwayTeamScore=None, Winner=None),
                fixturedownload_match(MatchNumber=2, HomeTeamScore=1, AwayTeamScore=None, Winner=None),
                fixturedownload_match(
                    MatchNumber=80,
                    RoundNumber=5,
                    Group=None,
                    HomeTeam="To be announced",
                    AwayTeam="To be announced",
                    HomeTeamScore=None,
                    AwayTeamScore=None,
                    Winner=None,
                ),
            ]
        )

        self.assertEqual(result.metadata["results"], 0)
        self.assertEqual(rows, [])
        self.assertEqual(len(diagnostics), 1)
        self.assertEqual(diagnostics[0]["reason"], "no_results_extracted")

    def test_drawn_knockout_match_records_shootout_winner_side(self) -> None:
        result, rows, _diagnostics = self._run_plugin(
            [
                fixturedownload_match(
                    MatchNumber=90,
                    RoundNumber=6,
                    Group=None,
                    DateUtc="2026-07-04 20:00:00Z",
                    HomeTeam="France",
                    AwayTeam="Morocco",
                    HomeTeamScore=1,
                    AwayTeamScore=1,
                    Winner="Morocco",
                )
            ]
        )

        self.assertEqual(result.metadata["results"], 1)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["home_score"], 1)
        self.assertEqual(rows[0]["away_score"], 1)
        self.assertEqual(rows[0]["metadata"]["winner"], "AWAY_TEAM")
        self.assertEqual(rows[0]["metadata"]["winner_label"], "Morocco")

    def test_drawn_knockout_match_without_winner_keeps_drawn_score_only(self) -> None:
        results, extraction_rows = parse_fixturedownload_matches(
            [
                fixturedownload_match(
                    MatchNumber=91,
                    RoundNumber=6,
                    Group=None,
                    HomeTeamScore=2,
                    AwayTeamScore=2,
                    Winner=None,
                )
            ]
        )

        self.assertEqual(extraction_rows, [])
        self.assertEqual(len(results), 1)
        self.assertEqual((results[0].score.home, results[0].score.away), (2, 2))
        self.assertNotIn("winner", results[0].metadata)

    def test_unresolvable_team_is_skipped_with_diagnostic(self) -> None:
        result, rows, diagnostics = self._run_plugin(
            [fixturedownload_match(AwayTeam="Atlantis Union", Winner="Mexico")]
        )

        self.assertEqual(result.metadata["results"], 0)
        self.assertEqual(rows, [])
        self.assertEqual(len(diagnostics), 2)
        by_reason = {row["reason"]: row for row in diagnostics}
        self.assertIn("unresolved_team", by_reason)
        self.assertIn("Atlantis Union", by_reason["unresolved_team"]["metadata"]["unresolved_teams"])
        self.assertIn("no_results_extracted", by_reason)

    def test_zero_result_feed_writes_extraction_diagnostic(self) -> None:
        result, rows, diagnostics = self._run_plugin(
            [fixturedownload_match(HomeTeamScore=None, AwayTeamScore=None, Winner=None)]
        )

        self.assertEqual(result.metadata["results"], 0)
        self.assertEqual(rows, [])
        self.assertEqual(len(diagnostics), 1)
        self.assertEqual(diagnostics[0]["source"], "fixturedownload")
        self.assertEqual(diagnostics[0]["extractor"], "fixturedownload_feed_v1")
        self.assertEqual(diagnostics[0]["reason"], "no_results_extracted")
        self.assertEqual(diagnostics[0]["metadata"]["matches_in_feed"], 1)


if __name__ == "__main__":
    unittest.main()
