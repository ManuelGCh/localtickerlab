"""Calidad point-in-time a partir de los filings de la SEC (XBRL).

Usa `companyconcept` y la fecha de presentación (`filed`) para que el score
de calidad solo utilice información pública en cada fecha del backtest.
"""
from __future__ import annotations

import datetime as dt
import json
import os
import sys
import threading
import time
from typing import Any

from . import config, indicators as ind, net, store

_UA_ENV = "STOCKLAB_SEC_UA"
_UA_DEFAULT = "StockLab research (contacto@example.com)"
_SEC_HEADERS = {
    "User-Agent": os.environ.get(_UA_ENV, _UA_DEFAULT),
    "Accept": "application/json",
    "Accept-Encoding": "gzip, deflate",
}
_ua_warned = False
_CIK_PATH = config.DATA_DIR / "sec_ciks.json"
_REV_TAGS = (
    "RevenueFromContractWithCustomerExcludingAssessedTax",
    "Revenues",
    "SalesRevenueNet",
    "RevenueFromContractWithCustomerIncludingAssessedTax",
)
_NI_TAGS = ("NetIncomeLoss", "ProfitLoss")
_CACHE_HOURS = 24 * 7
_polite_lock = threading.Lock()
_last_call = [0.0]


def _warn_missing_ua() -> None:
    """SEC EDGAR pide un contacto real; avisa una vez si solo hay marcador."""
    global _ua_warned
    if _ua_warned or os.environ.get(_UA_ENV):
        return
    _ua_warned = True
    print(
        "Aviso: define STOCKLAB_SEC_UA con un contacto real para consultar SEC EDGAR.",
        file=sys.stderr,
    )


def _polite_get(url: str) -> Any:
    _warn_missing_ua()
    # SEC permite ~10 req/s; espaciamos para no abusar.
    with _polite_lock:
        wait = 0.13 - (time.time() - _last_call[0])
        if wait > 0:
            time.sleep(wait)
        _last_call[0] = time.time()
    raw = net.get_bytes(url, headers=_SEC_HEADERS, retries=2)
    return json.loads(raw)


def _cik_map() -> dict[str, int]:
    try:
        data = json.loads(_CIK_PATH.read_text())
        stamp = dt.datetime.fromisoformat(data["fetched_at"])
        if stamp.tzinfo is None:
            stamp = stamp.replace(tzinfo=dt.timezone.utc)
        if dt.datetime.now(dt.timezone.utc) - stamp < dt.timedelta(days=30):
            return {k: int(v) for k, v in data["map"].items()}
    except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError):
        pass

    payload = _polite_get("https://www.sec.gov/files/company_tickers.json")
    mapping: dict[str, int] = {}
    for item in payload.values():
        ticker = str(item.get("ticker") or "").upper()
        if ticker:
            mapping[ticker] = int(item["cik_str"])
    config.DATA_DIR.mkdir(parents=True, exist_ok=True)
    _CIK_PATH.write_text(
        json.dumps(
            {
                "fetched_at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
                "map": mapping,
            }
        )
    )
    return mapping


def _quarterly_facts(facts: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """Serie trimestral por fecha de cierre, con el último filing por periodo."""
    out: dict[str, dict[str, Any]] = {}
    for fact in facts:
        start, end = fact.get("start"), fact.get("end")
        if not start or not end:
            continue
        try:
            days = (dt.date.fromisoformat(end) - dt.date.fromisoformat(start)).days
        except ValueError:
            continue
        if not (70 <= days <= 110):
            continue
        if fact.get("form") not in ("10-Q", "10-K", "10-Q/A", "10-K/A"):
            continue
        filed = fact.get("filed")
        value = fact.get("val")
        if filed is None or value is None:
            continue
        current = out.get(end)
        if current is None or filed > current["filed"]:
            out[end] = {"value": float(value), "filed": filed}
    return out


def _series_for(cik: int, tags: tuple[str, ...]) -> dict[str, dict[str, Any]]:
    best: dict[str, dict[str, Any]] = {}
    best_end = ""
    for tag in tags:
        url = (
            f"https://data.sec.gov/api/xbrl/companyconcept/"
            f"CIK{cik:010d}/us-gaap/{tag}.json"
        )
        try:
            payload = _polite_get(url)
        except (net.FetchError, ValueError):
            continue
        units = (payload.get("units") or {}).get("USD") or []
        series = _quarterly_facts(units)
        if len(series) < 4:
            continue
        latest = max(series)
        if latest > best_end:
            best, best_end = series, latest
        # Si la serie ya llega a los últimos dos años, no hace falta probar más tags.
        if latest >= (dt.date.today() - dt.timedelta(days=730)).isoformat():
            break
    return best


class SecService:
    def quarterly(self, symbol: str, force: bool = False) -> list[dict[str, Any]]:
        symbol = symbol.strip().upper()
        if not force:
            cached, age = store.get_sec_series(symbol)
            if cached is not None and age is not None and age < _CACHE_HOURS:
                return cached
        cik = _cik_map().get(symbol)
        if not cik:
            store.save_sec_series(symbol, [])
            return []
        revenue = _series_for(cik, _REV_TAGS)
        income = _series_for(cik, _NI_TAGS)
        records: dict[str, dict[str, Any]] = {}
        for end, item in revenue.items():
            records.setdefault(end, {})["revenue"] = item["value"]
            records[end]["revenue_filed"] = item["filed"]
        for end, item in income.items():
            records.setdefault(end, {})["net_income"] = item["value"]
            records[end]["net_income_filed"] = item["filed"]
        series = [
            {"end": end, **values}
            for end, values in sorted(records.items())
            if values.get("revenue")
        ]
        store.save_sec_series(symbol, series)
        return series


def quality_at(series: list[dict[str, Any]], date: str) -> float | None:
    """Score 0-100 usando solo trimestres presentados antes o en `date`."""
    if not series:
        return None
    available = [
        item
        for item in series
        if (item.get("revenue_filed") or "9999-12-31") <= date
        and item.get("revenue")
    ]
    if not available:
        return None
    latest = available[-1]
    try:
        latest_end = dt.date.fromisoformat(latest["end"])
    except (KeyError, ValueError):
        return None

    prior = None
    for item in reversed(available[:-1]):
        try:
            gap = (latest_end - dt.date.fromisoformat(item["end"])).days
        except (KeyError, ValueError):
            continue
        if 330 <= gap <= 400:
            prior = item
            break

    growth = None
    if prior and prior.get("revenue"):
        growth = latest["revenue"] / prior["revenue"] - 1.0
    margin = None
    if latest.get("net_income") is not None and latest.get("revenue"):
        if (latest.get("net_income_filed") or "9999-12-31") <= date:
            margin = latest["net_income"] / latest["revenue"]

    parts: list[tuple[float, float]] = []
    if growth is not None:
        parts.append((100.0 * ind.clamp((growth + 0.20) / 0.40), 0.60))
    if margin is not None:
        parts.append((100.0 * ind.clamp(margin / 0.20), 0.40))
    if not parts:
        return None
    total_weight = sum(weight for _, weight in parts)
    return round(sum(value * weight for value, weight in parts) / total_weight, 1)
