"""Caché local en SQLite: precios, instrumentos, universos y resultados."""
from __future__ import annotations

import datetime as dt
import json
import math
import sqlite3
import threading
from pathlib import Path
from typing import Any, Iterable
from zoneinfo import ZoneInfo

from . import config

_NY = ZoneInfo("America/New_York")
_local = threading.local()
_write_lock = threading.Lock()

_SCHEMA = """
CREATE TABLE IF NOT EXISTS prices (
    symbol TEXT NOT NULL,
    date   TEXT NOT NULL,
    open   REAL,
    high   REAL,
    low    REAL,
    close  REAL,
    volume REAL,
    PRIMARY KEY (symbol, date)
) WITHOUT ROWID;

CREATE INDEX IF NOT EXISTS idx_prices_date ON prices(date);

CREATE TABLE IF NOT EXISTS fetch_log (
    symbol     TEXT PRIMARY KEY,
    source     TEXT,
    rows       INTEGER,
    fetched_at TEXT
);

CREATE TABLE IF NOT EXISTS instruments (
    symbol     TEXT PRIMARY KEY,
    name       TEXT,
    sector     TEXT,
    industry   TEXT,
    country    TEXT,
    market_cap REAL,
    price      REAL,
    volume     REAL,
    change_pct REAL,
    kind       TEXT,
    updated_at TEXT
);

CREATE TABLE IF NOT EXISTS universes (
    key        TEXT NOT NULL,
    symbol     TEXT NOT NULL,
    updated_at TEXT,
    PRIMARY KEY (key, symbol)
);

CREATE TABLE IF NOT EXISTS fundamentals (
    symbol     TEXT PRIMARY KEY,
    payload    TEXT,
    fetched_at TEXT
);

CREATE TABLE IF NOT EXISTS alerts (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    symbol         TEXT NOT NULL,
    kind           TEXT NOT NULL,
    threshold      REAL,
    label          TEXT,
    active         INTEGER DEFAULT 1,
    created_at     TEXT,
    last_triggered TEXT
);

CREATE TABLE IF NOT EXISTS alert_events (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    alert_id   INTEGER,
    symbol     TEXT,
    kind       TEXT,
    message    TEXT,
    created_at TEXT,
    read       INTEGER DEFAULT 0
);

CREATE TABLE IF NOT EXISTS positions (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    symbol      TEXT NOT NULL,
    shares      REAL NOT NULL,
    entry_price REAL NOT NULL,
    entry_date  TEXT,
    stop        REAL,
    target      REAL,
    note        TEXT,
    created_at  TEXT
);

CREATE TABLE IF NOT EXISTS sec_series (
    symbol     TEXT PRIMARY KEY,
    payload    TEXT,
    fetched_at TEXT
);

CREATE TABLE IF NOT EXISTS news_cache (
    symbol     TEXT PRIMARY KEY,
    payload    TEXT,
    fetched_at TEXT
);

CREATE TABLE IF NOT EXISTS score_cache (
    symbol     TEXT PRIMARY KEY,
    as_of      TEXT,
    payload    TEXT,
    updated_at TEXT
);

CREATE TABLE IF NOT EXISTS screen_runs (
    id          TEXT PRIMARY KEY,
    universe    TEXT,
    params      TEXT,
    status      TEXT,
    created_at  TEXT,
    finished_at TEXT,
    total       INTEGER,
    done        INTEGER,
    errors      INTEGER,
    payload     TEXT,
    regime      TEXT
);
"""


def now_ny() -> dt.datetime:
    return dt.datetime.now(_NY)


