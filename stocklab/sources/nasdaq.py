"""Fuente de datos principal: API pública de nasdaq.com (sin clave)."""
from __future__ import annotations

import datetime as dt
import re
from typing import Any
from zoneinfo import ZoneInfo

from .. import config, net

_API = "https://api.nasdaq.com/api"
_HEADERS = {
    "Referer": "https://www.nasdaq.com/",
    "Origin": "https://www.nasdaq.com",
}
_NY = ZoneInfo("America/New_York")

_NUMBER = re.compile(r"-?\d+(?:\.\d+)?")


def today_ny() -> dt.date:
    return dt.datetime.now(_NY).date()


def _parse_number(raw: Any) -> float | None:
    if raw is None:
        return None
    if isinstance(raw, (int, float)):
        return float(raw)
    text = str(raw).strip().replace(",", "")
    if text in {"", "--", "-", "N/A", "n/a", "NA"}:
        return None
    match = _NUMBER.search(text)
    if not match:
        return None
    try:
        return float(match.group(0))
    except ValueError:
        return None


def _parse_date(raw: str) -> str | None:
    for fmt in ("%m/%d/%Y", "%Y-%m-%d", "%m/%d/%y"):
        try:
            return dt.datetime.strptime(raw.strip(), fmt).date().isoformat()
        except (ValueError, AttributeError):
            continue
    return None


def _rows(payload: Any) -> list[dict[str, Any]]:
    data = (payload or {}).get("data") or {}
    if isinstance(data, dict):
        if isinstance(data.get("rows"), list):
            return data["rows"]
        table = data.get("tradesTable") or data.get("data") or {}
        if isinstance(table, dict) and isinstance(table.get("rows"), list):
            return table["rows"]
    if isinstance(data, list):
        return data
    return []


def fetch_universe() -> list[dict[str, Any]]:
    """Listado completo de acciones cotizadas en el NASDAQ."""
    url = f"{_API}/screener/stocks?tableonly=true&limit=10000&offset=0&download=true"
    payload = net.get_json(url, headers=_HEADERS)
    rows = _rows(payload)
    if not rows:
        raise net.FetchError(f"el listado del NASDAQ llegó vacío ({len(rows)} filas)")

    universe: list[dict[str, Any]] = []
    for row in rows:
        symbol = (row.get("symbol") or "").strip().upper()
        if not symbol or not re.fullmatch(r"[A-Z][A-Z0-9.\-]{0,9}", symbol):
            continue
        price = _parse_number(row.get("lastsale"))
        if not price or price <= 0:
            continue
        universe.append(
            {
                "symbol": symbol,
                "name": (row.get("name") or "").strip(),
                "sector": (row.get("sector") or "").strip(),
                "industry": (row.get("industry") or "").strip(),
                "country": (row.get("country") or "").strip(),
                "market_cap": _parse_number(row.get("marketCap")),
                "price": price,
                "volume": _parse_number(row.get("volume")),
                "change_pct": _parse_number(row.get("pctchange")),
                "kind": "stock",
            }
        )
    return universe


def fetch_nasdaq100() -> list[dict[str, Any]]:
    """Constituyentes del NASDAQ-100 (el endpoint devuelve ~101 filas)."""
    url = f"{_API}/quote/list-type/nasdaq100"
    payload = net.get_json(url, headers=_HEADERS)
    rows = _rows(payload)
    universe = []
    for row in rows:
        symbol = (row.get("symbol") or "").strip().upper()
        if not symbol:
            continue
        universe.append(
            {
                "symbol": symbol,
                "name": (row.get("companyName") or "").strip(),
                "sector": (row.get("sector") or "").strip(),
                "industry": "",
                "country": "",
                "market_cap": _parse_number(row.get("marketCap")),
                "price": _parse_number(row.get("lastSalePrice")),
                "volume": None,
                "change_pct": _parse_number(row.get("percentageChange")),
                "kind": "stock",
            }
        )
    if len(universe) < 50:
        raise net.FetchError(f"NASDAQ-100 incompleto ({len(universe)} símbolos)")
    return universe


def fetch_history(
    symbol: str, days: int | None = None, min_bars: int = 30
) -> dict[str, Any]:
    """Histórico diario de un símbolo, ordenado de más antiguo a más reciente."""
    end = today_ny()
    start = end - dt.timedelta(days=days or config.HISTORY_DAYS)
    symbol = symbol.strip().upper()

    last_error: Exception | None = None
    empty = 0
    for assetclass in ("stocks", "etf"):
        url = (
            f"{_API}/quote/{net.quote_symbol(symbol)}/historical"
            f"?assetclass={assetclass}"
            f"&fromdate={start.isoformat()}&todate={end.isoformat()}&limit=9999"
        )
        try:
            payload = net.get_json(url, headers=_HEADERS)
            rows = _rows(payload)
            bars = _parse_bars(rows)
            if len(bars) >= min_bars:
                return {"symbol": symbol, "source": "nasdaq", "bars": bars}
            empty += 1
            last_error = net.FetchError(f"{symbol}: histórico insuficiente ({len(bars)})")
        except net.FetchError as exc:
            last_error = exc
    if empty == 2:
        raise net.NoDataError(f"{symbol}: sin histórico en el NASDAQ")
    raise last_error or net.FetchError(f"sin datos para {symbol}")


def _parse_bars(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    bars: dict[str, dict[str, Any]] = {}
    for row in rows:
        date = _parse_date(str(row.get("date") or ""))
        close = _parse_number(row.get("close"))
        if not date or close is None or close <= 0:
            continue
        open_ = _parse_number(row.get("open")) or close
        high = _parse_number(row.get("high")) or max(open_, close)
        low = _parse_number(row.get("low")) or min(open_, close)
        volume = _parse_number(row.get("volume")) or 0.0
        bars[date] = {
            "date": date,
            "open": open_,
            "high": max(high, open_, close),
            "low": min(low, open_, close),
            "close": close,
            "volume": volume,
        }
    return [bars[key] for key in sorted(bars)]
