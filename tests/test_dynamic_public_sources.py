from __future__ import annotations

import datetime as dt
import unittest

from worldcup_predictions.core.contracts import ScoreTip
from worldcup_predictions.plugins.signals.market_trend.plugin import public_market_history_rows
from worldcup_predictions.plugins.sources.fixtures.dynamic_public_sources.crawler import discover_candidate_links
from worldcup_predictions.plugins.sources.fixtures.dynamic_public_sources.consensus import (
    build_claim_consensus_rows,
    result_records_from_consensus,
)
from worldcup_predictions.plugins.sources.fixtures.dynamic_public_sources.extractors import extract_claims_from_page
from worldcup_predictions.plugins.sources.fixtures.dynamic_public_sources.reputation import build_reputation_rows
from worldcup_predictions.plugins.sources.fixtures.dynamic_public_sources.seeds import (
    DYNAMIC_PUBLIC_TRUSTED_SOURCE_BATCH_SIZE,
    dynamic_public_seeds,
    dynamic_public_seeds_for_run,
    trusted_public_source_seeds,
)
from worldcup_predictions.tournament import FixtureRecord, ResultRecord, TeamResolver, build_tournament_state


def fixture() -> FixtureRecord:
    resolver = TeamResolver.default()
    return FixtureRecord(
        event_date="2026-07-10T18:00:00Z",
        home_team=resolver.resolve("Brazil"),
        away_team=resolver.resolve("Japan"),
        stage="Group Stage",
        group="A",
    )


def result_page(score: str = "2-1") -> str:
    return f"""
    <html>
      <head><title>Brazil Japan final</title></head>
      <body>
        Final: Brazil {score} Japan.
        Betting odds: Brazil 1.80 Draw 3.40 Japan 4.50.
        Totals: Over 2.5 1.91 Under 2.5 1.91.
      </body>
    </html>
    """


