from __future__ import annotations

import hashlib
import os
import uuid
from pathlib import Path

from .settings import settings_dir


def device_hwid() -> str:
    """Stable device id for Remnawave HWID-limited subscriptions."""
    path = settings_dir() / "device_hwid.txt"
    if path.exists():
        value = path.read_text(encoding="utf-8").strip()
        if value:
            return value

    seed = "|".join(
        [
            os.environ.get("COMPUTERNAME") or "",
            os.environ.get("USERNAME") or "",
            str(uuid.getnode()),
            "XrayWifiShare",
        ]
    )
    digest = hashlib.sha256(seed.encode("utf-8")).hexdigest()[:16]
    path.write_text(digest, encoding="utf-8")
    return digest
