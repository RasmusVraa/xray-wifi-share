from __future__ import annotations

import sys
from pathlib import Path


def app_root() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent.parent


ROOT = app_root()
RUNTIME = ROOT / "runtime"
BIN = RUNTIME / "bin"
WORK = RUNTIME / "work"

XRAY_EXE = BIN / "xray.exe"
TUN2SOCKS_EXE = BIN / "tun2socks.exe"
WINTUN_DLL = BIN / "wintun.dll"
XRAY_CONFIG = WORK / "config.json"
JOB_FILE = WORK / "job.json"
STATUS_FILE = WORK / "status.json"
STATE_FILE = WORK / "state.json"
LOG_FILE = WORK / "gui.log"


def ensure_dirs() -> None:
    BIN.mkdir(parents=True, exist_ok=True)
    WORK.mkdir(parents=True, exist_ok=True)
