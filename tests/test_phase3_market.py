from __future__ import annotations

import unittest

from worldcup_predictions.core.contracts import ScoreMatrixEntry
from worldcup_predictions.market_prior import adjust_score_matrix_for_outrights, outcome_probabilities
from worldcup_predictions.plugins.sources.markets.market_odds.plugin import market_signals_from_rows
from worldcup_predictions.plugins.signals.market_trend.plugin import market_trend_rows, market_trend_signals


def _odds_row(fixture_key: str, observed_at: str, total_goals: float, over_prob: float | None = None) -> dict:
    return {
        "fixture_key": fixture_key,
        "observed_at_utc": observed_at,
        "total_goals": total_goals,
        "total_over_probability": over_prob,
        "totals_bookmaker_count": 3,
        "h2h_bookmaker_count": 0,
        "spreads_bookmaker_count": 0,
        "prob_home": 0.55,
        "prob_away": 0.25,
        "metadata": {"source": "the_odds_api"},
    }


class TotalsLineShiftTest(unittest.TestCase):
    def test_over_lean_shifts_total_up(self) -> None:
        row = _odds_row("F1", "2026-06-10T00:00:00Z", 2.5, over_prob=0.60)
        signals = market_signals_from_rows([row])
        totals = next(signal for signal in signals if signal.name == "market_total_goals")
        # 2.5 + (0.60 - 0.5) * 0.8 = 2.58
        self.assertAlmostEqual(totals.value, 2.58, places=6)

    def test_no_over_probability_leaves_line_unshifted(self) -> None:
        row = _odds_row("F2", "2026-06-10T00:00:00Z", 2.5, over_prob=None)
        signals = market_signals_from_rows([row])
        totals = next(signal for signal in signals if signal.name == "market_total_goals")
        self.assertAlmostEqual(totals.value, 2.5, places=6)


class MarketTrendTest(unittest.TestCase):
    def _history(self, fixture_key: str, totals: list[float]) -> list[dict]:
        return [
            _odds_row(fixture_key, f"2026-06-1{index}T00:00:00Z", total)
            for index, total in enumerate(totals)
        ]

    def test_rising_line_emits_positive_trend_factor(self) -> None:
        history = self._history("F1", [2.4, 2.5, 2.7])
        rows = market_trend_rows(history, open_keys={"F1"})

        self.assertEqual(len(rows), 1)
        row = rows[0]
        self.assertAlmostEqual(row["total_line_drift"], 0.3, places=6)
        self.assertGreater(row["trend_total_goals_factor"], 1.0)

        signals = market_trend_signals(rows)
        self.assertEqual(len(signals), 1)
        self.assertEqual(signals[0].name, "total_goals_factor")
        self.assertGreater(signals[0].value, 1.0)

    def test_too_few_snapshots_produce_no_trend(self) -> None:
        rows = market_trend_rows(self._history("F1", [2.4, 2.5]), open_keys={"F1"})
        self.assertEqual(rows, [])

    def test_closed_fixtures_are_excluded(self) -> None:
        rows = market_trend_rows(self._history("F1", [2.4, 2.5, 2.7]), open_keys={"OTHER"})
        self.assertEqual(rows, [])


class OutrightMarketMatrixAdjustmentTest(unittest.TestCase):
    def test_outrights_nudge_non_draw_share_without_changing_draw_rate(self) -> None:
        matrix = [
            ScoreMatrixEntry(1, 0, 0.35),
            ScoreMatrixEntry(1, 1, 0.30),
            ScoreMatrixEntry(0, 1, 0.35),
        ]
        adjusted, adjustment = adjust_score_matrix_for_outrights(
            matrix,
            "A",
            "B",
            {"A": 0.25, "B": 0.05},
        )

        self.assertIsNotNone(adjustment)
        before = outcome_probabilities(matrix)
        after = outcome_probabilities(adjusted)
        self.assertGreater(after.home, before.home)
        self.assertLess(after.away, before.away)
        self.assertAlmostEqual(after.draw, before.draw, places=9)
        self.assertEqual(adjustment["signal"], "market_outright_strength")

    def test_missing_outrights_leave_matrix_unchanged(self) -> None:
        matrix = [ScoreMatrixEntry(1, 0, 0.5), ScoreMatrixEntry(0, 1, 0.5)]
        adjusted, adjustment = adjust_score_matrix_for_outrights(matrix, "A", "B", {})

        self.assertEqual(adjusted, matrix)
        self.assertIsNone(adjustment)


if __name__ == "__main__":
    unittest.main()
