"""Resultados trimestrales, fundamentales y score de calidad.

Fuente principal: endpoints públicos de nasdaq.com (no oficiales).
Los estados financieros vienen expresados en miles de dólares.
"""
from __future__ import annotations

import concurrent.futures as futures
import datetime as dt
import json
import re
import threading
import time
from typing import Any, Iterable
from zoneinfo import ZoneInfo

from . import config, indicators as ind, net, store

_API = "https://api.nasdaq.com/api"
_HEADERS = {"Referer": "https://www.nasdaq.com/"}
_NY = ZoneInfo("America/New_York")
_CALENDAR_PATH = config.DATA_DIR / "earnings_calendar.json"
_CACHE_HOURS = 12
_calendar_lock = threading.Lock()

_TABLE_KEYS = {
    "income": "incomeStatementTable",
    "balance": "balanceSheetTable",
    "cashflow": "cashFlowTable",
    "ratios": "financialRatiosTable",
}


# --------------------------------------------------------------------------- #
# Parseo
# --------------------------------------------------------------------------- #
def _num(raw: Any, thousands: bool = False) -> float | None:
    if raw is None or isinstance(raw, bool):
        return None
    if isinstance(raw, (int, float)):
        value = float(raw)
    else:
        text = str(raw).strip()
        if text in {"", "--", "-", "N/A", "n/a", "NA", "None"}:
            return None
        text = text.replace("$", "").replace(",", "").replace("%", "")
        try:
            value = float(text)
        except ValueError:
            return None
    return value * 1000.0 if thousands else value


def _pct(raw: Any) -> float | None:
    value = _num(raw)
    return value / 100.0 if value is not None else None


def _iso_date(raw: str | None) -> str | None:
    if not raw:
        return None
    for fmt in ("%m/%d/%Y", "%b %d, %Y", "%Y-%m-%d", "%m/%d/%y"):
        try:
            return dt.datetime.strptime(str(raw).strip(), fmt).date().isoformat()
        except ValueError:
            continue
    return None


def _table(payload: Any, key: str, thousands: bool = True) -> tuple[list[str], dict[str, list[float | None]]]:
    data = (payload or {}).get("data") or {}
    table = data.get(key) or {}
    headers = table.get("headers") or {}
    indexes: list[int] = []
    periods: list[str] = []
    for header, label in headers.items():
        if header == "value1" or not str(header).startswith("value"):
            continue
        try:
            indexes.append(int(str(header)[5:]))
        except ValueError:
            continue
        periods.append(_iso_date(label) or str(label))
    rows: dict[str, list[float | None]] = {}
    for row in table.get("rows") or []:
        label = str(row.get("value1") or "").strip()
        if not label:
            continue
        rows[label] = [_num(row.get(f"value{i}"), thousands) for i in indexes]
    return periods, rows


def _pick(rows: dict[str, list[float | None]], label: str, index: int) -> float | None:
    values = rows.get(label) or []
    return values[index] if index < len(values) else None


def _summary_map(payload: Any) -> dict[str, Any]:
    data = (payload or {}).get("data") or {}
    out: dict[str, Any] = {}
    for item in (data.get("summaryData") or {}).values():
        if isinstance(item, dict) and item.get("label"):
            out[str(item["label"])] = item.get("value")
    return out


def _dividend_map(payload: Any) -> dict[str, Any]:
    data = (payload or {}).get("data") or {}
    out: dict[str, Any] = {}
    for item in data.get("dividendHeaderValues") or []:
        if isinstance(item, dict) and item.get("label"):
            out[str(item["label"])] = item.get("value")
    return out


def _safe_get(url: str) -> Any:
    try:
        return net.get_json(url, headers=_HEADERS, retries=2)
    except (net.FetchError, ValueError):
        return None


# --------------------------------------------------------------------------- #
# Calendario de resultados (una consulta por día, cacheado)
# --------------------------------------------------------------------------- #
def _read_calendar_file() -> dict[str, dict[str, Any]] | None:
    try:
        raw = json.loads(_CALENDAR_PATH.read_text())
        fetched = dt.datetime.fromisoformat(raw.get("fetched_at"))
        if fetched.tzinfo is None:
            fetched = fetched.replace(tzinfo=_NY)
        if store.now_ny() - fetched < dt.timedelta(hours=_CACHE_HOURS):
            return raw.get("map") or {}
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        pass
    return None


