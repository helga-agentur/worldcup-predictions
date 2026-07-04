from __future__ import annotations

import unittest

from worldcup_predictions.plugins.article_sources import extract_postmatch_stats_from_text, source_reliability, stat_row_from_public_analysis
from worldcup_predictions.plugins.fifa_match_centre import (
    parse_fifa_match_details,
    parse_fifa_match_fixtures,
    parse_fifa_match_results,
)
from worldcup_predictions.plugins.lineup_availability.plugin import (
    classify_availability_signal,
    fifa_match_detail_formation_rows,
    lineup_consensus_rows,
    lineup_availability_rows_from_articles,
    lineup_availability_signals_from_rows,
)
from worldcup_predictions.plugins.market_odds.plugin import market_signals_from_rows, odds_api_event_ids, odds_api_rows
from worldcup_predictions.plugins.public_analysis.plugin import (
    classify_tempo_signal,
    public_analysis_rows_from_articles,
    public_analysis_signals_from_rows,
)
from worldcup_predictions.tournament import FixtureRecord, TeamResolver

import datetime as dt
import os
import tempfile
import unittest.mock
from pathlib import Path

from worldcup_predictions.core.datasets import EXTRACTION_DIAGNOSTICS, TOURNAMENT_FIXTURES
from worldcup_predictions.core.events import EventName
from worldcup_predictions.core.workflow import WorkflowContext
from worldcup_predictions.plugins.football_data.plugin import FootballDataPlugin
from worldcup_predictions.plugins.source_runtime import SourceRuntime
from worldcup_predictions.plugins.srf_experts.plugin import SrfExpertsPlugin
from worldcup_predictions.storage import DuckDBStorage
from worldcup_predictions.tournament import build_tournament_state


def fixture() -> FixtureRecord:
    resolver = TeamResolver.default()
    return FixtureRecord(
        event_date="2026-07-10T18:00:00Z",
        home_team=resolver.resolve("Brazil"),
        away_team=resolver.resolve("Japan"),
        group="Group A",
        stage="Group Stage",
    )


def article(title: str, description: str, *, url: str = "https://www.bbc.com/sport/football/test"):
    return {
        "source": {"name": "BBC Sport"},
        "url": url,
        "publishedAt": "2026-07-10T09:00:00Z",
        "title": title,
        "description": description,
    }