def _connect() -> sqlite3.Connection:
    conn = getattr(_local, "conn", None)
    if conn is None:
        config.DATA_DIR.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(config.DB_PATH, timeout=30, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        conn.execute("PRAGMA busy_timeout=10000")
        _local.conn = conn
    return conn


def init() -> None:
    config.DATA_DIR.mkdir(parents=True, exist_ok=True)
    with _connect() as conn:
        conn.executescript(_SCHEMA)
        try:
            conn.execute("ALTER TABLE screen_runs ADD COLUMN regime TEXT")
        except sqlite3.OperationalError:
            pass


# --------------------------------------------------------------------------- #
# Precios
# --------------------------------------------------------------------------- #
def upsert_bars(symbol: str, bars: Iterable[dict[str, Any]]) -> int:
    rows = [
        (
            symbol,
            bar["date"],
            bar["open"],
            bar["high"],
            bar["low"],
            bar["close"],
            bar["volume"],
        )
        for bar in bars
    ]
    if not rows:
        return 0
    with _write_lock, _connect() as conn:
        conn.executemany(
            "INSERT INTO prices(symbol, date, open, high, low, close, volume) "
            "VALUES(?,?,?,?,?,?,?) "
            "ON CONFLICT(symbol, date) DO UPDATE SET "
            "open=excluded.open, high=excluded.high, low=excluded.low, "
            "close=excluded.close, volume=excluded.volume",
            rows,
        )
    return len(rows)


def get_bars(symbol: str, limit: int | None = None) -> list[dict[str, Any]]:
    sql = (
        "SELECT date, open, high, low, close, volume FROM prices "
        "WHERE symbol = ? ORDER BY date DESC"
    )
    params: list[Any] = [symbol]
    if limit:
        sql += " LIMIT ?"
        params.append(limit)
    with _connect() as conn:
        rows = conn.execute(sql, params).fetchall()
    return [dict(row) for row in reversed(rows)]


def get_last_sync(symbol: str) -> dict[str, Any] | None:
    with _connect() as conn:
        row = conn.execute(
            "SELECT symbol, source, rows, fetched_at FROM fetch_log WHERE symbol = ?",
            (symbol,),
        ).fetchone()
    return dict(row) if row else None


def mark_synced(symbol: str, source: str, rows: int) -> None:
    with _write_lock, _connect() as conn:
        conn.execute(
            "INSERT INTO fetch_log(symbol, source, rows, fetched_at) VALUES(?,?,?,?) "
            "ON CONFLICT(symbol) DO UPDATE SET source=excluded.source, "
            "rows=excluded.rows, fetched_at=excluded.fetched_at",
            (symbol, source, rows, now_ny().isoformat(timespec="seconds")),
        )


def last_market_close(now: dt.datetime | None = None) -> dt.datetime:
    """Último cierre aproximado (18:00 ET) de un día laborable."""
    now = now or now_ny()
    candidate = now.replace(hour=18, minute=0, second=0, microsecond=0)
    if now < candidate:
        candidate -= dt.timedelta(days=1)
    while candidate.weekday() >= 5:
        candidate -= dt.timedelta(days=1)
    return candidate


def latest_run_time() -> str | None:
    with _connect() as conn:
        row = conn.execute("SELECT MAX(created_at) AS t FROM screen_runs").fetchone()
    return row["t"] if row and row["t"] else None


def _last_market_close(now: dt.datetime) -> dt.datetime:
    """Momento aproximado del último cierre de mercado (18:00 hora NY)."""
    candidate = now.replace(hour=18, minute=0, second=0, microsecond=0)
    if now < candidate:
        candidate -= dt.timedelta(days=1)
    while candidate.weekday() >= 5:
        candidate -= dt.timedelta(days=1)
    return candidate


def last_price_date(symbol: str) -> str | None:
    with _connect() as conn:
        row = conn.execute(
            "SELECT MAX(date) AS d FROM prices WHERE symbol = ?", (symbol,)
        ).fetchone()
    return row["d"] if row and row["d"] else None


def needs_refresh(symbol: str) -> bool:
    log = get_last_sync(symbol)
    if not log:
        return True
    try:
        fetched = dt.datetime.fromisoformat(log["fetched_at"])
    except (TypeError, ValueError):
        return True
    if fetched.tzinfo is None:
        fetched = fetched.replace(tzinfo=_NY)
    rows = int(log.get("rows") or 0)
    if rows == 0:
        # Ticker sin datos: no reintentar durante 3 días.
        return fetched < now_ny() - dt.timedelta(days=3)
    if rows < 60:
        return True
    now = now_ny()
    close = _last_market_close(now)
    # Si el último cierre esperado no está en la serie, la caché está atrasada.
    # Se reintenta como máximo cada 6 horas para no martillear la fuente.
    last = last_price_date(symbol)
    if last and last < close.date().isoformat():
        return fetched < now - dt.timedelta(hours=6)
    return fetched < close


def summarize_symbol(symbol: str) -> dict[str, Any] | None:
    with _connect() as conn:
        row = conn.execute(
            "SELECT COUNT(*) AS n, MIN(date) AS first, MAX(date) AS last "
            "FROM prices WHERE symbol = ?",
            (symbol,),
        ).fetchone()
    if not row or not row["n"]:
        return None
    return {"rows": row["n"], "first": row["first"], "last": row["last"]}


# --------------------------------------------------------------------------- #
# Instrumentos y universos
# --------------------------------------------------------------------------- #
def upsert_instruments(rows: Iterable[dict[str, Any]]) -> None:
    stamp = now_ny().isoformat(timespec="seconds")
    payload = [
        (
            row["symbol"],
            row.get("name") or "",
            row.get("sector") or "",
            row.get("industry") or "",
            row.get("country") or "",
            row.get("market_cap"),
            row.get("price"),
            row.get("volume"),
            row.get("change_pct"),
            row.get("kind") or "stock",
            stamp,
        )
        for row in rows
    ]
    if not payload:
        return
    with _write_lock, _connect() as conn:
        conn.executemany(
            "INSERT INTO instruments(symbol, name, sector, industry, country, market_cap, "
            "price, volume, change_pct, kind, updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?) "
            "ON CONFLICT(symbol) DO UPDATE SET "
            "name=CASE WHEN excluded.name<>'' THEN excluded.name ELSE instruments.name END, "
            "sector=CASE WHEN excluded.sector<>'' THEN excluded.sector ELSE instruments.sector END, "
            "industry=CASE WHEN excluded.industry<>'' THEN excluded.industry ELSE instruments.industry END, "
            "country=CASE WHEN excluded.country<>'' THEN excluded.country ELSE instruments.country END, "
            "market_cap=COALESCE(excluded.market_cap, instruments.market_cap), "
            "price=COALESCE(excluded.price, instruments.price), "
            "volume=COALESCE(excluded.volume, instruments.volume), "
            "change_pct=COALESCE(excluded.change_pct, instruments.change_pct), "
            "kind=CASE WHEN excluded.kind<>'' THEN excluded.kind ELSE instruments.kind END, "
            "updated_at=excluded.updated_at",
            payload,
        )


def get_instrument(symbol: str) -> dict[str, Any] | None:
    with _connect() as conn:
        row = conn.execute("SELECT * FROM instruments WHERE symbol = ?", (symbol,)).fetchone()
    return dict(row) if row else None


def set_universe(key: str, symbols: Iterable[str]) -> None:
    stamp = now_ny().isoformat(timespec="seconds")
    rows = [(key, symbol, stamp) for symbol in symbols]
    with _write_lock, _connect() as conn:
        conn.execute("DELETE FROM universes WHERE key = ?", (key,))
        conn.executemany(
            "INSERT OR REPLACE INTO universes(key, symbol, updated_at) VALUES(?,?,?)", rows
        )


def get_universe(key: str) -> list[str]:
    with _connect() as conn:
        rows = conn.execute(
            "SELECT symbol FROM universes WHERE key = ? ORDER BY symbol", (key,)
        ).fetchall()
    return [row["symbol"] for row in rows]


def universe_age(key: str) -> float | None:
    """Horas desde la última actualización del universo."""
    with _connect() as conn:
        row = conn.execute(
            "SELECT updated_at FROM universes WHERE key = ? LIMIT 1", (key,)
        ).fetchone()
    if not row:
        return None
    try:
        stamp = dt.datetime.fromisoformat(row["updated_at"])
    except (TypeError, ValueError):
        return None
    if stamp.tzinfo is None:
        stamp = stamp.replace(tzinfo=_NY)
    return (now_ny() - stamp).total_seconds() / 3600.0


def instruments_for(symbols: list[str]) -> dict[str, dict[str, Any]]:
    if not symbols:
        return {}
    out: dict[str, dict[str, Any]] = {}
    chunk = 500
    with _connect() as conn:
        for i in range(0, len(symbols), chunk):
            part = symbols[i : i + chunk]
            marks = ",".join("?" for _ in part)
            rows = conn.execute(
                f"SELECT * FROM instruments WHERE symbol IN ({marks})", part
            ).fetchall()
            for row in rows:
                out[row["symbol"]] = dict(row)
    return out


def search_instruments(query: str, limit: int = 12) -> list[dict[str, Any]]:
    term = f"%{query.strip().upper()}%"
    with _connect() as conn:
        rows = conn.execute(
            "SELECT symbol, name, sector, market_cap, price FROM instruments "
            "WHERE symbol LIKE ? OR UPPER(name) LIKE ? "
            "ORDER BY CASE WHEN symbol LIKE ? THEN 0 ELSE 1 END, market_cap DESC "
            "LIMIT ?",
            (term, term, f"{query.strip().upper()}%", limit),
        ).fetchall()
    return [dict(row) for row in rows]


# --------------------------------------------------------------------------- #
# Ejecuciones del screener
# --------------------------------------------------------------------------- #
def save_screen_run(run: dict[str, Any]) -> None:
    with _write_lock, _connect() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO screen_runs(id, universe, params, status, created_at, "
            "finished_at, total, done, errors, payload, regime) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
            (
                run["id"],
                run.get("universe"),
                json.dumps(run.get("params") or {}),
                run.get("status"),
                run.get("created_at"),
                run.get("finished_at"),
                run.get("total"),
                run.get("done"),
                run.get("errors"),
                json.dumps(run.get("rows") or []),
                json.dumps(run.get("regime") or {}),
            ),
        )
        # Conserva el último resultado completado de cada universo y, además,
        # los más recientes. Así un escaneo manual no expulsa el diario.
        rows = conn.execute(
            "SELECT id, universe, status FROM screen_runs ORDER BY created_at DESC"
        ).fetchall()
        keep = set()
        latest_by_universe = set()
        for row in rows:
            if row["status"] == "done" and row["universe"] not in latest_by_universe:
                latest_by_universe.add(row["universe"])
                keep.add(row["id"])
        for row in rows:
            if len(keep) >= 12:
                break
            keep.add(row["id"])
        if keep:
            placeholders = ",".join("?" for _ in keep)
            conn.execute(
                f"DELETE FROM screen_runs WHERE id NOT IN ({placeholders})",
                tuple(keep),
            )


def mark_screen_run_interrupted(run_id: str, finished_at: str) -> None:
    with _write_lock, _connect() as conn:
        conn.execute(
            "UPDATE screen_runs SET status = 'interrupted', finished_at = ? "
            "WHERE id = ? AND status = 'running'",
            (finished_at, run_id),
        )


def load_screen_runs() -> list[dict[str, Any]]:
    with _connect() as conn:
        rows = conn.execute(
            "SELECT * FROM screen_runs ORDER BY created_at DESC LIMIT 20"
        ).fetchall()
    result = []
    for row in rows:
        item = dict(row)
        item["params"] = json.loads(item.get("params") or "{}")
        item["rows"] = json.loads(item.pop("payload") or "[]")
        item["regime"] = json.loads(item.pop("regime") or "{}")
        result.append(item)
    return result


def get_screen_run(run_id: str) -> dict[str, Any] | None:
    with _connect() as conn:
        row = conn.execute("SELECT * FROM screen_runs WHERE id = ?", (run_id,)).fetchone()
    if not row:
        return None
    item = dict(row)
    item["params"] = json.loads(item.get("params") or "{}")
    item["rows"] = json.loads(item.pop("payload") or "[]")
    item["regime"] = json.loads(item.pop("regime") or "{}")
    return item


def save_sec_series(symbol: str, payload: Any) -> None:
    with _write_lock, _connect() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO sec_series(symbol, payload, fetched_at) VALUES(?,?,?)",
            (symbol, json.dumps(payload, ensure_ascii=False), now_ny().isoformat(timespec="seconds")),
        )


