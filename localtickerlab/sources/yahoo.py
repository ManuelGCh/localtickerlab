"""Fuente de respaldo: endpoint público de gráficos de Yahoo Finance."""
from __future__ import annotations

import datetime as dt
from typing import Any
from zoneinfo import ZoneInfo

from .. import net

_NY = ZoneInfo("America/New_York")
_HOSTS = ("query1.finance.yahoo.com", "query2.finance.yahoo.com")


def fetch_history(symbol: str, rng: str = "3y") -> dict[str, Any]:
    symbol = symbol.strip().upper()
    last_error: Exception | None = None
    for host in _HOSTS:
        url = (
            f"https://{host}/v8/finance/chart/{net.quote_symbol(symbol)}"
            f"?range={rng}&interval=1d&includePrePost=false&events=div%2Csplit"
        )
        try:
            payload = net.get_json(url, retries=2)
            result = ((payload.get("chart") or {}).get("result") or [None])[0]
            if not result:
                continue
            bars = _parse_bars(result)
            if len(bars) >= 30:
                meta = result.get("meta") or {}
                return {
                    "symbol": symbol,
                    "source": "yahoo",
                    "bars": bars,
                    "meta": {
                        "name": meta.get("longName") or meta.get("shortName") or "",
                        "currency": meta.get("currency") or "USD",
                        "exchange": meta.get("fullExchangeName") or meta.get("exchangeName") or "",
                    },
                }
            last_error = net.FetchError(f"{symbol}: histórico insuficiente en Yahoo")
        except net.FetchError as exc:
            last_error = exc
    raise last_error or net.FetchError(f"sin datos para {symbol}")


def _parse_bars(result: dict[str, Any]) -> list[dict[str, Any]]:
    timestamps = result.get("timestamp") or []
    quote = ((result.get("indicators") or {}).get("quote") or [{}])[0]
    closes = quote.get("close") or []
    opens = quote.get("open") or []
    highs = quote.get("high") or []
    lows = quote.get("low") or []
    volumes = quote.get("volume") or []

    bars: dict[str, dict[str, Any]] = {}
    for i, ts in enumerate(timestamps):
        close = _at(closes, i)
        if close is None or close <= 0:
            continue
        date = dt.datetime.fromtimestamp(ts, tz=_NY).date().isoformat()
        open_ = _at(opens, i) or close
        high = _at(highs, i) or max(open_, close)
        low = _at(lows, i) or min(open_, close)
        volume = _at(volumes, i) or 0.0
        bars[date] = {
            "date": date,
            "open": float(open_),
            "high": float(max(high, open_, close)),
            "low": float(min(low, open_, close)),
            "close": float(close),
            "volume": float(volume),
        }
    return [bars[key] for key in sorted(bars)]


def _at(series: list[Any], index: int) -> float | None:
    if index >= len(series):
        return None
    value = series[index]
    return None if value is None else float(value)


def fetch_profile(symbol: str) -> dict[str, Any]:
    """Nombre y divisa de un ticker usando el propio histórico de Yahoo."""
    data = fetch_history(symbol, rng="5d")
    return data.get("meta") or {}
