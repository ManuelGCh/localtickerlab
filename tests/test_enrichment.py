"""Pruebas del enriquecimiento de filas del screener con fundamentales."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from stocklab import config, store
from stocklab.screener import ScreenJob


class FakeFundamentals:
    def get(self, symbol, force=False):
        return {
            "quality": {"score": 72.5, "label": "Buena"},
            "annual": {"revenue_growth": 0.12},
            "surprises": [
                {"surprise_pct": 0.05},
                {"surprise_pct": -0.02},
                {"surprise_pct": 0.03},
                {"surprise_pct": 0.01},
            ],
            "next_earnings": {"days": 5, "date": "2026-12-01"},
            "analyst": {"upside": 0.10},
            "company": {"pe": 20.0},
        }


class TestEnrichment(unittest.TestCase):
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

    def test_enrich_row(self):
        job = ScreenJob("ndx", {})
        row = {"symbol": "AAPL", "as_of": "2026-11-18", "score": 80}
        job.rows = [row]
        job._enrich_one(row, FakeFundamentals())

        self.assertEqual(row["quality"], 72.5)
        self.assertEqual(row["quality_label"], "Buena")
        self.assertAlmostEqual(row["revenue_growth"], 0.12)
        self.assertAlmostEqual(row["eps_surprise"], 0.05)
        self.assertEqual(row["beat_rate"], 0.75)
        self.assertEqual(row["days_to_earnings"], 5)
        self.assertAlmostEqual(row["analyst_upside"], 0.10)
        self.assertEqual(job.enrich_done, 1)
        self.assertEqual(job.enrich_errors, 0)

        cached = store.get_cached_score("AAPL")
        self.assertEqual(cached["row"]["quality"], 72.5)

    def test_failure_counts_error_without_raising(self):
        class Broken:
            def get(self, symbol, force=False):
                raise RuntimeError("sin datos")

        job = ScreenJob("ndx", {})
        row = {"symbol": "BAD", "as_of": "2026-11-18", "score": 10}
        job._enrich_one(row, Broken())
        self.assertEqual(job.enrich_errors, 1)
        self.assertEqual(job.enrich_done, 1)
        self.assertIsNone(row.get("quality"))


if __name__ == "__main__":
    unittest.main()
