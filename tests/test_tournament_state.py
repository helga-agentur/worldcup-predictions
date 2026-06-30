from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

try:
    import duckdb  # noqa: F401
except ModuleNotFoundError:  # pragma: no cover
    duckdb = None

from worldcup_predictions.core.contracts import ScoreTip
from worldcup_predictions.core.plugin import PluginManager
from worldcup_predictions.core.workflow import PredictionWorkflow
from worldcup_predictions.plugins.tournament_state import TournamentStatePlugin
from worldcup_predictions.storage import DuckDBStorage
from worldcup_predictions.tournament import (
    FixtureRecord,
    ResultRecord,
    TeamResolver,
    build_group_state_rows,
    build_tournament_state,
    parse_openfootball_text,
)
from worldcup_predictions.tournament.repository import load_tournament_state, write_fixtures, write_results


OPENFOOTBALL_SAMPLE = """
\u25aa Group A
Sat Jun 13
  (1) 13:00 UTC-5 Brazil 2-0 Japan @ Test Stadium
  (2) 16:00 UTC-5 Brazil v Scotland @ Test Stadium
"""


class TournamentStateTest(unittest.TestCase):
    def test_prediction_fixture_key_prefers_canonical_fifa_codes(self) -> None:
        resolver = TeamResolver.default()
        fixture = FixtureRecord(
            event_date="2026-06-29T20:30:00Z",
            home_team=resolver.resolve("Germany"),
            away_team=resolver.resolve("Paraguay"),
            stage="Group Stage",
        ).to_fixture()

        self.assertEqual(fixture.key, "2026-06-29T20:30:00Z|GER|PAR")

    def test_openfootball_parser_returns_canonical_fixtures_and_results(self) -> None:
        fixtures, results = parse_openfootball_text(OPENFOOTBALL_SAMPLE)

        self.assertEqual(len(fixtures), 2)
        self.assertEqual(len(results), 1)
        self.assertEqual(fixtures[0].home_team.fifa_code, "BRA")
        self.assertEqual(fixtures[0].away_team.fifa_code, "JPN")
        self.assertEqual(fixtures[0].event_date, "2026-06-13T18:00:00Z")
        self.assertEqual(results[0].score, ScoreTip(2, 0))

    def test_tournament_state_builds_standings_and_group_signals(self) -> None:
        resolver = TeamResolver.default()
        fixtures = [
            FixtureRecord(
                event_date="2026-06-13T18:00:00Z",
                home_team=resolver.resolve("Brazil"),
                away_team=resolver.resolve("Japan"),
                group="Group A",
                stage="Group Stage",
            ),
            FixtureRecord(
                event_date="2026-06-17T18:00:00Z",
                home_team=resolver.resolve("Brazil"),
                away_team=resolver.resolve("Scotland"),
                group="Group A",
                stage="Group Stage",
            ),
        ]
        results = [
            ResultRecord(
                event_date=fixtures[0].event_date,
                home_team=fixtures[0].home_team,
                away_team=fixtures[0].away_team,
                score=ScoreTip(2, 0),
                source=source,
            )
            for source in ("srf_public", "football_data_org")
        ]

        state = build_tournament_state(fixtures, results)
        rows = build_group_state_rows(state)

        self.assertEqual(state.standings["A"][0].team.fifa_code, "BRA")
        self.assertEqual(state.standings["A"][0].points, 3)
        self.assertEqual(len(rows), 2)
        self.assertEqual({row["fixture_key"] for row in rows}, {fixtures[1].key})

    def test_single_source_result_stays_internal_until_confirmed(self) -> None:
        resolver = TeamResolver.default()
        fixture = FixtureRecord(
            event_date="2026-06-13T18:00:00Z",
            home_team=resolver.resolve("Brazil"),
            away_team=resolver.resolve("Japan"),
            group="Group A",
            stage="Group Stage",
        )
        result = ResultRecord(
            event_date=fixture.event_date,
            home_team=fixture.home_team,
            away_team=fixture.away_team,
            score=ScoreTip(2, 0),
            source="openfootball_worldcup",
        )

        state = build_tournament_state([fixture], [result])

        self.assertEqual(state.results, [])
        self.assertEqual(state.result_checks[0]["status"], "unconfirmed")
        self.assertEqual(state.result_checks[0]["selected_score"], "")
        self.assertEqual(state.result_checks[0]["candidate_score"], "2:0")

    def test_three_matching_sources_confirm_result(self) -> None:
        resolver = TeamResolver.default()
        fixture = FixtureRecord(
            event_date="2026-06-13T18:00:00Z",
            home_team=resolver.resolve("Brazil"),
            away_team=resolver.resolve("Japan"),
            group="Group A",
            stage="Group Stage",
        )
        results = [
            ResultRecord(fixture.event_date, fixture.home_team, fixture.away_team, ScoreTip(2, 0), source=source)
            for source in ("openfootball_worldcup", "fotmob_public", "sofascore_public")
        ]

        state = build_tournament_state([fixture], results)

        self.assertEqual(len(state.results), 1)
        self.assertEqual(state.results[0].score, ScoreTip(2, 0))
        self.assertEqual(state.result_checks[0]["status"], "confirmed")
        self.assertEqual(state.result_checks[0]["confirmed_source_count"], 3)

    def test_two_high_authority_sources_confirm_result(self) -> None:
        resolver = TeamResolver.default()
        fixture = FixtureRecord(
            event_date="2026-06-13T18:00:00Z",
            home_team=resolver.resolve("Brazil"),
            away_team=resolver.resolve("Japan"),
            group="Group A",
            stage="Group Stage",
        )
        results = [
            ResultRecord(fixture.event_date, fixture.home_team, fixture.away_team, ScoreTip(2, 0), source=source)
            for source in ("srf_public", "football_data_org")
        ]

        state = build_tournament_state([fixture], results)

        self.assertEqual(len(state.results), 1)
        self.assertEqual(state.result_checks[0]["status"], "confirmed")
        self.assertEqual(state.result_checks[0]["high_authority_source_count"], 2)

    def test_conflicting_unconfirmed_sources_do_not_enter_state(self) -> None:
        resolver = TeamResolver.default()
        fixture = FixtureRecord(
            event_date="2026-06-13T18:00:00Z",
            home_team=resolver.resolve("Brazil"),
            away_team=resolver.resolve("Japan"),
            group="Group A",
            stage="Group Stage",
        )
        results = [
            ResultRecord(fixture.event_date, fixture.home_team, fixture.away_team, ScoreTip(2, 0), source="openfootball_worldcup"),
            ResultRecord(fixture.event_date, fixture.home_team, fixture.away_team, ScoreTip(1, 0), source="fotmob_public"),
        ]

        state = build_tournament_state([fixture], results)

        self.assertEqual(state.results, [])
        self.assertEqual(state.result_checks[0]["status"], "unconfirmed_conflict")

    @unittest.skipIf(duckdb is None, "duckdb dependency is not installed")
    def test_tournament_plugin_loads_confirmed_source_results_into_storage(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            storage = DuckDBStorage.at_data_root(Path(tmp) / "data")
            resolver = TeamResolver.default()
            fixture = FixtureRecord(
                event_date="2026-06-13T18:00:00Z",
                home_team=resolver.resolve("Brazil"),
                away_team=resolver.resolve("Japan"),
                group="Group A",
                stage="Group Stage",
            )
            write_fixtures(storage, [fixture], source="test")
            result = ResultRecord(
                event_date=fixture.event_date,
                home_team=fixture.home_team,
                away_team=fixture.away_team,
                score=ScoreTip(2, 0),
                source="test_source",
            )
            results = [
                ResultRecord(
                    event_date=result.event_date,
                    home_team=result.home_team,
                    away_team=result.away_team,
                    score=result.score,
                    source=source,
                )
                for source in ("srf_public", "football_data_org")
            ]
            write_results(storage, results, source="test_source")
            workflow = PredictionWorkflow.from_project_root(
                Path(tmp),
                PluginManager([TournamentStatePlugin()]),
            )

            workflow.manager.emit("workflow_started", workflow.context, {})
            state = load_tournament_state(workflow.context.storage)

            self.assertEqual(len(state.results), 1)
            self.assertEqual(state.standings["A"][0].points, 3)


if __name__ == "__main__":
    unittest.main()
