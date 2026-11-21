"""Servicio de datos y motor del screener con trabajos en segundo plano."""
from __future__ import annotations

import bisect
import concurrent.futures as futures
import datetime as dt
import re
import threading
import time
import uuid
from typing import Any, Callable, Iterable

from . import analysis, config, net, store
from .sources import nasdaq, yahoo

FUND_PATTERN = re.compile(
    r"\b(etf|fund|warrants?|rights?|units?|preferred|acquisition\s+corp)\b",
    re.IGNORECASE,
)


class DataService:
    """Descarga históricos con caché en SQLite y reintentos entre fuentes."""

    def __init__(self) -> None:
        self._locks: dict[str, threading.Lock] = {}
        self._guard = threading.Lock()
        self._failed_until: dict[str, float] = {}

    def _lock_for(self, symbol: str) -> threading.Lock:
        with self._guard:
            lock = self._locks.get(symbol)
            if lock is None:
                lock = threading.Lock()
                self._locks[symbol] = lock
            return lock

    def get_bars(self, symbol: str, force: bool = False) -> list[dict[str, Any]]:
        symbol = symbol.strip().upper()
        with self._lock_for(symbol):
            cached = [] if force else store.get_bars(symbol)
            if not force and not store.needs_refresh(symbol):
                if len(cached) >= 30:
                    return cached
                raise net.FetchError(f"{symbol}: sin datos disponibles")

            # Si solo faltan los últimos días, pide un rango corto y fusiona.
            if not force and len(cached) >= 60:
                try:
                    last = dt.date.fromisoformat(cached[-1]["date"])
                    gap = (store.now_ny().date() - last).days
                except (ValueError, TypeError):
                    gap = 999
                if 0 <= gap <= 45:
                    try:
                        payload = nasdaq.fetch_history(
                            symbol, days=max(10, gap + 7), min_bars=1
                        )
                        if payload.get("bars"):
                            store.upsert_bars(symbol, payload["bars"])
                        merged = store.get_bars(symbol)
                        store.mark_synced(symbol, "nasdaq", len(merged))
                        self._failed_until.pop(symbol, None)
                        return merged
                    except net.NoDataError:
                        if len(cached) >= 30:
                            # Sin novedades (festivo, fin de semana): sirve la caché.
                            store.mark_synced(symbol, "cache", len(cached))
                            return cached
                        self._failed_until[symbol] = time.time() + 12 * 3600
                        store.mark_synced(symbol, "none", 0)
                        raise
                    except (net.FetchError, KeyError, ValueError, TypeError):
                        pass

            failed_until = self._failed_until.get(symbol, 0)
            if not force and failed_until > time.time():
                if len(cached) >= 30:
                    return cached
                raise net.FetchError(f"{symbol}: sin datos (reintento más tarde)")

            last_error: Exception | None = None
            for source, fetcher in (
                ("nasdaq", nasdaq.fetch_history),
                ("yahoo", yahoo.fetch_history),
            ):
                try:
                    payload = fetcher(symbol)
                    bars = payload["bars"]
                    store.upsert_bars(symbol, bars)
                    store.mark_synced(symbol, source, len(bars))
                    self._failed_until.pop(symbol, None)
                    meta = payload.get("meta") or {}
                    if meta.get("name") and not store.get_instrument(symbol):
                        store.upsert_instruments(
                            [
                                {
                                    "symbol": symbol,
                                    "name": meta.get("name") or "",
                                    "kind": "stock",
                                }
                            ]
                        )
                    return store.get_bars(symbol)
                except net.NoDataError:
                    self._failed_until[symbol] = time.time() + 12 * 3600
                    store.mark_synced(symbol, "none", 0)
                    raise
                except (net.FetchError, KeyError, ValueError, TypeError) as exc:
                    last_error = exc

            self._failed_until[symbol] = time.time() + 6 * 3600
            cached = store.get_bars(symbol)
            if len(cached) >= 30:
                return cached
            raise net.FetchError(f"{symbol}: no se encontraron datos (¿ticker correcto?)")