def get_sec_series(symbol: str) -> tuple[Any | None, float | None]:
    with _connect() as conn:
        row = conn.execute(
            "SELECT payload, fetched_at FROM sec_series WHERE symbol = ?", (symbol,)
        ).fetchone()
    if not row:
        return None, None
    try:
        payload = json.loads(row["payload"])
    except json.JSONDecodeError:
        return None, None
    try:
        stamp = dt.datetime.fromisoformat(row["fetched_at"])
        if stamp.tzinfo is None:
            stamp = stamp.replace(tzinfo=_NY)
        age = (now_ny() - stamp).total_seconds() / 3600.0
    except (TypeError, ValueError):
        age = None
    return payload, age


def save_news(symbol: str, payload: dict[str, Any]) -> None:
    with _write_lock, _connect() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO news_cache(symbol, payload, fetched_at) VALUES(?,?,?)",
            (symbol, json.dumps(payload, ensure_ascii=False), now_ny().isoformat(timespec="seconds")),
        )


def get_news(symbol: str) -> tuple[dict[str, Any] | None, float | None]:
    with _connect() as conn:
        row = conn.execute(
            "SELECT payload, fetched_at FROM news_cache WHERE symbol = ?", (symbol,)
        ).fetchone()
    if not row:
        return None, None
    try:
        payload = json.loads(row["payload"])
    except json.JSONDecodeError:
        return None, None
    try:
        stamp = dt.datetime.fromisoformat(row["fetched_at"])
        if stamp.tzinfo is None:
            stamp = stamp.replace(tzinfo=_NY)
        age = (now_ny() - stamp).total_seconds() / 60.0
    except (TypeError, ValueError):
        age = None
    return payload, age