def _build_calendar() -> dict[str, dict[str, Any]]:
    mapping: dict[str, dict[str, Any]] = {}
    today = store.now_ny().date()
    for offset in range(0, 31):
        day = today + dt.timedelta(days=offset)
        if day.weekday() >= 5:
            continue
        payload = _safe_get(f"{_API}/calendar/earnings?date={day.isoformat()}")
        data = (payload or {}).get("data") or {}
        for row in data.get("rows") or []:
            symbol = str(row.get("symbol") or "").strip().upper()
            if not symbol:
                continue
            mapping[symbol] = {
                "date": day.isoformat(),
                "time": row.get("time") or "",
                "eps_forecast": _num(row.get("epsForecast")),
                "last_year_eps": _num(row.get("lastYearEPS")),
                "fiscal_quarter": row.get("fiscalQuarterEnding") or "",
            }
    config.DATA_DIR.mkdir(parents=True, exist_ok=True)
    _CALENDAR_PATH.write_text(
        json.dumps(
            {"fetched_at": store.now_ny().isoformat(timespec="seconds"), "map": mapping},
            ensure_ascii=False,
        )
    )
    return mapping


_calendar_cache: dict[str, dict[str, Any]] | None = None
_calendar_refreshing = False


def _refresh_calendar() -> None:
    global _calendar_cache, _calendar_refreshing
    if _calendar_refreshing:
        return
    with _calendar_lock:
        if _calendar_refreshing:
            return
        _calendar_refreshing = True
        try:
            fresh = _read_calendar_file()
            _calendar_cache = fresh if fresh is not None else _build_calendar()
        finally:
            _calendar_refreshing = False


def _load_calendar(block: bool = False) -> dict[str, dict[str, Any]]:
    """Devuelve el calendario cacheado; si no existe, lo construye en segundo plano."""
    global _calendar_cache
    if _calendar_cache is not None:
        # Si caducó, se refresca en segundo plano sin bloquear la petición.
        if _read_calendar_file() is None and not _calendar_refreshing:
            threading.Thread(target=_refresh_calendar, daemon=True).start()
        return _calendar_cache
    fresh = _read_calendar_file()
    if fresh is not None:
        _calendar_cache = fresh
        return fresh
    if block:
        _refresh_calendar()
        return _calendar_cache or {}
    threading.Thread(target=_refresh_calendar, daemon=True).start()
    return {}


# --------------------------------------------------------------------------- #
# Score de calidad
# --------------------------------------------------------------------------- #
def _label(score: float) -> str:
    if score >= 75:
        return "Excelente"
    if score >= 60:
        return "Buena"
    if score >= 45:
        return "Aceptable"
    if score >= 30:
        return "Débil"
    return "Muy débil"


