"""Backtesting walk-forward y calibración de los pesos de puntuación.

El motor evalúa la señal en el cierre de cada fecha de rebalanceo usando solo
información pasada, y mide retornos futuros, exceso frente a QQQ, acierto,
capacidad de los objetivos y la cartera de los mejores valores.
"""
from __future__ import annotations

import concurrent.futures as futures
import datetime as dt
import json
import math
import random
import sqlite3
import threading
import time
import uuid
from dataclasses import asdict, dataclass
from typing import Any, Callable, Iterable
from zoneinfo import ZoneInfo

from . import analysis, config, indicators as ind, net, sec, store
from .sources import nasdaq, yahoo

_NY = ZoneInfo("America/New_York")
BT_DB = config.DATA_DIR / "backtest.db"
BT_RESULT = config.DATA_DIR / "backtest_last.json"
_local = threading.local()
_write_lock = threading.Lock()

_SCHEMA = """
CREATE TABLE IF NOT EXISTS bt_prices (
    symbol TEXT NOT NULL,
    date   TEXT NOT NULL,
    open   REAL, high REAL, low REAL, close REAL, volume REAL,
    PRIMARY KEY (symbol, date)
) WITHOUT ROWID;
CREATE TABLE IF NOT EXISTS bt_meta (
    symbol TEXT PRIMARY KEY,
    rows INTEGER,
    first_date TEXT,
    last_date TEXT,
    fetched_at TEXT
);
"""


# --------------------------------------------------------------------------- #
# Caché de datos larga (10 años) separada de la base del screener
# --------------------------------------------------------------------------- #
class BacktestData:
    def __init__(self) -> None:
        config.DATA_DIR.mkdir(parents=True, exist_ok=True)

    def _connect(self) -> sqlite3.Connection:
        conn = getattr(_local, "conn", None)
        if conn is None:
            conn = sqlite3.connect(BT_DB, timeout=30, check_same_thread=False)
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA synchronous=NORMAL")
            conn.execute("PRAGMA busy_timeout=10000")
            conn.executescript(_SCHEMA)
            _local.conn = conn
        return conn

    def init(self) -> None:
        self._connect()

    def get_bars(self, symbol: str) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT date, open, high, low, close, volume FROM bt_prices "
                "WHERE symbol = ? ORDER BY date",
                (symbol,),
            ).fetchall()
        return [dict(row) for row in rows]

    def _meta(self, symbol: str) -> dict[str, Any] | None:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM bt_meta WHERE symbol = ?", (symbol,)).fetchone()
        return dict(row) if row else None

    def _store(self, symbol: str, bars: list[dict[str, Any]]) -> None:
        rows = [
            (symbol, b["date"], b["open"], b["high"], b["low"], b["close"], b["volume"])
            for b in bars
        ]
        with _write_lock, self._connect() as conn:
            conn.execute("DELETE FROM bt_prices WHERE symbol = ?", (symbol,))
            conn.executemany(
                "INSERT INTO bt_prices(symbol, date, open, high, low, close, volume) "
                "VALUES(?,?,?,?,?,?,?)",
                rows,
            )
            conn.execute(
                "INSERT OR REPLACE INTO bt_meta(symbol, rows, first_date, last_date, fetched_at) "
                "VALUES(?,?,?,?,?)",
                (
                    symbol,
                    len(bars),
                    bars[0]["date"] if bars else None,
                    bars[-1]["date"] if bars else None,
                    store.now_ny().isoformat(timespec="seconds"),
                ),
            )

    def _fresh(self, symbol: str) -> bool:
        meta = self._meta(symbol)
        if not meta or int(meta.get("rows") or 0) < 300:
            return False
        try:
            fetched = dt.datetime.fromisoformat(meta["fetched_at"])
        except (TypeError, ValueError):
            return False
        if fetched.tzinfo is None:
            fetched = fetched.replace(tzinfo=_NY)
        return (store.now_ny() - fetched) < dt.timedelta(days=5)

    def ensure(
        self,
        symbols: list[str],
        progress: Callable[[int, int], None] | None = None,
    ) -> dict[str, list[dict[str, Any]]]:
        self.init()
        missing = [s for s in symbols if not self._fresh(s)]
        done = 0

        def work(symbol: str) -> bool:
            for fetcher in (
                lambda s: nasdaq.fetch_history(s, days=365 * 12),
                lambda s: yahoo.fetch_history(s, rng="10y"),
            ):
                try:
                    payload = fetcher(symbol)
                    bars = payload.get("bars") or []
                    if len(bars) >= 300:
                        self._store(symbol, bars)
                        return True
                except (net.FetchError, KeyError, TypeError, ValueError):
                    continue
            return False

        if missing:
            with futures.ThreadPoolExecutor(max_workers=config.MAX_WORKERS) as pool:
                for _ in pool.map(work, missing):
                    done += 1
                    if progress and done % 10 == 0:
                        progress(done, len(missing))

        return {s: self.get_bars(s) for s in symbols}