class PublicSourcesTest(unittest.TestCase):
    def test_source_reliability_scores_known_domains(self) -> None:
        self.assertGreater(source_reliability("https://www.reuters.com/sports/soccer/story"), 0.85)
        self.assertLess(source_reliability("https://example-blog.invalid/story"), 0.70)

    def test_public_analysis_extracts_tempo_signal(self) -> None:
        rows = public_analysis_rows_from_articles(
            [article("Brazil Japan preview", "A tight game is expected with both teams compact.")],
            fixture(),
            phase="pregame",
        )

        signals = public_analysis_signals_from_rows(rows)

        self.assertEqual(len(rows), 1)
        self.assertEqual(classify_tempo_signal("this could be a high scoring open game")[0], "high_tempo_or_attacking")
        self.assertEqual(signals[0].name, "total_goals_factor")
        self.assertLess(signals[0].value, 1.0)

    def test_public_analysis_extracts_postmatch_stats_from_articles(self) -> None:
        rows = public_analysis_rows_from_articles(
            [
                article(
                    "Brazil Japan report",
                    "Brazil edged Japan after xG 1.8-0.7, shots on target 5-2, shots 14-6 and corners 4-1.",
                )
            ],
            fixture(),
            phase="postgame",
        )

        stat_row = stat_row_from_public_analysis(rows[0])

        self.assertEqual(rows[0]["postmatch_stats"]["home_xg"], 1.8)
        self.assertEqual(rows[0]["postmatch_stats"]["away_shots_on_target"], 2.0)
        self.assertEqual(stat_row["home_shots"], 14.0)
        self.assertEqual(extract_postmatch_stats_from_text("xG 2.2-1.1 shots 10-8", fixture())["away_xg"], 1.1)

    def test_fifa_match_centre_parses_official_fixture_result_and_details(self) -> None:
        rows = [
            {
                "IdCompetition": "17",
                "IdSeason": "285023",
                "IdStage": "289273",
                "IdGroup": "289275",
                "IdMatch": "400021443",
                "MatchNumber": 1,
                "Date": "2026-06-11T19:00:00Z",
                "Home": {
                    "Score": 2,
                    "IdTeam": "43911",
                    "IdCountry": "MEX",
                    "Tactics": "4-1-2-3",
                    "TeamName": [{"Locale": "en-GB", "Description": "Mexico"}],
                    "Abbreviation": "MEX",
                    "IdAssociation": "MEX",
                },
                "Away": {
                    "Score": 0,
                    "IdTeam": "43883",
                    "IdCountry": "RSA",
                    "Tactics": "5-3-2",
                    "TeamName": [{"Locale": "en-GB", "Description": "South Africa"}],
                    "Abbreviation": "RSA",
                    "IdAssociation": "RSA",
                },
                "HomeTeamScore": 2,
                "AwayTeamScore": 0,
                "Attendance": "80824",
                "StageName": [{"Locale": "en-GB", "Description": "First Stage"}],
                "GroupName": [{"Locale": "en-GB", "Description": "Group A"}],
                "CompetitionName": [{"Locale": "en-GB", "Description": "FIFA World Cup™"}],
                "SeasonName": [{"Locale": "en-GB", "Description": "FIFA World Cup 2026™"}],
                "Stadium": {
                    "Name": [{"Locale": "en-GB", "Description": "Mexico City Stadium"}],
                    "CityName": [{"Locale": "en-GB", "Description": "Mexico City"}],
                    "IdCountry": "MEX",
                },
                "Officials": [
                    {
                        "OfficialId": "361561",
                        "IdCountry": "BRA",
                        "OfficialType": 1,
                        "Name": [{"Locale": "en-GB", "Description": "Wilton SAMPAIO"}],
                        "TypeLocalized": [{"Locale": "en-GB", "Description": "Referee"}],
                    }
                ],
                "OfficialityStatus": 1,
                "ResultType": 1,
                "MatchStatus": 0,
            }
        ]

        fixtures = parse_fifa_match_fixtures(rows)
        results = parse_fifa_match_results(rows)
        details = parse_fifa_match_details(rows)
        formation_rows = fifa_match_detail_formation_rows(details)

        self.assertEqual(fixtures[0].home_team.fifa_code, "MEX")
        self.assertEqual(fixtures[0].venue, "Mexico City Stadium")
        self.assertEqual(results[0].score.as_text(), "2:0")
        self.assertEqual(results[0].source, "fifa_match_centre")
        self.assertEqual(details[0]["home_tactics"], "4-1-2-3")
        self.assertEqual(details[0]["referee"], "Wilton SAMPAIO")
        self.assertEqual(formation_rows[0]["signal_type"], "official_formation_available")

    def test_fifa_match_centre_parses_official_placeholder_sides(self) -> None:
        rows = [
            {
                "IdCompetition": "17",
                "IdSeason": "285023",
                "IdMatch": "400021534",
                "MatchNumber": 92,
                "Date": "2026-07-06T00:00:00Z",
                "Home": {
                    "IdCountry": "MEX",
                    "TeamName": [{"Locale": "en-GB", "Description": "Mexico"}],
                    "Abbreviation": "MEX",
                },
                "Away": None,
                "PlaceHolderA": "W79",
                "PlaceHolderB": "W80",
                "StageName": [{"Locale": "en-GB", "Description": "Round of 16"}],
                "Stadium": {"Name": [{"Locale": "en-GB", "Description": "Mexico City Stadium"}]},
                "MatchStatus": 1,
            }
        ]

        fixtures = parse_fifa_match_fixtures(rows)
        details = parse_fifa_match_details(rows)

        self.assertEqual(fixtures[0].key, "2026-07-06T00:00:00Z|MEX|W80")
        self.assertEqual(fixtures[0].home_team.fifa_code, "MEX")
        self.assertEqual(fixtures[0].away_team.name, "W80")
        self.assertIsNone(fixtures[0].away_team.fifa_code)
        self.assertEqual(details[0]["fixture_key"], "2026-07-06T00:00:00Z|MEX|W80")

    def test_fifa_match_centre_keeps_official_runner_up_placeholder_ids(self) -> None:
        rows = [
            {
                "IdCompetition": "17",
                "IdSeason": "285023",
                "IdMatch": "400021545",
                "MatchNumber": 103,
                "Date": "2026-07-18T21:00:00Z",
                "Home": None,
                "Away": None,
                "PlaceHolderA": "RU101",
                "PlaceHolderB": "RU102",
                "StageName": [{"Locale": "en-GB", "Description": "Play-off for third place"}],
                "MatchStatus": 1,
            }
        ]

        fixtures = parse_fifa_match_fixtures(rows)
        details = parse_fifa_match_details(rows)

        self.assertEqual(fixtures[0].key, "2026-07-18T21:00:00Z|RU101|RU102")
        self.assertEqual(fixtures[0].home_team.key, "RU101")
        self.assertEqual(fixtures[0].away_team.key, "RU102")
        self.assertEqual(details[0]["fixture_key"], "2026-07-18T21:00:00Z|RU101|RU102")

    def test_lineup_availability_extracts_side_specific_signal(self) -> None:
        rows = lineup_availability_rows_from_articles(
            [article("Brazil team news", "Brazil forward is doubtful after an injury in training.")],
            fixture(),
        )

        signals = lineup_availability_signals_from_rows(rows)

        self.assertEqual(len(rows), 1)
        self.assertEqual(classify_availability_signal("player is suspended")[0], "suspension_risk")
        self.assertEqual(signals[0].name, "team_expected_goals_factor")
        self.assertEqual(signals[0].metadata["side"], "home")
        self.assertLess(signals[0].value, 1.0)

    def test_lineup_consensus_summarizes_reliable_evidence(self) -> None:
        rows = lineup_availability_rows_from_articles(
            [
                article("Brazil team news", "Brazil forward is doubtful after an injury in training."),
                article("Brazil injury update", "Brazil attacker is doubtful and faces a fitness test."),
            ],
            fixture(),
        )

        consensus = lineup_consensus_rows(rows)

        self.assertEqual(len(consensus), 1)
        self.assertEqual(consensus[0]["affected_side"], "home")
        self.assertEqual(consensus[0]["reliable_evidence_count"], 2)
        self.assertEqual(consensus[0]["status"], "actionable_consensus")

    def test_odds_api_rows_extract_near_term_event_markets(self) -> None:
        match = fixture()
        event = {
            "id": "evt_1",
            "home_team": "Brazil",
            "away_team": "Japan",
            "bookmakers": [
                {
                    "key": "sample",
                    "markets": [
                        {
                            "key": "h2h",
                            "outcomes": [
                                {"name": "Brazil", "price": 1.8},
                                {"name": "Draw", "price": 3.4},
                                {"name": "Japan", "price": 4.5},
                            ],
                        },
                        {
                            "key": "totals",
                            "outcomes": [
                                {"name": "Over", "point": 2.5, "price": 1.95},
                                {"name": "Under", "point": 2.5, "price": 1.91},
                            ],
                        },
                        {
                            "key": "spreads",
                            "outcomes": [
                                {"name": "Brazil", "point": -0.5, "price": 1.9},
                                {"name": "Japan", "point": 0.5, "price": 1.9},
                            ],
                        },
                        {
                            "key": "draw_no_bet",
                            "outcomes": [
                                {"name": "Brazil", "price": 1.4},
                                {"name": "Japan", "price": 2.9},
                            ],
                        },
                        {
                            "key": "btts",
                            "outcomes": [
                                {"name": "Yes", "price": 1.7},
                                {"name": "No", "price": 2.1},
                            ],
                        },
                        {
                            "key": "team_totals",
                            "outcomes": [
                                {"name": "Over", "description": "Brazil", "point": 1.5, "price": 1.9},
                                {"name": "Under", "description": "Brazil", "point": 1.5, "price": 1.9},
                                {"name": "Over", "description": "Japan", "point": 0.5, "price": 1.8},
                                {"name": "Under", "description": "Japan", "point": 0.5, "price": 2.0},
                            ],
                        },
                        {
                            "key": "alternate_totals",
                            "outcomes": [
                                {"name": "Over", "point": 1.5, "price": 1.3},
                                {"name": "Under", "point": 1.5, "price": 3.4},
                                {"name": "Over", "point": 2.5, "price": 1.95},
                                {"name": "Under", "point": 2.5, "price": 1.95},
                            ],
                        },
                    ],
                }
            ],
        }

        rows = odds_api_rows([event], [match])
        event_ids = odds_api_event_ids([event], [match])
        signals = market_signals_from_rows(rows)

        self.assertEqual(event_ids[match.key], "evt_1")
        self.assertEqual(rows[0]["draw_no_bet_bookmaker_count"], 1)
        self.assertEqual(rows[0]["btts_bookmaker_count"], 1)
        self.assertEqual(rows[0]["team_total_home"], 1.5)
        self.assertAlmostEqual(rows[0]["goal_diff"], 0.5)
        total_signal = next(signal for signal in signals if signal.name == "market_total_goals")
        self.assertIn("btts_yes_probability", total_signal.metadata)


