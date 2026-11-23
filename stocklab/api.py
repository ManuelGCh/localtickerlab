"""Capa API JSON que consume el dashboard."""
from __future__ import annotations

import csv
import io
import json
import plistlib
import math
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs

from . import analysis, backtest, config, fundamentals, net, news, portfolio, scanstatus, store
from .screener import DataService, ScanBusy, ScreenerService, UniverseService, benchmark_bars

SYMBOL_RE = re.compile(r"^[A-Z0-9][A-Z0-9.\-]{0,9}$")
VERSION = "1.0.0"


@dataclass
class RawResponse:
    """Respuesta binaria/textual que el servidor envía tal cual."""

    body: bytes
    content_type: str
    filename: str | None = None


class ApiError(Exception):
    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.status = status


def _float(params: dict[str, list[str]], key: str, default: float = 0.0) -> float:
    raw = params.get(key, [""])[0]
    if raw in ("", None):
        return default
    try:
        return float(raw)
    except ValueError as exc:
        raise ApiError(f"parámetro inválido: {key}") from exc


def _bool(params: dict[str, list[str]], key: str, default: bool = False) -> bool:
    raw = params.get(key, [""])[0].lower()
    if raw in ("", None):
        return default
    return raw in ("1", "true", "yes", "si", "sí", "on")


def _list(params: dict[str, list[str]], key: str) -> list[str]:
    raw = params.get(key, [""])[0]
    if not raw:
        return []
    return [part.strip() for part in raw.split(",") if part.strip()]


