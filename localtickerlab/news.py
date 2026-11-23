"""Titulares de noticias, eventos y sentimiento aproximado.

Fuente: Google News RSS por ticker. Solo se analiza el titular, no el cuerpo
del artículo, así que el sentimiento es orientativo.
"""
from __future__ import annotations

import datetime as dt
import re
import xml.etree.ElementTree as ET
from email.utils import parsedate_to_datetime
from typing import Any
from urllib.parse import quote_plus

from . import net, store

_FEED = "https://news.google.com/rss/search"
_CACHE_MINUTES = 30

_POSITIVE = {
    "beat", "beats", "surge", "surges", "soar", "soars", "rally", "rallies",
    "record", "growth", "grows", "profit", "profits", "upgrade", "upgrades",
    "outperform", "buy", "strong", "raises", "raised", "boost", "boosts",
    "wins", "win", "approval", "approved", "partnership", "expansion",
    "breakthrough", "tops", "jumps", "gains", "gain", "bullish", "optimistic",
    "dividend", "buyback", "accelerates", "momentum", "higher", "positive",
}
_NEGATIVE = {
    "miss", "misses", "plunge", "plunges", "sink", "sinks", "drop", "drops",
    "fall", "falls", "decline", "declines", "loss", "losses", "downgrade",
    "downgrades", "underperform", "sell", "weak", "cuts", "cut", "slump",
    "warning", "warns", "lawsuit", "probe", "investigation", "recall",
    "layoffs", "bankruptcy", "fraud", "delay", "delays", "halt", "halts",
    "bearish", "concern", "concerns", "risk", "risks", "lower", "negative",
    "antitrust", "fine", "penalty", "resigns", "short",
}
_NEGATIONS = {"no", "not", "never", "without", "denies", "deny"}

_EVENTS: list[tuple[str, tuple[str, ...]]] = [
    ("Resultados", ("earnings", "quarter", "quarterly", "results", "revenue", "profit")),
    ("Guidance", ("guidance", "forecast", "outlook", "raises", "cuts", "estimates")),
    ("Analistas", ("upgrade", "downgrade", "price target", "rating", "initiates", "analyst")),
    ("M&A", ("acquisition", "merger", "buyout", "takeover", "deal")),
    ("Regulación", ("fda", "sec ", "lawsuit", "probe", "investigation", "antitrust", "court")),
    ("Dividendos", ("dividend", "buyback", "repurchase")),
    ("Empleo", ("layoffs", "job cuts", "hiring", "workforce")),
    ("Producto", ("launch", "unveils", "partnership", "contract", "expands")),
]


def _sentiment(text: str) -> int:
    words = re.findall(r"[a-z']+", text.lower())
    score = 0
    for index, word in enumerate(words):
        value = 1 if word in _POSITIVE else (-1 if word in _NEGATIVE else 0)
        if value and index > 0 and words[index - 1] in _NEGATIONS:
            value = -value
        score += value
    if score > 0:
        return 1
    if score < 0:
        return -1
    return 0


def _event(text: str) -> str | None:
    lowered = text.lower()
    for label, keywords in _EVENTS:
        if any(keyword in lowered for keyword in keywords):
            return label
    return None


def _parse_date(raw: str | None) -> str | None:
    if not raw:
        return None
    try:
        return parsedate_to_datetime(raw).astimezone(dt.timezone.utc).date().isoformat()
    except (TypeError, ValueError, OverflowError):
        return None


class NewsService:
    def get(self, symbol: str, force: bool = False) -> dict[str, Any]:
        symbol = symbol.strip().upper()
        if not force:
            cached, age = store.get_news(symbol)
            if cached and age is not None and age < _CACHE_MINUTES:
                return cached
        payload = self._fetch(symbol)
        store.save_news(symbol, payload)
        return payload

    def _fetch(self, symbol: str) -> dict[str, Any]:
        query = quote_plus(f"{symbol} stock")
        url = f"{_FEED}?q={query}&hl=en-US&gl=US&ceid=US:en"
        raw = net.get_bytes(
            url,
            headers={
                "Accept": "application/rss+xml, application/xml;q=0.9, */*;q=0.8",
                "Referer": "https://news.google.com/",
            },
            retries=2,
        )
        try:
            root = ET.fromstring(raw)
        except ET.ParseError as exc:
            raise net.FetchError(f"RSS no válido para {symbol}: {exc}") from exc

        items: list[dict[str, Any]] = []
        seen: set[str] = set()
        for node in root.iter("item"):
            title = (node.findtext("title") or "").strip()
            if not title:
                continue
            key = title.lower()
            if key in seen:
                continue
            seen.add(key)
            source = (node.findtext("source") or "").strip()
            link = (node.findtext("link") or "").strip()
            published = _parse_date(node.findtext("pubDate"))
            items.append(
                {
                    "title": title,
                    "link": link,
                    "source": source,
                    "published": published,
                    "sentiment": _sentiment(title),
                    "event": _event(title),
                }
            )
            if len(items) >= 20:
                break

        items.sort(key=lambda item: item.get("published") or "", reverse=True)
        items = items[:12]

        counts = {"positive": 0, "negative": 0, "neutral": 0}
        events: dict[str, int] = {}
        for item in items:
            counts[
                "positive"
                if item["sentiment"] > 0
                else "negative"
                if item["sentiment"] < 0
                else "neutral"
            ] += 1
            if item["event"]:
                events[item["event"]] = events.get(item["event"], 0) + 1

        total = max(1, len(items))
        avg = sum(item["sentiment"] for item in items) / total
        score = round(50 + 50 * avg, 1)

        return {
            "symbol": symbol,
            "fetched_at": store.now_ny().isoformat(timespec="seconds"),
            "items": items,
            "sentiment": {
                "score": score,
                "label": "Positivo" if score >= 60 else ("Negativo" if score <= 40 else "Neutro"),
                "positive": counts["positive"],
                "negative": counts["negative"],
                "neutral": counts["neutral"],
            },
            "events": sorted(
                ({"event": key, "count": value} for key, value in events.items()),
                key=lambda item: item["count"],
                reverse=True,
            ),
            "note": "Sentimiento calculado solo con titulares; es orientativo.",
        }
