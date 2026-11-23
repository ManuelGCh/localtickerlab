"""Pruebas del estado compartido del escaneo."""
from __future__ import annotations

import datetime as dt
import json
import tempfile
import unittest
from pathlib import Path

from localtickerlab import scanstatus, store


class TestScanStatus(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self._original = scanstatus.PATH
        scanstatus.PATH = Path(self.tmp.name) / "progress.json"

    def tearDown(self):
        scanstatus.PATH = self._original
        self.tmp.cleanup()

    def test_roundtrip(self):
        scanstatus.write(running=True, phase="precios", done=5, total=10, errors=0)
        data = scanstatus.read()
        self.assertTrue(data["running"])
        self.assertEqual(data["done"], 5)
        self.assertEqual(data["total"], 10)

    def test_finish(self):
        scanstatus.write(running=True, phase="precios")
        scanstatus.finish(phase="done", done=10, total=10)
        data = scanstatus.read()
        self.assertFalse(data["running"])
        self.assertEqual(data["phase"], "done")

    def test_stale(self):
        old = (store.now_ny() - dt.timedelta(minutes=10)).isoformat()
        scanstatus.PATH.write_text(
            json.dumps({"running": True, "phase": "precios", "updated_at": old})
        )
        self.assertTrue(scanstatus.read().get("stale"))


if __name__ == "__main__":
    unittest.main()
