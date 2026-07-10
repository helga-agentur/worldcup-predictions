from __future__ import annotations

import tempfile
import unittest
import unittest.mock
from pathlib import Path

from worldcup_predictions.core.datasets import EXTRACTION_DIAGNOSTICS, TOURNAMENT_RESULTS
from worldcup_predictions.core.events import EventName
from worldcup_predictions.core.workflow import WorkflowContext
from worldcup_predictions.plugins.source_runtime import SourceRuntime
from worldcup_predictions.plugins.sources.fixtures.thesportsdb_source import TheSportsDbSourcePlugin
from worldcup_predictions.plugins.sources.fixtures.thesportsdb_source.plugin import parse_thesportsdb_events
from worldcup_predictions.storage import DuckDBStorage


def thesportsdb_event(**overrides):
    event = {
        "idEvent": "2391728",
        "strEvent": "Mexico vs South Africa",
        "strTimestamp": "2026-06-11T19:00:00",
        "dateEvent": "2026-06-11",
        "strTime": "19:00:00",
        "strHomeTeam": "Mexico",
        "strAwayTeam": "South Africa",
        "intHomeScore": "2",
        "intAwayScore": "0",
        "strStatus": "FT",
        "intRound": "1",
    }
    event.update(overrides)
    return event


class TheSportsDbSourceTest(unittest.TestCase):
    def _run_plugin(self, payload):
        with tempfile.TemporaryDirectory() as tmp:
            storage = DuckDBStorage.at_data_root(Path(tmp) / "data")
            context = WorkflowContext(project_root=Path(tmp), data_root=Path(tmp) / "data", storage=storage, run_id="run-a")
            plugin = TheSportsDbSourcePlugin()

            def fake_fetch_json(self, endpoint, params=None, *, headers=None):
                return payload, {}

            with unittest.mock.patch.object(SourceRuntime, "fetch_json", fake_fetch_json):
                result = plugin.handle(EventName.FIXTURES_REQUESTED, context, {})

            results = storage.read_records(TOURNAMENT_RESULTS, latest_only=True)
            diagnostics = storage.read_records(EXTRACTION_DIAGNOSTICS, latest_only=True)
            return result, results, diagnostics

    def test_finished_event_writes_tournament_result_row(self) -> None:
        result, rows, diagnostics = self._run_plugin({"events": [thesportsdb_event()]})

        self.assertEqual(result.metadata["results"], 1)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["fixture_key"], "2026-06-11T19:00:00Z|MEX|RSA")
        self.assertEqual(rows[0]["home_team"], "Mexico")
        self.assertEqual(rows[0]["away_team"], "South Africa")
        self.assertEqual(rows[0]["home_fifa_code"], "MEX")
        self.assertEqual(rows[0]["away_fifa_code"], "RSA")
        self.assertEqual(rows[0]["home_score"], 2)
        self.assertEqual(rows[0]["away_score"], 0)
        self.assertEqual(rows[0]["event_date"], "2026-06-11T19:00:00Z")
        self.assertEqual(rows[0]["source"], "thesportsdb")
        self.assertEqual(diagnostics, [])

    def test_unfinished_or_scoreless_events_are_skipped(self) -> None:
        result, rows, _diagnostics = self._run_plugin(
            {
                "events": [
                    thesportsdb_event(strStatus="NS", intHomeScore=None, intAwayScore=None),
                    thesportsdb_event(idEvent="2391729", strStatus="1H", intHomeScore="1", intAwayScore="0"),
                    thesportsdb_event(idEvent="2391730", strStatus="FT", intHomeScore=None, intAwayScore="0"),
                ]
            }
        )

        self.assertEqual(result.metadata["results"], 0)
        self.assertEqual(rows, [])

    def test_unresolvable_team_is_skipped_with_diagnostic(self) -> None:
        result, rows, diagnostics = self._run_plugin(
            {"events": [thesportsdb_event(strAwayTeam="Atlantis Union", strEvent="Mexico vs Atlantis Union")]}
        )

        self.assertEqual(result.metadata["results"], 0)
        self.assertEqual(rows, [])
        self.assertEqual(len(diagnostics), 1)
        self.assertEqual(diagnostics[0]["source"], "thesportsdb")
        self.assertEqual(diagnostics[0]["status"], "rejected")
        self.assertEqual(diagnostics[0]["reason"], "unresolved_team")
        self.assertIn("Atlantis Union", diagnostics[0]["metadata"]["unresolved_teams"])

    def test_zero_events_payload_writes_diagnostic(self) -> None:
        result, rows, diagnostics = self._run_plugin({"events": None})

        self.assertEqual(result.metadata["results"], 0)
        self.assertEqual(rows, [])
        self.assertEqual(len(diagnostics), 1)
        self.assertEqual(diagnostics[0]["reason"], "no_events_extracted")
        self.assertEqual(diagnostics[0]["extractor"], "thesportsdb_events_v2")

    def test_parser_keeps_extra_time_score_and_penalty_metadata(self) -> None:
        results, extraction_rows = parse_thesportsdb_events(
            {
                "events": [
                    thesportsdb_event(
                        strStatus="AET",
                        intHomeScore="1",
                        intAwayScore="1",
                        intHomeScorePen="4",
                        intAwayScorePen="3",
                    )
                ]
            }
        )

        self.assertEqual(extraction_rows, [])
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0].score.as_text(), "1:1")
        self.assertEqual(results[0].metadata["status"], "AET")
        self.assertEqual(results[0].metadata["penalty_home"], 4)
        self.assertEqual(results[0].metadata["penalty_away"], 3)


if __name__ == "__main__":
    unittest.main()
