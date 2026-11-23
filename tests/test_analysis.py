"""Pruebas de métricas, puntuación, filtros y caché SQLite."""
from __future__ import annotations

import datetime as dt
import tempfile
import unittest
from pathlib import Path

from localtickerlab import analysis, config, indicators as ind, store
from localtickerlab.screener import apply_filters


def make_bars(
    days: int = 400,
    start: float = 100.0,
    drift: float = 0.0015,
    vol: float = 0.006,
) -> list[dict]:
    bars = []
    price = start
    day = dt.date(2024, 1, 2)
    for i in range(days):
        # Onda suave para que RSI no quede siempre en un extremo.
        price *= 1 + drift + vol * ((i % 7) - 3) / 3
        date = day + dt.timedelta(days=i)
        bars.append(
            {
                "date": date.isoformat(),
                "open": price * 0.998,
                "high": price * 1.01,
                "low": price * 0.99,
                "close": price,
                "volume": 2_000_000 + (i % 11) * 50_000,
            }
        )
    return bars


class TestMetrics(unittest.TestCase):
    def test_metrics_on_uptrend(self):
        metrics = analysis.compute_metrics(make_bars())
        self.assertGreater(metrics["price"], 100)
        self.assertIsNotNone(metrics["rsi"])
        self.assertTrue(0 <= metrics["rsi"] <= 100)
        self.assertGreater(metrics["ret_6m"], 0)
        self.assertGreater(metrics["sma50"], metrics["sma200"])

    def test_returns_negative_in_downtrend(self):
        metrics = analysis.compute_metrics(make_bars(drift=-0.002))
        self.assertLess(metrics["ret_3m"], 0)
        self.assertGreater(metrics["max_drawdown"], 0)

    def test_insufficient_history_raises(self):
        with self.assertRaises(ValueError):
            analysis.compute_metrics(make_bars(days=10))

    def test_chart_lengths_match(self):
        bars = make_bars(days=300)
        chart = analysis.build_chart(bars, points=120)
        self.assertEqual(len(chart["dates"]), 120)
        self.assertEqual(len(chart["candles"]), 120)
        self.assertEqual(len(chart["sma20"]), 120)
        self.assertEqual(len(chart["rsi"]), 120)

    def test_beta_against_benchmark(self):
        bars = make_bars()
        benchmark = [
            {"date": b["date"], "close": b["close"] * 0.5, "open": 0, "high": 0, "low": 0}
            for b in bars
        ]
        metrics = analysis.compute_metrics(bars, benchmark)
        self.assertIsNotNone(metrics["beta"])


class TestScoring(unittest.TestCase):
    def test_bullish_beats_bearish(self):
        bull = analysis.compute_metrics(make_bars(drift=0.002))
        bear = analysis.compute_metrics(make_bars(drift=-0.002))
        self.assertGreater(
            analysis.score_metrics(bull)["composite"],
            analysis.score_metrics(bear)["composite"],
        )

    def test_scores_are_bounded(self):
        metrics = analysis.compute_metrics(make_bars())
        scores = analysis.score_metrics(metrics)
        self.assertTrue(0 <= scores["composite"] <= 100)
        for value in scores["subscores"].values():
            self.assertTrue(0 <= value <= 100, value)

    def test_signals_and_summary(self):
        metrics = analysis.compute_metrics(make_bars(drift=0.002))
        scores = analysis.score_metrics(metrics)
        signals = analysis.build_signals(metrics, scores)
        self.assertTrue(signals)
        summary = analysis._summary("TEST", metrics, scores)
        self.assertIn("TEST", summary)
        self.assertIn("/100", summary)

    def test_rating_labels(self):
        self.assertEqual(analysis.rating_label(90), "Muy fuerte")
        self.assertEqual(analysis.rating_label(20), "Muy débil")


class TestNoiseResilience(unittest.TestCase):
    def test_indicator_none_padding(self):
        series = [1.0, 2.0, 3.0]
        result = ind.sma(series, 5)
        self.assertEqual(result, [None, None, None])


class TestFilters(unittest.TestCase):
    def rows(self):
        return [
            {"symbol": "AAPL", "name": "Apple Inc. Common Stock", "price": 200,
             "market_cap": 3e12, "volume": 5e7, "sector": "Technology"},
            {"symbol": "ARM", "name": "Arm Holdings plc American Depositary Shares",
             "price": 120, "market_cap": 1e11, "volume": 5e6, "sector": "Technology"},
            {"symbol": "SPAC", "name": "Example Acquisition Corp",
             "price": 10, "market_cap": 5e8, "volume": 1e6, "sector": "Financial"},
            {"symbol": "FUND", "name": "Example ETF Trust",
             "price": 50, "market_cap": 1e9, "volume": 1e6, "sector": "Financial"},
            {"symbol": "TINY", "name": "Tiny Co", "price": 1.5,
             "market_cap": 1e8, "volume": 1e5, "sector": "Industrials"},
        ]

    def test_adr_is_kept(self):
        symbols = [r["symbol"] for r in apply_filters(self.rows(), {"min_price": 3, "min_market_cap": 0})]
        self.assertIn("ARM", symbols)

    def test_funds_excluded(self):
        symbols = [r["symbol"] for r in apply_filters(self.rows(), {"min_price": 0})]
        self.assertNotIn("FUND", symbols)
        self.assertNotIn("SPAC", symbols)

    def test_price_and_cap_filters(self):
        params = {"min_price": 5, "min_market_cap": 1e9}
        symbols = [r["symbol"] for r in apply_filters(self.rows(), params)]
        self.assertNotIn("TINY", symbols)
        self.assertIn("AAPL", symbols)

    def test_unknown_volume_does_not_fail(self):
        rows = [{"symbol": "NDX", "name": "Ndx Co", "price": 100, "market_cap": 5e9, "volume": None}]
        params = {"min_price": 3, "min_dollar_vol": 1e6}
        self.assertEqual(len(apply_filters(rows, params)), 1)

    def test_sector_filter(self):
        params = {"sectors": ["technology"]}
        symbols = [r["symbol"] for r in apply_filters(self.rows(), params)]
        self.assertEqual(set(symbols), {"AAPL", "ARM"})


