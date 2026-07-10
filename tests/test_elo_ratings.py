from __future__ import annotations

import unittest
import unittest.mock
from pathlib import Path
from typing import Any, Iterable, Mapping

from worldcup_predictions.core.datasets import EXTRACTION_DIAGNOSTICS
from worldcup_predictions.core.events import EventName
from worldcup_predictions.core.workflow import WorkflowContext
from worldcup_predictions.plugins.source_runtime import SourceRuntime
from worldcup_predictions.plugins.sources.enrichment.elo_ratings.plugin import (
    ELO_CODE_TO_TEAM,
    ELO_RATINGS,
    EloRatingsPlugin,
    elo_hda_probabilities,
    elo_signals_from_rows,
    parse_elo_rating_rows,
)
from worldcup_predictions.storage.ledger import FetchDecision
from worldcup_predictions.tournament import FixtureRecord, TeamResolver


SAMPLE_TSV = "\n".join(
    [
        "1\t1\tES\t2177\t1\t2189\t7\t1947",
        "2\t2\tFR\t2163\t1\t2163\t16\t1795",
        "3\t3\tAR\t2156\t1\t2172\t5\t1987",
        "4\t4\tEN\t2076\t1\t2213\t4\t1983",
        "9\t9\tXX\t1960\t1\t1960\t9\t1700",  # unknown code: non-participant, skipped
        "32\t32\tSQ\t1745\t1\t1745\t32\t1600",
        "not\ta\tusable\trow",
    ]
)


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


def fixture() -> FixtureRecord:
    resolver = TeamResolver.default()
    return FixtureRecord(
        event_date="2026-07-14T18:00:00Z",
        home_team=resolver.resolve("Spain"),
        away_team=resolver.resolve("Scotland"),
        group="Group H",
        stage="Group Stage",
    )


class EloRatingsParsingTest(unittest.TestCase):
    def test_parses_tsv_into_participant_rating_rows(self) -> None:
        rows = parse_elo_rating_rows(SAMPLE_TSV)

        self.assertEqual(len(rows), 5)
        by_team = {row["team"]: row for row in rows}
        self.assertEqual(by_team["Spain"]["elo_rating"], 2177)
        self.assertEqual(by_team["Spain"]["elo_rank"], 1)
        self.assertEqual(by_team["Spain"]["record_key"], "Spain")
        self.assertEqual(by_team["Spain"]["source"], "elo_ratings")
        self.assertEqual(by_team["Scotland"]["elo_rank"], 32)
        self.assertNotIn("XX", {row["elo_code"] for row in rows})

    def test_code_mapping_resolves_all_participants_to_fifa_codes(self) -> None:
        resolver = TeamResolver.default(source="elo_ratings")

        self.assertEqual(len(ELO_CODE_TO_TEAM), 48)
        for code, name in ELO_CODE_TO_TEAM.items():
            team = resolver.resolve(name)
            self.assertIsNotNone(team.fifa_code, f"{code} -> {name} did not resolve to a FIFA code")

    def test_code_mapping_covers_elo_specific_codes(self) -> None:
        self.assertEqual(ELO_CODE_TO_TEAM["EN"], "England")
        self.assertEqual(ELO_CODE_TO_TEAM["SQ"], "Scotland")
        self.assertEqual(ELO_CODE_TO_TEAM["CH"], "Switzerland")
        self.assertEqual(ELO_CODE_TO_TEAM["DE"], "Germany")

    def test_zero_usable_rows_for_garbage_tsv(self) -> None:
        self.assertEqual(parse_elo_rating_rows("<html>blocked</html>"), [])
        self.assertEqual(parse_elo_rating_rows(""), [])