def _quality(
    quarters: list[dict[str, Any]],
    annual: dict[str, Any],
    surprises: list[dict[str, Any]],
    analyst_target: float | None,
    reference_price: float | None,
    pe: float | None,
) -> dict[str, Any]:
    latest = quarters[0] if quarters else {}
    rev_g = annual.get("revenue_growth")
    eps_g = annual.get("eps_growth")

    components: dict[str, float] = {}

    growth_parts = []
    if rev_g is not None:
        growth_parts.append(100.0 * ind.clamp((rev_g + 0.20) / 0.40))
    if eps_g is not None:
        growth_parts.append(100.0 * ind.clamp((eps_g + 0.20) / 0.45))
    if growth_parts:
        components["growth"] = sum(growth_parts) / len(growth_parts)

    margins = []
    gross = latest.get("gross_margin")
    operating = latest.get("operating_margin")
    net = latest.get("net_margin")
    if gross is not None:
        margins.append(100.0 * ind.clamp(gross / 0.60))
    if operating is not None:
        margins.append(100.0 * ind.clamp(operating / 0.30))
    if net is not None:
        margins.append(100.0 * ind.clamp(net / 0.20))
    fcf = latest.get("fcf")
    revenue = latest.get("revenue")
    if fcf is not None and revenue:
        margins.append(100.0 * ind.clamp((fcf / revenue) / 0.25))
    if margins:
        components["profitability"] = sum(margins) / len(margins)

    debt = latest.get("debt")
    equity = latest.get("equity")
    if debt is not None and equity and equity > 0:
        de = debt / equity
        components["balance"] = 100.0 * (1.0 - ind.clamp((de - 0.2) / 1.8))
    elif debt == 0:
        components["balance"] = 100.0

    if surprises:
        beats = [1.0 if (s.get("surprise_pct") or 0) > 0 else 0.0 for s in surprises[:4]]
        beat_rate = sum(beats) / len(beats)
        avg_surprise = sum((s.get("surprise_pct") or 0) for s in surprises[:4]) / len(beats)
        components["consistency"] = 100.0 * (
            0.6 * beat_rate + 0.4 * ind.clamp((avg_surprise + 0.02) / 0.10)
        )

    if analyst_target and reference_price:
        upside = analyst_target / reference_price - 1.0
        value = 100.0 * ind.clamp((upside + 0.10) / 0.35)
        if pe is not None and pe > 45 and (rev_g is None or rev_g < 0.10):
            value *= 0.75
        components["valuation"] = value

    weights = {
        "growth": 0.25,
        "profitability": 0.30,
        "balance": 0.15,
        "consistency": 0.20,
        "valuation": 0.10,
    }
    used = [key for key in weights if key in components]
    total = (
        sum(components[key] * weights[key] for key in used) / sum(weights[key] for key in used)
        if used
        else 50.0
    )

    risks: list[str] = []
    if rev_g is not None and rev_g < 0:
        risks.append(f"Ingresos en caída ({rev_g * 100:.1f}% anual).")
    if latest.get("net_income") is not None and latest["net_income"] < 0:
        risks.append("Pérdidas en el último trimestre.")
    if latest.get("fcf") is not None and latest["fcf"] < 0:
        risks.append("Flujo de caja libre negativo.")
    if debt is not None and equity and equity > 0 and debt / equity > 1.5:
        risks.append(f"Apalancamiento alto (deuda/equity {debt / equity:.1f}x).")
    if surprises and all((s.get("surprise_pct") or 0) < 0 for s in surprises[:2]):
        risks.append("Dos trimestres consecutivos por debajo del consenso.")
    if pe is not None and pe > 50 and (rev_g is None or rev_g < 0.10):
        risks.append(f"PER elevado ({pe:.0f}) sin crecimiento suficiente.")
    if not quarters:
        risks.append("Sin estados financieros recientes disponibles.")

    return {
        "score": round(total, 1),
        "label": _label(total),
        "subscores": {k: round(v, 1) for k, v in components.items()},
        "risks": risks,
    }


