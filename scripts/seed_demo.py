#!/usr/bin/env python3
"""Añade o elimina datos de demostración para capturas y pruebas.

Uso:
    python3 scripts/seed_demo.py          # añade 4 posiciones y 3 alertas
    python3 scripts/seed_demo.py --clear  # elimina posiciones y alertas
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from stocklab import store  # noqa: E402

POSITIONS = [
    ("AAPL", 10, 300.0, 280.0, 400.0, "2026-06-01", "Demostración"),
    ("MSFT", 5, 450.0, 420.0, 550.0, "2026-07-01", "Demostración"),
    ("NVDA", 4, 130.0, 115.0, 180.0, "2026-08-01", "Demostración"),
    ("AMZN", 6, 200.0, 185.0, 260.0, "2026-09-01", "Demostración"),
]

ALERTS = [
    ("AAPL", "price_above", 400.0, None),
    ("MSFT", "price_below", 420.0, None),
    ("NVDA", "price_above", 180.0, None),
]


def seed() -> None:
    store.init()
    for symbol, shares, price, stop, target, entry_date, note in POSITIONS:
        store.add_position(symbol, shares, price, entry_date, stop, target, note)
    for symbol, kind, threshold, label in ALERTS:
        if not store.has_alert(symbol, kind, threshold):
            store.add_alert(symbol, kind, threshold, label)
    print(f"Añadidas {len(POSITIONS)} posiciones y {len(ALERTS)} alertas de demostración.")
    print("Recuerda: son datos ficticios para capturas, no posiciones reales.")


def clear() -> None:
    store.init()
    for position in store.list_positions():
        store.delete_position(position["id"])
    for alert in store.list_alerts():
        store.delete_alert(alert["id"])
    store.clear_alert_events()
    print("Datos de demostración eliminados.")


def main() -> int:
    parser = argparse.ArgumentParser(description="Datos de demostración de StockLab")
    parser.add_argument("--clear", action="store_true", help="elimina posiciones y alertas")
    args = parser.parse_args()
    clear() if args.clear else seed()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