def get_cached_score(symbol: str) -> dict[str, Any] | None:
    with _connect() as conn:
        row = conn.execute(
            "SELECT as_of, payload, updated_at FROM score_cache WHERE symbol = ?", (symbol,)
        ).fetchone()
    if not row:
        return None
    try:
        payload = json.loads(row["payload"])
    except json.JSONDecodeError:
        return None
    age = None
    try:
        stamp = dt.datetime.fromisoformat(row["updated_at"])
        if stamp.tzinfo is None:
            stamp = stamp.replace(tzinfo=_NY)
        age = (now_ny() - stamp).total_seconds() / 3600.0
    except (TypeError, ValueError):
        pass
    return {"as_of": row["as_of"], "row": payload, "age_hours": age}


def get_cached_scores(symbols: list[str]) -> dict[str, dict[str, Any]]:
    """Devuelve la última fila cacheada por símbolo (consulta por lotes)."""
    unique = [str(s).upper() for s in dict.fromkeys(symbols) if s]
    if not unique:
        return {}
    out: dict[str, dict[str, Any]] = {}
    with _connect() as conn:
        for start in range(0, len(unique), 400):
            chunk = unique[start : start + 400]
            placeholders = ",".join("?" for _ in chunk)
            rows = conn.execute(
                f"SELECT symbol, as_of, payload FROM score_cache WHERE symbol IN ({placeholders})",
                chunk,
            ).fetchall()
            for row in rows:
                try:
                    payload = json.loads(row["payload"])
                except json.JSONDecodeError:
                    continue
                if not isinstance(payload, dict):
                    continue
                payload.setdefault("as_of", row["as_of"])
                out[row["symbol"].upper()] = payload
    return out


