from __future__ import annotations

import datetime as dt
import unittest

from worldcup_predictions.plugins.sources.enrichment.srf_experts.plugin import (
    _next_expert_fetch_at,
    expert_weights_from_performance,
    parse_expert_react_components,
    srf_expert_rows_from_bets,
    srf_expert_signals_from_rows,
)
from worldcup_predictions.tournament import FixtureRecord, TeamResolver


BASE_URL = "https://wmtippspiel.srf.ch/experts/kathrin-lehmann"

# Trimmed real page structure: a ScoreBet with a pregame pick and the round
# selector, both as HTML-escaped JSON in data-react-props.
PAGE_HTML = """
<div data-react-class="Chart" data-react-props="{&quot;data&quot;:[{&quot;round&quot;:&quot;1. Runde&quot;,&quot;points&quot;:97}]}"></div>
<div data-react-class="SelectRaceweek/index" data-react-props="{&quot;options&quot;:[
{&quot;url&quot;:&quot;/experts/kathrin-lehmann/round/40&quot;,&quot;name&quot;:&quot;Zusatzfragen&quot;,&quot;selected&quot;:false},
{&quot;url&quot;:&quot;/experts/kathrin-lehmann/round/47&quot;,&quot;name&quot;:&quot;Halbfinals (14. Juli - 15. Juli)&quot;,&quot;selected&quot;:true},
{&quot;url&quot;:&quot;/experts/kathrin-lehmann/round/48&quot;,&quot;name&quot;:&quot;Kleiner Final (18. Juli)&quot;,&quot;selected&quot;:false},
{&quot;url&quot;:&quot;/experts/kathrin-lehmann/round/49&quot;,&quot;name&quot;:&quot;Final (19. Juli)&quot;,&quot;selected&quot;:false}]}"></div>
<div data-react-class="ScoreBet" data-react-props="{&quot;bet&quot;:{&quot;event_date&quot;:&quot;2026-07-14T19:00:00Z&quot;,&quot;deadline&quot;:&quot;2026-07-14T19:00:00Z&quot;,&quot;round&quot;:47,&quot;bet_id&quot;:&quot;537&quot;,&quot;type&quot;:&quot;score&quot;,&quot;from_other&quot;:true,&quot;picks&quot;:[2,1],&quot;teams&quot;:[{&quot;id&quot;:&quot;7548&quot;,&quot;name&quot;:&quot;Frankreich&quot;},{&quot;id&quot;:&quot;7548&quot;,&quot;name&quot;:&quot;Spanien&quot;}],&quot;censored&quot;:false,&quot;event_state&quot;:&quot;open&quot;}}"></div>
<div data-react-class="ScoreBet" data-react-props="{&quot;bet&quot;:{&quot;event_date&quot;:&quot;2026-07-15T19:00:00Z&quot;,&quot;round&quot;:47,&quot;bet_id&quot;:&quot;538&quot;,&quot;type&quot;:&quot;score&quot;,&quot;picks&quot;:null,&quot;teams&quot;:[{&quot;name&quot;:&quot;England&quot;},{&quot;name&quot;:&quot;Argentinien&quot;}],&quot;censored&quot;:false,&quot;event_state&quot;:&quot;open&quot;}}"></div>
"""


def _fixture(home: str, away: str, event_date: str) -> FixtureRecord:
    resolver = TeamResolver.default()
    return FixtureRecord(
        event_date=event_date,
        home_team=resolver.resolve(home),
        away_team=resolver.resolve(away),
        stage="Semi-final",
    )