# --------------------------------------------------------------------------- #
# Configuración y utilidades estadísticas
# --------------------------------------------------------------------------- #
@dataclass
class BacktestConfig:
    max_symbols: int = 300
    horizon: int = 21
    rebalance: int = 21
    years: float = 3.0
    cost_bps: float = 10.0
    top_n: int = 20
    seed: int = 42
    include_quality: bool = False


def _rank(values: list[float]) -> list[float]:
    order = sorted(range(len(values)), key=values.__getitem__)
    ranks = [0.0] * len(values)
    i = 0
    n = len(values)
    while i < n:
        j = i
        while j + 1 < n and values[order[j + 1]] == values[order[i]]:
            j += 1
        avg = (i + j) / 2.0 + 1.0
        for k in range(i, j + 1):
            ranks[order[k]] = avg
        i = j + 1
    return ranks


def spearman(a: list[float], b: list[float]) -> float | None:
    n = len(a)
    if n < 10:
        return None
    ra, rb = _rank(a), _rank(b)
    mean = (n + 1) / 2.0
    num = sum((x - mean) * (y - mean) for x, y in zip(ra, rb))
    da = math.sqrt(sum((x - mean) ** 2 for x in ra))
    db = math.sqrt(sum((y - mean) ** 2 for y in rb))
    if da == 0 or db == 0:
        return None
    return num / (da * db)


def _mean(values: Iterable[float]) -> float | None:
    values = list(values)
    return sum(values) / len(values) if values else None


def _median(values: Iterable[float]) -> float | None:
    values = sorted(values)
    if not values:
        return None
    mid = len(values) // 2
    return values[mid] if len(values) % 2 else (values[mid - 1] + values[mid]) / 2.0


def _rolling_mean(values: list[float], window: int) -> list[float | None]:
    out: list[float | None] = [None] * len(values)
    total = 0.0
    for i, value in enumerate(values):
        total += value
        if i >= window:
            total -= values[i - window]
        if i >= window - 1:
            out[i] = total / window
    return out


def _rolling_std(values: list[float], window: int) -> list[float | None]:
    out: list[float | None] = [None] * len(values)
    total = 0.0
    total_sq = 0.0
    for i, value in enumerate(values):
        total += value
        total_sq += value * value
        if i >= window:
            old = values[i - window]
            total -= old
            total_sq -= old * old
        if i >= window - 1:
            mean = total / window
            var = max(0.0, total_sq / window - mean * mean)
            out[i] = math.sqrt(var)
    return out


# --------------------------------------------------------------------------- #
# Observaciones
# --------------------------------------------------------------------------- #
def _target_outcome(
    highs: list[float],
    lows: list[float],
    start: int,
    target: float,
    stop: float,
    sessions: int = 63,
) -> str | None:
    if target <= 0 or stop <= 0 or target <= stop:
        return None
    end = min(len(highs), start + sessions + 1)
    for k in range(start + 1, end):
        hit_target = highs[k] >= target
        hit_stop = lows[k] <= stop
        if hit_target and hit_stop:
            return "ambiguous"
        if hit_stop:
            return "stop"
        if hit_target:
            return "target"
    return "none"


