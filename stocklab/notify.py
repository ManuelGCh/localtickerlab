"""Notificaciones locales (macOS) y opcionales por Telegram."""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import urllib.parse
import urllib.request


def _macos(title: str, message: str) -> bool:
    if sys.platform != "darwin" or not shutil.which("osascript"):
        return False
    def esc(text: str) -> str:
        return text.replace("\\", "\\\\").replace('"', '\\"')

    script = (
        f'display notification "{esc(message[:500])}" '
        f'with title "{esc(title[:120])}"'
    )
    try:
        subprocess.run(
            ["osascript", "-e", script],
            check=False,
            capture_output=True,
            timeout=10,
        )
        return True
    except (OSError, subprocess.SubprocessError):
        return False


def _telegram(title: str, message: str) -> bool:
    token = os.environ.get("STOCKLAB_TELEGRAM_BOT_TOKEN")
    chat = os.environ.get("STOCKLAB_TELEGRAM_CHAT_ID")
    if not token or not chat:
        return False
    payload = urllib.parse.urlencode(
        {"chat_id": chat, "text": f"{title}\n\n{message}"}
    ).encode()
    try:
        with urllib.request.urlopen(
            f"https://api.telegram.org/bot{token}/sendMessage",
            data=payload,
            timeout=15,
        ) as response:
            response.read()
        return True
    except Exception:  # noqa: BLE001 - las notificaciones nunca deben romper el escaneo
        return False


def send(title: str, message: str) -> list[str]:
    """Envía la notificación por los canales disponibles y devuelve los usados."""
    channels = []
    if _macos(title, message):
        channels.append("macos")
    if _telegram(title, message):
        channels.append("telegram")
    return channels


def report_body(summary: dict) -> str:
    lines = [
        f"Analizados: {summary.get('done', 0)} · errores: {summary.get('errors', 0)}",
    ]
    counts = summary.get("counts") or {}
    if counts:
        lines.append(
            "Señales: "
            + ", ".join(f"{key} {value}" for key, value in counts.items())
        )
    if summary.get("alerts"):
        lines.append("Alertas: " + "; ".join(summary["alerts"]))
    if summary.get("top"):
        lines.append("Top:")
        for row in summary["top"]:
            lines.append(
                f"  {row['symbol']} {row['score']:.0f} {row['action']} "
                f"obj {row.get('target_3m') or '—'}"
            )
    return "\n".join(lines)


def write_report(path, summary: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(summary, ensure_ascii=False, indent=2))
