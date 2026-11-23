"""Alertas y cartera: comprobación de condiciones y valoración de posiciones."""
from __future__ import annotations

import bisect
import math
from typing import Any

from . import analysis, net, store

ALERT_KINDS = {
    "price_above": "Precio por encima de",
    "price_below": "Precio por debajo de",
    "signal": "Señal igual a",
    "rsi_above": "RSI por encima de",
    "rsi_below": "RSI por debajo de",
    "score_above": "Puntuación por encima de",
    "score_below": "Puntuación por debajo de",
    "high_52w": "A menos de (%) del máximo 52s",
    "volume_spike": "Volumen relativo ≥ (x media)",
    "earnings_within": "Resultados en menos de (días)",
}

SIGNALS = ["COMPRA FUERTE", "COMPRA", "NEUTRAL", "VENTA", "VENTA FUERTE"]


def _current(symbol: str, data: Any) -> dict[str, Any] | None:
    """Precio, señal y RSI actuales, preferentemente desde la caché de puntuaciones."""
    cached = store.get_cached_score(symbol)
    expected = store.last_market_close().date().isoformat()
    if (
        cached
        and (cached.get("age_hours") or 99) < 48
        and (cached.get("as_of") or "") >= expected
    ):
        return dict(cached["row"])
    try:
        bars = data.get_bars(symbol)
        metrics = analysis.compute_metrics(bars)
        scores = analysis.score_metrics(metrics)
        evaluation = analysis.evaluate(metrics, scores)
        return {
            "symbol": symbol,
            "price": metrics["price"],
            "rsi": metrics["rsi"],
            "action": evaluation["action"],
            "change_pct": metrics["change_pct"],
            "as_of": metrics["as_of"],
        }
    except (net.FetchError, ValueError, KeyError, TypeError):
        if cached and (cached.get("age_hours") or 99) < 48:
            return dict(cached["row"])
        return None


def describe(alert: dict[str, Any]) -> str:
    kind = alert.get("kind")
    if kind == "signal":
        return f"{alert['symbol']}: señal = {alert.get('label') or '—'}"
    if kind == "earnings_within":
        return f"{alert['symbol']}: resultados en ≤ {alert.get('threshold'):.0f} días"
    if kind == "high_52w":
        return f"{alert['symbol']}: a ≤ {alert.get('threshold'):.1f}% del máximo de 52s"
    if kind == "volume_spike":
        return f"{alert['symbol']}: volumen ≥ {alert.get('threshold'):.1f}x la media"
    return f"{alert['symbol']}: {ALERT_KINDS.get(kind, kind)} {alert.get('threshold')}"


def check_alerts(data: Any, fundamentals: Any | None = None) -> list[dict[str, Any]]:
    today = store.now_ny().date().isoformat()
    triggered: list[dict[str, Any]] = []
    for alert in store.list_alerts():
        if not alert.get("active"):
            continue
        last = (alert.get("last_triggered") or "")[:10]
        if last == today:
            continue
        symbol = alert["symbol"]
        kind = alert["kind"]
        threshold = alert.get("threshold")
        row = _current(symbol, data)
        if not row:
            continue

        hit = False
        message = ""
        if kind == "price_above" and row.get("price") is not None:
            hit = float(row["price"]) >= float(threshold or 0)
            message = f"{symbol} cotiza a {row['price']:.2f} (≥ {threshold:.2f})"
        elif kind == "price_below" and row.get("price") is not None:
            hit = float(row["price"]) <= float(threshold or 0)
            message = f"{symbol} cotiza a {row['price']:.2f} (≤ {threshold:.2f})"
        elif kind == "signal":
            hit = row.get("action") == alert.get("label")
            message = f"{symbol}: señal {row.get('action')}"
        elif kind == "rsi_above" and row.get("rsi") is not None:
            hit = float(row["rsi"]) >= float(threshold or 0)
            message = f"{symbol}: RSI {row['rsi']:.0f} (≥ {threshold:.0f})"
        elif kind == "rsi_below" and row.get("rsi") is not None:
            hit = float(row["rsi"]) <= float(threshold or 0)
            message = f"{symbol}: RSI {row['rsi']:.0f} (≤ {threshold:.0f})"
        elif kind in ("score_above", "score_below") and row.get("score") is not None:
            score = float(row["score"])
            if kind == "score_above":
                hit = score >= float(threshold or 0)
                message = f"{symbol}: puntuación {score:.0f} (≥ {threshold:.0f})"
            else:
                hit = score <= float(threshold or 0)
                message = f"{symbol}: puntuación {score:.0f} (≤ {threshold:.0f})"
        elif kind == "high_52w" and row.get("from_high") is not None:
            distance = abs(float(row["from_high"])) * 100.0
            hit = distance <= float(threshold or 0)
            message = f"{symbol}: a {distance:.1f}% del máximo de 52s"
        elif kind == "volume_spike":
            ratio = row.get("volume_ratio")
            if ratio is None:
                try:
                    ratio = analysis.compute_metrics(data.get_bars(symbol)).get("volume_ratio")
                except (net.FetchError, ValueError, KeyError, TypeError):
                    ratio = None
            if ratio is not None:
                hit = float(ratio) >= float(threshold or 0)
                message = f"{symbol}: volumen {float(ratio):.1f}x la media"
        elif kind == "earnings_within" and fundamentals is not None:
            try:
                payload = fundamentals.get(symbol)
            except net.FetchError:
                continue
            days = (payload.get("next_earnings") or {}).get("days")
            if days is not None:
                hit = days <= float(threshold or 0)
                message = f"{symbol}: resultados en {days} días"

        if hit:
            store.add_alert_event(alert["id"], symbol, kind, message)
            store.mark_alert_triggered(alert["id"])
            triggered.append({"symbol": symbol, "kind": kind, "message": message})
    return triggered


