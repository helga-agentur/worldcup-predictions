from __future__ import annotations

import os
import unittest

from worldcup_predictions.plugins.article_sources import fetch_news_api
from worldcup_predictions.plugins.article_sources import analysis_query, classify_public_note, lineup_query
from worldcup_predictions.plugins.sources.enrichment.lineup_availability.plugin import classify_availability_signal
from worldcup_predictions.plugins.sources.enrichment.lineup_availability.plugin import lineup_availability_signals_from_rows
from worldcup_predictions.plugins.signals.ml_outcome.plugin import RollingFeatureBuilder
from worldcup_predictions.tournament import FixtureRecord, TeamResolver


class MlFeatureParityTest(unittest.TestCase):
    def test_feature_vector_has_24_features(self) -> None:
        resolver = TeamResolver.default()
        fixture = FixtureRecord(
            event_date="2026-06-14T18:00:00Z",
            home_team=resolver.resolve("Brazil"),
            away_team=resolver.resolve("Japan"),
        )
        features = RollingFeatureBuilder().fixture_features(fixture)
        self.assertEqual(len(features), 24)


class NewsApiQueryTest(unittest.TestCase):
    class _FakeClient:
        def __init__(self) -> None:
            self.params: dict | None = None

        def get_json(self, url, params):
            self.params = params
            return {"articles": []}, {}

    def test_domain_allowlist_and_page_size_applied(self) -> None:
        client = self._FakeClient()
        os.environ["NEWS_API_KEY"] = "key"
        self.addCleanup(os.environ.pop, "NEWS_API_KEY", None)

        fetch_news_api(query="q", http_client=client)

        assert client.params is not None
        self.assertEqual(client.params["pageSize"], 25)
        self.assertIn("domains", client.params)
        self.assertIn("bbc.co.uk", client.params["domains"])

    def test_domains_can_be_disabled(self) -> None:
        client = self._FakeClient()
        os.environ["NEWS_API_KEY"] = "key"
        self.addCleanup(os.environ.pop, "NEWS_API_KEY", None)

        fetch_news_api(query="q", domains=None, http_client=client)

        assert client.params is not None
        self.assertNotIn("domains", client.params)

    def test_knockout_queries_include_stage_specific_terms(self) -> None:
        resolver = TeamResolver.default()
        fixture = FixtureRecord(
            event_date="2026-07-01T18:00:00Z",
            home_team=resolver.resolve("Brazil"),
            away_team=resolver.resolve("Japan"),
            stage="Round of 16",
        )

        self.assertIn("penalties", analysis_query(fixture, phase="pregame"))
        self.assertIn("extra time", lineup_query(fixture))

    def test_public_notes_and_availability_extract_card_context(self) -> None:
        note_type, metadata = classify_public_note("Star midfielder is suspended after yellow card accumulation.")
        self.assertEqual(note_type, "suspension_context")
        self.assertIn("suspension_context", metadata["categories"])

        signal_type, factor = classify_availability_signal("Defender is banned and suspended for the knockout match.")
        self.assertEqual(signal_type, "suspension_risk")
        self.assertLess(factor, 1.0)

        card_signal_type, card_factor = classify_availability_signal("Midfielder is on a yellow card warning due to card accumulation.")
        self.assertEqual(card_signal_type, "card_accumulation_risk")
        self.assertLess(card_factor, 1.0)


class ContinuousReliabilityTest(unittest.TestCase):
    def _row(self, reliability: float, signal_type: str = "injury_or_fitness_risk", factor: float = 0.95) -> dict:
        return {
            "fixture_key": "F",
            "affected_side": "home",
            "affected_team": "Brazil",
            "reliability": reliability,
            "expected_goals_factor": factor,
            "signal_type": signal_type,
            "source_url": "u",
        }

    def test_mid_reliability_source_now_contributes(self) -> None:
        # 0.50 (neutral/unknown) is below the old 0.70 wall but above the 0.40 spam floor.
        signals = lineup_availability_signals_from_rows([self._row(0.50)])
        self.assertEqual(len(signals), 1)

    def test_spam_floor_excludes_untrusted(self) -> None:
        self.assertEqual(lineup_availability_signals_from_rows([self._row(0.30)]), [])

    def test_confirmed_lineup_raises_confidence_without_diluting_effect(self) -> None:
        rows = [
            self._row(0.85, signal_type="injury_or_fitness_risk", factor=0.95),
            self._row(0.90, signal_type="official_lineup_available", factor=1.0),
        ]
        signals = lineup_availability_signals_from_rows(rows)
        self.assertEqual(len(signals), 1)
        signal = signals[0]
        # The neutral official marker must not pull the 0.95 injury factor toward 1.0.
        self.assertAlmostEqual(signal.value, 0.95, places=6)
        self.assertTrue(signal.metadata["confirmed_lineup"])
        self.assertGreater(signal.confidence, 0.85)

    def test_official_lineup_alone_emits_no_effect_signal(self) -> None:
        signals = lineup_availability_signals_from_rows(
            [self._row(0.90, signal_type="official_lineup_available", factor=1.0)]
        )
        self.assertEqual(signals, [])


if __name__ == "__main__":
    unittest.main()