class UniverseService:
    """Carga y cachea los listados de acciones (NASDAQ-100 y Composite)."""

    def __init__(self) -> None:
        self._lock = threading.Lock()

    def get(self, key: str, force: bool = False) -> list[dict[str, Any]]:
        key = key if key in ("ndx", "all") else "all"
        with self._lock:
            age = store.universe_age(key)
            symbols = store.get_universe(key)
            if force or not symbols or age is None or age > 24:
                try:
                    rows = nasdaq.fetch_nasdaq100() if key == "ndx" else nasdaq.fetch_universe()
                    store.upsert_instruments(rows)
                    store.set_universe(key, [row["symbol"] for row in rows])
                    symbols = [row["symbol"] for row in rows]
                except net.FetchError:
                    if not symbols:
                        raise

            instruments = store.instruments_for(symbols)
            return [instruments[s] for s in symbols if s in instruments]

    def search(self, query: str, limit: int = 12) -> list[dict[str, Any]]:
        query = (query or "").strip()
        if not query:
            return []
        found = store.search_instruments(query, limit)
        if not found:
            # Si aún no hay catálogo, intenta cargarlo una vez.
            try:
                self.get("ndx")
            except net.FetchError:
                return []
            found = store.search_instruments(query, limit)
        return found


def _median(values: list[float]) -> float | None:
    values = sorted(v for v in values if v is not None)
    if not values:
        return None
    mid = len(values) // 2
    return values[mid] if len(values) % 2 else (values[mid - 1] + values[mid]) / 2.0


def annotate_relative(
    rows: list[dict[str, Any]], qqq_bars: list[dict[str, Any]] | None = None
) -> dict[str, Any]:
    """Percentiles, fuerza relativa y régimen de mercado a partir de las filas."""
    if not rows:
        return {}

    def assign(key: str, getter: Any) -> None:
        values = sorted(v for v in (getter(row) for row in rows) if v is not None)
        size = len(values)
        for row in rows:
            value = getter(row)
            row[key] = (
                round(100.0 * bisect.bisect_left(values, value) / (size - 1), 1)
                if value is not None and size > 1
                else None
            )

    assign("pct_score", lambda row: row.get("score"))
    assign("pct_momentum", lambda row: (row.get("subscores") or {}).get("momentum"))
    assign("pct_trend", lambda row: (row.get("subscores") or {}).get("trend"))

    qqq_ret3 = qqq_ret6 = None
    qqq_above = None
    if qqq_bars and len(qqq_bars) > 130:
        closes = [float(b["close"]) for b in qqq_bars]
        if len(closes) > 63:
            qqq_ret3 = closes[-1] / closes[-64] - 1.0
        if len(closes) > 126:
            qqq_ret6 = closes[-1] / closes[-127] - 1.0
        if len(closes) >= 200:
            qqq_above = closes[-1] > sum(closes[-200:]) / 200.0

    sector_rows: dict[str, list[float]] = {}
    for row in rows:
        sector = row.get("sector") or "Sin sector"
        sector_rows.setdefault(sector, []).append(row.get("ret_3m") or 0.0)
    sector_avg = {key: sum(vals) / len(vals) for key, vals in sector_rows.items()}

    for row in rows:
        ret3 = row.get("ret_3m")
        if ret3 is not None and qqq_ret3 is not None and (1 + qqq_ret3) != 0:
            row["rs_qqq_3m"] = (1 + ret3) / (1 + qqq_ret3) - 1.0
        sector = row.get("sector") or "Sin sector"
        if ret3 is not None:
            row["rs_sector_3m"] = ret3 - sector_avg.get(sector, 0.0)

    above = [row["above_sma200"] for row in rows if row.get("above_sma200") is not None]
    above_pct = (sum(1 for v in above if v) / len(above)) if above else 0.0
    advancers = sum(1 for row in rows if (row.get("change_pct") or 0) > 0)
    highs = sum(1 for row in rows if (row.get("from_high") or -1) >= -0.03)
    lows = sum(1 for row in rows if (row.get("range_pos") is not None and row["range_pos"] <= 0.05))
    extremes = highs + lows
    low_pct = (lows / extremes) if extremes else 0.0
    breadth = 100.0 * (
        0.5 * above_pct + 0.3 * (advancers / max(1, len(rows))) + 0.2 * (1.0 - low_pct)
    )
    label = "Alcista" if breadth >= 60 else ("Bajista" if breadth <= 40 else "Mixto")

    sectors = []
    for sector, values in sector_rows.items():
        subset = [row for row in rows if (row.get("sector") or "Sin sector") == sector]
        sectors.append(
            {
                "sector": sector,
                "n": len(values),
                "ret_1m": _median([row.get("ret_1m") for row in subset]),
                "ret_3m": sum(values) / len(values),
                "positive": sum(
                    1 for row in subset if (row.get("change_pct") or 0) > 0
                )
                / max(1, len(subset)),
            }
        )
    sectors.sort(key=lambda item: item["ret_3m"], reverse=True)

    return {
        "breadth": {
            "n": len(rows),
            "above_sma200": round(above_pct * 100, 1),
            "advancers": round(100.0 * advancers / max(1, len(rows)), 1),
            "new_highs": highs,
            "new_lows": lows,
            "median_ret_1m": _median([row.get("ret_1m") for row in rows]),
            "median_ret_3m": _median([row.get("ret_3m") for row in rows]),
            "score": round(breadth, 1),
            "label": label,
            "qqq_ret_3m": qqq_ret3,
            "qqq_ret_6m": qqq_ret6,
            "qqq_above_sma200": qqq_above,
        },
        "sectors": sectors,
        "as_of": max((row.get("as_of") or "") for row in rows) or None,
    }


