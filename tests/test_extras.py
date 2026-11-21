"""Pruebas de calidad SEC, noticias y cartera."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from stocklab import config, portfolio, store
from stocklab.news import _event, _sentiment
from stocklab.sec import _quarterly_facts, quality_at


def series():
    return [
        {"end": "2022-12-31", "revenue": 100.0, "revenue_filed": "2023-02-01",
         "net_income": 10.0, "net_income_filed": "2023-02-01"},
        {"end": "2023-12-31", "revenue": 110.0, "revenue_filed": "2024-02-01",
         "net_income": 11.0, "net_income_filed": "2024-02-01"},
        {"end": "2024-12-31", "revenue": 132.0, "revenue_filed": "2025-02-01",
         "net_income": 20.0, "net_income_filed": "2025-02-01"},
    ]


class TestSecQuality(unittest.TestCase):
    def test_no_lookahead_before_filing(self):
        # Antes del 1-feb-2025 no se puede usar el trimestre de dic-2024.
        before = quality_at(series(), "2025-01-15")
        after = quality_at(series(), "2025-03-01")
        self.assertIsNotNone(before)
        self.assertIsNotNone(after)
        self.assertGreater(after, before)

    def test_quality_uses_growth_and_margin(self):
        value = quality_at(series(), "2025-03-01")
        # crecimiento +20% y margen 15,15% -> ~90
        self.assertGreater(value, 80)

    def test_old_filings_only(self):
        value = quality_at(series(), "2023-06-01")
        self.assertIsNotNone(value)  # usa dic-2022, sin año previo: solo margen

    def test_no_data(self):
        self.assertIsNone(quality_at([], "2024-01-01"))
        self.assertIsNone(quality_at(series(), "2020-01-01"))

    def test_quarterly_filter_and_restatement(self):
        facts = [
            {"start": "2023-01-01", "end": "2023-03-31", "val": 10, "filed": "2023-05-01", "form": "10-Q"},
            {"start": "2023-01-01", "end": "2023-03-31", "val": 11, "filed": "2023-06-01", "form": "10-Q/A"},
            {"start": "2023-01-01", "end": "2023-12-31", "val": 40, "filed": "2024-02-01", "form": "10-K"},
        ]
        result = _quarterly_facts(facts)
        self.assertEqual(result["2023-03-31"]["value"], 11.0)  # reexpresión posterior
        self.assertNotIn("2023-12-31", result)  # anual descartado


class TestNewsSentiment(unittest.TestCase):
    def test_positive_and_negative(self):
        self.assertGreater(_sentiment("Apple beats estimates and stock surges"), 0)
        self.assertLess(_sentiment("Apple misses estimates and stock plunges"), 0)

    def test_negation(self):
        self.assertLessEqual(_sentiment("Apple did not beat estimates"), 0)

    def test_events(self):
        self.assertEqual(_event("Analyst upgrades Apple price target"), "Analistas")
        self.assertEqual(_event("Apple announces acquisition of startup"), "M&A")


class TestPortfolio(unittest.TestCase):
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

    def test_alert_trigger(self):
        store.add_alert("AAPL", "price_above", 300.0, None)

        class FakeData:
            def get_bars(self, symbol):
                from tests.test_analysis import make_bars

                return make_bars(days=300, start=350.0)

        triggered = portfolio.check_alerts(FakeData())
        self.assertEqual(len(triggered), 1)
        self.assertIn("AAPL", triggered[0]["message"])
        self.assertEqual(len(store.list_alert_events()), 1)

    def test_portfolio_snapshot(self):
        store.add_position("AAPL", 10, 300.0, None, 280.0, 400.0, "prueba")

        class FakeData:
            def get_bars(self, symbol):
                from tests.test_analysis import make_bars

                return make_bars(days=300, start=350.0)

        snapshot = portfolio.portfolio_snapshot(FakeData())
        self.assertEqual(snapshot["totals"]["count"], 1)
        self.assertGreater(snapshot["totals"]["value"], 0)
        self.assertGreater(snapshot["positions"][0]["pnl"], 0)
        self.assertAlmostEqual(snapshot["positions"][0]["weight"], 1.0)


if __name__ == "__main__":
    unittest.main()
