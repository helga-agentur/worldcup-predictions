from __future__ import annotations

import tempfile
import unittest
import unittest.mock
from pathlib import Path

from worldcup_predictions.core.datasets import EXTRACTION_DIAGNOSTICS, PUBLIC_MATCH_ANALYSIS
from worldcup_predictions.core.events import EventName
from worldcup_predictions.core.workflow import WorkflowContext
from worldcup_predictions.plugins.source_runtime import SourceRuntime
from worldcup_predictions.plugins.sources.enrichment.google_news_rss.plugin import (
    GoogleNewsRssPlugin,
    google_news_query,
    parse_google_news_rss,
    reliable_articles_from_items,
    strip_publisher_suffix,
)
from worldcup_predictions.storage import DuckDBStorage
from worldcup_predictions.tournament import FixtureRecord, TeamResolver


def fixture() -> FixtureRecord:
    resolver = TeamResolver.default()
    return FixtureRecord(
        event_date="2026-07-20T18:00:00Z",
        home_team=resolver.resolve("Brazil"),
        away_team=resolver.resolve("Japan"),
        group="Group A",
        stage="Group Stage",
    )


RSS_BODY = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0">
  <channel>
    <title>"Brazil Japan world cup" - Google News</title>
    <item>
      <title>Brazil vs Japan preview: a tight game with both teams compact - BBC Sport</title>
      <link>https://news.google.com/rss/articles/abc123</link>
      <pubDate>Sat, 18 Jul 2026 09:00:00 GMT</pubDate>
      <description>&lt;a href="https://www.bbc.com/sport/football/1"&gt;Brazil and Japan are expected to produce a tight game with both teams compact.&lt;/a&gt;</description>
      <source url="https://www.bbc.com">BBC Sport</source>
    </item>
    <item>
      <title>Brazil Japan hot takes - Random Blog</title>
      <link>https://news.google.com/rss/articles/def456</link>
      <pubDate>Sat, 18 Jul 2026 08:00:00 GMT</pubDate>
      <description>Wild guesses about Brazil against Japan.</description>
      <source url="https://random-blog.example">Random Blog</source>
    </item>
  </channel>
</rss>
"""

RSS_BODY_ONLY_UNRELIABLE = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0">
  <channel>
    <title>"Brazil Japan world cup" - Google News</title>
    <item>
      <title>Brazil Japan hot takes - Random Blog</title>
      <link>https://news.google.com/rss/articles/def456</link>
      <pubDate>Sat, 18 Jul 2026 08:00:00 GMT</pubDate>
      <description>Wild guesses about Brazil against Japan.</description>
      <source url="https://random-blog.example">Random Blog</source>
    </item>
  </channel>
</rss>
"""


class GoogleNewsRssParsingTest(unittest.TestCase):
    def test_rss_items_parse_publisher_domain_and_iso_timestamps(self) -> None:
        items = parse_google_news_rss(RSS_BODY)

        self.assertEqual(len(items), 2)
        self.assertEqual(items[0]["publisher_domain"], "bbc.com")
        self.assertEqual(items[0]["publisher"], "BBC Sport")
        self.assertEqual(items[0]["published_at"], "2026-07-18T09:00:00Z")
        self.assertEqual(items[0]["link"], "https://news.google.com/rss/articles/abc123")
        self.assertNotIn("<a", items[0]["description"])
        self.assertIn("tight game", items[0]["description"])

    def test_reliable_articles_strip_title_suffix_and_use_news_api_shape(self) -> None:
        items = parse_google_news_rss(RSS_BODY)

        articles, dropped = reliable_articles_from_items(items, fixture(), phase="pregame")

        self.assertEqual(len(articles), 1)
        self.assertEqual(articles[0]["title"], "Brazil vs Japan preview: a tight game with both teams compact")
        self.assertEqual(articles[0]["source"], {"name": "BBC Sport"})
        self.assertEqual(articles[0]["publishedAt"], "2026-07-18T09:00:00Z")
        self.assertEqual(articles[0]["url"], "https://news.google.com/rss/articles/abc123")
        self.assertEqual(len(dropped), 1)

    def test_unreliable_domain_items_are_dropped_with_diagnostics(self) -> None:
        items = parse_google_news_rss(RSS_BODY_ONLY_UNRELIABLE)

        articles, dropped = reliable_articles_from_items(items, fixture(), phase="pregame")

        self.assertEqual(articles, [])
        self.assertEqual(len(dropped), 1)
        self.assertEqual(dropped[0]["status"], "rejected")
        self.assertEqual(dropped[0]["reason"], "unreliable_source_domains_summary")
        self.assertEqual(dropped[0]["metadata"]["dropped_items"], 1)
        self.assertEqual(dropped[0]["source"], "google_news_rss")
        self.assertEqual(dropped[0]["metadata"]["top_domains"], {"random-blog.example": 1})

    def test_query_and_suffix_helpers(self) -> None:
        self.assertEqual(google_news_query(fixture()), "Brazil Japan world cup")
        self.assertEqual(strip_publisher_suffix("Match preview - ESPN", "ESPN"), "Match preview")
        self.assertEqual(strip_publisher_suffix("Plain title", ""), "Plain title")