def _symbol_observations(
    symbol: str,
    bars: list[dict[str, Any]],
    calendar: list[str],
    rebalance_idx: list[int],
    bench_closes: list[float],
    horizon: int,
    quality_series: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    n = len(bars)
    if n < 280:
        return []
    closes = [float(b["close"]) for b in bars]
    highs = [float(b["high"]) for b in bars]
    lows = [float(b["low"]) for b in bars]
    volumes = [float(b.get("volume") or 0.0) for b in bars]
    date_pos = {b["date"]: i for i, b in enumerate(bars)}

    sma20 = ind.sma(closes, 20)
    sma50 = ind.sma(closes, 50)
    sma200 = ind.sma(closes, 200)
    rsi14 = ind.rsi(closes)
    atr14 = ind.atr(highs, lows, closes)
    bb = ind.bollinger(closes, 20)
    returns = ind.daily_returns(closes)
    vol60 = _rolling_std([r or 0.0 for r in returns], 60)
    dollar = [c * v for c, v in zip(closes, volumes)]
    dv20 = _rolling_mean(dollar, 20)
    vmean20 = _rolling_mean(volumes, 20)

    out: list[dict[str, Any]] = []
    for t in rebalance_idx:
        date = calendar[t]
        j = date_pos.get(date)
        if j is None or j < 260 or j + horizon >= n:
            continue
        price = closes[j]
        if price <= 0:
            continue
        s20, s50, s200 = sma20[j], sma50[j], sma200[j]
        rsi_val = rsi14[j]
        atr_val = atr14[j]
        vol = vol60[j]
        if rsi_val is None or vol is None:
            continue

        lo = max(0, j - 251)
        window = closes[lo : j + 1]
        high52, low52 = max(window), min(window)
        dd = ind.max_drawdown(window)

        slope_series = [v for v in sma50[max(0, j - 40) : j + 1] if v is not None]
        trend_slope = ind.slope_pct(slope_series, 20) if len(slope_series) >= 5 else 0.0

        def ret(offset: int) -> float | None:
            return closes[j] / closes[j - offset] - 1.0 if j >= offset else None

        m: dict[str, Any] = {
            "as_of": date,
            "price": price,
            "prev_close": closes[j - 1],
            "change_pct": price / closes[j - 1] - 1.0,
            "sma20": s20,
            "sma50": s50,
            "sma200": s200,
            "rsi": rsi_val,
            "atr": atr_val,
            "atr_pct": atr_val / price if atr_val else None,
            "bb_upper": bb["upper"][j],
            "ret_1m": ret(21),
            "ret_3m": ret(63),
            "ret_6m": ret(126),
            "ret_1y": ret(252),
            "ret_1d": price / closes[j - 1] - 1.0,
            "high_52w": high52,
            "low_52w": low52,
            "high_20d": max(highs[j - 19 : j + 1]),
            "low_20d": min(lows[j - 19 : j + 1]),
            "high_60d": max(highs[j - 59 : j + 1]),
            "low_60d": min(lows[j - 59 : j + 1]),
            "range_pos": (price - low52) / (high52 - low52) if high52 > low52 else None,
            "from_high": price / high52 - 1.0 if high52 else None,
            "vol_20d": ind.annualized_volatility(
                [r for r in returns[j - 19 : j + 1] if r is not None]
            ),
            "vol_60d": vol,
            "vol_252d": ind.annualized_volatility(
                [r for r in returns[j - 251 : j + 1] if r is not None]
            ),
            "max_drawdown": dd,
            "dollar_volume_20d": dv20[j] or 0.0,
            "volume_ratio": volumes[j] / vmean20[j] if vmean20[j] else 1.0,
            "trend_slope": trend_slope,
            "golden_cross": bool(s50 and s200 and s50 > s200),
            "above_sma200": bool(s200 and price > s200),
        }

        scores = analysis.score_metrics(m)
        ev = analysis.evaluate(m, scores)
        fwd = closes[j + horizon] / price - 1.0
        bench = bench_closes[t + horizon] / bench_closes[t] - 1.0 if bench_closes[t] else 0.0
        hit = None
        if ev["action"] in ("COMPRA", "COMPRA FUERTE") and ev["target_3m"] and ev["stop"]:
            hit = _target_outcome(highs, lows, j, ev["target_3m"], ev["stop"])

        out.append(
            {
                "date": date,
                "symbol": symbol,
                "score": scores["composite"],
                "action": ev["action"],
                "action_rank": ev["action_rank"],
                "confidence": ev["confidence"],
                "subscores": scores["subscores"],
                "fwd": fwd,
                "bench": bench,
                "excess": fwd - bench,
                "price": round(price, 4),
                "target": ev["target_3m"],
                "stop": ev["stop"],
                "upside": ev["upside_3m"],
                "hit": hit,
                "quality": sec.quality_at(quality_series, date) if quality_series else None,
            }
        )
    return out


# --------------------------------------------------------------------------- #
# Agregación
# --------------------------------------------------------------------------- #
def _quality_report(observations: list[dict[str, Any]]) -> dict[str, Any] | None:
    subset = [obs for obs in observations if obs.get("quality") is not None]
    if len(subset) < 50:
        return None
    by_date: dict[str, list[dict[str, Any]]] = {}
    for obs in subset:
        by_date.setdefault(obs["date"], []).append(obs)

    quality_ics = []
    combined_ics = []
    spreads = []
    for group in by_date.values():
        if len(group) < 10:
            continue
        ic = spearman([o["quality"] for o in group], [o["fwd"] for o in group])
        if ic is not None:
            quality_ics.append(ic)
        combined = [0.6 * o["score"] + 0.4 * o["quality"] for o in group]
        ic2 = spearman(combined, [o["fwd"] for o in group])
        if ic2 is not None:
            combined_ics.append(ic2)
        ordered = sorted(group, key=lambda o: o["quality"])
        width = max(1, len(ordered) // 10)
        spreads.append(
            _mean(o["fwd"] for o in ordered[-width:])
            - _mean(o["fwd"] for o in ordered[:width])
        )

    return {
        "coverage": len(subset),
        "dates": len(by_date),
        "ic": _mean(quality_ics),
        "combined_ic": _mean(combined_ics),
        "spread": _mean(spreads),
        "positive_ratio": _mean(1.0 if v > 0 else 0.0 for v in quality_ics)
        if quality_ics
        else None,
    }


def _period_summary(observations: list[dict[str, Any]]) -> dict[str, Any] | None:
    if len(observations) < 120:
        return None
    by_date: dict[str, list[dict[str, Any]]] = {}
    for obs in observations:
        by_date.setdefault(obs["date"], []).append(obs)
    ics = []
    for group in by_date.values():
        value = spearman([o["score"] for o in group], [o["fwd"] for o in group])
        if value is not None:
            ics.append(value)
    ordered = sorted(observations, key=lambda o: o["score"])
    width = max(1, len(ordered) // 10)
    spread = _mean(o["fwd"] for o in ordered[-width:]) - _mean(
        o["fwd"] for o in ordered[:width]
    )
    buys = [o for o in observations if o["action"] in ("COMPRA", "COMPRA FUERTE")]
    return {
        "observations": len(observations),
        "dates": len(by_date),
        "start": min(by_date),
        "end": max(by_date),
        "ic": _mean(ics),
        "positive_ratio": _mean(1.0 if v > 0 else 0.0 for v in ics) if ics else None,
        "spread": spread,
        "buy_excess": _mean(o["excess"] for o in buys) if buys else None,
        "buy_hit": _mean(1.0 if o["fwd"] > 0 else 0.0 for o in buys) if buys else None,
    }


def _aggregate(
    observations: list[dict[str, Any]],
    cfg: BacktestConfig,
    calendar: list[str],
    bench_closes: list[float],
    rebalance_idx: list[int],
    elapsed: float,
) -> dict[str, Any]:
    if not observations:
        raise ValueError("no hay observaciones suficientes para el backtest")

    buckets = {}
    for action in ("COMPRA FUERTE", "COMPRA", "NEUTRAL", "VENTA", "VENTA FUERTE"):
        subset = [o for o in observations if o["action"] == action]
        if not subset:
            continue
        buckets[action] = {
            "n": len(subset),
            "avg_fwd": _mean(o["fwd"] for o in subset),
            "median_fwd": _median(o["fwd"] for o in subset),
            "avg_excess": _mean(o["excess"] for o in subset),
            "hit_rate": _mean(1.0 if o["fwd"] > 0 else 0.0 for o in subset),
            "beat_bench": _mean(1.0 if o["excess"] > 0 else 0.0 for o in subset),
        }

    ordered = sorted(observations, key=lambda o: o["score"])
    deciles = []
    size = max(1, len(ordered) // 10)
    for d in range(10):
        chunk = ordered[d * size : (d + 1) * size] if d < 9 else ordered[9 * size :]
        if not chunk:
            continue
        deciles.append(
            {
                "decile": d + 1,
                "avg_fwd": _mean(o["fwd"] for o in chunk),
                "avg_excess": _mean(o["excess"] for o in chunk),
                "n": len(chunk),
            }
        )

    by_date: dict[str, list[dict[str, Any]]] = {}
    for obs in observations:
        by_date.setdefault(obs["date"], []).append(obs)

    ic_series = []
    for date in sorted(by_date):
        group = by_date[date]
        value = spearman([o["score"] for o in group], [o["fwd"] for o in group])
        if value is not None:
            ic_series.append({"date": date, "ic": round(value, 4), "n": len(group)})
    ic_values = [item["ic"] for item in ic_series]

    # Cartera de los mejores por puntuación, equiponderada y con costes.
    # Si el horizonte supera el rebalanceo, se muestrea para no encadenar
    # retornos solapados (que inflarían la curva y el total).
    step = max(1, math.ceil(cfg.horizon / max(1, cfg.rebalance)))
    sample_dates = sorted(by_date)[::step]
    equity = [1.0]
    bench_equity = [1.0]
    curve_dates = [sample_dates[0]] if sample_dates else []
    cost = cfg.cost_bps / 10_000.0
    for date in sample_dates:
        group = sorted(by_date[date], key=lambda o: o["score"], reverse=True)[: cfg.top_n]
        if not group:
            continue
        period = _mean(o["fwd"] for o in group) or 0.0
        equity.append(equity[-1] * (1.0 + period - cost))
        bench_period = _mean(o["bench"] for o in group) or 0.0
        bench_equity.append(bench_equity[-1] * (1.0 + bench_period))
        curve_dates.append(date)

    periods = len(equity) - 1
    years = max(0.5, periods * step * cfg.rebalance / 252.0)
    cagr = equity[-1] ** (1.0 / years) - 1.0 if equity[-1] > 0 else -1.0
    bench_cagr = bench_equity[-1] ** (1.0 / years) - 1.0 if bench_equity[-1] > 0 else -1.0

    def max_dd(series: list[float]) -> float:
        peak = -math.inf
        worst = 0.0
        for value in series:
            peak = max(peak, value)
            if peak > 0:
                worst = max(worst, (peak - value) / peak)
        return worst

    hits = [o["hit"] for o in observations if o["hit"]]
    target_stats = {
        "evaluated": len(hits),
        "target": hits.count("target"),
        "stop": hits.count("stop"),
        "ambiguous": hits.count("ambiguous"),
        "none": hits.count("none"),
        "hit_rate": (
            (hits.count("target") + 0.5 * hits.count("ambiguous")) / len(hits)
            if hits
            else None
        ),
    }

    return {
        "coverage": {
            "observations": len(observations),
            "symbols": len({o["symbol"] for o in observations}),
            "dates": len(by_date),
            "start": min(by_date),
            "end": max(by_date),
            "duration_s": round(elapsed, 1),
        },
        "buckets": buckets,
        "deciles": deciles,
        "ic": {
            "mean": _mean(ic_values),
            "median": _median(ic_values),
            "positive_ratio": _mean(1.0 if v > 0 else 0.0 for v in ic_values)
            if ic_values
            else None,
            "series": ic_series,
        },
        "portfolio": {
            "dates": curve_dates[1:],
            "equity": [round(v, 5) for v in equity[1:]],
            "benchmark": [round(v, 5) for v in bench_equity[1:]],
            "total": equity[-1] - 1.0,
            "bench_total": bench_equity[-1] - 1.0,
            "cagr": cagr,
            "bench_cagr": bench_cagr,
            "max_drawdown": max_dd(equity),
            "bench_max_drawdown": max_dd(bench_equity),
            "periods": periods,
            "cost_bps": cfg.cost_bps,
        },
        "targets": target_stats,
        "quality": _quality_report(observations) if cfg.include_quality else None,
        "walk_forward": walk_forward(observations),
        "is": _period_summary(
            [o for o in observations if o["date"] in set(sorted(by_date)[: max(1, int(len(by_date) * 0.7))])]
        ),
        "oos": _period_summary(
            [o for o in observations if o["date"] in set(sorted(by_date)[max(1, int(len(by_date) * 0.7)):])]
        ),
        "config": asdict(cfg),
    }


# --------------------------------------------------------------------------- #
# Calibración de pesos
# --------------------------------------------------------------------------- #
_KEYS = ("trend", "momentum", "risk", "rsi", "liquidity")


def _factor_value(obs: dict[str, Any], key: str, invert: Iterable[str]) -> float:
    value = obs["subscores"][key]
    return 100.0 - value if key in invert else value


def _evaluate_weights(
    observations: list[dict[str, Any]],
    weights: dict[str, float],
    invert: Iterable[str] = (),
) -> dict[str, Any]:
    invert = set(invert)
    by_date: dict[str, list[dict[str, Any]]] = {}
    for obs in observations:
        by_date.setdefault(obs["date"], []).append(obs)

    ic_values = []
    spreads = []
    for group in by_date.values():
        scored = [
            (
                sum(_factor_value(obs, k, invert) * weights[k] for k in _KEYS),
                obs["fwd"],
            )
            for obs in group
        ]
        value = spearman([s for s, _ in scored], [r for _, r in scored])
        if value is not None:
            ic_values.append(value)
        scored.sort(key=lambda item: item[0])
        width = max(1, len(scored) // 10)
        bottom = scored[:width]
        top = scored[-width:]
        if bottom and top:
            spreads.append(
                _mean(r for _, r in top) - _mean(r for _, r in bottom)
            )

    return {
        "ic": _mean(ic_values),
        "spread": _mean(s for s in spreads if s is not None),
    }


def _optimize_weights(
    observations: list[dict[str, Any]],
    invert: Iterable[str] = (),
    samples: int = 40,
    seed: int = 42,
) -> tuple[dict[str, float], dict[str, Any]]:
    rng = random.Random(seed)
    best_weights = dict(config.SCORE_WEIGHTS)
    best = _evaluate_weights(observations, best_weights, invert)
    for _ in range(samples):
        raw = [rng.expovariate(1.0) + 0.05 for _ in _KEYS]
        total = sum(raw)
        weights = {key: value / total for key, value in zip(_KEYS, raw)}
        metrics = _evaluate_weights(observations, weights, invert)
        if metrics["ic"] is not None and (
            best["ic"] is None or metrics["ic"] > best["ic"]
        ):
            best_weights, best = weights, metrics
    return best_weights, best


def walk_forward(
    observations: list[dict[str, Any]],
    folds: int = 3,
    train_periods: int = 24,
    test_periods: int = 6,
    samples: int = 40,
) -> dict[str, Any] | None:
    """Ventanas móviles: optimiza en train y evalúa en el tramo siguiente.

    Los periodos se cuentan en fechas de rebalanceo (21 sesiones = 1 mes).
    """
    dates = sorted({obs["date"] for obs in observations})
    if len(dates) < 20:
        return None
    invert: set[str] = set()
    results = []
    for fold in range(folds):
        test_end = len(dates) - (folds - 1 - fold) * test_periods
        test_start = test_end - test_periods
        train_end = test_start
        train_start = max(0, train_end - train_periods)
        if test_start <= 0 or test_end > len(dates):
            continue
        train_set = set(dates[train_start:train_end])
        test_set = set(dates[test_start:test_end])
        train_obs = [obs for obs in observations if obs["date"] in train_set]
        test_obs = [obs for obs in observations if obs["date"] in test_set]
        if len(train_obs) < 150 or len(test_obs) < 40:
            continue

        base = _evaluate_weights(test_obs, dict(config.SCORE_WEIGHTS), invert)
        optimized_weights, _ = _optimize_weights(
            train_obs, invert=invert, samples=samples, seed=42 + fold
        )
        optimized = _evaluate_weights(test_obs, optimized_weights, invert)
        results.append(
            {
                "fold": fold + 1,
                "train_start": dates[train_start],
                "train_end": dates[train_end - 1],
                "test_start": dates[test_start],
                "test_end": dates[test_end - 1],
                "current_ic": base["ic"],
                "current_spread": base["spread"],
                "optimized_ic": optimized["ic"],
                "optimized_spread": optimized["spread"],
                "weights": {key: round(value, 3) for key, value in optimized_weights.items()},
            }
        )
    if not results:
        return None
    return {
        "folds": results,
        "current_ic_mean": _mean(item["current_ic"] for item in results),
        "optimized_ic_mean": _mean(item["optimized_ic"] for item in results),
        "current_positive": _mean(
            1.0 if (item["current_ic"] or 0) > 0 else 0.0 for item in results
        ),
        "optimized_positive": _mean(
            1.0 if (item["optimized_ic"] or 0) > 0 else 0.0 for item in results
        ),
    }


def factor_table(observations: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """IC y spread de cada bloque de la puntuación, directo e invertido."""
    by_date: dict[str, list[dict[str, Any]]] = {}
    for obs in observations:
        by_date.setdefault(obs["date"], []).append(obs)

    table = []
    for key in _KEYS:
        for sign, side in ((1.0, "directo"), (-1.0, "invertido")):
            ics = []
            spreads = []
            for group in by_date.values():
                vals = [(sign * obs["subscores"][key], obs["fwd"]) for obs in group]
                ic = spearman([v for v, _ in vals], [r for _, r in vals])
                if ic is not None:
                    ics.append(ic)
                vals.sort(key=lambda item: item[0])
                width = max(1, len(vals) // 10)
                spreads.append(_mean(r for _, r in vals[-width:]) - _mean(r for _, r in vals[:width]))
            table.append(
                {
                    "factor": key,
                    "side": side,
                    "ic": _mean(ics),
                    "spread": _mean(spreads),
                }
            )
    table.sort(key=lambda row: row["ic"] or 0.0, reverse=True)
    return table


def calibrate(
    observations: list[dict[str, Any]],
    samples: int = 60,
    seed: int = 42,
) -> dict[str, Any]:
    factors = factor_table(observations)
    invert = [
        row["factor"]
        for row in factors
        if row["side"] == "directo" and (row["ic"] or 0.0) < 0
    ]

    presets: list[tuple[str, dict[str, float]]] = [
        ("Actual (sin invertir)", dict(config.SCORE_WEIGHTS)),
        ("Tendencia", {"trend": 0.45, "momentum": 0.25, "risk": 0.10, "rsi": 0.05, "liquidity": 0.15}),
        ("Momento", {"trend": 0.20, "momentum": 0.45, "risk": 0.10, "rsi": 0.10, "liquidity": 0.15}),
        ("Riesgo", {"trend": 0.22, "momentum": 0.23, "risk": 0.35, "rsi": 0.05, "liquidity": 0.15}),
        ("Equilibrado", {k: 0.20 for k in _KEYS}),
    ]
    rng = random.Random(seed)
    for i in range(samples):
        raw = [rng.expovariate(1.0) + 0.05 for _ in _KEYS]
        total = sum(raw)
        weights = {k: value / total for k, value in zip(_KEYS, raw)}
        presets.append((f"Aleatorio {i + 1}", weights))

    results = []
    for index, (name, weights) in enumerate(presets):
        metrics = _evaluate_weights(
            observations, weights, () if index == 0 else invert
        )
        results.append(
            {
                "name": name,
                "weights": {k: round(weights[k], 4) for k in _KEYS},
                "invert": [] if index == 0 else invert,
                "ic": metrics["ic"],
                "spread": metrics["spread"],
            }
        )

    valid = [r for r in results if r["ic"] is not None]
    valid.sort(key=lambda r: (r["ic"], r["spread"] or 0.0), reverse=True)
    best = valid[0] if valid else None
    return {
        "variants": valid[:10],
        "factors": factors,
        "inversion": (best or {}).get("invert", []),
        "tested": len(results),
    }


class BacktestJob:
    def __init__(self, cfg: BacktestConfig) -> None:
        self.id = uuid.uuid4().hex[:12]
        self.cfg = cfg
        self.status = "running"
        self.stage = "preparando datos"
        self.progress = 0
        self.error: str | None = None
        self.result: dict[str, Any] | None = None
        self.observations: list[dict[str, Any]] = []
        self.calibration: dict[str, Any] | None = None
        self.created_at = store.now_ny().isoformat(timespec="seconds")
        self.finished_at: str | None = None

    def snapshot(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "status": self.status,
            "stage": self.stage,
            "progress": self.progress,
            "error": self.error,
            "config": asdict(self.cfg),
            "created_at": self.created_at,
            "finished_at": self.finished_at,
            "result": self.result,
        }

    def run(self, symbols: list[str], data: BacktestData) -> None:
        started = time.monotonic()
        try:
            total = len(symbols)
            self.stage = f"descargando históricos (0/{total})"
            data.ensure(
                symbols,
                progress=lambda done, count: self._fetch_progress(done, count, total),
            )
            self.progress = 40
            self.stage = "cargando precios"
            qqq = data.get_bars("QQQ")
            if len(qqq) < 500:
                raise ValueError("QQQ sin histórico suficiente")
            calendar = [b["date"] for b in qqq]
            bench_closes = [float(b["close"]) for b in qqq]

            last = len(calendar) - 1
            cutoff = (
                dt.date.fromisoformat(calendar[last]) - dt.timedelta(days=int(self.cfg.years * 365))
            ).isoformat()
            start = max(270, next((i for i, d in enumerate(calendar) if d >= cutoff), 270))
            rebalance_idx = list(
                range(start, last - self.cfg.horizon, max(1, self.cfg.rebalance))
            )
            if len(rebalance_idx) < 6:
                raise ValueError("periodo demasiado corto para el backtest")

            quality_series: dict[str, list[dict[str, Any]]] = {}
            if self.cfg.include_quality:
                sec_service = sec.SecService()
                self.stage = "descargando datos SEC"
                for index, symbol in enumerate(symbols):
                    try:
                        quality_series[symbol] = sec_service.quarterly(symbol)
                    except Exception:  # noqa: BLE001
                        quality_series[symbol] = []
                    if index % 5 == 0:
                        self.progress = 40 + int(12 * index / max(1, len(symbols)))
                        self.stage = f"descargando datos SEC ({index}/{len(symbols)})"

            self.progress = 55
            self.stage = "calculando señales"
            observations: list[dict[str, Any]] = []
            done = 0
            for symbol in symbols:
                bars = data.get_bars(symbol)
                if len(bars) >= 280:
                    observations.extend(
                        _symbol_observations(
                            symbol,
                            bars,
                            calendar,
                            rebalance_idx,
                            bench_closes,
                            self.cfg.horizon,
                            quality_series.get(symbol),
                        )
                    )
                done += 1
                if done % 10 == 0:
                    self.progress = 55 + int(40 * done / max(1, len(symbols)))
                    self.stage = f"calculando señales ({done}/{len(symbols)})"

            self.stage = "agregando resultados"
            self.observations = observations
            self.result = _aggregate(
                observations,
                self.cfg,
                calendar,
                bench_closes,
                rebalance_idx,
                time.monotonic() - started,
            )
            self.result["run_id"] = self.id
            self.progress = 100
            self.status = "done"
            BT_RESULT.write_text(json.dumps(self.result, ensure_ascii=False))
        except Exception as exc:  # noqa: BLE001
            self.status = "error"
            self.error = str(exc)
        finally:
            self.finished_at = store.now_ny().isoformat(timespec="seconds")

    def _fetch_progress(self, done: int, count: int, total: int) -> None:
        self.progress = int(35 * done / max(1, count))
        self.stage = f"descargando históricos ({done}/{count})"


class BacktestService:
    def __init__(self, data: BacktestData) -> None:
        self.data = data
        self.jobs: dict[str, BacktestJob] = {}
        self.latest_id: str | None = None
        self._lock = threading.Lock()
        self._load_last()

    def _load_last(self) -> None:
        if BT_RESULT.exists():
            try:
                self._last = json.loads(BT_RESULT.read_text())
            except (json.JSONDecodeError, OSError):
                self._last = None
        else:
            self._last = None

    def start(self, cfg: BacktestConfig) -> BacktestJob:
        with self._lock:
            if self.latest_id:
                current = self.jobs.get(self.latest_id)
                if current and current.status == "running":
                    return current
            symbols = _select_symbols(cfg.max_symbols)
            if "QQQ" not in symbols:
                symbols.append("QQQ")
            job = BacktestJob(cfg)
            self.jobs[job.id] = job
            self.latest_id = job.id
        thread = threading.Thread(
            target=job.run, args=(symbols, self.data), name=f"backtest-{job.id}", daemon=True
        )
        thread.start()
        return job

    def get(self, job_id: str) -> BacktestJob | None:
        return self.jobs.get(job_id)

    def latest(self) -> dict[str, Any] | None:
        if self.latest_id:
            job = self.jobs.get(self.latest_id)
            if job:
                return job.snapshot()
        if self._last:
            return {
                "id": self._last.get("run_id"),
                "status": "done",
                "stage": "",
                "progress": 100,
                "error": None,
                "config": self._last.get("config") or {},
                "created_at": None,
                "finished_at": None,
                "result": self._last,
            }
        return None


def _select_symbols(limit: int) -> list[str]:
    symbols = store.get_universe("all") or store.get_universe("ndx")
    instruments = store.instruments_for(symbols)
    ranked = sorted(
        (instruments.get(s) or {"symbol": s} for s in symbols),
        key=lambda r: ((r.get("volume") or 0) * (r.get("price") or 0)),
        reverse=True,
    )
    chosen = [r["symbol"] for r in ranked[: max(20, limit)]]
    if not chosen:
        chosen = store.get_universe("ndx")[:limit]
    return chosen
