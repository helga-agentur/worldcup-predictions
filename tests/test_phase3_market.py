from __future__ import annotations

import unittest
from collections import Counter

from worldcup_predictions.plugins.market_odds.plugin import market_signals_from_rows
from worldcup_predictions.plugins.market_trend.plugin import market_trend_rows, market_trend_signals
from worldcup_predictions.simulations.monte_carlo import SimulationInputs, TournamentSimulator


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


class ChampionMarketBlendTest(unittest.TestCase):
    def test_blend_mixes_simulation_and_outright(self) -> None:
        sim = TournamentSimulator(SimulationInputs(fixtures=[], team_strengths={"A": 0.6, "B": 0.4}))
        blend = sim._champion_market_blend(Counter({"A": 800, "B": 200}), sim_weight=0.45)

        assert blend is not None
        by_team = {row["answer"]: row["probability"] for row in blend}
        # A: 0.45*0.8 + 0.55*0.6 = 0.69 ; B: 0.45*0.2 + 0.55*0.4 = 0.31
        self.assertAlmostEqual(by_team["A"], 0.69, places=6)
        self.assertAlmostEqual(by_team["B"], 0.31, places=6)
        self.assertEqual(blend[0]["answer"], "A")

    def test_no_outrights_returns_none(self) -> None:
        sim = TournamentSimulator(SimulationInputs(fixtures=[], team_strengths={}))
        self.assertIsNone(sim._champion_market_blend(Counter({"A": 800, "B": 200})))


if __name__ == "__main__":
    unittest.main()
