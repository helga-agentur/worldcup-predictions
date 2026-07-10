from __future__ import annotations

import unittest
import unittest.mock
from pathlib import Path
from typing import Any, Iterable, Mapping

from worldcup_predictions.core.datasets import EXTRACTION_DIAGNOSTICS, TOURNAMENT_RESULTS
from worldcup_predictions.core.events import EventName
from worldcup_predictions.core.workflow import WorkflowContext
from worldcup_predictions.plugins.source_runtime import SourceRuntime
from worldcup_predictions.plugins.sources.fixtures.openligadb_source import OpenLigaDbSourcePlugin
from worldcup_predictions.plugins.sources.fixtures.openligadb_source.plugin import parse_openligadb_matches
from worldcup_predictions.storage.ledger import FetchDecision


class FakeStorage:
    """Minimal in-memory storage standing in for DuckDBStorage in plugin tests."""

    def __init__(self) -> None:
        self.datasets: dict[str, list[dict[str, Any]]] = {}
        self.ledger: list[Any] = []

    def should_fetch(self, request) -> FetchDecision:
        return FetchDecision(True, "due", request.request_key)

    def record_fetch(self, record) -> None:
        self.ledger.append(record)

    def write_records(self, dataset: str, rows: Iterable[Mapping[str, Any]], *, source: str, run_id: str) -> int:
        stored = [dict(row) for row in rows]
        self.datasets.setdefault(dataset, []).extend(stored)
        return len(stored)

    def read_records(self, dataset: str, *, latest_only: bool = False) -> list[dict[str, Any]]:
        return list(self.datasets.get(dataset, []))


def openligadb_match(**overrides):
    match = {
        "matchID": 71001,
        "matchDateTimeUTC": "2026-06-11T19:00:00Z",
        "matchIsFinished": True,
        "group": {"groupName": "1. Spieltag"},
        "team1": {"teamId": 761, "teamName": "Mexiko", "shortName": "MEX"},
        "team2": {"teamId": 3332, "teamName": "Südafrika", "shortName": "RSA"},
        "matchResults": [
            {"resultName": "Halbzeit", "pointsTeam1": 1, "pointsTeam2": 0},
            {"resultName": "Endergebnis", "pointsTeam1": 2, "pointsTeam2": 0},
        ],
    }
    match.update(overrides)
    return match


