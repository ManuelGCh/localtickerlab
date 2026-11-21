"""Cálculo de métricas, puntuaciones y señales a partir de series de precios."""
from __future__ import annotations

import math
from typing import Any, Sequence

from . import config, indicators as ind

TRADING_DAYS = 252


def _r(value: float | None, digits: int = 4) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, float) and (math.isnan(value) or math.isinf(value)):
        return None
    return round(float(value), digits)


def _pct(new: float | None, old: float | None) -> float | None:
    if new is None or old in (None, 0):
        return None
    return new / old - 1.0


def _last(series: Sequence[float | None]) -> float | None:
    return ind.last_defined(series)


def _offset_return(closes: list[float], periods: int) -> float | None:
    if len(closes) <= periods:
        return None
    return _pct(closes[-1], closes[-1 - periods])


def compute_metrics(
    bars: list[dict[str, Any]],
    benchmark: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Métricas técnicas y de riesgo de un histórico diario (orden ascendente)."""
    if len(bars) < 30:
        raise ValueError("histórico insuficiente para calcular métricas")

    closes = [float(b["close"]) for b in bars]
    highs = [float(b["high"]) for b in bars]
    lows = [float(b["low"]) for b in bars]
    volumes = [float(b.get("volume") or 0.0) for b in bars]
    dates = [b["date"] for b in bars]

    sma20, sma50, sma200 = ind.sma(closes, 20), ind.sma(closes, 50), ind.sma(closes, 200)
    macd_data = ind.macd(closes)
    rsi_series = ind.rsi(closes, 14)
    atr_series = ind.atr(highs, lows, closes, 14)
    bb = ind.bollinger(closes, 20, 2.0)
    returns = ind.daily_returns(closes)

    price = closes[-1]
    prev = closes[-2] if len(closes) > 1 else None
    s20, s50, s200 = _last(sma20), _last(sma50), _last(sma200)
    rsi_val = _last(rsi_series)
    atr_val = _last(atr_series)
    macd_hist = _last(macd_data["hist"])
    macd_line = _last(macd_data["line"])

    ret_1d = _pct(price, prev)
    ret_1w = _offset_return(closes, 5)
    ret_1m = _offset_return(closes, 21)
    ret_3m = _offset_return(closes, 63)
    ret_6m = _offset_return(closes, 126)
    ret_1y = _offset_return(closes, 252)

    ytd = None
    year_start = dates[-1][:4]
    base = next((c for d, c in zip(dates, closes) if d.startswith(year_start)), None)
    if base:
        ytd = _pct(price, base)

    window_252 = closes[-TRADING_DAYS:] if len(closes) >= TRADING_DAYS else closes
    high_52 = max(window_252)
    low_52 = min(window_252)
    low_20 = min(lows[-20:])
    high_20 = max(highs[-20:])
    low_60 = min(lows[-60:])
    high_60 = max(highs[-60:])
    range_pos = (price - low_52) / (high_52 - low_52) if high_52 > low_52 else None

    vol_20 = ind.annualized_volatility(
        [r for r in returns[-20:] if r is not None]
    )
    vol_60 = ind.annualized_volatility(
        [r for r in returns[-60:] if r is not None]
    )
    vol_252 = ind.annualized_volatility(
        [r for r in returns[-TRADING_DAYS:] if r is not None]
    )

    dd_252 = ind.max_drawdown(window_252)

    recent_returns = [r for r in returns[-TRADING_DAYS:] if r is not None]
    sharpe = None
    if len(recent_returns) > 20:
        mean = sum(recent_returns) / len(recent_returns)
        sd = ind.stdev(recent_returns)
        if sd > 0:
            sharpe = (mean / sd) * math.sqrt(TRADING_DAYS)

    annual_return = None
    if len(window_252) > 1:
        annual_return = ind.cagr(window_252, len(window_252) / TRADING_DAYS)

    calmar = None
    if annual_return is not None and dd_252 > 0:
        calmar = annual_return / dd_252

    avg_vol20 = sum(volumes[-20:]) / min(20, len(volumes))
    avg_vol60 = sum(volumes[-60:]) / min(60, len(volumes))
    dollar_vol20 = sum(c * v for c, v in zip(closes[-20:], volumes[-20:])) / min(
        20, len(volumes)
    )
    volume_ratio = volumes[-1] / avg_vol20 if avg_vol20 else None

    beta_val = None
    if benchmark:
        bench_map = {b["date"]: float(b["close"]) for b in benchmark}
        pairs = [(d, c) for d, c in zip(dates, closes) if d in bench_map]
        if len(pairs) > 40:
            pairs = pairs[-TRADING_DAYS:]
            asset_rets: list[float] = []
            bench_rets: list[float] = []
            for i in range(1, len(pairs)):
                a0, a1 = pairs[i - 1][1], pairs[i][1]
                b0 = bench_map[pairs[i - 1][0]]
                b1 = bench_map[pairs[i][0]]
                if a0 and b0:
                    asset_rets.append(a1 / a0 - 1.0)
                    bench_rets.append(b1 / b0 - 1.0)
            beta_val = ind.beta(asset_rets, bench_rets)

    trend_slope = ind.slope_pct([v for v in sma50 if v is not None][-30:], 20)

    bandwidth = None
    if bb["upper"][-1] and bb["lower"][-1] and bb["mid"][-1]:
        bandwidth = (bb["upper"][-1] - bb["lower"][-1]) / bb["mid"][-1]
    percent_b = None
    if bb["upper"][-1] is not None and bb["lower"][-1] is not None:
        spread = bb["upper"][-1] - bb["lower"][-1]
        if spread > 0:
            percent_b = (price - bb["lower"][-1]) / spread

    return {
        "as_of": dates[-1],
        "bars": len(bars),
        "price": _r(price, 4),
        "prev_close": _r(prev, 4),
        "change": _r(price - prev, 4) if prev is not None else None,
        "change_pct": _r(ret_1d),
        "sma20": _r(s20, 4),
        "sma50": _r(s50, 4),
        "sma200": _r(s200, 4),
        "rsi": _r(rsi_val, 2),
        "macd_line": _r(macd_line, 4),
        "macd_hist": _r(macd_hist, 4),
        "atr": _r(atr_val, 4),
        "atr_pct": _r(atr_val / price if atr_val and price else None),
        "bb_upper": _r(bb["upper"][-1], 4),
        "bb_lower": _r(bb["lower"][-1], 4),
        "bb_percent": _r(percent_b),
        "bb_bandwidth": _r(bandwidth),
        "ret_1d": _r(ret_1d),
        "ret_1w": _r(ret_1w),
        "ret_1m": _r(ret_1m),
        "ret_3m": _r(ret_3m),
        "ret_6m": _r(ret_6m),
        "ret_1y": _r(ret_1y),
        "ret_ytd": _r(ytd),
        "high_52w": _r(high_52, 4),
        "low_52w": _r(low_52, 4),
        "high_20d": _r(high_20, 4),
        "low_20d": _r(low_20, 4),
        "high_60d": _r(high_60, 4),
        "low_60d": _r(low_60, 4),
        "range_pos": _r(range_pos),
        "from_high": _r(_pct(price, high_52)),
        "vol_20d": _r(vol_20),
        "vol_60d": _r(vol_60),
        "vol_252d": _r(vol_252),
        "max_drawdown": _r(dd_252),
        "sharpe": _r(sharpe, 3),
        "annual_return": _r(annual_return),
        "calmar": _r(calmar, 3),
        "avg_volume_20d": _r(avg_vol20, 0),
        "avg_volume_60d": _r(avg_vol60, 0),
        "dollar_volume_20d": _r(dollar_vol20, 0),
        "volume_ratio": _r(volume_ratio, 3),
        "beta": _r(beta_val, 3),
        "trend_slope": _r(trend_slope, 6),
        "golden_cross": bool(s50 and s200 and s50 > s200),
        "above_sma200": bool(s200 and price > s200),
    }


def score_metrics(m: dict[str, Any]) -> dict[str, Any]:
    """Convierte métricas en puntuaciones 0-100 (reglas transparentes)."""
    price = m.get("price") or 0.0
    s20, s50, s200 = m.get("sma20"), m.get("sma50"), m.get("sma200")

    # --- Tendencia -------------------------------------------------------- #
    trend = 0.0
    if s20 and price > s20:
        trend += 20
    if s50 and price > s50:
        trend += 20
    if s200 and price > s200:
        trend += 20
    if s20 and s50 and s20 > s50:
        trend += 15
    if s50 and s200 and s50 > s200:
        trend += 15
    slope = m.get("trend_slope") or 0.0
    trend += 10 * ind.clamp(0.5 + slope * 120)
    trend = ind.clamp(trend / 100.0) * 100

    # --- Momento ---------------------------------------------------------- #
    r1m = m.get("ret_1m") or 0.0
    r3m = m.get("ret_3m") or 0.0
    r6m = m.get("ret_6m") or 0.0
    r1y = m.get("ret_1y") or 0.0
    blended = 0.25 * r1m + 0.35 * r3m + 0.25 * r6m + 0.15 * r1y
    momentum = 100.0 * ind.sigmoid(blended / 0.16)

    # --- Riesgo ----------------------------------------------------------- #
    vol60 = m.get("vol_60d") or 0.6
    dd = m.get("max_drawdown") or 0.5
    vol_component = 100.0 * (1.0 - ind.clamp((vol60 - 0.12) / 0.55))
    dd_component = 100.0 * (1.0 - ind.clamp((dd - 0.08) / 0.45))
    risk = 0.5 * vol_component + 0.5 * dd_component

    # --- Zona de RSI ------------------------------------------------------ #
    rsi_val = m.get("rsi")
    if rsi_val is None:
        rsi_score = 50.0
    else:
        rsi_score = 100.0 - min(100.0, abs(rsi_val - 60.0) * 2.2)

    # --- Liquidez --------------------------------------------------------- #
    dollar_vol = m.get("dollar_volume_20d") or 0.0
    if dollar_vol <= 0:
        liq = 0.0
    else:
        liq = 100.0 * ind.clamp((math.log10(dollar_vol) - 6.0) / 2.5)
    volume_ratio = m.get("volume_ratio") or 1.0
    if volume_ratio >= 1.5:
        liq = min(100.0, liq + 8)
    elif volume_ratio < 0.5:
        liq = max(0.0, liq - 5)

    subscores = {
        "trend": round(trend, 1),
        "momentum": round(momentum, 1),
        "risk": round(risk, 1),
        "rsi": round(rsi_score, 1),
        "liquidity": round(liq, 1),
    }
    composite = sum(subscores[key] * weight for key, weight in config.SCORE_WEIGHTS.items())

    return {
        "subscores": subscores,
        "composite": round(composite, 1),
        "rating": rating_label(composite),
    }


def rating_label(score: float) -> str:
    if score >= 80:
        return "Muy fuerte"
    if score >= 65:
        return "Fuerte"
    if score >= 50:
        return "Neutral"
    if score >= 35:
        return "Débil"
    return "Muy débil"


def evaluate(m: dict[str, Any], scores: dict[str, Any]) -> dict[str, Any]:
    """Señal de compra/venta y precios objetivo a partir de las métricas técnicas.

    Es un modelo técnico transparente, no un consenso de analistas: combina
    persistencia del momento, pendiente de tendencia y reversión hacia la SMA200,
    con bandas de incertidumbre basadas en la volatilidad.
    """
    sub = scores["subscores"]
    composite = scores["composite"]
    trend, momentum = sub["trend"], sub["momentum"]
    rsi_val = m.get("rsi") or 50.0
    price = m.get("price") or 0.0
    if price <= 0:
        raise ValueError("precio no disponible")

    action, rank = config.ACTION_THRESHOLDS[-1][1], config.ACTION_THRESHOLDS[-1][2]
    for threshold, label, level in config.ACTION_THRESHOLDS:
        if composite >= threshold:
            action, rank = label, level
            break

    # Coherencia: evita señales extremas si los bloques se contradicen.
    if action == "COMPRA FUERTE" and not (trend >= 70 and momentum >= 60):
        action, rank = "COMPRA", 3
    if action == "COMPRA" and trend < 45 and momentum < 45:
        action, rank = "NEUTRAL", 2
    if action == "VENTA FUERTE" and not (trend <= 35 and momentum <= 40):
        action, rank = "VENTA", 1
    if action == "VENTA" and trend > 55:
        action, rank = "NEUTRAL", 2
    if action in ("COMPRA", "COMPRA FUERTE") and rsi_val >= 82 and action == "COMPRA FUERTE":
        action, rank = "COMPRA", 3

    directional = [trend, momentum, sub["risk"], sub["rsi"]]
    aligned = sum(1 for v in directional if (v - 50.0) * (composite - 50.0) >= 0)
    confidence = 40.0 + 0.7 * abs(composite - 50.0) + 20.0 * (aligned / len(directional) - 0.5) * 2
    confidence = int(round(ind.clamp(confidence, 5.0, 99.0)))

    sma200 = m.get("sma200")
    reversion = (sma200 / price - 1.0) * 0.20 if sma200 else 0.0
    mom_q = (
        0.30 * (m.get("ret_1m") or 0.0)
        + 0.45 * (m.get("ret_3m") or 0.0)
        + 0.25 * ((m.get("ret_6m") or 0.0) / 2.0)
    )
    slope_q = (m.get("trend_slope") or 0.0) * 63.0
    expected_3m = 0.45 * mom_q + 0.25 * slope_q + 0.25 * reversion
    if rsi_val >= 75:
        expected_3m -= 0.02
    elif rsi_val <= 30:
        expected_3m += 0.01
    # Saturación suave: evita objetivos irreales y agrupados en el tope.
    expected_3m = 0.30 * math.tanh(expected_3m / 0.30)

    target_3m = price * (1.0 + expected_3m)
    vol = m.get("vol_60d") or 0.35
    sigma_3m = vol * math.sqrt(63.0 / TRADING_DAYS)
    target_low = max(price * 0.35, target_3m * (1.0 - 0.8 * sigma_3m))
    target_high = target_3m * (1.0 + 0.8 * sigma_3m)

    expected_12m = 0.80 * math.tanh((expected_3m * 2.2 + reversion) / 0.80)
    target_12m = price * (1.0 + expected_12m)

    # Coherencia entre la señal y el potencial calculado.
    adjustment_note = None
    if action in ("COMPRA", "COMPRA FUERTE") and expected_3m <= 0.0:
        action, rank = "NEUTRAL", 2
        adjustment_note = "Señal rebajada: el objetivo técnico no ofrece potencial positivo."
    elif action == "VENTA FUERTE" and expected_3m >= 0.05:
        action, rank = "VENTA", 1
        adjustment_note = "Señal rebajada: el objetivo técnico no confirma la caída."
    elif action == "VENTA" and expected_3m >= 0.10:
        action, rank = "NEUTRAL", 2
        adjustment_note = "Señal rebajada: el objetivo técnico es positivo."
    if adjustment_note:
        confidence = min(confidence, 65)

    atr = m.get("atr") or price * 0.03
    low_20 = m.get("low_20d") or price * 0.94
    stop = max(price - 2.5 * atr, low_20 * 0.985)
    stop = min(stop, price * 0.96)
    stop = max(stop, price * 0.72)
    risk = price - stop
    reward = max(0.0, target_3m - price)
    risk_reward = reward / risk if risk > 0 and reward > 0 else None

    levels = [m.get("sma20"), m.get("sma50"), m.get("sma200"), m.get("low_20d")]
    supports = [v for v in levels if v and v < price]
    support = max(supports) if supports else m.get("low_52w")
    resistances = [
        v
        for v in (m.get("high_60d"), m.get("high_52w"), m.get("bb_upper"))
        if v and v > price * 1.005
    ]
    resistance = min(resistances) if resistances else m.get("high_52w")

    caveats: list[str] = []
    if adjustment_note:
        caveats.append(adjustment_note)
    if rsi_val >= 75:
        caveats.append("RSI en sobrecompra: el objetivo asume una entrada escalonada.")
    if m.get("price") and m.get("sma20") and price > m["sma20"] * 1.12:
        caveats.append("Precio muy extendido sobre la SMA20; riesgo de retroceso.")
    if (m.get("vol_60d") or 0) >= 0.55:
        caveats.append("Volatilidad muy alta: reduce el tamaño de la posición.")
    if (m.get("dollar_volume_20d") or 0) < 5_000_000:
        caveats.append("Liquidez limitada: cuidado con el deslizamiento.")
    if risk_reward is not None and risk_reward < 0.7 and action in ("COMPRA", "COMPRA FUERTE"):
        caveats.append("Relación beneficio/riesgo baja: objetivo cercano y stop lejano.")

    return {
        "action": action,
        "action_rank": rank,
        "confidence": confidence,
        "composite": composite,
        "horizon": "3 meses",
        "target_3m": _r(target_3m, 2),
        "upside_3m": _r(target_3m / price - 1.0),
        "target_low": _r(target_low, 2),
        "target_high": _r(target_high, 2),
        "target_12m": _r(target_12m, 2),
        "upside_12m": _r(target_12m / price - 1.0),
        "stop": _r(stop, 2),
        "stop_pct": _r(stop / price - 1.0),
        "risk_reward": _r(risk_reward, 2),
        "support": _r(support, 2),
        "resistance": _r(resistance, 2),
        "caveats": caveats,
        "method": (
            "Modelo técnico: 45% persistencia del momento, 25% pendiente de tendencia, "
            "25% reversión a la SMA200 y ajuste por RSI. Bandas según volatilidad 60d."
        ),
    }


def build_signals(m: dict[str, Any], scores: dict[str, Any]) -> list[dict[str, str]]:
    signals: list[dict[str, str]] = []
    rsi_val = m.get("rsi")
    trend = scores["subscores"]["trend"]
    momentum = scores["subscores"]["momentum"]

    if m.get("golden_cross") and m.get("above_sma200"):
        signals.append(
            {
                "type": "positive",
                "title": "Estructura alcista",
                "detail": "Precio sobre la SMA200 y SMA50 por encima de la SMA200.",
            }
        )
    elif not m.get("above_sma200"):
        signals.append(
            {
                "type": "negative",
                "title": "Bajo la SMA200",
                "detail": "El precio cotiza por debajo de su media de 200 sesiones.",
            }
        )

    if trend >= 80 and momentum >= 65:
        signals.append(
            {
                "type": "positive",
                "title": "Tendencia y momento alineados",
                "detail": "Precio y medias apuntan al alza con retornos positivos.",
            }
        )

    if rsi_val is not None:
        if rsi_val >= 75:
            signals.append(
                {
                    "type": "negative",
                    "title": f"Sobrecompra (RSI {rsi_val:.0f})",
                    "detail": "Un RSI alto indica riesgo de corrección a corto plazo.",
                }
            )
        elif rsi_val <= 30:
            signals.append(
                {
                    "type": "positive",
                    "title": f"Sobreventa (RSI {rsi_val:.0f})",
                    "detail": "Zona de posible rebote, aunque puede haber caída estructural.",
                }
            )

    pos = m.get("range_pos")
    if pos is not None:
        if pos >= 0.95:
            signals.append(
                {
                    "type": "positive",
                    "title": "Cerca de máximos de 52 semanas",
                    "detail": "Cotiza en el 5% superior de su rango anual.",
                }
            )
        elif pos <= 0.10:
            signals.append(
                {
                    "type": "negative",
                    "title": "Cerca de mínimos de 52 semanas",
                    "detail": "Cotiza en el 10% inferior de su rango anual.",
                }
            )

    ratio = m.get("volume_ratio")
    if ratio and ratio >= 2.0:
        signals.append(
            {
                "type": "neutral",
                "title": "Volumen anómalo",
                "detail": f"El volumen de la última sesión es {ratio:.1f}x su media de 20 días.",
            }
        )

    vol = m.get("vol_60d") or 0.0
    if vol >= 0.55:
        signals.append(
            {
                "type": "negative",
                "title": "Volatilidad elevada",
                "detail": f"Volatilidad anualizada del {vol * 100:.0f}%.",
            }
        )

    if (m.get("max_drawdown") or 0) >= 0.35:
        signals.append(
            {
                "type": "negative",
                "title": "Caída profunda en el último año",
                "detail": f"Drawdown máximo del {(m.get('max_drawdown') or 0) * 100:.0f}%.",
            }
        )

    if not signals:
        signals.append(
            {
                "type": "neutral",
                "title": "Sin señales destacadas",
                "detail": "El valor no muestra extremos técnicos relevantes.",
            }
        )
    return signals


def build_chart(bars: list[dict[str, Any]], points: int = config.CHART_POINTS) -> dict[str, Any]:
    """Series preparadas para dibujar velas, medias, volumen, RSI y MACD."""
    closes = [float(b["close"]) for b in bars]
    sma20 = ind.sma(closes, 20)
    sma50 = ind.sma(closes, 50)
    sma200 = ind.sma(closes, 200)
    rsi_series = ind.rsi(closes, 14)
    macd_data = ind.macd(closes)

    start = max(0, len(bars) - points)
    window = bars[start:]
    return {
        "dates": [b["date"] for b in window],
        "candles": [
            [
                round(float(b["open"]), 4),
                round(float(b["high"]), 4),
                round(float(b["low"]), 4),
                round(float(b["close"]), 4),
            ]
            for b in window
        ],
        "sma20": [_r(v) for v in sma20[start:]],
        "sma50": [_r(v) for v in sma50[start:]],
        "sma200": [_r(v) for v in sma200[start:]],
        "volume": [round(float(b.get("volume") or 0.0), 0) for b in window],
        "rsi": [_r(v, 2) for v in rsi_series[start:]],
        "macd_hist": [_r(v, 4) for v in macd_data["hist"][start:]],
    }


def analyze(
    symbol: str,
    bars: list[dict[str, Any]],
    instrument: dict[str, Any] | None = None,
    benchmark: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    metrics = compute_metrics(bars, benchmark)
    scores = score_metrics(metrics)
    evaluation = evaluate(metrics, scores)
    signals = build_signals(metrics, scores)
    instrument = instrument or {}

    summary = _summary(symbol, metrics, scores, evaluation)
    payload = {
        "symbol": symbol,
        "name": instrument.get("name") or "",
        "sector": instrument.get("sector") or "",
        "industry": instrument.get("industry") or "",
        "country": instrument.get("country") or "",
        "market_cap": instrument.get("market_cap"),
        "currency": "USD",
        "metrics": metrics,
        "scores": scores,
        "evaluation": evaluation,
        "signals": signals,
        "summary": summary,
        "chart": build_chart(bars),
        "disclaimer": config.DISCLAIMER,
    }
    return payload


def _summary(
    symbol: str,
    m: dict[str, Any],
    s: dict[str, Any],
    evaluation: dict[str, Any] | None = None,
) -> str:
    rating = s["rating"].lower()
    trend = s["subscores"]["trend"]
    mom = s["subscores"]["momentum"]
    if trend >= 65 and mom >= 65:
        tone = "muestra una tendencia alcista consolidada"
    elif trend >= 65:
        tone = "mantiene una tendencia alcista pero con momento moderado"
    elif trend < 40 and mom < 40:
        tone = "presenta una tendencia débil y momento negativo"
    else:
        tone = "se encuentra en una fase mixta, sin dirección clara"

    parts = [f"{symbol} {tone} (puntuación {s['composite']:.0f}/100, {rating})."]
    if evaluation:
        parts.append(
            f"Señal {evaluation['action']} (confianza {evaluation['confidence']}%), "
            f"objetivo a 3 meses en {evaluation['target_3m']:.2f} USD "
            f"({evaluation['upside_3m'] * 100:+.1f}%)."
        )
    if m.get("ret_3m") is not None:
        parts.append(f"Rentabilidad a 3 meses: {m['ret_3m'] * 100:+.1f}%.")
    if m.get("rsi") is not None:
        parts.append(f"RSI(14) en {m['rsi']:.0f}.")
    if m.get("vol_60d") is not None:
        parts.append(f"Volatilidad anualizada: {m['vol_60d'] * 100:.0f}%.")
    if m.get("from_high") is not None:
        parts.append(f"A {abs(m['from_high']) * 100:.1f}% de su máximo de 52 semanas.")
    return " ".join(parts)