class Api:
    def __init__(
        self,
        data: DataService,
        universes: UniverseService,
        screener: ScreenerService,
        backtests: backtest.BacktestService | None = None,
        fundamentals_service: fundamentals.FundamentalsService | None = None,
        news_service: news.NewsService | None = None,
    ) -> None:
        self.data = data
        self.universes = universes
        self.screener = screener
        self.backtests = backtests
        self.fundamentals_service = fundamentals_service
        self.news_service = news_service

    # ------------------------------------------------------------------ #
    def status(self, params: dict[str, list[str]]) -> dict[str, Any]:
        universes = {}
        for key in ("ndx", "all"):
            row = {"size": len(store.get_universe(key)), "age_hours": store.universe_age(key)}
            latest = self.screener.latest_meta(key)
            if latest:
                row["last_run"] = latest
            universes[key] = row
        return {
            "ok": True,
            "version": VERSION,
            "db": store.stats(),
            "universes": universes,
            "last_run": store.latest_run_time(),
            "weights": config.SCORE_WEIGHTS,
            "sources": config.DATA_SOURCES,
            "disclaimer": config.DISCLAIMER,
        }

    def universes_list(self, params: dict[str, list[str]]) -> dict[str, Any]:
        items = []
        for key, label in config.UNIVERSES.items():
            if key == "watchlist":
                items.append({"key": key, "label": label, "size": None})
                continue
            items.append(
                {
                    "key": key,
                    "label": label,
                    "size": len(store.get_universe(key)),
                    "age_hours": store.universe_age(key),
                }
            )
        return {"items": items, "default": "ndx"}

    def search(self, params: dict[str, list[str]]) -> dict[str, Any]:
        query = (params.get("q", [""])[0] or "").strip()
        if len(query) < 1:
            return {"items": []}
        try:
            items = self.universes.search(query, limit=12)
        except net.FetchError as exc:
            raise ApiError(f"no se pudo cargar el catálogo: {exc}", 503) from exc
        return {"items": items}

    # ------------------------------------------------------------------ #
    def analyze(self, params: dict[str, list[str]]) -> dict[str, Any]:
        raw = (params.get("symbol", [""])[0] or "").strip().upper()
        if not SYMBOL_RE.match(raw):
            raise ApiError("símbolo no válido")
        force = _bool(params, "force")
        try:
            bars = self.data.get_bars(raw, force=force)
        except net.FetchError as exc:
            raise ApiError(str(exc), 404) from exc

        instrument = store.get_instrument(raw) or {}
        bench = None
        if raw != "QQQ":
            bench = benchmark_bars(self.data)
        try:
            payload = analysis.analyze(raw, bars, instrument, bench)
        except ValueError as exc:
            raise ApiError(str(exc), 422) from exc
        payload["source"] = "cache" if not force else "live"
        return payload

    # ------------------------------------------------------------------ #
    def screen_start(self, params: dict[str, list[str]]) -> dict[str, Any]:
        universe = (params.get("universe", ["ndx"])[0] or "ndx").lower()
        if universe not in config.UNIVERSES:
            raise ApiError("universo desconocido")

        filters = {
            "min_price": _float(params, "min_price", config.DEFAULT_FILTERS["min_price"]),
            "min_market_cap": _float(
                params, "min_market_cap", config.DEFAULT_FILTERS["min_market_cap"]
            ),
            "min_dollar_vol": _float(
                params, "min_dollar_vol", config.DEFAULT_FILTERS["min_dollar_vol"]
            ),
            "exclude_funds": _bool(params, "exclude_funds", True),
            "max_symbols": int(_float(params, "max_symbols", 0)),
            "sectors": _list(params, "sectors"),
        }
        symbols = _list(params, "symbols")
        force = _bool(params, "force")

        if filters["max_symbols"] < 0:
            raise ApiError("el número de valores no puede ser negativo")
        if filters["min_price"] < 0 or filters["min_market_cap"] < 0:
            raise ApiError("los filtros no pueden ser negativos")

        if universe == "watchlist" and not symbols:
            raise ApiError("la watchlist está vacía")

        try:
            job = self.screener.start(universe, filters, symbols=symbols, force=force)
        except ScanBusy as exc:
            raise ApiError(str(exc), 409) from exc
        except net.FetchError as exc:
            raise ApiError(f"no se pudo cargar el universo: {exc}", 503) from exc
        return job.snapshot()

    def screen_find(self, params: dict[str, list[str]]) -> dict[str, Any]:
        """Busca en todas las filas de una ejecución, no solo en el top mostrado."""
        job_id = (params.get("id", [""])[0] or "").strip()
        query = (params.get("q", [""])[0] or "").strip()
        if not query:
            return {"rows": [], "total": 0, "q": ""}
        job = self.screener.get(job_id)
        if job:
            rows = job.rows if job.status == "running" else self.screener.refresh(job.rows)
        else:
            run = store.get_screen_run(job_id)
            if not run:
                raise ApiError("trabajo no encontrado", 404)
            rows = self.screener.refresh(run["rows"])
        needle = query.lower()
        upper = query.upper()

        def relevance(row: dict[str, Any]) -> int:
            symbol = (row.get("symbol") or "").upper()
            if symbol == upper:
                return 0
            if symbol.startswith(upper):
                return 1
            if upper in symbol:
                return 2
            name = (row.get("name") or "").lower()
            sector = (row.get("sector") or "").lower()
            if needle in name:
                return 3
            if needle in sector:
                return 4
            return 5

        matches = [
            row
            for row in rows
            if needle in (row.get("symbol") or "").lower()
            or needle in (row.get("name") or "").lower()
            or needle in (row.get("sector") or "").lower()
        ]
        matches.sort(key=lambda row: (relevance(row), -(row.get("score") or 0)))
        return {"rows": matches[:200], "total": len(matches), "q": query}

    def screen_all(self, params: dict[str, list[str]]) -> dict[str, Any]:
        job_id = (params.get("id", [""])[0] or "").strip()
        job = self.screener.get(job_id)
        if job:
            rows = job.rows if job.status == "running" else self.screener.refresh(job.rows)
            return {"id": job.id, "rows": rows}
        run = store.get_screen_run(job_id)
        if not run:
            raise ApiError("trabajo no encontrado", 404)
        return {"id": run["id"], "rows": self.screener.refresh(run["rows"])}

    def screen_status(self, params: dict[str, list[str]]) -> dict[str, Any]:
        job_id = (params.get("id", [""])[0] or "").strip()
        job = self.screener.get(job_id)
        if not job:
            raise ApiError("trabajo no encontrado", 404)
        snapshot = job.snapshot()
        if job.status != "running":
            snapshot["rows"] = self.screener.refresh(snapshot["rows"])
        return snapshot

    def screen_latest(self, params: dict[str, list[str]]) -> dict[str, Any]:
        universe = (params.get("universe", ["ndx"])[0] or "ndx").lower()
        if universe == "watchlist":
            return {"status": "empty", "rows": []}
        latest = self.screener.latest(universe)
        if not latest:
            return {"status": "empty", "rows": []}
        return latest

    def screen_export(self, params: dict[str, list[str]]) -> RawResponse:
        job_id = (params.get("id", [""])[0] or "").strip()
        rows: list[dict[str, Any]] = []
        if job_id:
            job = self.screener.get(job_id)
            if job:
                rows = job.rows if job.status == "running" else self.screener.refresh(job.rows)
            else:
                run = store.get_screen_run(job_id)
                rows = self.screener.refresh(run["rows"]) if run else []
        else:
            universe = (params.get("universe", ["ndx"])[0] or "ndx").lower()
            latest = self.screener.latest(universe)
            rows = latest.get("rows", []) if latest else []

        requested = {symbol.upper() for symbol in _list(params, "symbols")}
        if requested:
            rows = [row for row in rows if (row.get("symbol") or "").upper() in requested]

        if not rows:
            raise ApiError("no hay resultados para exportar", 404)

        def dec(value: Any, digits: int = 2) -> str:
            if value is None:
                return ""
            try:
                return f"{float(value):.{digits}f}".replace(".", ",")
            except (TypeError, ValueError):
                return str(value)

        def pct(value: Any, digits: int = 2) -> str:
            if value is None:
                return ""
            return dec(float(value) * 100, digits) + "%"

        buffer = io.StringIO()
        writer = csv.writer(buffer, delimiter=";", lineterminator="\n")
        writer.writerow(
            [
                "Simbolo", "Nombre", "Sector", "Precio", "Var. dia", "Senal",
                "Confianza", "Puntuacion", "Objetivo 3M", "Potencial 3M",
                "Objetivo 12M", "Stop", "R:R", "RSI", "Tendencia", "Momento",
                "Riesgo", "Retorno 1M", "Retorno 3M", "Retorno 6M", "Retorno 1A",
                "Volatilidad 60d", "Max. caida", "Cap. mercado", "Vol. dolares 20d",
                "Calidad", "Crec. ingresos", "Sorpresa EPS", "Dias a resultados",
                "Upside analistas", "PER", "Percentil", "RS vs QQQ 3M", "RS vs sector 3M",
            ]
        )
        for row in rows:
            sub = row.get("subscores") or {}
            writer.writerow(
                [
                    row.get("symbol", ""),
                    row.get("name", ""),
                    row.get("sector", ""),
                    dec(row.get("price")),
                    pct(row.get("change_pct")),
                    row.get("action", ""),
                    row.get("confidence", ""),
                    dec(row.get("score"), 1),
                    dec(row.get("target_3m")),
                    pct(row.get("upside_3m")),
                    dec(row.get("target_12m")),
                    dec(row.get("stop")),
                    dec(row.get("risk_reward")),
                    dec(row.get("rsi"), 1),
                    dec(sub.get("trend"), 0),
                    dec(sub.get("momentum"), 0),
                    dec(sub.get("risk"), 0),
                    pct(row.get("ret_1m")),
                    pct(row.get("ret_3m")),
                    pct(row.get("ret_6m")),
                    pct(row.get("ret_1y")),
                    pct(row.get("vol_60d")),
                    pct(row.get("max_drawdown")),
                    dec(row.get("market_cap"), 0),
                    dec(row.get("dollar_volume"), 0),
                    dec(row.get("quality"), 1),
                    pct(row.get("revenue_growth")),
                    pct(row.get("eps_surprise")),
                    row.get("days_to_earnings") if row.get("days_to_earnings") is not None else "",
                    pct(row.get("analyst_upside")),
                    dec(row.get("pe"), 1),
                    dec(row.get("pct_score"), 0),
                    pct(row.get("rs_qqq_3m")),
                    pct(row.get("rs_sector_3m")),
                ]
            )
        body = ("\ufeff" + buffer.getvalue()).encode("utf-8")
        return RawResponse(body, "text/csv; charset=utf-8", "stocklab_screener.csv")

    def screen_cancel(self, params: dict[str, list[str]]) -> dict[str, Any]:
        job_id = (params.get("id", [""])[0] or "").strip()
        job = self.screener.get(job_id)
        if not job:
            raise ApiError("trabajo no encontrado", 404)
        job.cancel()
        return {"ok": True}

    def fundamentals(self, params: dict[str, list[str]]) -> dict[str, Any]:
        if not self.fundamentals_service:
            raise ApiError("fundamentales no disponibles", 503)
        symbol = (params.get("symbol", [""])[0] or "").strip().upper()
        if not SYMBOL_RE.match(symbol):
            raise ApiError("símbolo no válido")
        try:
            payload = self.fundamentals_service.get(symbol, force=_bool(params, "force"))
        except net.NoDataError as exc:
            raise ApiError(str(exc), 404) from exc
        except net.FetchError as exc:
            raise ApiError(str(exc), 503) from exc
        if not payload.get("quarters"):
            raise ApiError(f"{symbol}: sin estados financieros disponibles", 404)
        return payload

    def fundamentals_enrich(self, params: dict[str, list[str]]) -> dict[str, Any]:
        if not self.fundamentals_service:
            raise ApiError("fundamentales no disponibles", 503)
        job_id = (params.get("id", [""])[0] or "").strip()
        job = self.screener.get(job_id)
        if not job:
            raise ApiError(
                "ejecución no encontrada; vuelve a lanzar el screener para enriquecer",
                404,
            )
        if job.status == "running":
            raise ApiError("espera a que termine el screener antes de enriquecer", 409)
        limit = int(_float(params, "limit", 300))
        return job.start_enrichment(self.fundamentals_service, limit=max(10, min(1000, limit)))

    # ------------------------------------------------------------------ #
    def scan_status(self, params: dict[str, list[str]]) -> dict[str, Any]:
        return scanstatus.read()

    def system(self, params: dict[str, list[str]]) -> dict[str, Any]:
        plist = Path.home() / "Library" / "LaunchAgents" / "com.stocklab.daily.plist"
        interval = None
        if plist.exists():
            try:
                interval = plistlib.loads(plist.read_bytes()).get("StartInterval")
            except (OSError, ValueError, plistlib.InvalidFileException):
                interval = None
        log_path = config.DATA_DIR / "scan.log"
        log_tail = ""
        if log_path.exists():
            try:
                log_tail = log_path.read_text(errors="replace")[-1500:]
            except OSError:
                log_tail = ""
        return {
            "scheduler": {
                "installed": plist.exists(),
                "path": str(plist),
                "interval": interval,
            },
            "last_daily": store.daily_marker(),
            "last_run": store.latest_run_time(),
            "quality": store.data_quality(),
            "db": store.stats(),
            "sources": config.DATA_SOURCES,
            "log_tail": log_tail,
            "scan": scanstatus.read(),
        }

    def system_retry(self, params: dict[str, list[str]]) -> dict[str, Any]:
        cleared = store.clear_failed_fetches()
        self.data.clear_failed()
        return {"ok": True, "cleared": cleared}

    def user_export(self, params: dict[str, list[str]]) -> RawResponse:
        body = json.dumps(store.export_user_data(), ensure_ascii=False, indent=2).encode()
        return RawResponse(body, "application/json", "stocklab_datos.json")

    def user_import(self, payload: dict[str, Any]) -> dict[str, Any]:
        if not isinstance(payload, dict):
            raise ApiError("formato de importación no válido")
        clean = {"positions": [], "alerts": [], "watchlist": [], "views": []}
        for item in payload.get("positions") or []:
            if not isinstance(item, dict):
                continue
            symbol = str(item.get("symbol") or "").strip().upper()
            if not SYMBOL_RE.match(symbol):
                continue
            clean["positions"].append({**item, "symbol": symbol})
        for item in payload.get("alerts") or []:
            if not isinstance(item, dict):
                continue
            symbol = str(item.get("symbol") or "").strip().upper()
            kind = str(item.get("kind") or "")
            if not SYMBOL_RE.match(symbol) or kind not in portfolio.ALERT_KINDS:
                continue
            if kind == "signal":
                if item.get("label") not in portfolio.SIGNALS:
                    continue
            else:
                try:
                    threshold = float(item.get("threshold") or 0)
                except (TypeError, ValueError):
                    continue
                if not math.isfinite(threshold) or threshold <= 0:
                    continue
            clean["alerts"].append({**item, "symbol": symbol})
        for symbol in payload.get("watchlist") or []:
            value = str(symbol or "").strip().upper()
            if SYMBOL_RE.match(value):
                clean["watchlist"].append(value)
        for item in payload.get("views") or []:
            if not isinstance(item, dict):
                continue
            name = str(item.get("name") or "").strip()
            config = item.get("config")
            if name and len(name) <= 80 and isinstance(config, dict):
                clean["views"].append({"name": name, "config": config})
        return {"ok": True, **store.import_user_data(clean)}

    def backup(self, params: dict[str, list[str]]) -> RawResponse:
        body = store.backup_bytes()
        filename = f"stocklab_backup_{store.now_ny().date().isoformat()}.db"
        return RawResponse(body, "application/octet-stream", filename)

    def watchlist_get(self, params: dict[str, list[str]]) -> dict[str, Any]:
        return {"symbols": store.list_watchlist()}

    def watchlist_add(self, params: dict[str, list[str]]) -> dict[str, Any]:
        symbol = (params.get("symbol", [""])[0] or "").strip().upper()
        if not SYMBOL_RE.match(symbol):
            raise ApiError("símbolo no válido")
        store.add_watch(symbol)
        return {"symbols": store.list_watchlist()}

    def watchlist_remove(self, params: dict[str, list[str]]) -> dict[str, Any]:
        symbol = (params.get("symbol", [""])[0] or "").strip().upper()
        if symbol:
            store.remove_watch(symbol)
        return {"symbols": store.list_watchlist()}

    def watchlist_replace(self, params: dict[str, list[str]]) -> dict[str, Any]:
        symbols = [
            symbol
            for symbol in _list(params, "symbols")
            if SYMBOL_RE.match(symbol.upper())
        ]
        store.replace_watchlist(symbols)
        return {"symbols": store.list_watchlist()}

    def watchlist_clear(self, params: dict[str, list[str]]) -> dict[str, Any]:
        store.clear_watchlist()
        return {"symbols": []}

    def views_list(self, params: dict[str, list[str]]) -> dict[str, Any]:
        return {"views": store.list_views()}

    def views_save(self, params: dict[str, list[str]]) -> dict[str, Any]:
        name = (params.get("name", [""])[0] or "").strip()
        raw = params.get("config", [""])[0] or ""
        if not name or len(name) > 80:
            raise ApiError("nombre de vista no válido")
        if len(raw) > 20_000:
            raise ApiError("vista demasiado grande")
        try:
            config = json.loads(raw)
        except (TypeError, ValueError, json.JSONDecodeError) as exc:
            raise ApiError("configuración de vista no válida") from exc
        if not isinstance(config, dict):
            raise ApiError("configuración de vista no válida")
        store.save_view(name, config)
        return {"views": store.list_views()}

    def views_delete(self, params: dict[str, list[str]]) -> dict[str, Any]:
        name = (params.get("name", [""])[0] or "").strip()
        if name:
            store.delete_view(name)
        return {"views": store.list_views()}

    def alerts_list(self, params: dict[str, list[str]]) -> dict[str, Any]:
        events = store.list_alert_events(50)
        return {
            "alerts": store.list_alerts(),
            "events": events,
            "unread": sum(1 for event in events if not event.get("read")),
            "kinds": portfolio.ALERT_KINDS,
            "signals": portfolio.SIGNALS,
        }

    def alerts_add(self, params: dict[str, list[str]]) -> dict[str, Any]:
        symbol = (params.get("symbol", [""])[0] or "").strip().upper()
        if not SYMBOL_RE.match(symbol):
            raise ApiError("símbolo no válido")
        kind = (params.get("kind", [""])[0] or "").strip()
        if kind not in portfolio.ALERT_KINDS:
            raise ApiError("tipo de alerta no válido")
        label = (params.get("label", [""])[0] or "").strip().upper() or None
        threshold: float | None = None
        if kind == "signal":
            if label not in portfolio.SIGNALS:
                raise ApiError("señal no válida")
        else:
            threshold = _float(params, "threshold", 0.0)
            if not math.isfinite(threshold) or threshold <= 0:
                raise ApiError("el umbral debe ser positivo")
            upper = 100.0 if kind in (
                "score_above",
                "score_below",
                "rsi_above",
                "rsi_below",
                "high_52w",
            ) else None
            if upper is not None and threshold > upper:
                raise ApiError(f"el umbral debe estar entre 0 y {upper:.0f}")
        alert_id = store.add_alert(symbol, kind, threshold, label)
        return {"ok": True, "id": alert_id}

    def alerts_delete(self, params: dict[str, list[str]]) -> dict[str, Any]:
        alert_id = int(_float(params, "id", 0))
        store.delete_alert(alert_id)
        return {"ok": True}

    def alerts_toggle(self, params: dict[str, list[str]]) -> dict[str, Any]:
        alert_id = int(_float(params, "id", 0))
        active = _bool(params, "active", True)
        store.set_alert_active(alert_id, active)
        return {"ok": True, "active": active}

    def alerts_check(self, params: dict[str, list[str]]) -> dict[str, Any]:
        triggered = portfolio.check_alerts(self.data, self.fundamentals_service)
        events = store.list_alert_events(50)
        return {
            "triggered": triggered,
            "events": events,
            "unread": sum(1 for event in events if not event.get("read")),
        }

    def alerts_read(self, params: dict[str, list[str]]) -> dict[str, Any]:
        store.mark_alert_events_read()
        return {"ok": True}

    def portfolio_get(self, params: dict[str, list[str]]) -> dict[str, Any]:
        return portfolio.portfolio_snapshot(self.data)

    def portfolio_add(self, params: dict[str, list[str]]) -> dict[str, Any]:
        symbol = (params.get("symbol", [""])[0] or "").strip().upper()
        if not SYMBOL_RE.match(symbol):
            raise ApiError("símbolo no válido")
        shares = _float(params, "shares", 0.0)
        price = _float(params, "price", 0.0)
        if (
            not math.isfinite(shares)
            or not math.isfinite(price)
            or shares <= 0
            or price <= 0
        ):
            raise ApiError("acciones y precio deben ser positivos")
        stop = _float(params, "stop", 0.0) or None
        target = _float(params, "target", 0.0) or None
        if stop is not None and (not math.isfinite(stop) or stop <= 0):
            raise ApiError("el stop debe ser positivo")
        if target is not None and (not math.isfinite(target) or target <= 0):
            raise ApiError("el objetivo debe ser positivo")
        entry_date = (params.get("date", [""])[0] or "").strip() or None
        note = (params.get("note", [""])[0] or "").strip()
        position_id = store.add_position(
            symbol, shares, price, entry_date, stop, target, note
        )
        created = []
        for kind, value in (("price_below", stop), ("price_above", target)):
            if value and not store.has_alert(symbol, kind, value):
                store.add_alert(symbol, kind, value, None)
                created.append({"kind": kind, "threshold": value})
        return {"ok": True, "id": position_id, "alerts_created": created}

    def portfolio_update(self, params: dict[str, list[str]]) -> dict[str, Any]:
        position_id = int(_float(params, "id", 0))
        if not store.get_position(position_id):
            raise ApiError("posición no encontrada", 404)
        symbol = (params.get("symbol", [""])[0] or "").strip().upper()
        if not SYMBOL_RE.match(symbol):
            raise ApiError("símbolo no válido")
        shares = _float(params, "shares", 0.0)
        price = _float(params, "price", 0.0)
        if (
            not math.isfinite(shares)
            or not math.isfinite(price)
            or shares <= 0
            or price <= 0
        ):
            raise ApiError("acciones y precio deben ser positivos")
        stop = _float(params, "stop", 0.0) or None
        target = _float(params, "target", 0.0) or None
        if stop is not None and (not math.isfinite(stop) or stop <= 0):
            raise ApiError("el stop debe ser positivo")
        if target is not None and (not math.isfinite(target) or target <= 0):
            raise ApiError("el objetivo debe ser positivo")
        entry_date = (params.get("date", [""])[0] or "").strip() or None
        note = (params.get("note", [""])[0] or "").strip()
        previous = store.get_position(position_id) or {}
        store.update_position(
            position_id, symbol, shares, price, entry_date, stop, target, note
        )
        others = [p for p in store.list_positions() if p["id"] != position_id]
        for kind, field, new_value in (
            ("price_below", "stop", stop),
            ("price_above", "target", target),
        ):
            old_value = previous.get(field)
            if old_value and old_value != new_value:
                still_used = any(
                    p.get("symbol") == previous.get("symbol")
                    and p.get(field) == old_value
                    for p in others
                )
                if not still_used:
                    store.delete_alert_matching(previous["symbol"], kind, old_value)
            if new_value and not store.has_alert(symbol, kind, new_value):
                store.add_alert(symbol, kind, new_value, None)
        return {"ok": True, "id": position_id}

    def portfolio_delete(self, params: dict[str, list[str]]) -> dict[str, Any]:
        position_id = int(_float(params, "id", 0))
        position = store.get_position(position_id)
        if position:
            others = [p for p in store.list_positions() if p["id"] != position_id]
            for kind, field in (("price_below", "stop"), ("price_above", "target")):
                value = position.get(field)
                if not value:
                    continue
                still_used = any(
                    p.get("symbol") == position["symbol"]
                    and p.get(field) == value
                    for p in others
                )
                if not still_used:
                    store.delete_alert_matching(position["symbol"], kind, value)
        store.delete_position(position_id)
        return {"ok": True}

    def stock_news(self, params: dict[str, list[str]]) -> dict[str, Any]:
        if not self.news_service:
            raise ApiError("noticias no disponibles", 503)
        symbol = (params.get("symbol", [""])[0] or "").strip().upper()
        if not SYMBOL_RE.match(symbol):
            raise ApiError("símbolo no válido")
        try:
            return self.news_service.get(symbol, force=_bool(params, "force"))
        except net.FetchError as exc:
            raise ApiError(f"no se pudieron cargar las noticias: {exc}", 503) from exc

    # ------------------------------------------------------------------ #
    def backtest_start(self, params: dict[str, list[str]]) -> dict[str, Any]:
        if not self.backtests:
            raise ApiError("backtesting no disponible", 503)
        cfg = backtest.BacktestConfig(
            max_symbols=int(_float(params, "symbols", 300)),
            horizon=int(_float(params, "horizon", 21)),
            rebalance=int(_float(params, "rebalance", 21)),
            years=_float(params, "years", 3),
            cost_bps=_float(params, "cost", 10),
            top_n=int(_float(params, "top_n", 20)),
            include_quality=_bool(params, "quality"),
        )
        if cfg.max_symbols < 20 or cfg.max_symbols > 2000:
            raise ApiError("número de símbolos fuera de rango (20-2000)")
        if cfg.horizon not in (5, 10, 21, 42, 63, 126):
            raise ApiError("horizonte no válido")
        if cfg.rebalance < 5 or cfg.rebalance > 126:
            raise ApiError("rebalanceo fuera de rango (5-126)")
        if cfg.years < 1 or cfg.years > 9:
            raise ApiError("años fuera de rango (1-9)")
        if cfg.top_n < 1 or cfg.top_n > 200:
            raise ApiError("cartera fuera de rango (1-200)")
        if cfg.cost_bps < 0 or cfg.cost_bps > 500:
            raise ApiError("coste fuera de rango (0-500 pb)")
        job = self.backtests.start(cfg)
        return job.snapshot()

    def backtest_status(self, params: dict[str, list[str]]) -> dict[str, Any]:
        if not self.backtests:
            raise ApiError("backtesting no disponible", 503)
        job_id = (params.get("id", [""])[0] or "").strip()
        job = self.backtests.get(job_id)
        if not job:
            raise ApiError("trabajo no encontrado", 404)
        return job.snapshot()

    def backtest_cancel(self, params: dict[str, list[str]]) -> dict[str, Any]:
        if not self.backtests:
            raise ApiError("backtesting no disponible", 503)
        job_id = (params.get("id", [""])[0] or "").strip()
        job = self.backtests.cancel(job_id)
        if not job:
            raise ApiError("trabajo no encontrado", 404)
        return job

    def backtest_latest(self, params: dict[str, list[str]]) -> dict[str, Any]:
        if not self.backtests:
            raise ApiError("backtesting no disponible", 503)
        latest = self.backtests.latest()
        if not latest:
            return {"status": "empty"}
        return latest

    def backtest_calibrate(self, params: dict[str, list[str]]) -> dict[str, Any]:
        if not self.backtests:
            raise ApiError("backtesting no disponible", 503)
        job_id = (params.get("id", [""])[0] or "").strip()
        job = self.backtests.get(job_id)
        if not job or not job.observations:
            raise ApiError(
                "no hay observaciones en memoria; vuelve a ejecutar el backtest para calibrar",
                409,
            )
        samples = int(_float(params, "samples", 40))
        result = backtest.calibrate(job.observations, samples=max(10, min(120, samples)))
        job.calibration = result
        result["run_id"] = job.id
        return result

