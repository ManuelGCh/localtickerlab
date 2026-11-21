"""Estado compartido del escaneo en segundo plano (archivo JSON)."""
from __future__ import annotations

import datetime as dt
import json

from . import config, store

PATH = config.DATA_DIR / "scan_progress.json"


def write(**fields) -> None:
    current = read()
    current.update(fields)
    current["updated_at"] = store.now_ny().isoformat(timespec="seconds")
    config.DATA_DIR.mkdir(parents=True, exist_ok=True)
    tmp = PATH.with_suffix(".tmp")
    try:
        tmp.write_text(json.dumps(current, ensure_ascii=False), encoding="utf-8")
        tmp.replace(PATH)
    except OSError:
        pass


def read() -> dict:
    try:
        data = json.loads(PATH.read_text())
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        return {}
    if not isinstance(data, dict):
        return {}
    if data.get("running") and data.get("updated_at"):
        try:
            updated = dt.datetime.fromisoformat(data["updated_at"])
            if (store.now_ny() - updated).total_seconds() > 180:
                data["stale"] = True
        except (TypeError, ValueError):
            pass
    return data


def finish(**fields) -> None:
    write(running=False, **fields)