def save_cached_score(symbol: str, as_of: str, row: dict[str, Any]) -> None:
    with _write_lock, _connect() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO score_cache(symbol, as_of, payload, updated_at) VALUES(?,?,?,?)",
            (symbol, as_of, json.dumps(row, ensure_ascii=False), now_ny().isoformat(timespec="seconds")),
        )


def clear_score_cache() -> None:
    with _write_lock, _connect() as conn:
        conn.execute("DELETE FROM score_cache")


def save_fundamentals(symbol: str, payload: dict[str, Any]) -> None:
    with _write_lock, _connect() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO fundamentals(symbol, payload, fetched_at) VALUES(?,?,?)",
            (symbol, json.dumps(payload, ensure_ascii=False), now_ny().isoformat(timespec="seconds")),
        )


def get_fundamentals(symbol: str) -> dict[str, Any] | None:
    with _connect() as conn:
        row = conn.execute(
            "SELECT payload, fetched_at FROM fundamentals WHERE symbol = ?", (symbol,)
        ).fetchone()
    if not row:
        return None
    try:
        payload = json.loads(row["payload"])
    except json.JSONDecodeError:
        return None
    payload["cached_at"] = row["fetched_at"]
    return payload


def get_fundamentals_fields(symbols: list[str]) -> dict[str, dict[str, Any]]:
    """Campos de contexto fundamental por símbolo, sin cargar el payload completo."""
    unique = [str(s).upper() for s in dict.fromkeys(symbols) if s]
    if not unique:
        return {}
    out: dict[str, dict[str, Any]] = {}
    with _connect() as conn:
        for start in range(0, len(unique), 400):
            chunk = unique[start : start + 400]
            placeholders = ",".join("?" for _ in chunk)
            rows = conn.execute(
                f"""
                SELECT symbol, fetched_at,
                       json_extract(payload, '$.quality.score') AS quality,
                       json_extract(payload, '$.quality.label') AS quality_label,
                       json_extract(payload, '$.annual.revenue_growth') AS revenue_growth,
                       json_extract(payload, '$.surprises[0].surprise_pct') AS eps_surprise,
                       json_extract(payload, '$.next_earnings.days') AS days_to_earnings,
                       json_extract(payload, '$.next_earnings.date') AS next_earnings,
                       json_extract(payload, '$.analyst.upside') AS analyst_upside,
                       json_extract(payload, '$.company.pe') AS pe
                FROM fundamentals
                WHERE symbol IN ({placeholders})
                  AND json_valid(payload)
                  AND json_type(payload, '$.quarters') = 'array'
                  AND json_array_length(payload, '$.quarters') > 0
                """,
                chunk,
            ).fetchall()
            for row in rows:
                out[row["symbol"].upper()] = dict(row)
    return out


