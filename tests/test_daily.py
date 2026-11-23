"""Pruebas del modo sin servidor: disparo diario y notificaciones."""
from __future__ import annotations

import datetime as dt
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import run
from localtickerlab import notify, store


class TestDailyTrigger(unittest.TestCase):
    def test_run_when_no_marker(self):
        with mock.patch("run._read_last_daily", return_value=None):
            self.assertTrue(run.should_run_daily())

    def test_skip_after_last_close(self):
        after = (store.last_market_close() + dt.timedelta(minutes=15)).isoformat()
        with mock.patch("run._read_last_daily", return_value=after):
            self.assertFalse(run.should_run_daily())

    def test_run_if_before_last_close(self):
        before = (store.last_market_close() - dt.timedelta(hours=3)).isoformat()
        with mock.patch("run._read_last_daily", return_value=before):
            self.assertTrue(run.should_run_daily())


class TestNotify(unittest.TestCase):
    def test_no_channels_is_silent(self):
        with mock.patch("localtickerlab.notify._macos", return_value=False), mock.patch(
            "localtickerlab.notify._telegram", return_value=False
        ):
            self.assertEqual(notify.send("t", "m"), [])

    def test_report_body(self):
        body = notify.report_body(
            {
                "done": 100,
                "errors": 2,
                "counts": {"COMPRA FUERTE": 5},
                "alerts": ["AAPL supera 400"],
                "top": [
                    {"symbol": "AAPL", "score": 88, "action": "COMPRA FUERTE", "target_3m": 400}
                ],
            }
        )
        self.assertIn("Analizados: 100", body)
        self.assertIn("COMPRA FUERTE", body)
        self.assertIn("AAPL supera 400", body)

    def test_write_report(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "report.json"
            notify.write_report(path, {"done": 1})
            self.assertEqual(json.loads(path.read_text())["done"], 1)


if __name__ == "__main__":
    unittest.main()
