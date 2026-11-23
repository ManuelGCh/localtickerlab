"""Pruebas del walk-forward y del riesgo de cartera."""
from __future__ import annotations

import datetime as dt
import tempfile
import unittest
from pathlib import Path

from localtickerlab import backtest as bt
from localtickerlab import config, portfolio, store
from tests.test_analysis import make_bars


def synthetic_observations(days: int = 700, per_day: int = 12):
    observations = []
    start = dt.date(2020, 1, 1)
    for d in range(days):
        date = (start + dt.timedelta(days=d)).isoformat()
        for i in range(per_day):
            value = 50.0 + i * 2.0
            observations.append(
                {
                    "date": date,
                    "symbol": f"S{i}",
                    "score": value,
                    "action": "COMPRA",
                    "subscores": {key: value for key in bt._KEYS},
                    "fwd": (value - 50.0) / 1000.0,
                    "excess": (value - 50.0) / 1000.0,
                    "bench": 0.0,
                    "hit": None,
                    "quality": None,
                }
            )
    return observations


class TestWalkForward(unittest.TestCase):
    def test_folds_and_ic(self):
        result = bt.walk_forward(
            synthetic_observations(), folds=2, train_periods=60, test_periods=20, samples=5
        )
        self.assertIsNotNone(result)
        self.assertEqual(len(result["folds"]), 2)
        self.assertGreater(result["current_ic_mean"], 0.9)
        self.assertGreater(result["optimized_ic_mean"], 0.9)
        for fold in result["folds"]:
            self.assertLess(fold["test_start"], fold["test_end"])

    def test_too_little_data(self):
        self.assertIsNone(bt.walk_forward(synthetic_observations(days=10)))


class TestPortfolioRisk(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self._original = config.DB_PATH
        config.DB_PATH = Path(self.tmp.name) / "test.db"
        store._local.__dict__.pop("conn", None)
        store.init()

    def tearDown(self):
        conn = getattr(store._local, "conn", None)
        if conn is not None:
            conn.close()
        store._local.__dict__.pop("conn", None)
        config.DB_PATH = self._original
        self.tmp.cleanup()

    def test_correlation_and_var(self):
        bars_a = make_bars(days=300, start=100.0, drift=0.001)
        bars_b = make_bars(days=300, start=50.0, drift=0.001)
        store.upsert_bars("TESTA", bars_a)
        store.upsert_bars("TESTB", bars_b)
        store.add_position("TESTA", 10, 100.0, None, 90.0, 130.0, "")
        store.add_position("TESTB", 20, 50.0, None, 45.0, 70.0, "")

        class FakeData:
            def get_bars(self, symbol):
                return store.get_bars(symbol)

        snapshot = portfolio.portfolio_snapshot(FakeData())
        risk = snapshot["risk"]
        self.assertIsNotNone(risk)
        self.assertEqual(len(risk["symbols"]), 2)
        self.assertGreater(risk["var_95"], 0)
        self.assertGreater(risk["volatility"], 0)
        self.assertGreaterEqual(risk["max_drawdown"], 0)
        self.assertAlmostEqual(risk["matrix"][0][0], 1.0, places=5)

    def test_one_position_has_no_risk_block(self):
        store.upsert_bars("TESTA", make_bars(days=200))
        store.add_position("TESTA", 10, 100.0, None, 90.0, 130.0, "")

        class FakeData:
            def get_bars(self, symbol):
                return store.get_bars(symbol)

        self.assertIsNone(portfolio.portfolio_snapshot(FakeData())["risk"])

    def test_has_alert(self):
        store.add_alert("TESTA", "price_below", 90.0, None)
        self.assertTrue(store.has_alert("TESTA", "price_below", 90.0))
        self.assertFalse(store.has_alert("TESTA", "price_below", 80.0))


if __name__ == "__main__":
    unittest.main()
