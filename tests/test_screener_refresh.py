"""El screener debe mostrar la última caché, no la foto del escaneo antiguo."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from stocklab import config, store
from stocklab.screener import ScreenerService


class TestScreenerRefresh(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self._original = config.DB_PATH
        config.DB_PATH = Path(self.tmp.name) / "test.db"
        store._local.__dict__.pop("conn", None)
        store.init()
        self.service = ScreenerService(None, None)

    def tearDown(self):
        conn = getattr(store._local, "conn", None)
        if conn is not None:
            conn.close()
        config.DB_PATH = self._original
        self.tmp.cleanup()

    def test_refresh_uses_newer_cache(self):
        store.save_cached_score(
            "AAPL",
            "2026-11-20",
            {"symbol": "AAPL", "price": 342.56, "change_pct": -0.019, "score": 77.7},
        )
        rows = [
            {
                "symbol": "AAPL",
                "name": "Apple Inc.",
                "price": 352.45,
                "change_pct": 0.0015,
                "as_of": "2026-11-18",
            }
        ]
        refreshed = self.service.refresh(rows)
        self.assertEqual(refreshed[0]["price"], 342.56)
        self.assertEqual(refreshed[0]["as_of"], "2026-11-20")
        self.assertEqual(refreshed[0]["name"], "Apple Inc.")  # conserva lo que no trae la caché

    def test_refresh_keeps_newer_row(self):
        store.save_cached_score(
            "AAPL",
            "2026-11-18",
            {"symbol": "AAPL", "price": 352.45, "as_of": "2026-11-18"},
        )
        rows = [{"symbol": "AAPL", "price": 342.56, "as_of": "2026-11-20"}]
        refreshed = self.service.refresh(rows)
        self.assertEqual(refreshed[0]["price"], 342.56)


if __name__ == "__main__":
    unittest.main()