# --------------------------------------------------------------------------- #
# Servicio
# --------------------------------------------------------------------------- #
class FundamentalsService:
    def __init__(self) -> None:
        self._locks: dict[str, threading.Lock] = {}
        self._guard = threading.Lock()
        self._failed: dict[str, float] = {}
        # Calienta el calendario de resultados sin bloquear el arranque.
        threading.Thread(target=_load_calendar, daemon=True).start()

    def _lock_for(self, symbol: str) -> threading.Lock:
        with self._guard:
            lock = self._locks.get(symbol)
            if lock is None:
                lock = threading.Lock()
                self._locks[symbol] = lock
            return lock

    def get(self, symbol: str, force: bool = False) -> dict[str, Any]:
        symbol = symbol.strip().upper()
        if not force:
            cached = store.get_fundamentals(symbol)
            age = store.fundamentals_age(symbol)
            if cached and age is not None and age < _CACHE_HOURS:
                return cached

        with self._lock_for(symbol):
            if not force:
                cached = store.get_fundamentals(symbol)
                age = store.fundamentals_age(symbol)
                if cached and age is not None and age < _CACHE_HOURS:
                    return cached
            if self._failed.get(symbol, 0.0) > time.time():
                raise net.NoDataError(f"{symbol}: sin estados financieros disponibles")
            payload = self._fetch(symbol)
            if not payload.get("quarters"):
                self._failed[symbol] = time.time() + 3600
                raise net.NoDataError(f"{symbol}: sin estados financieros disponibles")
            store.save_fundamentals(symbol, payload)
            return payload

    # -- descarga ---------------------------------------------------------- #
    def _fetch(self, symbol: str) -> dict[str, Any]:
        urls = {
            "summary": f"{_API}/quote/{net.quote_symbol(symbol)}/summary?assetclass=stocks",
            "dividends": f"{_API}/quote/{net.quote_symbol(symbol)}/dividends?assetclass=stocks",
            "surprise": f"{_API}/company/{net.quote_symbol(symbol)}/earnings-surprise",
            "forecast": f"{_API}/analyst/{net.quote_symbol(symbol)}/earnings-forecast",
            "ratings": f"{_API}/analyst/{net.quote_symbol(symbol)}/ratings",
            "financials_q": f"{_API}/company/{net.quote_symbol(symbol)}/financials?frequency=2",
            "financials_a": f"{_API}/company/{net.quote_symbol(symbol)}/financials?frequency=1",
        }
        results: dict[str, Any] = {}
        with futures.ThreadPoolExecutor(max_workers=7) as pool:
            pending = {pool.submit(_safe_get, url): key for key, url in urls.items()}
            for future in futures.as_completed(pending):
                results[pending[future]] = future.result()

        summary = _summary_map(results.get("summary"))
        dividends = _dividend_map(results.get("dividends"))

        # --- trimestres ---------------------------------------------------- #
        q_income = _table(results.get("financials_q"), _TABLE_KEYS["income"])
        q_balance = _table(results.get("financials_q"), _TABLE_KEYS["balance"])
        q_cash = _table(results.get("financials_q"), _TABLE_KEYS["cashflow"])
        q_ratios = _table(results.get("financials_q"), _TABLE_KEYS["ratios"], thousands=False)
        periods = q_income[0] or q_balance[0] or q_cash[0]

        quarters = []
        for i in range(min(4, len(periods))):
            revenue = _pick(q_income[1], "Total Revenue", i)
            gross = _pick(q_income[1], "Gross Profit", i)
            operating = _pick(q_income[1], "Operating Income", i)
            net_income = _pick(q_income[1], "Net Income", i)
            ocf = _pick(q_cash[1], "Net Cash Flow-Operating", i)
            capex = _pick(q_cash[1], "Capital Expenditures", i)
            fcf = (ocf + capex) if ocf is not None and capex is not None else None
            cash = (_pick(q_balance[1], "Cash and Cash Equivalents", i) or 0.0) + (
                _pick(q_balance[1], "Short-Term Investments", i) or 0.0
            )
            debt = (_pick(q_balance[1], "Short-Term Debt / Current Portion of Long-Term Debt", i) or 0.0) + (
                _pick(q_balance[1], "Long-Term Debt", i) or 0.0
            )
            equity = _pick(q_balance[1], "Total Equity", i)

            gm = _pct(_pick(q_ratios[1], "Gross Margin", i))
            om = _pct(_pick(q_ratios[1], "Operating Margin", i))
            nm = _pct(_pick(q_ratios[1], "Profit Margin", i))
            roe = _pct(_pick(q_ratios[1], "After Tax ROE", i))
            if gm is None and gross and revenue:
                gm = gross / revenue
            if om is None and operating and revenue:
                om = operating / revenue
            if nm is None and net_income and revenue:
                nm = net_income / revenue

            quarters.append(
                {
                    "period": periods[i],
                    "revenue": revenue,
                    "gross_profit": gross,
                    "operating_income": operating,
                    "net_income": net_income,
                    "fcf": fcf,
                    "cash": cash,
                    "debt": debt,
                    "equity": equity,
                    "gross_margin": gm,
                    "operating_margin": om,
                    "net_margin": nm,
                    "roe": roe,
                }
            )

        # --- anual y crecimiento ------------------------------------------ #
        a_income = _table(results.get("financials_a"), _TABLE_KEYS["income"])
        a_periods = a_income[0]
        revenues = a_income[1].get("Total Revenue") or []
        net_incomes = a_income[1].get("Net Income") or []
        annual = {
            "periods": a_periods[:4],
            "revenue": revenues[:4],
            "net_income": net_incomes[:4],
            "revenue_growth": (
                revenues[0] / revenues[1] - 1.0
                if len(revenues) > 1 and revenues[0] and revenues[1]
                else None
            ),
            "net_income_growth": (
                net_incomes[0] / net_incomes[1] - 1.0
                if len(net_incomes) > 1 and net_incomes[0] and net_incomes[1]
                else None
            ),
        }

        # --- sorpresas de EPS --------------------------------------------- #
        surprises = []
        surprise_payload = results.get("surprise") or {}
        table = ((surprise_payload.get("data") or {}).get("earningsSurpriseTable") or {})
        for row in table.get("rows") or []:
            eps = _num(row.get("eps"))
            consensus = _num(row.get("consensusForecast"))
            surprise = _num(row.get("percentageSurprise"))
            if surprise is not None:
                surprise /= 100.0
            elif eps is not None and consensus:
                surprise = eps / consensus - 1.0
            surprises.append(
                {
                    "period": row.get("fiscalQtrEnd") or "",
                    "reported": _iso_date(row.get("dateReported")),
                    "eps": eps,
                    "consensus": consensus,
                    "surprise_pct": surprise,
                }
            )
        surprises = surprises[:8]
        eps_ttm = [s["eps"] for s in surprises if s.get("eps") is not None]
        annual["eps_growth"] = (
            (sum(eps_ttm[:4]) / sum(eps_ttm[4:8]) - 1.0)
            if len(eps_ttm) >= 8 and sum(eps_ttm[4:8]) > 0
            else None
        )

        # --- analistas y próximo reporte ---------------------------------- #
        ratings = (results.get("ratings") or {}).get("data") or {}
        count_match = re.search(r"(\d+)\s+analysts", ratings.get("ratingsSummary") or "")
        forecast = (results.get("forecast") or {}).get("data") or {}
        quarterly_forecast = (forecast.get("quarterlyForecast") or {}).get("rows") or []
        next_consensus = quarterly_forecast[0] if quarterly_forecast else {}

        reference_price = _num(summary.get("Previous Close"))
        target = _num(summary.get("1 Year Target"))
        pe = _num(dividends.get("P/E Ratio"))

        calendar = _load_calendar()
        event = calendar.get(symbol)
        if event:
            event_date = event["date"]
            next_earnings = {
                "date": event_date,
                "days": max(
                    0,
                    (dt.date.fromisoformat(event_date) - store.now_ny().date()).days,
                ),
                "confirmed": True,
                "time": event.get("time") or "",
                "eps_consensus": event.get("eps_forecast") or next_consensus.get("consensusEPSForecast"),
                "fiscal_period": event.get("fiscal_quarter") or next_consensus.get("fiscalEnd"),
            }
        else:
            next_earnings = {
                "date": None,
                "days": None,
                "confirmed": False,
                "time": "",
                "eps_consensus": next_consensus.get("consensusEPSForecast"),
                "fiscal_period": next_consensus.get("fiscalEnd"),
            }

        quality = _quality(quarters, annual, surprises, target, reference_price, pe)
        quality["next_earnings"] = next_earnings
        if next_earnings["days"] is not None and next_earnings["days"] <= 7:
            quality["risks"].insert(
                0, f"Resultados en {next_earnings['days']} días: el objetivo pierde fiabilidad."
            )

        payload = {
            "symbol": symbol,
            "source": "nasdaq",
            "fetched_at": store.now_ny().isoformat(timespec="seconds"),
            "partial": not quarters,
            "company": {
                "sector": summary.get("Sector") or "",
                "industry": summary.get("Industry") or "",
                "exchange": summary.get("Exchange") or "",
                "market_cap": _num(summary.get("Market Cap")),
                "reference_price": reference_price,
                "pe": pe,
                "dividend_yield": _num(summary.get("Current Yield")),
                "annual_dividend": _num(summary.get("Annualized Dividend")),
                "payout_ratio": _num(dividends.get("Payout Ratio")),
                "week_52": summary.get("52 Week High/Low") or "",
            },
            "analyst": {
                "rating": ratings.get("meanRatingType") or "",
                "count": int(count_match.group(1)) if count_match else None,
                "target": target,
                "upside": (target / reference_price - 1.0) if target and reference_price else None,
                "years": [
                    {
                        "period": row.get("fiscalEnd"),
                        "eps": _num(row.get("consensusEPSForecast")),
                    }
                    for row in ((forecast.get("yearlyForecast") or {}).get("rows") or [])[:2]
                ],
            },
            "next_earnings": next_earnings,
            "quarters": quarters,
            "annual": annual,
            "surprises": surprises,
            "quality": quality,
            "notes": [
                "Estados financieros en miles de USD convertidos a USD.",
                "El consenso es de EPS, no de ingresos.",
                "Datos públicos de nasdaq.com; pueden llegar con retraso.",
            ],
        }
        return payload