class OpenLigaDbSourceTest(unittest.TestCase):
    def _run_plugin(self, payload):
        storage = FakeStorage()
        context = WorkflowContext(
            project_root=Path("."),
            data_root=Path("."),
            storage=storage,
            run_id="run-openligadb",
        )
        plugin = OpenLigaDbSourcePlugin()

        def fake_fetch_json(self, endpoint, params=None, *, headers=None):
            return payload, {}

        with unittest.mock.patch.object(SourceRuntime, "fetch_json", fake_fetch_json):
            result = plugin.handle(EventName.FIXTURES_REQUESTED, context, {})

        rows = storage.datasets.get(TOURNAMENT_RESULTS, [])
        diagnostics = storage.datasets.get(EXTRACTION_DIAGNOSTICS, [])
        return result, rows, diagnostics, storage

    def test_finished_match_writes_tournament_result_row(self) -> None:
        result, rows, diagnostics, _storage = self._run_plugin([openligadb_match()])

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
        self.assertEqual(rows[0]["source"], "openligadb")
        self.assertEqual(diagnostics, [])

    def test_german_team_name_resolves_when_short_code_is_unknown(self) -> None:
        result, rows, diagnostics, _storage = self._run_plugin(
            [
                openligadb_match(
                    team2={"teamId": 141, "teamName": "Österreich", "shortName": "NOPE"},
                )
            ]
        )

        self.assertEqual(result.metadata["results"], 1)
        self.assertEqual(rows[0]["away_fifa_code"], "AUT")
        self.assertEqual(rows[0]["away_team"], "Austria")
        self.assertEqual(diagnostics, [])

    def test_unfinished_match_is_skipped(self) -> None:
        result, rows, diagnostics, _storage = self._run_plugin(
            [
                openligadb_match(),
                openligadb_match(
                    matchID=71002,
                    matchIsFinished=False,
                    matchResults=[{"resultName": "Halbzeit", "pointsTeam1": 0, "pointsTeam2": 0}],
                ),
            ]
        )

        self.assertEqual(result.metadata["results"], 1)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["fixture_key"], "2026-06-11T19:00:00Z|MEX|RSA")
        self.assertEqual(diagnostics, [])

    def test_empty_array_records_success_without_diagnostics(self) -> None:
        result, rows, diagnostics, storage = self._run_plugin([])

        self.assertEqual(result.metadata["results"], 0)
        self.assertTrue(result.metadata["empty_match_list"])
        self.assertEqual(rows, [])
        self.assertEqual(diagnostics, [])
        self.assertEqual(len(storage.ledger), 1)
        self.assertEqual(storage.ledger[0].status, "success")
        self.assertTrue(storage.ledger[0].metadata["empty_match_list"])

    def test_zero_usable_rows_from_nonempty_array_writes_diagnostic(self) -> None:
        result, rows, diagnostics, storage = self._run_plugin(
            [
                openligadb_match(
                    matchIsFinished=False,
                    matchResults=[{"resultName": "Halbzeit", "pointsTeam1": 1, "pointsTeam2": 0}],
                )
            ]
        )

        self.assertEqual(result.metadata["results"], 0)
        self.assertEqual(rows, [])
        self.assertEqual(len(diagnostics), 1)
        self.assertEqual(diagnostics[0]["source"], "openligadb")
        self.assertEqual(diagnostics[0]["extractor"], "openligadb_getmatchdata_v1")
        self.assertEqual(diagnostics[0]["status"], "rejected")
        self.assertEqual(diagnostics[0]["reason"], "no_results_extracted")
        self.assertEqual(diagnostics[0]["metadata"]["matches"], 1)
        self.assertEqual(storage.ledger[0].status, "success")

    def test_unresolvable_team_is_skipped_with_diagnostic(self) -> None:
        result, rows, diagnostics, _storage = self._run_plugin(
            [openligadb_match(team2={"teamId": 999, "teamName": "Atlantis Union", "shortName": None})]
        )

        self.assertEqual(result.metadata["results"], 0)
        self.assertEqual(rows, [])
        reasons = [diagnostic["reason"] for diagnostic in diagnostics]
        self.assertIn("unresolved_team", reasons)
        unresolved = next(diagnostic for diagnostic in diagnostics if diagnostic["reason"] == "unresolved_team")
        self.assertEqual(unresolved["source"], "openligadb")
        self.assertEqual(unresolved["status"], "rejected")
        self.assertIn("Atlantis Union", unresolved["metadata"]["unresolved_teams"])

    def test_parser_keeps_extra_time_and_penalty_metadata(self) -> None:
        results, extraction_rows = parse_openligadb_matches(
            [
                openligadb_match(
                    matchResults=[
                        {"resultName": "Halbzeit", "pointsTeam1": 0, "pointsTeam2": 0},
                        {"resultName": "Endergebnis", "pointsTeam1": 1, "pointsTeam2": 1},
                        {"resultName": "Verlängerung", "pointsTeam1": 1, "pointsTeam2": 1},
                        {"resultName": "Elfmeterschießen", "pointsTeam1": 4, "pointsTeam2": 3},
                    ]
                )
            ]
        )

        self.assertEqual(extraction_rows, [])
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0].score.as_text(), "1:1")
        self.assertEqual(results[0].metadata["extra_time_home"], 1)
        self.assertEqual(results[0].metadata["extra_time_away"], 1)
        self.assertEqual(results[0].metadata["penalty_home"], 4)
        self.assertEqual(results[0].metadata["penalty_away"], 3)


if __name__ == "__main__":
    unittest.main()
