"""El screener debe mostrar la última caché, no la foto del escaneo antiguo."""
from __future__ import annotations

import datetime as dt
import tempfile
import unittest
from pathlib import Path
from unittest import mock
from zoneinfo import ZoneInfo

from localtickerlab import config, store
from localtickerlab import portfolio
from localtickerlab.screener import ScreenerService

NY = ZoneInfo("America/New_York")


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

    def test_quality_comes_from_fundamentals_not_score_cache(self):
        store.save_cached_score(
            "REGN",
            "2026-11-20",
            {"symbol": "REGN", "price": 900.0, "quality": 86.8, "quality_label": "Excelente"},
        )
        store.save_fundamentals(
            "REGN",
            {
                "quality": {"score": 83.3, "label": "Excelente"},
                "annual": {"revenue_growth": 0.01},
                "surprises": [{"surprise_pct": 0.05}],
                "next_earnings": {"days": 3, "date": "2026-12-01"},
                "analyst": {"upside": 0.10},
                "company": {"pe": 20.0},
                "quarters": [{"period": "2026-09-30", "revenue": 1.0}],
            },
        )
        refreshed = self.service.refresh([{"symbol": "REGN", "as_of": "2026-11-20"}])
        self.assertEqual(refreshed[0]["quality"], 83.3)
        self.assertTrue(refreshed[0]["enriched"])

    def test_missing_fundamentals_clears_stale_quality(self):
        store.save_cached_score(
            "MDLZ",
            "2026-11-20",
            {"symbol": "MDLZ", "price": 60.0, "quality": 70.0, "quality_label": "Buena"},
        )
        refreshed = self.service.refresh([{"symbol": "MDLZ", "as_of": "2026-11-20"}])
        self.assertIsNone(refreshed[0]["quality"])
        self.assertFalse(refreshed[0]["enriched"])

    def test_scan_lock_is_exclusive(self):
        first = store.acquire_scan_lock()
        self.assertIsNotNone(first)
        try:
            second = store.acquire_scan_lock()
            self.assertIsNone(second)
        finally:
            store.release_scan_lock(first)
        again = store.acquire_scan_lock()
        self.assertIsNotNone(again)
        store.release_scan_lock(again)

    def test_position_update_and_history(self):
        for i in range(30):
            date = (dt.date(2026, 6, 1) + dt.timedelta(days=i)).isoformat()
            store.upsert_bars(
                "QQQ",
                [{"date": date, "open": 100 + i, "high": 101 + i, "low": 99 + i,
                  "close": 100 + i, "volume": 1000}],
            )
            store.upsert_bars(
                "AAPL",
                [{"date": date, "open": 10 + i, "high": 11 + i, "low": 9 + i,
                  "close": 10 + i, "volume": 1000}],
            )
        position_id = store.add_position("AAPL", 10, 10.0, "2026-06-01", 8.0, 15.0, "")
        store.update_position(position_id, "AAPL", 12, 10.5, "2026-06-01", 8.5, 16.0, "")
        updated = store.get_position(position_id)
        self.assertEqual(updated["shares"], 12)
        self.assertEqual(updated["stop"], 8.5)
        history = portfolio._history(store.list_positions())
        self.assertEqual(history["portfolio"][0], 100.0)
        self.assertEqual(len(history["dates"]), 30)

    def test_watchlist_and_views_roundtrip(self):
        store.add_watch("msft")
        store.add_watch("AAPL")
        self.assertEqual(store.list_watchlist(), ["AAPL", "MSFT"])
        store.replace_watchlist(["NVDA", "AAPL"])
        self.assertEqual(store.list_watchlist(), ["AAPL", "NVDA"])
        store.save_view("Tecnología", {"universe": "ndx", "signal": "COMPRA"})
        views = store.list_views()
        self.assertEqual(views[0]["name"], "Tecnología")
        self.assertEqual(views[0]["config"]["signal"], "COMPRA")
        exported = store.export_user_data()
        self.assertEqual(exported["watchlist"], ["AAPL", "NVDA"])
        self.assertTrue(exported["views"])
        store.delete_view("Tecnología")
        self.assertEqual(store.list_views(), [])

    def test_upsert_instruments_keeps_sector_when_source_lacks_it(self):
        store.upsert_instruments(
            [{"symbol": "AAPL", "name": "Apple Inc.", "sector": "Technology"}]
        )
        store.upsert_instruments([{"symbol": "AAPL", "name": "Apple Inc. Common Stock"}])
        instrument = store.get_instrument("AAPL")
        self.assertEqual(instrument["sector"], "Technology")
        self.assertEqual(instrument["name"], "Apple Inc. Common Stock")

    def test_stale_bars_trigger_refresh_even_after_close(self):
        store.upsert_bars(
            "AAPL",
            [{"date": "2026-11-17", "open": 1, "high": 1, "low": 1, "close": 1, "volume": 1}],
        )
        fetched = dt.datetime(2026, 11, 18, 20, 0, tzinfo=NY)
        with mock.patch.object(store, "now_ny", return_value=fetched):
            store.mark_synced("AAPL", "nasdaq", 700)
        later = dt.datetime(2026, 11, 19, 3, 0, tzinfo=NY)  # 7 h después
        with mock.patch.object(store, "now_ny", return_value=later):
            self.assertTrue(store.needs_refresh("AAPL"))

    def test_fresh_bar_does_not_force_refresh(self):
        store.upsert_bars(
            "AAPL",
            [{"date": "2026-11-18", "open": 1, "high": 1, "low": 1, "close": 1, "volume": 1}],
        )
        fetched = dt.datetime(2026, 11, 18, 20, 0, tzinfo=NY)
        with mock.patch.object(store, "now_ny", return_value=fetched):
            store.mark_synced("AAPL", "nasdaq", 700)
        later = dt.datetime(2026, 11, 19, 3, 0, tzinfo=NY)
        with mock.patch.object(store, "now_ny", return_value=later):
            self.assertFalse(store.needs_refresh("AAPL"))

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
