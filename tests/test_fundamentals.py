"""Pruebas de parseo y puntuación de fundamentales (sin red)."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from localtickerlab import config, fundamentals as f, store


class TestParsing(unittest.TestCase):
    def test_number_with_thousands(self):
        self.assertEqual(f._num("$114,477,000", thousands=True), 114_477_000_000.0)
        self.assertEqual(f._num("-$6,422,000", thousands=True), -6_422_000_000.0)

    def test_placeholders(self):
        for raw in (None, "", "--", "N/A", "n/a", "-"):
            self.assertIsNone(f._num(raw))

    def test_percent(self):
        self.assertAlmostEqual(f._pct("47.88735%"), 0.4788735)

    def test_iso_date(self):
        self.assertEqual(f._iso_date("9/26/2026"), "2026-09-26")
        self.assertIsNone(f._iso_date("N/A"))

    def test_table_alignment(self):
        payload = {
            "data": {
                "incomeStatementTable": {
                    "headers": {
                        "value1": "Quarterly Ending:",
                        "value2": "9/26/2026",
                        "value3": "6/27/2026",
                    },
                    "rows": [
                        {"value1": "Total Revenue", "value2": "$114,477,000", "value3": "$109,417,000"},
                        {"value1": "Empty", "value2": "", "value3": "--"},
                    ],
                }
            }
        }
        periods, rows = f._table(payload, "incomeStatementTable")
        self.assertEqual(periods, ["2026-09-26", "2026-06-27"])
        self.assertEqual(rows["Total Revenue"][0], 114_477_000_000.0)
        self.assertEqual(rows["Total Revenue"][1], 109_417_000_000.0)
        self.assertEqual(rows["Empty"], [None, None])

    def test_summary_map(self):
        payload = {
            "data": {
                "summaryData": {
                    "Sector": {"label": "Sector", "value": "Technology"},
                    "Target": {"label": "1 Year Target", "value": "$360.00"},
                }
            }
        }
        summary = f._summary_map(payload)
        self.assertEqual(summary["Sector"], "Technology")
        self.assertEqual(f._num(summary["1 Year Target"]), 360.0)


class TestQuality(unittest.TestCase):
    def _quarters(self, strong=True):
        return [
            {
                "period": "2026-09-26",
                "revenue": 100e9 if strong else 80e9,
                "net_income": 20e9 if strong else -5e9,
                "fcf": 18e9 if strong else -4e9,
                "debt": 10e9 if strong else 300e9,
                "equity": 100e9,
                "gross_margin": 0.55 if strong else 0.15,
                "operating_margin": 0.30 if strong else -0.10,
                "net_margin": 0.20 if strong else -0.06,
            }
        ]

    def test_strong_company_scores_high(self):
        result = f._quality(
            self._quarters(True),
            {"revenue_growth": 0.20, "eps_growth": 0.25},
            [{"surprise_pct": 0.05}] * 4,
            115.0,
            100.0,
            25.0,
        )
        self.assertGreater(result["score"], 75)
        self.assertGreater(result["subscores"]["profitability"], 85.0)
        self.assertFalse(
            any("Apalancamiento" in risk for risk in result["risks"])
        )

    def test_weak_company_scores_low_and_flags(self):
        result = f._quality(
            self._quarters(False),
            {"revenue_growth": -0.15, "eps_growth": -0.30},
            [{"surprise_pct": -0.08}] * 4,
            70.0,
            100.0,
            80.0,
        )
        self.assertLess(result["score"], 40)
        self.assertTrue(any("Apalancamiento" in risk for risk in result["risks"]))
        self.assertTrue(any("Pérdidas" in risk for risk in result["risks"]))

    def test_missing_data_is_neutral(self):
        result = f._quality([], {}, [], None, None, None)
        self.assertEqual(result["score"], 50.0)
        self.assertTrue(result["risks"])


class TestCache(unittest.TestCase):
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

    def test_roundtrip(self):
        payload = {"symbol": "TEST", "quality": {"score": 70}, "cached_at": None}
        store.save_fundamentals("TEST", payload)
        loaded = store.get_fundamentals("TEST")
        self.assertEqual(loaded["quality"]["score"], 70)
        self.assertIsNotNone(loaded["cached_at"])
        self.assertIsNotNone(store.fundamentals_age("TEST"))


if __name__ == "__main__":
    unittest.main()
