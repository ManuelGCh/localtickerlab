"""Configuración global de StockLab."""
from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"
WEB_DIR = ROOT / "web"
DB_PATH = DATA_DIR / "market.db"

HOST = "127.0.0.1"
PORT = 8765

# Ventana de histórico que se descarga y se guarda (días naturales).
HISTORY_DAYS = 3 * 365

# Nº de descargas simultáneas contra las fuentes públicas.
MAX_WORKERS = 16
REQUEST_TIMEOUT = 30
REQUEST_RETRIES = 3

# Caché: si los datos se descargaron antes de esta hora (zona NY) del día
# en curso, se consideran obsoletos y se vuelven a pedir.
REFRESH_AFTER_HOUR_NY = 18

# Series que se envían al frontend para dibujar los gráficos.
CHART_POINTS = 520

USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)

# Universos disponibles en la interfaz.
UNIVERSES = {
    "ndx": "NASDAQ-100",
    "all": "NASDAQ Composite",
    "watchlist": "Mi watchlist",
}

# Filtros por defecto del screener.
DEFAULT_FILTERS = {
    "min_price": 3.0,
    "min_market_cap": 300_000_000,
    "min_dollar_vol": 0,
    "exclude_funds": True,
    "max_symbols": 0,  # 0 = sin límite
}

# Peso de cada bloque en la puntuación final (0-100).
_DEFAULT_WEIGHTS = {
    "trend": 0.30,
    "momentum": 0.28,
    "risk": 0.17,
    "rsi": 0.10,
    "liquidity": 0.15,
}

SCORE_WEIGHTS = dict(_DEFAULT_WEIGHTS)

DISCLAIMER = (
    "Herramienta educativa. Los datos proceden de fuentes públicas y pueden "
    "tener errores o retrasos. No es asesoramiento financiero."
)

# Umbrales de la señal de compra/venta (puntuación compuesta).
ACTION_THRESHOLDS = [
    (78.0, "COMPRA FUERTE", 4),
    (63.0, "COMPRA", 3),
    (45.0, "NEUTRAL", 2),
    (32.0, "VENTA", 1),
    (0.0, "VENTA FUERTE", 0),
]
