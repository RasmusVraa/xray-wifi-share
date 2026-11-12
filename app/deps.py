from __future__ import annotations

import io
import json
import shutil
import zipfile
from pathlib import Path
from typing import Callable
from urllib.request import Request, urlopen

from .paths import BIN, TUN2SOCKS_EXE, WINTUN_DLL, XRAY_EXE, ensure_dirs

LogFn = Callable[[str], None]


def _http_get(url: str) -> bytes:
    req = Request(url, headers={"User-Agent": "xray-wifi-share/0.1"})
    with urlopen(req, timeout=120) as resp:
        return resp.read()


def _github_latest_asset(repo: str, name_substr: str) -> str:
    api = f"https://api.github.com/repos/{repo}/releases/latest"
    data = json.loads(_http_get(api).decode("utf-8"))
    for asset in data.get("assets", []):
        name = asset.get("name") or ""
        if name_substr in name:
            return asset["browser_download_url"]
    raise RuntimeError(f"Не найден ассет '{name_substr}' в {repo}")


def _extract_file(zip_bytes: bytes, wanted_names: list[str], dest: Path) -> None:
    with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
        names = zf.namelist()
        for wanted in wanted_names:
            for name in names:
                if name.replace("\\", "/").endswith(wanted):
                    dest.write_bytes(zf.read(name))
                    return
        raise RuntimeError(f"В архиве нет файлов: {wanted_names}")


def ensure_runtime(log: LogFn | None = None) -> None:
    ensure_dirs()
    _log = log or (lambda _m: None)

    if not XRAY_EXE.exists():
        _log("Скачиваю Xray-core...")
        url = _github_latest_asset("XTLS/Xray-core", "windows-64.zip")
        raw = _http_get(url)
        _extract_file(raw, ["xray.exe"], XRAY_EXE)
        # geo files optional
        with zipfile.ZipFile(io.BytesIO(raw)) as zf:
            for geo in ("geoip.dat", "geosite.dat"):
                for name in zf.namelist():
                    if name.endswith(geo):
                        (BIN / geo).write_bytes(zf.read(name))
        _log(f"Xray готов: {XRAY_EXE}")

    if not TUN2SOCKS_EXE.exists():
        _log("Скачиваю tun2socks...")
        url = _github_latest_asset("xjasonlyu/tun2socks", "windows-amd64.zip")
        raw = _http_get(url)
        _extract_file(raw, ["tun2socks.exe", "tun2socks-windows-amd64.exe"], TUN2SOCKS_EXE)
        _log(f"tun2socks готов: {TUN2SOCKS_EXE}")

    if not WINTUN_DLL.exists():
        _log("Скачиваю wintun.dll...")
        # official wintun redistributable
        url = "https://www.wintun.net/builds/wintun-0.14.1.zip"
        raw = _http_get(url)
        with zipfile.ZipFile(io.BytesIO(raw)) as zf:
            candidate = None
            for name in zf.namelist():
                norm = name.replace("\\", "/")
                if norm.endswith("amd64/wintun.dll"):
                    candidate = name
                    break
            if not candidate:
                raise RuntimeError("wintun.dll amd64 не найден в архиве")
            WINTUN_DLL.write_bytes(zf.read(candidate))
        _log(f"wintun готов: {WINTUN_DLL}")

    # tun2socks loads wintun.dll from its directory / PATH
    beside = TUN2SOCKS_EXE.parent / "wintun.dll"
    if WINTUN_DLL.exists() and not beside.exists():
        shutil.copy2(WINTUN_DLL, beside)
