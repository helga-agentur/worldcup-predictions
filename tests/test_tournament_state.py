from __future__ import annotations

import datetime as dt
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
from worldcup_predictions.plugins.workflow.tournament_state import TournamentStatePlugin
from worldcup_predictions.storage import DuckDBStorage
from worldcup_predictions.tournament import (
    FixtureRecord,
    ResultRecord,
    TeamResolver,
    build_group_state_rows,
    build_tournament_state,
    canonical_slot_code,
    has_defined_teams,
    parse_openfootball_text,
    slot_display_name,
)
from worldcup_predictions.tournament.repository import load_active_fixture_rows, load_tournament_state, write_fixtures, write_results


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

    def test_fixture_record_key_uses_stable_source_slot_when_available(self) -> None:
        resolver = TeamResolver.default()
        old_placeholder = FixtureRecord(
            event_date="2026-07-04T17:00:00Z",
            home_team=resolver.resolve("Canada"),
            away_team=resolver.resolve("W75"),
            source_id="75",
            metadata={"source": "srf_public"},
        )
        renamed_placeholder = FixtureRecord(
            event_date="2026-07-04T17:00:00Z",
            home_team=resolver.resolve("Canada"),
            away_team=resolver.resolve("Sieger Sechzehntelfinal 4"),
            source_id="75",
            metadata={"source": "srf_public"},
        )

        self.assertNotEqual(old_placeholder.key, renamed_placeholder.key)
        self.assertEqual(old_placeholder.record_key, renamed_placeholder.record_key)

    def test_team_resolver_normalizes_winner_slots(self) -> None:
        resolver = TeamResolver.default()

        direct = resolver.resolve("W75")
        prose = resolver.resolve("Winner Match 75")
        german_round = resolver.resolve("Sieger Sechzehntelfinal 4")
        english_round = resolver.resolve("Winner Round of 32 match 4")

        self.assertEqual(direct.name, "W75")
        self.assertIsNone(direct.fifa_code)
        self.assertEqual(direct.key, "W75")
        self.assertEqual(prose.key, "W75")
        self.assertEqual(german_round.key, "W76")
        self.assertEqual(english_round.key, "W76")
        self.assertEqual(canonical_slot_code("Verlierer Halbfinal 2"), "RU102")
        self.assertEqual(canonical_slot_code("Loser Match 101"), "RU101")
        self.assertEqual(canonical_slot_code("RU101"), "RU101")
        self.assertEqual(canonical_slot_code("L101"), "")

    def test_slot_display_names_include_knockout_round(self) -> None:
        self.assertEqual(slot_display_name("W76"), "Sieger Sechzehntelfinal 4")
        self.assertEqual(slot_display_name("W76", locale="en"), "Winner Round of 32 match 4")
        self.assertEqual(slot_display_name("W102"), "Sieger Halbfinal 2")
        self.assertEqual(slot_display_name("W102", locale="en"), "Winner Semi-final match 2")
        self.assertEqual(slot_display_name("RU101"), "Verlierer Halbfinal 1")
        self.assertEqual(slot_display_name("RU101", locale="en"), "Loser Semi-final match 1")
        self.assertEqual(slot_display_name("W10"), "Sieger Spiel 10")

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

    def test_scraped_source_family_counts_as_one_witness(self) -> None:
        resolver = TeamResolver.default()
        fixture = FixtureRecord(
            event_date="2026-07-05T20:00:00Z",
            home_team=resolver.resolve("Brazil"),
            away_team=resolver.resolve("Norway"),
            stage="Round of 16",
        )
        wrong = [
            ResultRecord(fixture.event_date, fixture.home_team, fixture.away_team, ScoreTip(3, 2), source=f"dynamic_public:{domain}")
            for domain in ("goal.com", "apnews.com", "livescore.com", "nbcsports.com", "theanalyst.com", "onefootball.com")
        ]
        right = [
            ResultRecord(fixture.event_date, fixture.home_team, fixture.away_team, ScoreTip(1, 2), source=source)
            for source in ("srf_public", "fifa_match_centre", "football_data_org", "openfootball/worldcup:cup_finals.txt")
        ]

        state = build_tournament_state([fixture], [*wrong, *right])

        self.assertEqual(len(state.results), 1)
        self.assertEqual(state.results[0].score.as_text(), "1:2")
        self.assertEqual(state.result_checks[0]["selected_score"], "1:2")

    def test_scraped_domains_alone_do_not_confirm_a_result(self) -> None:
        resolver = TeamResolver.default()
        fixture = FixtureRecord(
            event_date="2026-07-05T20:00:00Z",
            home_team=resolver.resolve("Brazil"),
            away_team=resolver.resolve("Norway"),
            stage="Round of 16",
        )
        results = [
            ResultRecord(fixture.event_date, fixture.home_team, fixture.away_team, ScoreTip(3, 2), source=f"dynamic_public:{domain}")
            for domain in ("goal.com", "apnews.com", "livescore.com", "nbcsports.com")
        ]

        state = build_tournament_state([fixture], results)

        self.assertEqual(state.results, [])
        self.assertEqual(state.result_checks[0]["status"], "unconfirmed")

    def test_result_kickoff_variants_share_one_consensus_pool(self) -> None:
        resolver = TeamResolver.default()
        canonical = FixtureRecord(
            event_date="2026-07-06T01:00:00Z",
            home_team=resolver.resolve("Mexico"),
            away_team=resolver.resolve("England"),
            stage="Round of 16",
            metadata={"source": "fifa_match_centre"},
        )
        drifted = FixtureRecord(
            event_date="2026-07-06T00:00:00Z",
            home_team=resolver.resolve("Mexico"),
            away_team=resolver.resolve("England"),
            stage="Round of 16",
            metadata={"source": "openfootball/worldcup"},
        )
        results = [
            ResultRecord("2026-07-06T01:00:00Z", canonical.home_team, canonical.away_team, ScoreTip(2, 3), source="srf_public"),
            ResultRecord("2026-07-06T01:00:00Z", canonical.home_team, canonical.away_team, ScoreTip(2, 3), source="fifa_match_centre"),
            ResultRecord("2026-07-06T00:00:00Z", canonical.home_team, canonical.away_team, ScoreTip(2, 3), source="openfootball/worldcup:cup_finals.txt"),
            ResultRecord("2026-07-06T00:00:00Z", canonical.home_team, canonical.away_team, ScoreTip(3, 2), source="dynamic_public:apnews.com"),
            ResultRecord("2026-07-06T00:00:00Z", canonical.home_team, canonical.away_team, ScoreTip(3, 2), source="dynamic_public:koreatimes.co.kr"),
            ResultRecord("2026-07-06T00:00:00Z", canonical.home_team, canonical.away_team, ScoreTip(3, 2), source="dynamic_public:nbcsports.com"),
        ]

        state = build_tournament_state([canonical, drifted], results)

        self.assertEqual(len(state.results), 1)
        confirmed = state.results[0]
        self.assertEqual(confirmed.event_date, "2026-07-06T01:00:00Z")
        self.assertEqual(confirmed.score.as_text(), "2:3")
        confirmed_keys = {check["fixture_key"] for check in state.result_checks if check["status"] == "confirmed"}
        self.assertEqual(len(confirmed_keys), 1)

    def test_future_result_consensus_is_ignored_until_kickoff(self) -> None:
        resolver = TeamResolver.default()
        fixture = FixtureRecord(
            event_date="2099-07-11T21:00:00Z",
            home_team=resolver.resolve("Norway"),
            away_team=resolver.resolve("England"),
            stage="Quarter-final",
        )
        results = [
            ResultRecord(fixture.event_date, fixture.home_team, fixture.away_team, ScoreTip(3, 2), source=source)
            for source in ("srf_public", "football_data_org")
        ]

        state = build_tournament_state(
            [fixture],
            results,
            now=dt.datetime(2026, 7, 8, 12, tzinfo=dt.timezone.utc),
        )

        self.assertEqual(state.results, [])
        self.assertEqual(state.result_checks[0]["status"], "future_result_ignored")
        self.assertEqual(state.result_checks[0]["selected_score"], "")
        self.assertEqual(state.result_checks[0]["candidate_score"], "3:2")
        self.assertEqual(state.result_checks[0]["ignored_future_source_count"], 2)

    def test_tournament_state_canonicalizes_unresolved_knockout_slots(self) -> None:
        resolver = TeamResolver.default()
        round_of_32 = FixtureRecord(
            event_date="2026-06-29T17:00:00Z",
            home_team=resolver.resolve("Paraguay"),
            away_team=resolver.resolve("Japan"),
            stage="Round of 32",
            source_id="74",
            status="final",
            metadata={"source": "fifa_match_centre", "match_number": "74"},
        )
        source_slot = FixtureRecord(
            event_date="2026-07-04T21:00:00Z",
            home_team=resolver.resolve("W74"),
            away_team=resolver.resolve("W77"),
            stage="Round of 16",
            source_id="89",
            metadata={"source": "openfootball/worldcup:cup_finals.txt", "match_number": "89"},
        )
        provider_slot = FixtureRecord(
            event_date="2026-07-04T21:00:00Z",
            home_team=resolver.resolve("Paraguay"),
            away_team=resolver.resolve("Sieger Sechzehntelfinal 5"),
            stage="knockout",
            metadata={"source": "srf_public"},
        )
        results = [
            ResultRecord(round_of_32.event_date, round_of_32.home_team, round_of_32.away_team, ScoreTip(1, 0), source=source)
            for source in ("srf_public", "fifa_match_centre")
        ]

        state = build_tournament_state([round_of_32, source_slot, provider_slot], results)
        fixture_keys = {fixture.key for fixture in state.fixtures}
        canonical_fixture = next(fixture for fixture in state.fixtures if fixture.key.endswith("|PAR|W77"))

        self.assertIn("2026-07-04T21:00:00Z|PAR|W77", fixture_keys)
        self.assertNotIn("2026-07-04T21:00:00Z|W74|W77", fixture_keys)
        self.assertNotIn("2026-07-04T21:00:00Z|PAR|Sieger Sechzehntelfinal 5", fixture_keys)
        self.assertFalse(has_defined_teams(canonical_fixture))
        self.assertFalse(any(fixture.key.endswith("|PAR|W77") for fixture in state.open_fixtures()))

    def test_tournament_state_preserves_official_placeholder_order(self) -> None:
        resolver = TeamResolver.default()
        official_slot = FixtureRecord(
            event_date="2026-07-06T19:00:00Z",
            home_team=resolver.resolve("W83"),
            away_team=resolver.resolve("W84"),
            stage="Round of 16",
            source_id="93",
            metadata={"source": "fifa_match_centre", "match_number": "93"},
        )
        reversed_provider_slot = FixtureRecord(
            event_date="2026-07-06T19:00:00Z",
            home_team=resolver.resolve("W84"),
            away_team=resolver.resolve("W83"),
            stage="knockout",
            metadata={"source": "srf_public"},
        )

        state = build_tournament_state([reversed_provider_slot, official_slot], [])

        self.assertEqual([fixture.key for fixture in state.fixtures], ["2026-07-06T19:00:00Z|W83|W84"])

    def test_tournament_state_rejects_impossible_duplicate_side_fixtures(self) -> None:
        resolver = TeamResolver.default()
        fake_fixture = FixtureRecord(
            event_date="2026-07-07T00:00:00Z",
            home_team=resolver.resolve("Belgium"),
            away_team=resolver.resolve("Belgium"),
            stage="Round of 16",
            metadata={"source": "football_data"},
        )
        official_fixture = FixtureRecord(
            event_date="2026-07-07T00:00:00Z",
            home_team=resolver.resolve("W81"),
            away_team=resolver.resolve("W82"),
            stage="Round of 16",
            metadata={"source": "fifa_match_centre", "match_number": "94"},
        )

        state = build_tournament_state([fake_fixture, official_fixture], [])

        self.assertEqual([fixture.key for fixture in state.fixtures], ["2026-07-07T00:00:00Z|W81|W82"])

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

    @unittest.skipIf(duckdb is None, "duckdb dependency is not installed")
    def test_active_fixture_rows_ignore_stale_source_batches(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            storage = DuckDBStorage.at_data_root(Path(tmp) / "data")
            resolver = TeamResolver.default()
            stale = FixtureRecord(
                event_date="2026-07-04T17:00:00Z",
                home_team=resolver.resolve("Canada"),
                away_team=resolver.resolve("W75"),
                stage="knockout",
                status="open",
                metadata={"source": "srf_public"},
            )
            current = FixtureRecord(
                event_date="2026-07-04T17:00:00Z",
                home_team=resolver.resolve("Canada"),
                away_team=resolver.resolve("Sieger Sechzehntelfinal 4"),
                stage="knockout",
                status="open",
                metadata={"source": "srf_public"},
            )
            write_fixtures(storage, [stale], source="srf_public", run_id="old")
            con = storage._connect()
            try:
                con.execute(
                    """
                    UPDATE structured_records
                    SET observed_at_utc = '2026-01-01T00:00:00Z'
                    WHERE dataset = 'tournament_fixtures' AND source = 'srf_public'
                    """
                )
            finally:
                con.close()
            write_fixtures(storage, [current], source="srf_public", run_id="current")

            rows = load_active_fixture_rows(storage)

            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]["away_team"], "W76")


if __name__ == "__main__":
    unittest.main()
