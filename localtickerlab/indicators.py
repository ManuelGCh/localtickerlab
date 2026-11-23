"""Indicadores técnicos y estadísticos en Python puro (sin numpy/pandas).

Todas las funciones devuelven listas de la misma longitud que la entrada,
rellenando con ``None`` los tramos donde el indicador aún no puede calcularse.
"""
from __future__ import annotations

import math
from typing import Sequence

Number = float | int | None


def clamp(value: float, low: float = 0.0, high: float = 1.0) -> float:
    return max(low, min(high, value))


def sma(values: Sequence[float], period: int) -> list[float | None]:
    out: list[float | None] = [None] * len(values)
    if period <= 0 or len(values) < period:
        return out
    window = 0.0
    for i, value in enumerate(values):
        window += value
        if i >= period:
            window -= values[i - period]
        if i >= period - 1:
            out[i] = window / period
    return out


def _ema_compact(values: Sequence[float], period: int) -> list[float]:
    if not values:
        return []
    k = 2.0 / (period + 1.0)
    result = [float(values[0])]
    for value in values[1:]:
        result.append(value * k + result[-1] * (1.0 - k))
    return result


def ema(values: Sequence[float], period: int) -> list[float | None]:
    out: list[float | None] = [None] * len(values)
    if len(values) < period or period <= 0:
        return out
    seed = sum(values[:period]) / period
    out[period - 1] = seed
    k = 2.0 / (period + 1.0)
    prev = seed
    for i in range(period, len(values)):
        prev = values[i] * k + prev * (1.0 - k)
        out[i] = prev
    return out


def rsi(values: Sequence[float], period: int = 14) -> list[float | None]:
    out: list[float | None] = [None] * len(values)
    if len(values) <= period:
        return out
    gains = 0.0
    losses = 0.0
    for i in range(1, period + 1):
        change = values[i] - values[i - 1]
        gains += max(change, 0.0)
        losses += max(-change, 0.0)
    avg_gain = gains / period
    avg_loss = losses / period
    out[period] = _rsi_value(avg_gain, avg_loss)
    for i in range(period + 1, len(values)):
        change = values[i] - values[i - 1]
        avg_gain = (avg_gain * (period - 1) + max(change, 0.0)) / period
        avg_loss = (avg_loss * (period - 1) + max(-change, 0.0)) / period
        out[i] = _rsi_value(avg_gain, avg_loss)
    return out


def _rsi_value(avg_gain: float, avg_loss: float) -> float:
    if avg_loss == 0:
        return 100.0 if avg_gain > 0 else 50.0
    rs = avg_gain / avg_loss
    return 100.0 - (100.0 / (1.0 + rs))


def macd(
    values: Sequence[float],
    fast: int = 12,
    slow: int = 26,
    signal_period: int = 9,
) -> dict[str, list[float | None]]:
    ema_fast = ema(values, fast)
    ema_slow = ema(values, slow)
    line: list[float | None] = [None] * len(values)
    for i in range(len(values)):
        if ema_fast[i] is not None and ema_slow[i] is not None:
            line[i] = ema_fast[i] - ema_slow[i]

    first = next((i for i, v in enumerate(line) if v is not None), None)
    signal: list[float | None] = [None] * len(values)
    hist: list[float | None] = [None] * len(values)
    if first is not None:
        compact = [v for v in line[first:] if v is not None]
        if len(compact) >= signal_period:
            sig = _ema_compact(compact, signal_period)
            for offset, value in enumerate(sig):
                idx = first + offset
                signal[idx] = value
                if line[idx] is not None:
                    hist[idx] = line[idx] - value
    return {"line": line, "signal": signal, "hist": hist}


def bollinger(
    values: Sequence[float], period: int = 20, k: float = 2.0
) -> dict[str, list[float | None]]:
    mid = sma(values, period)
    upper: list[float | None] = [None] * len(values)
    lower: list[float | None] = [None] * len(values)
    for i in range(period - 1, len(values)):
        window = values[i - period + 1 : i + 1]
        mean = mid[i]
        if mean is None:
            continue
        variance = sum((x - mean) ** 2 for x in window) / period
        sd = math.sqrt(variance)
        upper[i] = mean + k * sd
        lower[i] = mean - k * sd
    return {"mid": mid, "upper": upper, "lower": lower}