def action_counts(rows: list[dict[str, Any]]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for row in rows:
        action = row.get("action")
        if action:
            counts[action] = counts.get(action, 0) + 1
    return counts


class ScreenJob:
    def __init__(self, universe: str, params: dict[str, Any]) -> None:
        self.id = uuid.uuid4().hex[:12]
        self.universe = universe
        self.params = params
        self.status = "running"
        self.total = 0
        self.done = 0
        self.errors = 0
        self.rows: list[dict[str, Any]] = []
        self.error: str | None = None
        self.cancelled = False
        self.created_at = store.now_ny().isoformat(timespec="seconds")
        self.finished_at: str | None = None
        self.started = time.monotonic()
        self._lock = threading.Lock()
        self.enrich_status = "idle"
        self.enrich_total = 0
        self.enrich_done = 0
        self.enrich_errors = 0
        self.regime: dict[str, Any] = {}

    # -- estado ------------------------------------------------------------ #
    def snapshot(self, include_rows: bool = True) -> dict[str, Any]:
        with self._lock:
            elapsed = max(0.001, time.monotonic() - self.started)
            rate = self.done / elapsed
            remaining = max(0, self.total - self.done)
            eta = remaining / rate if rate > 0 and self.status == "running" else 0
            data = {
                "id": self.id,
                "universe": self.universe,
                "params": self.params,
                "status": self.status,
                "total": self.total,
                "done": self.done,
                "errors": self.errors,
                "error": self.error,
                "created_at": self.created_at,
                "finished_at": self.finished_at,
                "elapsed": round(elapsed, 1),
                "eta": round(eta, 1),
                "rows": list(self.rows[:500]) if include_rows else [],
                "action_counts": action_counts(self.rows),
                "enrich_status": self.enrich_status,
                "enrich_total": self.enrich_total,
                "enrich_done": self.enrich_done,
                "enrich_errors": self.enrich_errors,
                "regime": self.regime,
            }
        return data

    def cancel(self) -> None:
        self.cancelled = True

    # -- ejecución --------------------------------------------------------- #
    def run(self, symbols: list[str], data: DataService) -> None:
        self.total = len(symbols)
        try:
            with futures.ThreadPoolExecutor(max_workers=config.MAX_WORKERS) as pool:
                pending = {pool.submit(self._analyze_one, sym, data): sym for sym in symbols}
                for future in futures.as_completed(pending):
                    if self.cancelled:
                        for f in pending:
                            f.cancel()
                        break
                    symbol = pending[future]
                    try:
                        row = future.result()
                        if row:
                            with self._lock:
                                self.rows.append(row)
                                if len(self.rows) % 25 == 0:
                                    self._sort_rows()
                    except Exception:  # noqa: BLE001
                        with self._lock:
                            self.errors += 1
                    finally:
                        with self._lock:
                            self.done += 1
                            snapshot_now = self.done % 400 == 0
                        if snapshot_now:
                            self.persist()
            with self._lock:
                self._sort_rows()
            try:
                benchmark = data.get_bars("QQQ")
            except Exception:  # noqa: BLE001
                benchmark = None
            self.regime = annotate_relative(self.rows, benchmark)
            self.status = "cancelled" if self.cancelled else "done"
        except Exception as exc:  # noqa: BLE001
            self.status = "error"
            self.error = str(exc)
        finally:
            self.finished_at = store.now_ny().isoformat(timespec="seconds")
            self.persist()

    def _sort_rows(self) -> None:
        self.rows.sort(key=lambda r: r["score"], reverse=True)

    def _analyze_one(self, symbol: str, data: DataService) -> dict[str, Any] | None:
        bars = data.get_bars(symbol)
        if len(bars) < 60:
            return None
        as_of = bars[-1]["date"]
        cached = store.get_cached_score(symbol)
        if cached and cached.get("as_of") == as_of and (cached.get("age_hours") or 99) < 24:
            return cached["row"]
        metrics = analysis.compute_metrics(bars)
        scores = analysis.score_metrics(metrics)
        evaluation = analysis.evaluate(metrics, scores)
        instrument = store.get_instrument(symbol) or {}
        row = {
            "symbol": symbol,
            "name": instrument.get("name") or "",
            "sector": instrument.get("sector") or "",
            "price": metrics["price"],
            "change_pct": metrics["change_pct"],
            "market_cap": instrument.get("market_cap"),
            "score": scores["composite"],
            "rating": scores["rating"],
            "action": evaluation["action"],
            "action_rank": evaluation["action_rank"],
            "confidence": evaluation["confidence"],
            "target_3m": evaluation["target_3m"],
            "upside_3m": evaluation["upside_3m"],
            "target_12m": evaluation["target_12m"],
            "stop": evaluation["stop"],
            "risk_reward": evaluation["risk_reward"],
            "support": evaluation["support"],
            "resistance": evaluation["resistance"],
            "subscores": scores["subscores"],
            "rsi": metrics["rsi"],
            "ret_1m": metrics["ret_1m"],
            "ret_3m": metrics["ret_3m"],
            "ret_6m": metrics["ret_6m"],
            "ret_1y": metrics["ret_1y"],
            "vol_60d": metrics["vol_60d"],
            "max_drawdown": metrics["max_drawdown"],
            "from_high": metrics["from_high"],
            "range_pos": metrics["range_pos"],
            "dollar_volume": metrics["dollar_volume_20d"],
            "above_sma200": metrics["above_sma200"],
            "as_of": metrics["as_of"],
        }
        store.save_cached_score(symbol, as_of, row)
        return row

    def persist(self) -> None:
        try:
            with self._lock:
                rows = [dict(row) for row in self.rows]
            store.save_screen_run(
                {
                    "id": self.id,
                    "universe": self.universe,
                    "params": self.params,
                    "status": self.status,
                    "created_at": self.created_at,
                    "finished_at": self.finished_at,
                    "total": self.total,
                    "done": self.done,
                    "errors": self.errors,
                    "rows": rows,
                    "regime": self.regime,
                }
            )
        except Exception:  # noqa: BLE001 - la caché de resultados es opcional
            pass

    # -- enriquecimiento con fundamentales -------------------------------- #
    def enrich_snapshot(self) -> dict[str, Any]:
        with self._lock:
            return {
                "status": self.enrich_status,
                "total": self.enrich_total,
                "done": self.enrich_done,
                "errors": self.enrich_errors,
            }

    def start_enrichment(self, fundamentals: Any, limit: int = 300) -> dict[str, Any]:
        with self._lock:
            if self.enrich_status == "running":
                return {
                    "status": self.enrich_status,
                    "total": self.enrich_total,
                    "done": self.enrich_done,
                    "errors": self.enrich_errors,
                }
            candidates = [
                row
                for row in self.rows
                if not row.get("enriched") and row.get("quality") is None
            ][: max(1, limit)]
            self.enrich_status = "running"
            self.enrich_total = len(candidates)
            self.enrich_done = 0
            self.enrich_errors = 0

        thread = threading.Thread(
            target=self._enrich_worker,
            args=(candidates, fundamentals),
            name=f"enrich-{self.id}",
            daemon=True,
        )
        thread.start()
        return {
            "status": "running",
            "total": self.enrich_total,
            "done": 0,
            "errors": 0,
        }

    def _enrich_worker(self, candidates: list[dict[str, Any]], fundamentals: Any) -> None:
        try:
            with futures.ThreadPoolExecutor(max_workers=6) as pool:
                list(pool.map(lambda row: self._enrich_one(row, fundamentals), candidates))
            with self._lock:
                self.enrich_status = "done"
        except Exception as exc:  # noqa: BLE001
            with self._lock:
                self.enrich_status = "error"
                self.error = f"enriquecimiento: {exc}"
        finally:
            self.persist()

    def _enrich_one(self, row: dict[str, Any], fundamentals: Any) -> None:
        should_persist = False
        try:
            data = fundamentals.get(row["symbol"])
            quality = data.get("quality") or {}
            annual = data.get("annual") or {}
            surprises = data.get("surprises") or []
            last = surprises[0] if surprises else {}
            beats = [1.0 if (s.get("surprise_pct") or 0) > 0 else 0.0 for s in surprises[:4]]
            earnings = data.get("next_earnings") or {}
            analyst = data.get("analyst") or {}
            company = data.get("company") or {}
            fields = {
                "quality": quality.get("score"),
                "quality_label": quality.get("label"),
                "revenue_growth": annual.get("revenue_growth"),
                "eps_surprise": last.get("surprise_pct"),
                "beat_rate": (sum(beats) / len(beats)) if beats else None,
                "days_to_earnings": earnings.get("days"),
                "next_earnings": earnings.get("date"),
                "analyst_upside": analyst.get("upside"),
                "pe": company.get("pe"),
                "enriched": True,
                "fundamentals_error": False,
            }
            with self._lock:
                row.update(fields)
                self.enrich_done += 1
                should_persist = self.enrich_done % 50 == 0
            try:
                store.save_cached_score(row["symbol"], row.get("as_of"), row)
            except Exception:  # noqa: BLE001
                pass
        except Exception:  # noqa: BLE001
            with self._lock:
                row["enriched"] = True
                row["fundamentals_error"] = True
                self.enrich_done += 1
                self.enrich_errors += 1
                should_persist = self.enrich_done % 50 == 0
        if should_persist:
            self.persist()


class ScreenerService:
    def __init__(self, data: DataService, universes: UniverseService) -> None:
        self.data = data
        self.universes = universes
        self.jobs: dict[str, ScreenJob] = {}
        self._lock = threading.Lock()
        self._latest: dict[str, str] = {}
        self._restored: dict[str, dict[str, Any]] = {}
        self._restore()

    def _restore(self) -> None:
        for run in store.load_screen_runs():
            self._restored[run["id"]] = run
            if run.get("status") in ("done", "cancelled") and run["universe"] not in self._latest:
                self._latest[run["universe"]] = run["id"]

    def latest(self, universe: str) -> dict[str, Any] | None:
        run_id = self._latest.get(universe)
        if not run_id:
            return None
        job = self.jobs.get(run_id)
        if job:
            return job.snapshot()
        run = self._restored.get(run_id)
        if not run:
            return None
        return {
            "id": run["id"],
            "universe": run["universe"],
            "params": run["params"],
            "status": run["status"],
            "total": run["total"],
            "done": run["done"],
            "errors": run["errors"],
            "created_at": run["created_at"],
            "finished_at": run["finished_at"],
            "rows": run["rows"][:500],
            "action_counts": action_counts(run["rows"]),
            "regime": run.get("regime") or {},
            "elapsed": 0,
            "eta": 0,
        }

    def start(
        self,
        universe: str,
        params: dict[str, Any],
        symbols: list[str] | None = None,
        force: bool = False,
    ) -> ScreenJob:
        with self._lock:
            running = self.jobs.get(self._latest.get(universe, ""), None)
            if running and running.status == "running" and not force:
                return running
            if running and running.status == "running" and force:
                running.cancel()

            if universe == "watchlist":
                rows = [{"symbol": s.strip().upper()} for s in (symbols or []) if s.strip()]
                filtered = rows
            else:
                rows = self.universes.get(universe, force=force)
                filtered = apply_filters(rows, params)

            symbols = [row["symbol"] for row in filtered]
            if params.get("max_symbols"):
                symbols = symbols[: int(params["max_symbols"])]

            job = ScreenJob(universe, params)
            self.jobs[job.id] = job
            self._latest[universe] = job.id
            self._prune_jobs()

        thread = threading.Thread(
            target=job.run, args=(symbols, self.data), name=f"screen-{job.id}", daemon=True
        )
        thread.start()
        return job

    def _prune_jobs(self, keep: int = 6) -> None:
        finished = [
            (jid, job)
            for jid, job in self.jobs.items()
            if job.status != "running"
        ]
        if len(finished) <= keep:
            return
        finished.sort(key=lambda item: item[1].created_at)
        for jid, _ in finished[: len(finished) - keep]:
            self.jobs.pop(jid, None)

    def get(self, job_id: str) -> ScreenJob | None:
        job = self.jobs.get(job_id)
        if job:
            return job
        run = self._restored.get(job_id)
        if not run:
            return None
        return self._hydrate(run)

    def _hydrate(self, run: dict[str, Any]) -> ScreenJob:
        job = ScreenJob(run.get("universe") or "ndx", run.get("params") or {})
        job.id = run["id"]
        job.status = run.get("status") or "done"
        job.total = run.get("total") or len(run.get("rows") or [])
        job.done = run.get("done") or len(run.get("rows") or [])
        job.errors = run.get("errors") or 0
        job.rows = list(run.get("rows") or [])
        job.created_at = run.get("created_at") or job.created_at
        job.finished_at = run.get("finished_at")
        job.enrich_status = "idle"
        job.regime = run.get("regime") or {}
        self.jobs[job.id] = job
        return job


def apply_filters(
    rows: list[dict[str, Any]], params: dict[str, Any]
) -> list[dict[str, Any]]:
    min_price = float(params.get("min_price") or 0)
    min_cap = float(params.get("min_market_cap") or 0)
    min_dollar = float(params.get("min_dollar_vol") or 0)
    exclude_funds = bool(params.get("exclude_funds", True))
    sectors = {s.lower() for s in (params.get("sectors") or []) if s}

    out = []
    for row in rows:
        symbol = row.get("symbol") or ""
        name = row.get("name") or ""
        if not symbol:
            continue
        if exclude_funds and FUND_PATTERN.search(name):
            continue
        price = row.get("price") or 0
        if price < min_price:
            continue
        cap = row.get("market_cap") or 0
        if min_cap and (not cap or cap < min_cap):
            continue
        dollar_vol = (row.get("volume") or 0) * price
        if min_dollar and dollar_vol and dollar_vol < min_dollar:
            continue
        if sectors and (row.get("sector") or "").lower() not in sectors:
            continue
        out.append(row)

    # Prioriza por liquidez para que un límite de símbolos coja lo más operable.
    out.sort(key=lambda r: ((r.get("volume") or 0) * (r.get("price") or 0)), reverse=True)
    return out


def benchmark_bars(data: DataService) -> list[dict[str, Any]] | None:
    try:
        return data.get_bars("QQQ")
    except Exception:  # noqa: BLE001
        return None