class EloHdaProbabilitiesTest(unittest.TestCase):
    def test_probabilities_sum_to_one_and_favor_higher_elo(self) -> None:
        prob_home, prob_draw, prob_away = elo_hda_probabilities(2000, 1700)

        self.assertAlmostEqual(prob_home + prob_draw + prob_away, 1.0)
        self.assertGreater(prob_home, prob_away)
        self.assertGreater(prob_home, 0.5)
        self.assertGreater(prob_draw, 0.0)

    def test_equal_ratings_split_evenly_with_baseline_draw(self) -> None:
        prob_home, prob_draw, prob_away = elo_hda_probabilities(1800, 1800)

        self.assertAlmostEqual(prob_home, prob_away)
        self.assertAlmostEqual(prob_draw, 0.28)

    def test_bigger_gap_means_higher_win_and_lower_draw_probability(self) -> None:
        _, draw_small, _ = elo_hda_probabilities(1800, 1750)
        home_big, draw_big, away_big = elo_hda_probabilities(2200, 1400)

        self.assertLess(draw_big, draw_small)
        self.assertGreater(home_big, 0.85)
        self.assertLess(away_big, 0.05)
        self.assertAlmostEqual(home_big + draw_big + away_big, 1.0)


class EloSignalsTest(unittest.TestCase):
    def test_emits_hda_signal_for_open_fixture(self) -> None:
        match = fixture()
        rows = parse_elo_rating_rows(SAMPLE_TSV)

        signals = elo_signals_from_rows(rows, [match])

        self.assertEqual(len(signals), 1)
        signal = signals[0]
        self.assertEqual(signal.name, "expert_hda_probabilities")
        self.assertEqual(signal.source, "elo_ratings")
        self.assertEqual(signal.fixture_key, match.key)
        self.assertIsNone(signal.value)
        self.assertEqual(signal.weight, 0.20)
        probabilities = (
            signal.metadata["prob_home"],
            signal.metadata["prob_draw"],
            signal.metadata["prob_away"],
        )
        self.assertAlmostEqual(sum(probabilities), 1.0)
        self.assertGreater(signal.metadata["prob_home"], signal.metadata["prob_away"])
        self.assertEqual(signal.metadata["home_elo"], 2177)
        self.assertEqual(signal.metadata["away_elo"], 1745)

    def test_skips_fixture_when_a_rating_is_missing(self) -> None:
        resolver = TeamResolver.default()
        match = FixtureRecord(
            event_date="2026-07-14T18:00:00Z",
            home_team=resolver.resolve("Spain"),
            away_team=resolver.resolve("Panama"),
        )
        rows = parse_elo_rating_rows(SAMPLE_TSV)  # sample has no Panama row

        self.assertEqual(elo_signals_from_rows(rows, [match]), [])


class EloRatingsPluginTest(unittest.TestCase):
    def _runtime(self, storage: FakeStorage) -> tuple[EloRatingsPlugin, SourceRuntime]:
        context = WorkflowContext(
            project_root=Path("."),
            data_root=Path("."),
            storage=storage,
            run_id="run-elo",
        )
        plugin = EloRatingsPlugin()
        return plugin, SourceRuntime(plugin, EventName.FEATURE_SIGNALS_REQUESTED, context)

    def test_fetch_writes_rating_rows(self) -> None:
        storage = FakeStorage()
        plugin, runtime = self._runtime(storage)

        with unittest.mock.patch.object(SourceRuntime, "fetch_text", return_value=(SAMPLE_TSV, {})):
            result = plugin._fetch_ratings(runtime)

        self.assertEqual(result.metadata["written_rows"], 5)
        self.assertEqual(len(storage.datasets[ELO_RATINGS]), 5)
        self.assertNotIn(EXTRACTION_DIAGNOSTICS, storage.datasets)

    def test_zero_parse_writes_extraction_diagnostic(self) -> None:
        storage = FakeStorage()
        plugin, runtime = self._runtime(storage)

        with unittest.mock.patch.object(SourceRuntime, "fetch_text", return_value=("<html>blocked</html>", {})):
            result = plugin._fetch_ratings(runtime)

        self.assertNotIn(ELO_RATINGS, storage.datasets)
        diagnostics = storage.datasets[EXTRACTION_DIAGNOSTICS]
        self.assertEqual(len(diagnostics), 1)
        self.assertEqual(diagnostics[0]["reason"], "no_usable_elo_ratings_in_tsv")
        self.assertEqual(diagnostics[0]["source"], "elo_ratings")
        self.assertTrue(any(diagnostic.level == "warning" for diagnostic in result.diagnostics))


if __name__ == "__main__":
    unittest.main()
