#!/usr/bin/env python3
"""Instala (o desinstala) el escaneo diario con launchd en macOS.

El agente comprueba cada hora si los datos son anteriores al último cierre y,
solo entonces, ejecuta el escaneo. Así no hace falta tener el servidor abierto.
"""
from __future__ import annotations

import argparse
import os
import plistlib
import subprocess
import sys
from pathlib import Path

if sys.version_info < (3, 10):
    print("Este instalador necesita Python 3.10 o superior.")
    raise SystemExit(1)

ROOT = Path(__file__).resolve().parent.parent
LABEL = "com.localtickerlab.daily"
PLIST = Path.home() / "Library" / "LaunchAgents" / f"{LABEL}.plist"


def load_env_file() -> dict[str, str]:
    env: dict[str, str] = {}
    path = ROOT / "data" / "notify.env"
    if not path.exists():
        return env
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        env[key.strip()] = value.strip()
    return env


def install(interval: int, enrich: int) -> int:
    if sys.platform != "darwin":
        print("Este instalador es para macOS. En Linux usa cron:")
        print(
            f"  0 * * * * cd {ROOT} && {sys.executable} run.py --daily-if-needed "
            f"--universe all --enrich {enrich} --quiet"
        )
        return 1

    env = {
        "PATH": "/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin",
        "PYTHONUNBUFFERED": "1",
        **load_env_file(),
    }
    payload = {
        "Label": LABEL,
        "ProgramArguments": [
            sys.executable,
            str(ROOT / "run.py"),
            "--daily-if-needed",
            "--universe",
            "all",
            "--enrich",
            str(enrich),
            "--quiet",
        ],
        "WorkingDirectory": str(ROOT),
        "RunAtLoad": False,
        "StartInterval": interval,
        "StandardOutPath": str(ROOT / "data" / "scan.log"),
        "StandardErrorPath": str(ROOT / "data" / "scan.log"),
        "EnvironmentVariables": env,
    }

    PLIST.parent.mkdir(parents=True, exist_ok=True)
    with PLIST.open("wb") as handle:
        plistlib.dump(payload, handle)

    uid = os.getuid()
    subprocess.run(
        ["launchctl", "bootout", f"gui/{uid}/{LABEL}"],
        check=False,
        capture_output=True,
    )
    result = subprocess.run(
        ["launchctl", "bootstrap", f"gui/{uid}", str(PLIST)],
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        print("No se pudo cargar el agente automáticamente.")
        print(result.stderr.strip())
        print(f"Plist creado en: {PLIST}")
        print(f"Cárgalo manualmente con: launchctl bootstrap gui/{uid} {PLIST}")
        return 1

    print(f"Instalado: {PLIST}")
    print("Comprueba cada hora si hay un cierre nuevo y, si lo hay, escanea una vez.")
    print("Notificaciones: macOS (Centro de notificaciones) y Telegram si defines")
    print(f"  LOCALTICKERLAB_TELEGRAM_BOT_TOKEN y LOCALTICKERLAB_TELEGRAM_CHAT_ID en {ROOT / 'data' / 'notify.env'}")
    print("Registro:", ROOT / "data" / "scan.log")
    print("Desinstalar: python3 scripts/install_schedule.py --uninstall")
    return 0


def uninstall() -> int:
    uid = os.getuid()
    subprocess.run(
        ["launchctl", "bootout", f"gui/{uid}/{LABEL}"],
        check=False,
        capture_output=True,
    )
    try:
        PLIST.unlink()
        print("Agente eliminado:", PLIST)
    except FileNotFoundError:
        print("El agente no estaba instalado.")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Programa el escaneo diario con launchd")
    parser.add_argument("--uninstall", action="store_true")
    parser.add_argument("--interval", type=int, default=3600, help="segundos entre comprobaciones")
    parser.add_argument("--enrich", type=int, default=300, help="valores a enriquecer")
    args = parser.parse_args()
    return uninstall() if args.uninstall else install(args.interval, args.enrich)


if __name__ == "__main__":
    raise SystemExit(main())