class TestStore(unittest.TestCase):
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

    def test_roundtrip_bars(self):
        bars = make_bars(days=90)
        store.upsert_bars("TEST", bars)
        loaded = store.get_bars("TEST")
        self.assertEqual(len(loaded), 90)
        self.assertEqual(loaded[0]["date"], bars[0]["date"])
        self.assertAlmostEqual(loaded[-1]["close"], bars[-1]["close"])
        self.assertIsNotNone(store.summarize_symbol("TEST"))

    def test_upsert_updates_existing(self):
        bars = make_bars(days=60)
        store.upsert_bars("TEST", bars)
        changed = [dict(b) for b in bars]
        changed[-1]["close"] = 999.0
        store.upsert_bars("TEST", changed)
        self.assertAlmostEqual(store.get_bars("TEST")[-1]["close"], 999.0)
        self.assertEqual(len(store.get_bars("TEST")), 60)

    def test_score_cache_roundtrip(self):
        store.save_cached_score("AAPL", "2026-11-18", {"symbol": "AAPL", "score": 80})
        cached = store.get_cached_score("AAPL")
        self.assertEqual(cached["as_of"], "2026-11-18")
        self.assertEqual(cached["row"]["score"], 80)
        self.assertIsNotNone(cached["age_hours"])
        store.clear_score_cache()
        self.assertIsNone(store.get_cached_score("AAPL"))

    def test_data_quality_and_backup(self):
        store.mark_synced("BAD", "none", 0)
        quality = store.data_quality()
        self.assertIn("BAD", quality["failed"])
        self.assertGreaterEqual(quality["failed_count"], 1)
        self.assertGreater(store.clear_failed_fetches(), 0)
        self.assertNotIn("BAD", store.data_quality()["failed"])
        backup = store.backup_bytes()
        self.assertTrue(backup.startswith(b"SQLite format 3"))

    def test_export_import_user_data(self):
        store.add_position("AAPL", 2, 100.0, None, 90.0, 130.0, "demo")
        store.add_alert("AAPL", "price_below", 90.0, None)
        exported = store.export_user_data()
        self.assertEqual(len(exported["positions"]), 1)
        self.assertEqual(len(exported["alerts"]), 1)

        store.delete_position(exported["positions"][0]["id"])
        store.delete_alert(exported["alerts"][0]["id"])

        counts = store.import_user_data(exported)
        self.assertEqual(counts["positions"], 1)
        self.assertEqual(counts["alerts"], 1)
        # la alerta duplicada no se vuelve a crear
        again = store.import_user_data(exported)
        self.assertEqual(again["alerts"], 0)

    def test_universe_and_instruments(self):
        store.upsert_instruments(
            [{"symbol": "AAPL", "name": "Apple Inc.", "price": 200, "market_cap": 3e12}]
        )
        store.set_universe("ndx", ["AAPL"])
        self.assertEqual(store.get_universe("ndx"), ["AAPL"])
        found = store.search_instruments("app")
        self.assertEqual([row["symbol"] for row in found], ["AAPL"])


if __name__ == "__main__":
    unittest.main()


class TestEvaluation(unittest.TestCase):
    def _eval(self, drift: float):
        metrics = analysis.compute_metrics(make_bars(drift=drift))
        scores = analysis.score_metrics(metrics)
        return metrics, analysis.evaluate(metrics, scores)

    def test_bullish_signal_and_targets(self):
        metrics, ev = self._eval(0.002)
        self.assertIn(ev["action"], ("COMPRA", "COMPRA FUERTE"))
        self.assertGreater(ev["target_3m"], metrics["price"])
        self.assertLess(ev["stop"], metrics["price"])
        self.assertGreater(ev["risk_reward"], 0)
        self.assertGreater(ev["confidence"], 40)
        self.assertLess(ev["target_low"], ev["target_3m"])
        self.assertGreater(ev["target_high"], ev["target_3m"])

    def test_bearish_signal(self):
        metrics, ev = self._eval(-0.002)
        self.assertIn(ev["action"], ("VENTA", "VENTA FUERTE", "NEUTRAL"))
        self.assertLess(ev["composite"], 65)

    def test_stop_is_below_price_and_not_absurd(self):
        metrics, ev = self._eval(0.001)
        self.assertLess(ev["stop"], metrics["price"])
        self.assertGreater(ev["stop"], metrics["price"] * 0.7)

    def test_action_rank_matches_label(self):
        _, ev = self._eval(0.002)
        mapping = {"VENTA FUERTE": 0, "VENTA": 1, "NEUTRAL": 2, "COMPRA": 3, "COMPRA FUERTE": 4}
        self.assertEqual(mapping[ev["action"]], ev["action_rank"])

    def test_support_below_and_resistance_above(self):
        metrics, ev = self._eval(0.0015)
        if ev["support"] is not None:
            self.assertLessEqual(ev["support"], metrics["price"] * 1.001)
        if ev["resistance"] is not None:
            self.assertGreater(ev["resistance"], metrics["price"])
