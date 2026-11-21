#!/usr/bin/env python3
"""StockLab · analizador de acciones del NASDAQ.

Uso:
    python3 run.py                 # abre el dashboard en el navegador
    python3 run.py --no-open       # solo arranca el servidor
    python3 run.py --port 9000     # puerto concreto
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
import threading
import time
import webbrowser

if sys.version_info < (3, 10):
    sys.stderr.write(
        "StockLab necesita Python 3.10 o superior.\n"
        f"Estás usando Python {sys.version.split()[0]} en {sys.executable}.\n"
        "Prueba con: /opt/homebrew/bin/python3 run.py\n"
    )
    raise SystemExit(1)

from stocklab import config, notify, scanstatus, store
from stocklab.api import Api
from stocklab.backtest import BacktestData, BacktestService
from stocklab.fundamentals import FundamentalsService
from stocklab.news import NewsService
from stocklab import portfolio
from stocklab.screener import DataService, ScreenerService, UniverseService
from stocklab.server import create_server, find_port


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Dashboard local de análisis de acciones NASDAQ")
    parser.add_argument("--host", default=config.HOST, help="interfaz de escucha (por defecto 127.0.0.1)")
    parser.add_argument("--port", type=int, default=config.PORT, help="puerto (por defecto 8765)")
    parser.add_argument("--no-open", action="store_true", help="no abrir el navegador automáticamente")
    parser.add_argument("--quiet", action="store_true", help="menos mensajes en consola")
    parser.add_argument(
        "--scan-once",
        action="store_true",
        help="ejecuta un escaneo completo, imprime el resumen y sale (para cron/launchd)",
    )
    parser.add_argument(
        "--universe",
        choices=["ndx", "all"],
        default="all",
        help="universo para --scan-once (por defecto all)",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=0,
        help="nº máximo de valores en --scan-once (0 = todos)",
    )
    parser.add_argument(
        "--enrich",
        type=int,
        default=0,
        metavar="N",
        help="enriquece con fundamentales los N primeros tras el escaneo",
    )
    parser.add_argument(
        "--daily-if-needed",
        action="store_true",
        help="escanea solo si los datos son anteriores al último cierre (para launchd)",
    )
    parser.add_argument(
        "--no-notify",
        action="store_true",
        help="no enviar notificación al terminar --scan-once/--daily-if-needed",
    )
    return parser.parse_args()


DAILY_MARKER = config.DATA_DIR / "last_daily.json"


def _read_last_daily() -> str | None:
    try:
        return json.loads(DAILY_MARKER.read_text()).get("at")
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        return None


def should_run_daily() -> bool:
    marker = _read_last_daily()
    if not marker:
        return True
    try:
        stamp = dt.datetime.fromisoformat(marker)
    except (TypeError, ValueError):
        return True
    close = store.last_market_close()
    if stamp.tzinfo is None:
        stamp = stamp.replace(tzinfo=close.tzinfo)
    return stamp < close


def run_scan_once(args, screener, fundamentals, data) -> int:
    params = {
        "min_price": 1,
        "min_market_cap": 0,
        "min_dollar_vol": 0,
        "max_symbols": args.limit,
        "exclude_funds": True,
    }
    label = "NASDAQ-100" if args.universe == "ndx" else "NASDAQ Composite"
    scanstatus.write(
        running=True,
        phase="preparando",
        done=0,
        total=0,
        errors=0,
        universe=args.universe,
        label=label,
        message="Preparando escaneo",
        started_at=store.now_ny().isoformat(timespec="seconds"),
    )
    print(f"Escaneando {label}…")
    job = screener.start(args.universe, params)
    scanstatus.write(phase="precios", total=job.total, done=0, errors=0, message="Descargando precios")
    while job.status == "running":
        time.sleep(3)
        scanstatus.write(
            phase="precios",
            done=job.done,
            total=job.total,
            errors=job.errors,
            message="Descargando precios y calculando señales",
        )
        print(f"\r  {job.done}/{job.total} valores", end="", flush=True)
    print()
    if job.status != "done":
        message = job.error or job.status
        scanstatus.finish(phase="error", message=message)
        notify.send("StockLab · error de escaneo", message)
        print(f"Error: {message}")
        return 1

    if args.enrich:
        print(f"Enriqueciendo los {args.enrich} primeros con fundamentales…")
        job.start_enrichment(fundamentals, args.enrich)
        scanstatus.write(
            phase="enriquecimiento",
            done=0,
            total=job.enrich_total,
            errors=job.enrich_errors,
            message="Enriqueciendo con fundamentales",
        )
        while job.enrich_status == "running":
            time.sleep(2)
            scanstatus.write(
                phase="enriquecimiento",
                done=job.enrich_done,
                total=job.enrich_total,
                errors=job.enrich_errors,
                message="Enriqueciendo con fundamentales",
            )
            print(
                f"\r  {job.enrich_done}/{job.enrich_total} enriquecidos",
                end="",
                flush=True,
            )
        print()

    counts = {}
    for row in job.rows:
        counts[row.get("action")] = counts.get(row.get("action"), 0) + 1

    scanstatus.write(phase="alertas", done=job.done, total=job.total, message="Comprobando alertas")
    triggered = []
    try:
        triggered = portfolio.check_alerts(data, fundamentals)
    except Exception as exc:  # noqa: BLE001 - las alertas no deben tumbar el escaneo
        print(f"Aviso: no se pudieron comprobar las alertas ({exc})")

    summary = {
        "at": store.now_ny().isoformat(timespec="seconds"),
        "universe": args.universe,
        "done": job.done,
        "errors": job.errors,
        "counts": counts,
        "alerts": [item["message"] for item in triggered],
        "top": [
            {
                "symbol": row["symbol"],
                "score": row["score"],
                "action": row["action"],
                "target_3m": row.get("target_3m"),
                "upside_3m": row.get("upside_3m"),
            }
            for row in job.rows[:10]
        ],
    }
    scanstatus.finish(
        phase="done",
        done=job.done,
        total=job.total,
        errors=job.errors,
        alerts=summary["alerts"],
        counts=counts,
        message="Escaneo completado",
        finished_at=store.now_ny().isoformat(timespec="seconds"),
    )
    notify.write_report(config.DATA_DIR / "daily_report.json", summary)
    try:
        DAILY_MARKER.write_text(json.dumps(summary, ensure_ascii=False))
    except OSError:
        pass

    print(f"Analizados: {job.done} · errores: {job.errors}")
    print("Señales:", ", ".join(f"{k}: {v}" for k, v in counts.items()) or "sin datos")
    if summary["alerts"]:
        print("Alertas:", "; ".join(summary["alerts"]))
    print("Top 10:")
    for row in summary["top"]:
        target = row.get("target_3m")
        print(
            f"  {row['symbol']:6} {row['score']:5.1f}  {row['action']:13} "
            f"obj {('%.2f' % target) if target else '—':>9}  "
            f"({(row.get('upside_3m') or 0) * 100:+.1f}%)"
        )

    if not args.no_notify:
        channels = notify.send("StockLab · resumen diario", notify.report_body(summary))
        if channels:
            print("Notificación enviada por:", ", ".join(channels))
    return 0


def rotate_scan_log() -> None:
    path = config.DATA_DIR / "scan.log"
    try:
        if path.exists() and path.stat().st_size > 5_000_000:
            target = config.DATA_DIR / "scan.log.1"
            if target.exists():
                target.unlink()
            path.replace(target)
    except OSError:
        pass


def main() -> int:
    args = parse_args()
    store.init()
    rotate_scan_log()
    try:
        store.prune()
    except Exception:  # noqa: BLE001 - la limpieza nunca debe impedir arrancar
        pass

    data = DataService()
    universes = UniverseService()
    screener = ScreenerService(data, universes)
    backtests = BacktestService(BacktestData())
    fundamentals = FundamentalsService()
    api = Api(data, universes, screener, backtests, fundamentals, NewsService())

    if args.daily_if_needed:
        if not should_run_daily():
            print("Sin cambios: los datos ya están actualizados tras el último cierre.")
            return 0
        args.scan_once = True

    if args.scan_once:
        try:
            return run_scan_once(args, screener, fundamentals, data)
        except Exception as exc:  # noqa: BLE001
            scanstatus.finish(phase="error", message=str(exc))
            notify.send("StockLab · error de escaneo", str(exc))
            print(f"Error: {exc}")
            return 1
    port = find_port(args.host, args.port)
    server = create_server(args.host, port, api)
    url = f"http://{args.host}:{port}"

    print()
    print("  StockLab — analizador NASDAQ")
    print(f"  Dashboard: {url}")
    print(f"  Base de datos: {config.DB_PATH}")
    print("  Ctrl+C para detener.")
    print()

    if not args.no_open:
        threading.Timer(0.7, lambda: webbrowser.open(url)).start()

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nDeteniendo…")
    finally:
        server.shutdown()
        server.server_close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
