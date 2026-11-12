from __future__ import annotations

import ctypes
import sys


def is_admin() -> bool:
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return False


def relaunch_as_admin() -> None:
    if getattr(sys, "frozen", False):
        executable = sys.executable
        params = " ".join(f'"{a}"' if " " in a else a for a in sys.argv[1:])
    else:
        executable = sys.executable
        args = [sys.argv[0], *sys.argv[1:]]
        params = " ".join(f'"{a}"' if " " in a else a for a in args)
    ctypes.windll.shell32.ShellExecuteW(None, "runas", executable, params, None, 1)
