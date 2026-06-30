from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

try:
    import duckdb  # noqa: F401
except ModuleNotFoundError:  # pragma: no cover
    duckdb = None

from worldcup_predictions.core.contracts import ScoreTip
from worldcup_predictions.core.datasets import CALIBRATION_DECISIONS, RESULT_UPDATE_AUDIT
from worldcup_predictions.core.plugin import PluginManager
from worldcup_predictions.core.workflow import PredictionWorkflow, result_row_changes
from worldcup_predictions.plugins.live_calibration import LiveCalibrationPlugin
from worldcup_predictions.plugins.result_monitoring import ResultMonitoringPlugin
from worldcup_predictions.plugins.tournament_state import TournamentStatePlugin
from worldcup_predictions.storage import DuckDBStorage
from worldcup_predictions.tournament import FixtureRecord, ResultRecord, TeamResolver
from worldcup_predictions.tournament.repository import write_fixtures, write_results


class ResultUpdateMonitoringTest(unittest.TestCase):
    def test_result_row_changes_detects_new_and_changed_scores(self) -> None:
        previous = [
            {
                "record_key": "a",
                "fixture_key": "fixture",
                "event_date": "2026-06-13T18:00:00Z",
                "home_team": "Brazil",
                "away_team": "Japan",
                "home_score": 1,
                "away_score": 0,
                "status": "final",
            }
        ]
        current = [
            {**previous[0], "home_score": 2},
            {
                "record_key": "b",
                "fixture_key": "fixture-2",
                "event_date": "2026-06-14T18:00:00Z",
                "home_team": "Germany",
                "away_team": "Paraguay",
                "home_score": 0,
                "away_score": 0,
                "status": "final",
            },
        ]

        new_results, changed_results = result_row_changes(previous, current)

        self.assertEqual(len(new_results), 1)
        self.assertEqual(new_results[0]["current"]["record_key"], "b")
        self.assertEqual(len(changed_results), 1)
        self.assertEqual(changed_results[0]["previous"]["home_score"], 1)
        self.assertEqual(changed_results[0]["current"]["home_score"], 2)

    @unittest.skipIf(duckdb is None, "duckdb dependency is not installed")
    def test_result_update_event_waits_for_confirmed_consensus(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            project_root = Path(tmp)
            storage = DuckDBStorage.at_data_root(project_root / "data")
            resolver = TeamResolver.default()
            fixture = FixtureRecord(
                event_date="2026-06-13T18:00:00Z",
                home_team=resolver.resolve("Brazil"),
                away_team=resolver.resolve("Japan"),
                group="Group A",
                stage="Group Stage",
            )
            write_fixtures(storage, [fixture], source="test")
            workflow = PredictionWorkflow.from_project_root(
                project_root,
                PluginManager([TournamentStatePlugin(), ResultMonitoringPlugin(), LiveCalibrationPlugin()]),
            )
            previous_results = workflow.latest_result_rows()

            unconfirmed = ResultRecord(
                event_date=fixture.event_date,
                home_team=fixture.home_team,
                away_team=fixture.away_team,
                score=ScoreTip(2, 0),
                source="openfootball_worldcup",
            )
            write_results(workflow.context.storage, [unconfirmed], source="test_source", run_id=workflow.context.run_id)
            workflow.emit_results_updated_if_changed(previous_results, source_event="source_result_write")

            self.assertEqual(workflow.context.storage.read_records(RESULT_UPDATE_AUDIT), [])
            self.assertEqual(workflow.context.storage.read_records(CALIBRATION_DECISIONS, latest_only=True), [])

            confirmed_sources = [
                ResultRecord(
                    event_date=fixture.event_date,
                    home_team=fixture.home_team,
                    away_team=fixture.away_team,
                    score=ScoreTip(2, 0),
                    source=source,
                )
                for source in ("srf_public", "football_data_org")
            ]
            write_results(workflow.context.storage, confirmed_sources, source="test_source", run_id=workflow.context.run_id)
            workflow.emit_results_updated_if_changed(previous_results, source_event="source_result_write")

            result_audit_rows = workflow.context.storage.read_records(RESULT_UPDATE_AUDIT)
            decision_rows = workflow.context.storage.read_records(CALIBRATION_DECISIONS, latest_only=True)

            self.assertEqual(len(result_audit_rows), 1)
            self.assertEqual(result_audit_rows[0]["fixture_key"], fixture.key)
            self.assertEqual(result_audit_rows[0]["current_score"], "2:0")
            self.assertEqual(result_audit_rows[0]["update_type"], "new")
            self.assertIn("live_calibration_weight_recommendation", {row["parameter"] for row in decision_rows})
            weight_row = next(row for row in decision_rows if row["parameter"] == "live_calibration_weight_recommendation")
            self.assertEqual(weight_row["action"], "initialized")
            self.assertTrue(weight_row["metadata"]["report_only"])


if __name__ == "__main__":
    unittest.main()