def _returns(symbol: str) -> dict[str, float]:
    bars = store.get_bars(symbol)
    if len(bars) < 40:
        return {}
    closes = {b["date"]: float(b["close"]) for b in bars}
    dates = sorted(closes)
    return {
        dates[i]: closes[dates[i]] / closes[dates[i - 1]] - 1.0
        for i in range(1, len(dates))
        if closes[dates[i - 1]]
    }


def _risk_block(rows: list[dict[str, Any]], total_value: float) -> dict[str, Any] | None:
    usable = [row for row in rows if row.get("value")][:10]
    if len(usable) < 2 or not total_value:
        return None

    returns = {row["symbol"]: _returns(row["symbol"]) for row in usable}
    returns = {key: value for key, value in returns.items() if len(value) >= 40}
    if len(returns) < 2:
        return None

    symbols = list(returns)
    common = set.intersection(*(set(value) for value in returns.values()))
    dates = sorted(common)[-252:]
    if len(dates) < 60:
        return None

    weights = {
        row["symbol"]: row["value"] / total_value
        for row in usable
        if row["symbol"] in returns
    }
    weight_sum = sum(weights.values()) or 1.0
    weights = {key: value / weight_sum for key, value in weights.items()}

    portfolio = [
        sum(weights[symbol] * returns[symbol].get(date, 0.0) for symbol in symbols)
        for date in dates
    ]
    mean = sum(portfolio) / len(portfolio)
    volatility = math.sqrt(
        sum((value - mean) ** 2 for value in portfolio) / (len(portfolio) - 1)
    ) * math.sqrt(252)

    ordered = sorted(portfolio)
    var_index = max(0, int(0.05 * len(ordered)) - 1)
    var_95 = max(0.0, -ordered[var_index])

    equity = 1.0
    peak = 1.0
    max_drawdown = 0.0
    for value in portfolio:
        equity *= 1.0 + value
        peak = max(peak, equity)
        max_drawdown = max(max_drawdown, (peak - equity) / peak)

    window = dates[-60:]
    series = {symbol: [returns[symbol][date] for date in window] for symbol in symbols}
    matrix: list[list[float]] = []
    for first in symbols:
        row = []
        for second in symbols:
            x, y = series[first], series[second]
            mean_x = sum(x) / len(x)
            mean_y = sum(y) / len(y)
            covariance = sum((a - mean_x) * (b - mean_y) for a, b in zip(x, y))
            dev_x = math.sqrt(sum((a - mean_x) ** 2 for a in x))
            dev_y = math.sqrt(sum((b - mean_y) ** 2 for b in y))
            row.append(covariance / (dev_x * dev_y) if dev_x and dev_y else 0.0)
        matrix.append(row)

    pairs = [
        matrix[i][j]
        for i in range(len(symbols))
        for j in range(i + 1, len(symbols))
    ]
    average = sum(pairs) / len(pairs) if pairs else None

    return {
        "var_95": var_95 * total_value,
        "var_95_pct": var_95,
        "volatility": volatility,
        "max_drawdown": max_drawdown,
        "symbols": symbols,
        "matrix": matrix,
        "limited": len([row for row in rows if row.get("value")]) > 10,
        "analyzed": len(symbols),
        "avg_correlation": average,
        "diversification": (
            "Baja" if (average or 0) >= 0.7 else "Media" if (average or 0) >= 0.4 else "Alta"
        ),
    }