class DynamicPublicSourcesTest(unittest.TestCase):
    def test_extracts_result_claim_and_market_observation(self) -> None:
        match = fixture()
        claims, market_rows, diagnostics = extract_claims_from_page(
            result_page(),
            state=build_tournament_state([match], []),
            source_url="https://www.bbc.com/sport/football/brazil-japan",
            domain="bbc.com",
            source_name="BBC Sport",
        )

        result_claims = [row for row in claims if row["claim_type"] == "result"]

        self.assertEqual(len(result_claims), 1)
        self.assertEqual(result_claims[0]["fixture_key"], match.key)
        self.assertEqual(result_claims[0]["value_signature"], "2:1")
        self.assertEqual(len(market_rows), 1)
        self.assertGreaterEqual(market_rows[0]["confidence"], 0.60)
        self.assertTrue(any(row["status"] == "accepted" for row in diagnostics))

    def test_result_claims_need_multi_domain_weighted_consensus(self) -> None:
        match = fixture()
        state = build_tournament_state([match], [])
        claims = []
        for url, domain, source_name in (
            ("https://www.fifa.com/match/brazil-japan", "fifa.com", "FIFA"),
            ("https://www.bbc.com/sport/football/brazil-japan", "bbc.com", "BBC Sport"),
            ("https://www.espn.com/soccer/match/brazil-japan", "espn.com", "ESPN"),
        ):
            rows, _market_rows, _diagnostics = extract_claims_from_page(
                result_page(),
                state=state,
                source_url=url,
                domain=domain,
                source_name=source_name,
            )
            claims.extend(row for row in rows if row["claim_type"] == "result")

        consensus = build_claim_consensus_rows(claims)
        results = result_records_from_consensus(consensus, claims)

        self.assertEqual(consensus[0]["status"], "strong")
        self.assertEqual(consensus[0]["domain_count"], 3)
        self.assertEqual(len(results), 3)
        self.assertEqual({row.source for row in results}, {"dynamic_public:fifa.com", "dynamic_public:bbc.com", "dynamic_public:espn.com"})

    def test_two_dynamic_domains_do_not_promote_result(self) -> None:
        match = fixture()
        state = build_tournament_state([match], [])
        claims = []
        for url, domain in (
            ("https://www.bbc.com/sport/football/brazil-japan", "bbc.com"),
            ("https://www.espn.com/soccer/match/brazil-japan", "espn.com"),
        ):
            rows, _market_rows, _diagnostics = extract_claims_from_page(
                result_page(),
                state=state,
                source_url=url,
                domain=domain,
                source_name=domain,
            )
            claims.extend(row for row in rows if row["claim_type"] == "result")

        consensus = build_claim_consensus_rows(claims)

        self.assertEqual(consensus[0]["status"], "candidate")
        self.assertEqual(result_records_from_consensus(consensus, claims), [])

    def test_reputation_scores_against_independently_confirmed_results(self) -> None:
        match = fixture()
        confirmed = ResultRecord(
            event_date=match.event_date,
            home_team=match.home_team,
            away_team=match.away_team,
            score=ScoreTip(2, 1),
            source="fifa_match_centre",
            metadata={"confirmation": {"sources": ["fifa_match_centre", "srf_public"]}},
        )
        state = build_tournament_state([match], [confirmed])
        correct, _market_rows, _diagnostics = extract_claims_from_page(
            result_page("2-1"),
            state=state,
            source_url="https://www.bbc.com/sport/football/brazil-japan",
            domain="bbc.com",
            source_name="BBC Sport",
        )
        wrong, _market_rows, _diagnostics = extract_claims_from_page(
            result_page("1-2"),
            state=state,
            source_url="https://www.example.com/brazil-japan",
            domain="example.com",
            source_name="Example",
        )

        rows = build_reputation_rows(
            [row for row in [*correct, *wrong] if row["claim_type"] == "result"],
            [confirmed],
        )
        by_domain = {row["domain"]: row for row in rows}

        self.assertGreater(by_domain["bbc.com"]["source_score"], 0.5)
        self.assertLess(by_domain["example.com"]["source_score"], 0.5)

    def test_public_market_history_filters_low_confidence_rows(self) -> None:
        rows = public_market_history_rows(
            [
                {"fixture_key": "F1", "confidence": 0.59, "domain": "low.example"},
                {"fixture_key": "F1", "confidence": 0.60, "domain": "high.example", "metadata": {}},
            ]
        )

        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["metadata"]["source"], "dynamic_public")
        self.assertEqual(rows[0]["metadata"]["bookmaker"], "high.example")

    def test_discovers_same_domain_tournament_links(self) -> None:
        match = fixture()
        state = build_tournament_state([match], [])
        links = discover_candidate_links(
            """
            <a href="/football/world-cup/brazil-japan-preview">Brazil Japan preview</a>
            <a href="https://other.example/football/world-cup/brazil-japan">External</a>
            """,
            base_url="https://www.example.com/sport",
            state=state,
            limit=5,
        )

        self.assertEqual(len(links), 1)
        self.assertEqual(links[0].url, "https://www.example.com/football/world-cup/brazil-japan-preview")

    def test_trusted_seed_registry_contains_unique_audited_sources(self) -> None:
        # The registry was trimmed 2026-07-10 to sources that actually yield
        # extracted claims (a live-ledger audit removed 54 blocked or
        # zero-yield domains), so the floor asserts against silent shrinkage,
        # not the old 100+ breadth goal.
        seeds = trusted_public_source_seeds()

        self.assertGreaterEqual(len(seeds), 50)
        self.assertEqual(len({seed.url for seed in seeds}), len(seeds))
        self.assertTrue(all(seed.category != "core" for seed in seeds))

    def test_run_seed_batch_keeps_trusted_sources_bounded(self) -> None:
        match = fixture()
        state = build_tournament_state([match], [])
        all_seeds = dynamic_public_seeds(state)
        run_seeds = dynamic_public_seeds_for_run(
            state,
            now=dt.datetime(2026, 7, 6, 12, tzinfo=dt.UTC),
        )
        trusted_run_seeds = [seed for seed in run_seeds if seed.category not in {"core", "scoreboard"}]

        self.assertGreaterEqual(len(all_seeds), 50)
        self.assertEqual(len(trusted_run_seeds), DYNAMIC_PUBLIC_TRUSTED_SOURCE_BATCH_SIZE)


if __name__ == "__main__":
    unittest.main()