if __name__ == "__main__":
    unittest.main()

class SourceRequestHygieneTest(unittest.TestCase):
    def _future_fixture(self, *, source_id: str | None = None) -> FixtureRecord:
        resolver = TeamResolver.default()
        event_date = (dt.datetime.now(dt.timezone.utc) + dt.timedelta(days=2)).strftime("%Y-%m-%dT18:00:00Z")
        return FixtureRecord(
            event_date=event_date,
            home_team=resolver.resolve("Brazil"),
            away_team=resolver.resolve("Japan"),
            stage="Round of 32",
            source_id=source_id,
        )

    def test_srf_experts_zero_pick_page_writes_diagnostic_and_backs_off(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            storage = DuckDBStorage.at_data_root(Path(tmp) / "data")
            context = WorkflowContext(project_root=Path(tmp), data_root=Path(tmp) / "data", storage=storage, run_id="run-a")
            plugin = SrfExpertsPlugin()
            runtime = SourceRuntime(plugin, EventName.FEATURE_SIGNALS_REQUESTED, context)
            page = "<html><body>Bruno Berner 498 Punkte. Keine Tipps sichtbar.</body></html>"

            with unittest.mock.patch.object(SourceRuntime, "fetch_text", return_value=(page, {})):
                result = plugin._fetch_expert(
                    runtime,
                    [self._future_fixture()],
                    expert_id="bruno-berner",
                    url="https://wmtippspiel.srf.ch/experts/bruno-berner",
                )

            self.assertEqual(result.metadata["written_rows"], 0)
            diagnostics = storage.read_records(EXTRACTION_DIAGNOSTICS, latest_only=True)
            self.assertEqual(len(diagnostics), 1)
            self.assertEqual(diagnostics[0]["reason"], "no_expert_picks_on_page")
            ledger = storage.read_source_ledger(run_id="run-a")
            success_rows = [row for row in ledger if row["status"] == "success"]
            self.assertEqual(len(success_rows), 1)
            next_safe = dt.datetime.fromisoformat(str(success_rows[0]["next_safe_fetch_at"]).replace("Z", "+00:00"))
            hours_out = (next_safe - dt.datetime.now(dt.timezone.utc)).total_seconds() / 3600
            self.assertGreater(hours_out, 5.5)
            self.assertLess(hours_out, 6.5)

    def test_football_data_match_details_use_only_own_match_ids(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            storage = DuckDBStorage.at_data_root(Path(tmp) / "data")
            context = WorkflowContext(project_root=Path(tmp), data_root=Path(tmp) / "data", storage=storage, run_id="run-a")
            plugin = FootballDataPlugin()
            runtime = SourceRuntime(plugin, EventName.FEATURE_SIGNALS_REQUESTED, context)
            fixture = self._future_fixture(source_id="400021515")
            storage.write_records(
                TOURNAMENT_FIXTURES,
                [{**fixture.to_record(), "source_id": "537123"}],
                source=plugin.id,
                run_id="run-a",
            )
            state = build_tournament_state([fixture], [])
            fetched_endpoints: list[str] = []

            def fake_fetch_json(self, endpoint, params=None, *, headers=None):
                fetched_endpoints.append(endpoint)
                return {"matches": []}, {}

            with unittest.mock.patch.dict(os.environ, {"FOOTBALL_DATA_API_KEY": "test-key"}):
                with unittest.mock.patch.object(SourceRuntime, "tournament_state", return_value=state):
                    with unittest.mock.patch.object(SourceRuntime, "fetch_json", fake_fetch_json):
                        result = plugin._fetch_match_details(runtime)

            self.assertEqual(fetched_endpoints, ["https://api.football-data.org/v4/matches/537123"])
            self.assertEqual(result.metadata["unmapped_fixtures"], 0)

    def test_football_data_match_details_skip_fixtures_without_own_id(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            storage = DuckDBStorage.at_data_root(Path(tmp) / "data")
            context = WorkflowContext(project_root=Path(tmp), data_root=Path(tmp) / "data", storage=storage, run_id="run-a")
            plugin = FootballDataPlugin()
            runtime = SourceRuntime(plugin, EventName.FEATURE_SIGNALS_REQUESTED, context)
            state = build_tournament_state([self._future_fixture(source_id="400021515")], [])
            fetched_endpoints: list[str] = []

            def fake_fetch_json(self, endpoint, params=None, *, headers=None):
                fetched_endpoints.append(endpoint)
                return {"matches": []}, {}

            with unittest.mock.patch.dict(os.environ, {"FOOTBALL_DATA_API_KEY": "test-key"}):
                with unittest.mock.patch.object(SourceRuntime, "tournament_state", return_value=state):
                    with unittest.mock.patch.object(SourceRuntime, "fetch_json", fake_fetch_json):
                        result = plugin._fetch_match_details(runtime)

            self.assertEqual(fetched_endpoints, [])
            self.assertEqual(result.metadata["unmapped_fixtures"], 1)