def atr(
    highs: Sequence[float],
    lows: Sequence[float],
    closes: Sequence[float],
    period: int = 14,
) -> list[float | None]:
    n = len(closes)
    out: list[float | None] = [None] * n
    if n <= period:
        return out
    trs: list[float] = [0.0] * n
    for i in range(1, n):
        trs[i] = max(
            highs[i] - lows[i],
            abs(highs[i] - closes[i - 1]),
            abs(lows[i] - closes[i - 1]),
        )
    prev = sum(trs[1 : period + 1]) / period
    out[period] = prev
    for i in range(period + 1, n):
        prev = (prev * (period - 1) + trs[i]) / period
        out[i] = prev
    return out


def daily_returns(values: Sequence[float]) -> list[float | None]:
    out: list[float | None] = [None] * len(values)
    for i in range(1, len(values)):
        if values[i - 1]:
            out[i] = values[i] / values[i - 1] - 1.0
    return out


def stdev(values: Sequence[float]) -> float:
    n = len(values)
    if n < 2:
        return 0.0
    mean = sum(values) / n
    return math.sqrt(sum((v - mean) ** 2 for v in values) / (n - 1))


def annualized_volatility(returns: Sequence[float], periods: int = 252) -> float:
    if len(returns) < 3:
        return 0.0
    return stdev(returns) * math.sqrt(periods)


def max_drawdown(values: Sequence[float]) -> float:
    """Máxima caída desde un máximo previo, como fracción positiva."""
    peak = -math.inf
    worst = 0.0
    for value in values:
        peak = max(peak, value)
        if peak > 0:
            worst = max(worst, (peak - value) / peak)
    return worst


def cagr(values: Sequence[float], years: float) -> float:
    if len(values) < 2 or years <= 0 or values[0] <= 0:
        return 0.0
    return (values[-1] / values[0]) ** (1.0 / years) - 1.0


def total_return(values: Sequence[float]) -> float:
    if len(values) < 2 or values[0] <= 0:
        return 0.0
    return values[-1] / values[0] - 1.0


def beta(asset: Sequence[float], benchmark: Sequence[float]) -> float | None:
    """Beta de los retornos del activo frente al benchmark (misma longitud)."""
    n = min(len(asset), len(benchmark))
    if n < 30:
        return None
    asset = asset[-n:]
    benchmark = benchmark[-n:]
    mean_a = sum(asset) / n
    mean_b = sum(benchmark) / n
    cov = sum((a - mean_a) * (b - mean_b) for a, b in zip(asset, benchmark))
    var = sum((b - mean_b) ** 2 for b in benchmark)
    if var == 0:
        return None
    return cov / var


def slope_pct(values: Sequence[float], window: int = 20) -> float:
    """Pendiente normalizada por el precio medio (regresión lineal simple)."""
    series = [v for v in values[-window:] if v is not None]
    n = len(series)
    if n < 2:
        return 0.0
    xs = list(range(n))
    mean_x = (n - 1) / 2
    mean_y = sum(series) / n
    denom = sum((x - mean_x) ** 2 for x in xs)
    if denom == 0 or mean_y == 0:
        return 0.0
    slope = sum((x - mean_x) * (y - mean_y) for x, y in zip(xs, series)) / denom
    return slope / mean_y


def percentile_rank(values: Sequence[float], value: float) -> float:
    """Percentil (0-100) del valor dentro del conjunto."""
    if not values:
        return 50.0
    below = sum(1 for v in values if v < value)
    equal = sum(1 for v in values if v == value)
    return 100.0 * (below + 0.5 * equal) / len(values)


def sigmoid(x: float) -> float:
    if x < -60:
        return 0.0
    if x > 60:
        return 1.0
    return 1.0 / (1.0 + math.exp(-x))


def last_defined(series: Sequence[float | None]) -> float | None:
    for value in reversed(series):
        if value is not None:
            return value
    return None
