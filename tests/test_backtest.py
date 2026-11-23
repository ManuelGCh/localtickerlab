"""Pruebas del motor de backtesting (sin red)."""
from __future__ import annotations

import unittest

from localtickerlab import backtest as bt


def obs(score, fwd, action="COMPRA", date="2024-01-31", subs=None):
    base = {
        "date": date,
        "symbol": f"S{score}{fwd}",
        "score": score,
        "action": action,
        "action_rank": 3,
        "confidence": 60,
        "subscores": subs
        or {"trend": score, "momentum": score, "risk": score, "rsi": score, "liquidity": score},
        "fwd": fwd,
        "bench": 0.0,
        "excess": fwd,
        "price": 100.0,
        "target": 110.0,
        "stop": 95.0,
        "upside": 0.10,
        "hit": "target",
    }
    return base


class TestStatistics(unittest.TestCase):
    def test_spearman_monotonic(self):
        self.assertAlmostEqual(bt.spearman([1, 2, 3, 4, 5, 6, 7, 8, 9, 10],
                                           [2, 4, 6, 8, 10, 12, 14, 16, 18, 20]), 1.0)

    def test_spearman_inverse(self):
        self.assertAlmostEqual(bt.spearman([1, 2, 3, 4, 5, 6, 7, 8, 9, 10],
                                           [20, 18, 16, 14, 12, 10, 8, 6, 4, 2]), -1.0)

    def test_spearman_requires_sample(self):
        self.assertIsNone(bt.spearman([1, 2, 3], [1, 2, 3]))

    def test_rolling_std_constant(self):
        values = [0.01] * 100
        result = bt._rolling_std(values, 20)
        self.assertAlmostEqual(result[-1], 0.0, places=9)


class TestTargetOutcome(unittest.TestCase):
    def test_target_first(self):
        highs = [100] + [115] + [100] * 10
        lows = [100] + [99] + [100] * 10
        self.assertEqual(bt._target_outcome(highs, lows, 0, 110, 95), "target")

    def test_stop_first(self):
        highs = [100] + [101] + [100] * 10
        lows = [100] + [90] + [100] * 10
        self.assertEqual(bt._target_outcome(highs, lows, 0, 110, 95), "stop")

    def test_same_day_is_ambiguous(self):
        highs = [100, 115] + [100] * 10
        lows = [100, 90] + [100] * 10
        self.assertEqual(bt._target_outcome(highs, lows, 0, 110, 95), "ambiguous")

    def test_none_hit(self):
        highs = [100] * 12
        lows = [100] * 12
        self.assertEqual(bt._target_outcome(highs, lows, 0, 110, 95), "none")


class TestAggregate(unittest.TestCase):
    def setUp(self):
        self.rows = []
        for d in range(3):
            date = f"2024-0{d + 1}-28"
            for i in range(20):
                self.rows.append(
                    obs(
                        score=10 + i * 4,
                        fwd=0.01 * (i - 10),
                        action="COMPRA FUERTE" if i > 15 else ("VENTA" if i < 5 else "NEUTRAL"),
                        date=date,
                    )
                )
        self.cfg = bt.BacktestConfig()

    def test_buckets_and_deciles(self):
        result = bt._aggregate(self.rows, self.cfg, [], [], [], 1.0)
        self.assertEqual(result["coverage"]["observations"], 60)
        self.assertIn("COMPRA FUERTE", result["buckets"])
        self.assertEqual(len(result["deciles"]), 10)
        self.assertAlmostEqual(result["ic"]["mean"], 1.0, places=3)
        self.assertEqual(len(result["portfolio"]["equity"]), len(result["portfolio"]["dates"]))
        self.assertEqual(len(result["portfolio"]["equity"]), 3)

    def test_empty_raises(self):
        with self.assertRaises(ValueError):
            bt._aggregate([], self.cfg, [], [], [], 0.0)


class TestCalibration(unittest.TestCase):
    def setUp(self):
        self.rows = []
        for d in range(4):
            for i in range(30):
                self.rows.append(
                    obs(
                        score=50,
                        fwd=0.005 * i,
                        date=f"2024-0{d + 1}-28",
                        subs={
                            "trend": 10 * i,
                            "momentum": 100 - 10 * i,
                            "risk": 50,
                            "rsi": 50,
                            "liquidity": 50,
                        },
                    )
                )

    def test_factor_table_detects_relation(self):
        table = bt.factor_table(self.rows)
        momentum = next(r for r in table if r["factor"] == "momentum" and r["side"] == "directo")
        trend = next(r for r in table if r["factor"] == "trend" and r["side"] == "directo")
        self.assertLess(momentum["ic"], -0.9)
        self.assertGreater(trend["ic"], 0.9)

    def test_calibration_is_deterministic(self):
        a = bt.calibrate(self.rows, samples=5, seed=7)
        b = bt.calibrate(self.rows, samples=5, seed=7)
        self.assertEqual(a["variants"][0]["name"], b["variants"][0]["name"])
        self.assertEqual(len(a["variants"]), len(b["variants"]))
        self.assertIn("momentum", [f["factor"] for f in a["factors"]])


class TestBacktestCancel(unittest.TestCase):
    def test_cancel_marks_job_as_cancelling(self):
        job = bt.BacktestJob(bt.BacktestConfig())
        self.assertFalse(job.cancelled)
        job.cancel()
        self.assertTrue(job.cancelled)
        self.assertTrue(job.snapshot()["cancelling"])
        job.status = "cancelled"
        self.assertFalse(job.snapshot()["cancelling"])


if __name__ == "__main__":
    unittest.main()