def fundamentals_age(symbol: str) -> float | None:
    with _connect() as conn:
        row = conn.execute(
            "SELECT fetched_at FROM fundamentals WHERE symbol = ?", (symbol,)
        ).fetchone()
    if not row:
        return None
    try:
        stamp = dt.datetime.fromisoformat(row["fetched_at"])
    except (TypeError, ValueError):
        return None
    if stamp.tzinfo is None:
        stamp = stamp.replace(tzinfo=_NY)
    return (now_ny() - stamp).total_seconds() / 3600.0


# --------------------------------------------------------------------------- #
# Alertas y cartera
# --------------------------------------------------------------------------- #
def add_alert(symbol: str, kind: str, threshold: float | None, label: str | None) -> int:
    with _write_lock, _connect() as conn:
        cur = conn.execute(
            "INSERT INTO alerts(symbol, kind, threshold, label, active, created_at) "
            "VALUES(?,?,?,?,1,?)",
            (symbol, kind, threshold, label, now_ny().isoformat(timespec="seconds")),
        )
        return int(cur.lastrowid)


def list_alerts() -> list[dict[str, Any]]:
    with _connect() as conn:
        rows = conn.execute("SELECT * FROM alerts ORDER BY id DESC").fetchall()
    return [dict(row) for row in rows]