class GoogleNewsRssPluginTest(unittest.TestCase):
    def _run_fixture_fetch(self, body: str):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        storage = DuckDBStorage.at_data_root(Path(tmp.name) / "data")
        context = WorkflowContext(project_root=Path(tmp.name), data_root=Path(tmp.name) / "data", storage=storage, run_id="run-a")
        plugin = GoogleNewsRssPlugin()
        runtime = SourceRuntime(plugin, EventName.FEATURE_SIGNALS_REQUESTED, context)

        with unittest.mock.patch.object(SourceRuntime, "fetch_text", return_value=(body, {})):
            result = plugin._fetch_fixture_rss(runtime, fixture(), phase="pregame")
        return storage, result

    def test_rows_are_written_to_public_match_analysis_with_plugin_source(self) -> None:
        storage, result = self._run_fixture_fetch(RSS_BODY)

        self.assertEqual(result.metadata["written_rows"], 1)
        rows = storage.read_records(PUBLIC_MATCH_ANALYSIS, source="google_news_rss", latest_only=True)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["phase"], "pregame")
        self.assertEqual(rows[0]["home_team"], "Brazil")
        self.assertEqual(rows[0]["away_team"], "Japan")
        self.assertEqual(rows[0]["source_name"], "BBC Sport")
        self.assertEqual(rows[0]["signal_type"], "low_tempo_or_defensive")
        self.assertEqual(rows[0]["total_goals_factor"], 0.96)
        self.assertGreater(rows[0]["reliability"], 0.80)
        self.assertEqual(rows[0]["title"], "Brazil vs Japan preview: a tight game with both teams compact")

        ledger = storage.read_source_ledger(run_id="run-a")
        success_rows = [row for row in ledger if row["status"] == "success"]
        self.assertEqual(len(success_rows), 1)
        self.assertEqual(success_rows[0]["source"], "google_news_rss")

    def test_zero_usable_items_write_info_extraction_diagnostic(self) -> None:
        storage, result = self._run_fixture_fetch(RSS_BODY_ONLY_UNRELIABLE)

        self.assertEqual(result.metadata["written_rows"], 0)
        self.assertEqual(storage.read_records(PUBLIC_MATCH_ANALYSIS, latest_only=True), [])
        diagnostics = storage.read_records(EXTRACTION_DIAGNOSTICS, source="google_news_rss", latest_only=True)
        reasons = {row["reason"] for row in diagnostics}
        self.assertIn("unreliable_source_domains_summary", reasons)
        self.assertIn("no_usable_items", reasons)
        empty_rows = [row for row in diagnostics if row["reason"] == "no_usable_items"]
        self.assertEqual(empty_rows[0]["severity"], "info")
        self.assertEqual(empty_rows[0]["status"], "empty")
        self.assertTrue(any(diag.level == "info" for diag in result.diagnostics))


if __name__ == "__main__":
    unittest.main()