class SrfExpertParserTest(unittest.TestCase):
    def test_parses_score_bets_and_round_urls(self) -> None:
        bets, round_urls = parse_expert_react_components(PAGE_HTML, base_url=BASE_URL)

        self.assertEqual(len(bets), 2)
        self.assertEqual(bets[0]["picks"], [2, 1])
        self.assertEqual(bets[0]["teams"][0]["name"], "Frankreich")
        # Selected round and Zusatzfragen are excluded; URLs are absolute.
        self.assertEqual(
            round_urls,
            [
                "https://wmtippspiel.srf.ch/experts/kathrin-lehmann/round/48",
                "https://wmtippspiel.srf.ch/experts/kathrin-lehmann/round/49",
            ],
        )

    def test_rows_match_fixtures_and_skip_unpicked_bets(self) -> None:
        bets, _ = parse_expert_react_components(PAGE_HTML, base_url=BASE_URL)
        fixtures = [
            _fixture("France", "Spain", "2026-07-14T19:00:00Z"),
            _fixture("England", "Argentina", "2026-07-15T19:00:00Z"),
        ]

        rows = srf_expert_rows_from_bets(bets, expert_id="kathrin_lehmann", expert_url=BASE_URL, fixtures=fixtures)

        self.assertEqual(len(rows), 1, "the bet without picks must be skipped")
        row = rows[0]
        self.assertEqual(row["fixture_key"], fixtures[0].key)
        self.assertEqual((row["tip_home"], row["tip_away"]), (2, 1))
        self.assertEqual(row["expert_id"], "kathrin_lehmann")
        self.assertEqual(row["metadata"]["event_state"], "open")

    def test_unknown_pairing_is_skipped_not_guessed(self) -> None:
        bets, _ = parse_expert_react_components(PAGE_HTML, base_url=BASE_URL)
        fixtures = [_fixture("Norway", "England", "2026-07-11T21:00:00Z")]

        rows = srf_expert_rows_from_bets(bets, expert_id="kathrin_lehmann", expert_url=BASE_URL, fixtures=fixtures)

        self.assertEqual(rows, [])


class SrfExpertCadenceTest(unittest.TestCase):
    NOW = dt.datetime(2026, 7, 13, 12, 0, tzinfo=dt.timezone.utc)

    def _bet(self, deadline: str, state: str = "open") -> dict:
        return {"deadline": deadline, "event_state": state}

    def test_settled_pages_reprobe_weekly(self) -> None:
        self.assertEqual(
            _next_expert_fetch_at([self._bet("2026-07-10T19:00:00Z", state="finished")], now=self.NOW),
            self.NOW + dt.timedelta(days=7),
        )

    def test_empty_future_rounds_reprobe_within_hours(self) -> None:
        # Rounds without bets (finals before the pairing resolves) must not
        # sleep for a week, or new bets would be missed.
        self.assertEqual(_next_expert_fetch_at([], now=self.NOW), self.NOW + dt.timedelta(hours=6))

    def test_open_bets_poll_on_every_scheduled_run(self) -> None:
        # Experts can revise tips until the deadline; revisions are trend
        # information, so open pages carry no explicit schedule and fall back
        # to the per-run freshness interval.
        for deadline in ("2026-07-15T19:00:00Z", "2026-07-13T13:00:00Z"):
            self.assertIsNone(_next_expert_fetch_at([self._bet(deadline)], now=self.NOW))


class SrfExpertConsensusTest(unittest.TestCase):
    def test_consensus_shifts_toward_stronger_expert(self) -> None:
        rows = [
            {"fixture_key": "F", "expert_id": "a", "tip_home": 2, "tip_away": 0, "observed_at_utc": "t"},
            {"fixture_key": "F", "expert_id": "b", "tip_home": 0, "tip_away": 1, "observed_at_utc": "t"},
        ]
        equal = srf_expert_signals_from_rows(rows)[0]
        self.assertAlmostEqual(equal.metadata["prob_home"], 0.5, places=6)

        weighted = srf_expert_signals_from_rows(rows, weights={"a": 1.35, "b": 0.75})[0]
        self.assertGreater(weighted.metadata["prob_home"], 0.5)
        self.assertTrue(weighted.metadata["performance_weighted"])

    def test_weights_reward_accuracy_and_clamp(self) -> None:
        performance = [
            {"expert_id": "a", "points": 8.0},
            {"expert_id": "a", "points": 8.0},
            {"expert_id": "b", "points": 1.0},
            {"expert_id": "b", "points": 1.0},
        ]
        weights = expert_weights_from_performance(performance)
        self.assertGreater(weights["a"], 1.0)
        self.assertLess(weights["b"], 1.0)
        self.assertGreaterEqual(weights["b"], 0.75)
        self.assertLessEqual(weights["a"], 1.35)

    def test_no_performance_yields_equal_weights(self) -> None:
        self.assertEqual(expert_weights_from_performance([]), {})


if __name__ == "__main__":
    unittest.main()