def has_alert(symbol: str, kind: str, threshold: float | None) -> bool:
    with _connect() as conn:
        row = conn.execute(
            "SELECT 1 FROM alerts WHERE symbol = ? AND kind = ? AND IFNULL(threshold, -1) = ?"
            " AND active = 1 LIMIT 1",
            (symbol, kind, threshold if threshold is not None else -1),
        ).fetchone()
    return row is not None


def delete_alert(alert_id: int) -> None:
    with _write_lock, _connect() as conn:
        conn.execute("DELETE FROM alerts WHERE id = ?", (alert_id,))


def set_alert_active(alert_id: int, active: bool) -> None:
    with _write_lock, _connect() as conn:
        conn.execute("UPDATE alerts SET active = ? WHERE id = ?", (1 if active else 0, alert_id))


def mark_alert_triggered(alert_id: int) -> None:
    with _write_lock, _connect() as conn:
        conn.execute(
            "UPDATE alerts SET last_triggered = ? WHERE id = ?",
            (now_ny().isoformat(timespec="seconds"), alert_id),
        )


def add_alert_event(alert_id: int, symbol: str, kind: str, message: str) -> None:
    with _write_lock, _connect() as conn:
        conn.execute(
            "INSERT INTO alert_events(alert_id, symbol, kind, message, created_at, read) "
            "VALUES(?,?,?,?,?,0)",
            (alert_id, symbol, kind, message, now_ny().isoformat(timespec="seconds")),
        )


def list_alert_events(limit: int = 50, unread_only: bool = False) -> list[dict[str, Any]]:
    sql = "SELECT * FROM alert_events"
    if unread_only:
        sql += " WHERE read = 0"
    sql += " ORDER BY id DESC LIMIT ?"
    with _connect() as conn:
        rows = conn.execute(sql, (limit,)).fetchall()
    return [dict(row) for row in rows]


def mark_alert_events_read() -> None:
    with _write_lock, _connect() as conn:
        conn.execute("UPDATE alert_events SET read = 1 WHERE read = 0")


def add_position(
    symbol: str,
    shares: float,
    entry_price: float,
    entry_date: str | None,
    stop: float | None,
    target: float | None,
    note: str | None,
) -> int:
    with _write_lock, _connect() as conn:
        cur = conn.execute(
            "INSERT INTO positions(symbol, shares, entry_price, entry_date, stop, target, note, created_at) "
            "VALUES(?,?,?,?,?,?,?,?)",
            (
                symbol,
                shares,
                entry_price,
                entry_date or now_ny().date().isoformat(),
                stop,
                target,
                note or "",
                now_ny().isoformat(timespec="seconds"),
            ),
        )
        return int(cur.lastrowid)


def list_positions() -> list[dict[str, Any]]:
    with _connect() as conn:
        rows = conn.execute("SELECT * FROM positions ORDER BY id DESC").fetchall()
    return [dict(row) for row in rows]


def delete_position(position_id: int) -> None:
    with _write_lock, _connect() as conn:
        conn.execute("DELETE FROM positions WHERE id = ?", (position_id,))


def clear_failed_fetches() -> int:
    with _write_lock, _connect() as conn:
        cur = conn.execute("DELETE FROM fetch_log WHERE rows IS NULL OR rows = 0")
        return cur.rowcount


