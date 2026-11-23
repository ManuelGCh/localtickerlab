"""Pruebas de los indicadores técnicos (sin red)."""
from __future__ import annotations

import math
import unittest

from localtickerlab import indicators as ind


def ramp(n: int, start: float = 1.0, step: float = 1.0) -> list[float]:
    return [start + i * step for i in range(n)]


class TestSma(unittest.TestCase):
    def test_sma_values(self):
        values = [1, 2, 3, 4, 5, 6]
        result = ind.sma(values, 3)
        self.assertEqual(result[:2], [None, None])
        self.assertAlmostEqual(result[2], 2.0)
        self.assertAlmostEqual(result[-1], 5.0)

    def test_sma_short_series(self):
        self.assertEqual(ind.sma([1, 2], 5), [None, None])


class TestEma(unittest.TestCase):
    def test_ema_seed_is_sma(self):
        result = ind.ema([1, 2, 3, 4], 3)
        self.assertIsNone(result[1])
        self.assertAlmostEqual(result[2], 2.0)
        self.assertGreater(result[3], 2.0)

    def test_ema_constant_series(self):
        result = ind.ema([7.0] * 20, 5)
        self.assertAlmostEqual(result[-1], 7.0)


class TestRsi(unittest.TestCase):
    def test_rsi_all_gains(self):
        self.assertEqual(ind.rsi(ramp(40))[-1], 100.0)

    def test_rsi_all_losses(self):
        self.assertEqual(ind.rsi(list(reversed(ramp(40))))[-1], 0.0)

    def test_rsi_wilde_reference(self):
        # Serie clásica de Wilder: RSI final ≈ 57.92
        prices = [
            44.34, 44.09, 44.15, 43.61, 44.33, 44.83, 45.10, 45.42, 45.84,
            46.08, 45.89, 46.03, 45.61, 46.28, 46.28, 46.00, 46.03, 46.41,
            46.22, 45.64,
        ]
        self.assertAlmostEqual(ind.rsi(prices)[-1], 57.92, places=1)

    def test_rsi_bounds(self):
        prices = [100 + 10 * math.sin(i / 3) for i in range(200)]
        values = [v for v in ind.rsi(prices) if v is not None]
        self.assertTrue(all(0 <= v <= 100 for v in values))


class TestMacd(unittest.TestCase):
    def test_macd_flat_series_is_zero(self):
        result = ind.macd([50.0] * 80)
        self.assertAlmostEqual(result["line"][-1], 0.0, places=6)
        self.assertAlmostEqual(result["hist"][-1], 0.0, places=6)

    def test_macd_positive_in_uptrend(self):
        result = ind.macd(ramp(80))
        self.assertGreater(result["line"][-1], 0)

    def test_macd_series_alignment(self):
        result = ind.macd(ramp(80))
        for key in ("line", "signal", "hist"):
            self.assertEqual(len(result[key]), 80)


class TestBollinger(unittest.TestCase):
    def test_bands_around_mean(self):
        result = ind.bollinger([10.0] * 30)
        self.assertAlmostEqual(result["upper"][-1], 10.0)
        self.assertAlmostEqual(result["lower"][-1], 10.0)


class TestRisk(unittest.TestCase):
    def test_max_drawdown(self):
        self.assertAlmostEqual(ind.max_drawdown([100, 120, 60, 80]), 0.5)

    def test_max_drawdown_no_loss(self):
        self.assertEqual(ind.max_drawdown([1, 2, 3]), 0.0)

    def test_volatility_zero_for_flat(self):
        self.assertEqual(ind.annualized_volatility([0.0] * 30), 0.0)

    def test_beta_of_identical_series(self):
        returns = [0.01, -0.02, 0.015, 0.0, -0.01] * 10
        self.assertAlmostEqual(ind.beta(returns, returns), 1.0, places=6)

    def test_beta_requires_enough_data(self):
        self.assertIsNone(ind.beta([0.01, 0.02], [0.01, 0.02]))


class TestHelpers(unittest.TestCase):
    def test_percentile_rank(self):
        self.assertAlmostEqual(ind.percentile_rank([1, 2, 3, 4], 2), 37.5)

    def test_slope_positive(self):
        self.assertGreater(ind.slope_pct(ramp(50)), 0)

    def test_atr_constant_range(self):
        closes = [100.0] * 30
        highs = [101.0] * 30
        lows = [99.0] * 30
        self.assertAlmostEqual(ind.atr(highs, lows, closes)[-1], 2.0)


if __name__ == "__main__":
    unittest.main()
