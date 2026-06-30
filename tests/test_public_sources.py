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
