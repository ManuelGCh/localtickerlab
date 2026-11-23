"""Cliente HTTP mínimo con reintentos, límite de concurrencia y backoff."""
from __future__ import annotations

import gzip
import json
import random
import threading
import time
import urllib.error
import urllib.request
from typing import Any

from . import config


class FetchError(RuntimeError):
    """Error recuperable al descargar datos de una fuente externa."""

    def __init__(self, message: str, status: int | None = None):
        super().__init__(message)
        self.status = status


class NoDataError(FetchError):
    """La fuente respondió correctamente pero el ticker no tiene histórico."""


_semaphore = threading.Semaphore(config.MAX_WORKERS)
_local = threading.local()


def _headers(extra: dict[str, str] | None = None) -> dict[str, str]:
    base = {
        "User-Agent": config.USER_AGENT,
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "en-US,en;q=0.9",
        "Accept-Encoding": "gzip, deflate",
        "Connection": "close",
    }
    if extra:
        base.update(extra)
    return base


def get_bytes(
    url: str,
    *,
    headers: dict[str, str] | None = None,
    timeout: int = config.REQUEST_TIMEOUT,
    retries: int = config.REQUEST_RETRIES,
) -> bytes:
    """Descarga una URL y devuelve el cuerpo descomprimido."""
    last_error: Exception | None = None
    for attempt in range(retries):
        try:
            with _semaphore:
                req = urllib.request.Request(url, headers=_headers(headers))
                with urllib.request.urlopen(req, timeout=timeout) as resp:
                    raw = resp.read()
                    encoding = (resp.headers.get("Content-Encoding") or "").lower()
            if encoding == "gzip":
                raw = gzip.decompress(raw)
            if not raw:
                raise FetchError("respuesta vacía")
            return raw
        except urllib.error.HTTPError as exc:
            last_error = exc
            if exc.code in (400, 401, 403, 404):
                raise FetchError(f"HTTP {exc.code}", exc.code) from exc
            # 429 / 5xx: backoff progresivo con jitter.
            time.sleep(0.8 * (2**attempt) + random.uniform(0, 0.4))
        except (urllib.error.URLError, TimeoutError, OSError, FetchError) as exc:
            last_error = exc
            time.sleep(0.6 * (2**attempt) + random.uniform(0, 0.3))
    raise FetchError(f"no se pudo descargar {url}: {last_error}")


def get_json(url: str, **kwargs: Any) -> Any:
    raw = get_bytes(url, **kwargs)
    try:
        return json.loads(raw)
    except json.JSONDecodeError as exc:
        raise FetchError(f"respuesta no es JSON válido: {exc}") from exc


def quote_symbol(symbol: str) -> str:
    """Codifica un ticker para usarlo en una URL."""
    return urllib.request.quote(symbol.strip().upper(), safe="")
