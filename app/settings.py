from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any


def settings_dir() -> Path:
    base = os.environ.get("APPDATA") or str(Path.home() / "AppData" / "Roaming")
    path = Path(base) / "XrayWifiShare"
    path.mkdir(parents=True, exist_ok=True)
    return path


SETTINGS_FILE = settings_dir() / "settings.json"

DEFAULTS = {
    "ssid": "VPN-Share",
    "password": "",
    "link": "",
    "adapter": "",
    "show_password": False,
}


def load_settings() -> dict[str, Any]:
    data = dict(DEFAULTS)
    if SETTINGS_FILE.exists():
        try:
            loaded = json.loads(SETTINGS_FILE.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                data.update({k: loaded[k] for k in DEFAULTS if k in loaded})
        except Exception:
            pass
    return data


def save_settings(values: dict[str, Any]) -> None:
    current = load_settings()
    for key in DEFAULTS:
        if key in values:
            current[key] = values[key]
    SETTINGS_FILE.write_text(
        json.dumps(current, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