def data_quality() -> dict[str, Any]:
    close = last_market_close().isoformat(timespec="seconds")
    with _connect() as conn:
        failed = [
            row["symbol"]
            for row in conn.execute(
                "SELECT symbol FROM fetch_log WHERE rows IS NULL OR rows = 0 ORDER BY symbol LIMIT 50"
            ).fetchall()
        ]
        failed_count = conn.execute(
            "SELECT COUNT(*) AS n FROM fetch_log WHERE rows IS NULL OR rows = 0"
        ).fetchone()["n"]
        stale_count = conn.execute(
            "SELECT COUNT(*) AS n FROM fetch_log WHERE rows > 0 AND datetime(fetched_at) < datetime(?)",
            (close,),
        ).fetchone()["n"]
    return {"failed": failed, "failed_count": failed_count, "stale_count": stale_count}


def daily_marker() -> str | None:
    try:
        return json.loads((config.DATA_DIR / "last_daily.json").read_text()).get("at")
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        return None


def backup_bytes() -> bytes:
    import os
    import tempfile

    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as handle:
        path = handle.name
    source = sqlite3.connect(config.DB_PATH)
    target = sqlite3.connect(path)
    try:
        source.backup(target)
    finally:
        target.close()
        source.close()
    try:
        return Path(path).read_bytes()
    finally:
        os.unlink(path)


def clear_alert_events() -> None:
    with _write_lock, _connect() as conn:
        conn.execute("DELETE FROM alert_events")


def export_user_data() -> dict[str, Any]:
    return {
        "version": 1,
        "exported_at": now_ny().isoformat(timespec="seconds"),
        "positions": list_positions(),
        "alerts": list_alerts(),
    }


def import_user_data(payload: dict[str, Any]) -> dict[str, int]:
    added_positions = 0
    added_alerts = 0
    for item in payload.get("positions") or []:
        try:
            symbol = str(item.get("symbol") or "").strip().upper()
            shares = float(item.get("shares") or 0)
            entry = float(item.get("entry_price") or 0)
            stop = float(item["stop"]) if item.get("stop") else None
            target = float(item["target"]) if item.get("target") else None
            if (
                not symbol
                or not math.isfinite(shares)
                or not math.isfinite(entry)
                or shares <= 0
                or entry <= 0
            ):
                continue
            if stop is not None and (not math.isfinite(stop) or stop <= 0):
                continue
            if target is not None and (not math.isfinite(target) or target <= 0):
                continue
            add_position(
                symbol,
                shares,
                entry,
                item.get("entry_date"),
                stop,
                target,
                item.get("note") or "",
            )
            added_positions += 1
        except (TypeError, ValueError):
            continue
    for item in payload.get("alerts") or []:
        try:
            symbol = str(item.get("symbol") or "").strip().upper()
            kind = str(item.get("kind") or "")
            threshold = float(item["threshold"]) if item.get("threshold") is not None else None
            label = item.get("label")
            if not symbol or not kind:
                continue
            if threshold is not None and (not math.isfinite(threshold) or threshold <= 0):
                continue
            if has_alert(symbol, kind, threshold):
                continue
            add_alert(symbol, kind, threshold, label)
            added_alerts += 1
        except (TypeError, ValueError):
            continue
    return {"positions": added_positions, "alerts": added_alerts}


def stats() -> dict[str, Any]:
    with _connect() as conn:
        symbols = conn.execute("SELECT COUNT(DISTINCT symbol) AS n FROM prices").fetchone()["n"]
        bars = conn.execute("SELECT COUNT(*) AS n FROM prices").fetchone()["n"]
        instruments = conn.execute("SELECT COUNT(*) AS n FROM instruments").fetchone()["n"]
    size_mb = config.DB_PATH.stat().st_size / 1e6 if config.DB_PATH.exists() else 0.0
    return {
        "cached_symbols": symbols,
        "bars": bars,
        "instruments": instruments,
        "db_mb": round(size_mb, 1),
    }


def prune(years: int = 4) -> int:
    cutoff = (now_ny().date() - dt.timedelta(days=365 * years)).isoformat()
    with _write_lock, _connect() as conn:
        cur = conn.execute("DELETE FROM prices WHERE date < ?", (cutoff,))
        return cur.rowcount