def _history(positions: list[dict[str, Any]]) -> dict[str, Any] | None:
    """Evolución reconstruida: efectivo + posiciones desde su fecha de entrada."""
    qqq = store.get_bars("QQQ")
    if len(qqq) < 30:
        return None
    qdates = [bar["date"] for bar in qqq]
    qcloses = {bar["date"]: float(bar["close"]) for bar in qqq}
    parts = []
    for position in positions:
        bars = store.get_bars(position["symbol"])
        if len(bars) < 5:
            continue
        dates = [bar["date"] for bar in bars]
        closes = {bar["date"]: float(bar["close"]) for bar in bars}
        entry = (
            position.get("entry_date")
            or (position.get("created_at") or "")[:10]
            or dates[0]
        )
        index = bisect.bisect_left(dates, entry)
        if index >= len(dates):
            continue
        parts.append(
            {
                "shares": float(position["shares"]),
                "entry_price": float(position["entry_price"]),
                "entry": dates[index],
                "dates": dates,
                "closes": closes,
            }
        )
    if not parts:
        return None
    start = min(part["entry"] for part in parts)
    dates = [date for date in qdates if date >= start]
    if len(dates) < 5:
        return None
    total_cost = sum(part["shares"] * part["entry_price"] for part in parts)
    if total_cost <= 0:
        return None
    values = []
    for date in dates:
        cash = total_cost
        holdings = 0.0
        for part in parts:
            if part["entry"] > date:
                continue
            cost = part["shares"] * part["entry_price"]
            cash -= cost
            if date == part["entry"]:
                price = part["entry_price"]
            else:
                index = bisect.bisect_right(part["dates"], date) - 1
                price = (
                    part["closes"][part["dates"][index]]
                    if index >= 0
                    else part["entry_price"]
                )
            holdings += part["shares"] * price
        values.append(cash + holdings)
    base = values[0] or total_cost
    bench_base = qcloses[dates[0]]
    if not bench_base:
        return None
    return {
        "dates": dates,
        "portfolio": [round(value / base * 100.0, 3) for value in values],
        "benchmark": [
            round(qcloses.get(date, bench_base) / bench_base * 100.0, 3)
            for date in dates
        ],
        "start": dates[0],
        "end": dates[-1],
        "positions": len(parts),
        "note": "Efectivo + posiciones actuales desde su fecha de entrada.",
    }


def portfolio_snapshot(data: Any) -> dict[str, Any]:
    positions = store.list_positions()
    rows: list[dict[str, Any]] = []
    total_value = 0.0
    total_cost = 0.0
    day_pnl = 0.0

    for position in positions:
        symbol = position["symbol"]
        current = _current(symbol, data) or {}
        price = current.get("price") or position["entry_price"]
        change_pct = current.get("change_pct") or 0.0
        shares = position["shares"]
        value = shares * price
        cost = shares * position["entry_price"]
        pnl = value - cost
        total_value += value
        total_cost += cost
        day_pnl += value * change_pct
        stop = position.get("stop")
        risk_per_share = (price - stop) if stop and stop < price else None
        risk_amount = risk_per_share * shares if risk_per_share else None
        instrument = store.get_instrument(symbol) or {}
        rows.append(
            {
                "id": position["id"],
                "symbol": symbol,
                "shares": shares,
                "entry_price": position["entry_price"],
                "entry_date": position["entry_date"],
                "price": price,
                "change_pct": change_pct,
                "value": value,
                "cost": cost,
                "pnl": pnl,
                "pnl_pct": (price / position["entry_price"] - 1.0)
                if position["entry_price"]
                else None,
                "signal": current.get("action"),
                "stop": position.get("stop"),
                "target": position.get("target"),
                "stop_distance": (
                    price / position["stop"] - 1.0
                    if position.get("stop") and price
                    else None
                ),
                "target_distance": (
                    position["target"] / price - 1.0
                    if position.get("target") and price
                    else None
                ),
                "note": position.get("note") or "",
                "sector": instrument.get("sector") or "",
                "risk_amount": risk_amount,
                "risk_per_share": risk_per_share,
                "r_multiple": (
                    (price - position["entry_price"]) / risk_per_share
                    if risk_per_share and position["entry_price"] > (stop or 0)
                    else None
                ),
            }
        )

    sector_values: dict[str, float] = {}
    for row in rows:
        row["weight"] = (row["value"] / total_value) if total_value else 0.0
        row["risk_pct"] = (
            (row["risk_amount"] or 0.0) / total_value if total_value else None
        )
        sector = row.get("sector") or "Sin sector"
        sector_values[sector] = sector_values.get(sector, 0.0) + row["value"]

    open_risk = sum(row["risk_amount"] or 0.0 for row in rows)
    rows.sort(key=lambda item: item["value"], reverse=True)
    return {
        "positions": rows,
        "history": _history(positions),
        "risk": _risk_block(rows, total_value),
        "totals": {
            "value": total_value,
            "cost": total_cost,
            "pnl": total_value - total_cost,
            "pnl_pct": (total_value / total_cost - 1.0) if total_cost else None,
            "day_pnl": day_pnl,
            "count": len(rows),
            "open_risk": open_risk,
            "open_risk_pct": (open_risk / total_value) if total_value else None,
        },
        "sectors": sorted(
            (
                {
                    "sector": sector,
                    "value": value,
                    "weight": (value / total_value) if total_value else 0.0,
                }
                for sector, value in sector_values.items()
            ),
            key=lambda item: item["value"],
            reverse=True,
        ),
    }
