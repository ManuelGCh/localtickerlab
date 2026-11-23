"""Servidor HTTP local: API JSON + dashboard estático."""
from __future__ import annotations

import gzip
import json
import mimetypes
import socket
import sys
import traceback
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, unquote, urlparse

from . import config
from .api import Api, ApiError, RawResponse

ROUTES = {
    "/api/status": "status",
    "/api/universes": "universes_list",
    "/api/search": "search",
    "/api/analyze": "analyze",
    "/api/quote": "analyze",
    "/api/screen/start": "screen_start",
    "/api/screen/status": "screen_status",
    "/api/screen/all": "screen_all",
    "/api/screen/find": "screen_find",
    "/api/screen/latest": "screen_latest",
    "/api/screen/cancel": "screen_cancel",
    "/api/screen/export": "screen_export",
    "/api/fundamentals": "fundamentals",
    "/api/news": "stock_news",
    "/api/scan": "scan_status",
    "/api/system": "system",
    "/api/system/retry": "system_retry",
    "/api/backup": "backup",
    "/api/user-data/export": "user_export",
    "/api/watchlist": "watchlist_get",
    "/api/watchlist/add": "watchlist_add",
    "/api/watchlist/remove": "watchlist_remove",
    "/api/watchlist/replace": "watchlist_replace",
    "/api/watchlist/clear": "watchlist_clear",
    "/api/views": "views_list",
    "/api/views/save": "views_save",
    "/api/views/delete": "views_delete",
    "/api/alerts": "alerts_list",
    "/api/alerts/add": "alerts_add",
    "/api/alerts/delete": "alerts_delete",
    "/api/alerts/toggle": "alerts_toggle",
    "/api/alerts/check": "alerts_check",
    "/api/alerts/read": "alerts_read",
    "/api/portfolio": "portfolio_get",
    "/api/portfolio/add": "portfolio_add",
    "/api/portfolio/update": "portfolio_update",
    "/api/portfolio/delete": "portfolio_delete",
    "/api/fundamentals/enrich": "fundamentals_enrich",
    "/api/backtest/start": "backtest_start",
    "/api/backtest/status": "backtest_status",
    "/api/backtest/latest": "backtest_latest",
    "/api/backtest/cancel": "backtest_cancel",
    "/api/backtest/calibrate": "backtest_calibrate",
}

mimetypes.add_type("application/javascript", ".js")
mimetypes.add_type("text/css", ".css")
mimetypes.add_type("image/svg+xml", ".svg")


class Handler(BaseHTTPRequestHandler):
    server_version = "StockLab/1.0"
    api: Api

    # Silencia el log ruidoso de cada petición.
    def log_message(self, fmt: str, *args: Any) -> None:  # noqa: A003
        if self.path.startswith("/api/"):
            return
        sys.stderr.write("[web] %s\n" % (fmt % args))

    def do_GET(self) -> None:  # noqa: N802
        self._handle(head=False)

    def do_HEAD(self) -> None:  # noqa: N802
        self._handle(head=True)

    def do_POST(self) -> None:  # noqa: N802
        self._handle(head=False)

    # ------------------------------------------------------------------ #
    def _handle(self, head: bool = False) -> None:
        parsed = urlparse(self.path)
        path = parsed.path.rstrip("/") or "/"

        if self.command == "POST" and path == "/api/user-data/import":
            try:
                length = int(self.headers.get("Content-Length") or 0)
                if length <= 0 or length > 2_000_000:
                    raise ApiError("cuerpo de importación no válido")
                request = json.loads(self.rfile.read(length))
                response = self.api.user_import(request)
                status = 200
            except ApiError as exc:
                response, status = {"error": str(exc)}, exc.status
            except (json.JSONDecodeError, ValueError) as exc:
                response, status = {"error": f"JSON no válido: {exc}"}, 400
            self._send_json(response, status, head=head)
            return

        if path in ROUTES:
            params = parse_qs(parsed.query)
            try:
                method = getattr(self.api, ROUTES[path])
                payload = method(params)
                status = 200
            except ApiError as exc:
                payload = {"error": str(exc)}
                status = exc.status
            except Exception as exc:  # noqa: BLE001
                traceback.print_exc()
                payload = {"error": f"error interno: {exc}"}
                status = 500
            if isinstance(payload, RawResponse):
                self._send_bytes(payload, head=head)
            else:
                self._send_json(payload, status, head=head)
            return

        if path.startswith("/api/"):
            self._send_json({"error": "ruta no encontrada"}, 404, head=head)
            return

        self._serve_static(path, head=head)

    def _serve_static(self, path: str, head: bool = False) -> None:
        relative = unquote(path).lstrip("/")
        if not relative or relative == "/":
            relative = "index.html"
        target = (config.WEB_DIR / relative).resolve()
        try:
            target.relative_to(config.WEB_DIR.resolve())
        except ValueError:
            self._send_json({"error": "ruta no permitida"}, 403, head=head)
            return
        if not target.is_file():
            self._send_json({"error": "archivo no encontrado"}, 404, head=head)
            return

        content_type, _ = mimetypes.guess_type(target.name)
        data = target.read_bytes()
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", content_type or "application/octet-stream")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        if not head:
            self.wfile.write(data)

    def _send_bytes(self, payload: RawResponse, head: bool = False) -> None:
        try:
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", payload.content_type)
            self.send_header("Content-Length", str(len(payload.body)))
            if payload.filename:
                self.send_header(
                    "Content-Disposition", f'attachment; filename="{payload.filename}"'
                )
            self.end_headers()
            if not head:
                self.wfile.write(payload.body)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def _send_json(self, payload: Any, status: int = 200, head: bool = False) -> None:
        body = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode()
        use_gzip = (
            len(body) > 1024
            and "gzip" in (self.headers.get("Accept-Encoding") or "").lower()
        )
        if use_gzip:
            body = gzip.compress(body, compresslevel=6)
        try:
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            if use_gzip:
                self.send_header("Content-Encoding", "gzip")
                self.send_header("Vary", "Accept-Encoding")
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            if not head:
                self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            pass


def create_server(host: str, port: int, api: Api) -> ThreadingHTTPServer:
    handler = type("BoundHandler", (Handler,), {"api": api})
    ThreadingHTTPServer.daemon_threads = True
    ThreadingHTTPServer.allow_reuse_address = True
    return ThreadingHTTPServer((host, port), handler)


def find_port(host: str, start: int, attempts: int = 12) -> int:
    for offset in range(attempts):
        port = start + offset
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                sock.bind((host, port))
                return port
            except OSError:
                continue
    raise RuntimeError(f"no hay puertos libres desde {start}")
