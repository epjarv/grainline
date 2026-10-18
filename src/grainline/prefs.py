"""Remember the last temperatures and grain settings on this PC."""

from __future__ import annotations

import json
import os
from pathlib import Path


def app_dir() -> Path:
    base = os.environ.get("APPDATA")
    path = Path(base) / "Grainline" if base else Path.home() / ".grainline"
    path.mkdir(parents=True, exist_ok=True)
    return path


def load_prefs() -> dict:
    path = app_dir() / "settings.json"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def save_prefs(data: dict) -> None:
    path = app_dir() / "settings.json"
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")
