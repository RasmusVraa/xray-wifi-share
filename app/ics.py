from __future__ import annotations

import re
import time
from contextlib import contextmanager
from typing import Iterable, Iterator

from .netutil import run_ps

try:
    import pythoncom  # type: ignore
    import win32com.client  # type: ignore
except ImportError:  # pragma: no cover
    pythoncom = None  # type: ignore
    win32com = None  # type: ignore


ICSS_PUBLIC = 0
ICSS_PRIVATE = 1


def _require_win32() -> None:
    if win32com is None or pythoncom is None:
        raise RuntimeError("Нужен пакет pywin32 (pip install pywin32)")


@contextmanager
def _com_initialized() -> Iterator[None]:
    """ICS COM must be initialized on the calling thread (job runs in a worker)."""
    _require_win32()
    initialized = False
    try:
        pythoncom.CoInitializeEx(pythoncom.COINIT_APARTMENTTHREADED)
        initialized = True
    except Exception as exc:
        raise RuntimeError(f"CoInitialize failed: {exc}") from exc
    try:
        yield
    finally:
        if initialized:
            try:
                pythoncom.CoUninitialize()
            except Exception:
                pass


def _ensure_shared_access() -> None:
    """SharedAccess must be running or EnableSharing returns 0x80040201."""
    ps = r"""
$ErrorActionPreference = 'SilentlyContinue'
$svc = Get-Service SharedAccess -ErrorAction SilentlyContinue
if ($null -eq $svc) { return }
if ($svc.StartType -eq 'Disabled') {
  Set-Service SharedAccess -StartupType Manual -ErrorAction SilentlyContinue
}
if ($svc.Status -ne 'Running') {
  Start-Service SharedAccess -ErrorAction SilentlyContinue
  Start-Sleep -Milliseconds 800
}
# Touch hnetcfg registration (harmless if already registered)
regsvr32 /s hnetcfg.dll | Out-Null
"""
    run_ps(ps)


def _cleanup_wmi_sharing() -> None:
    """Stale WMI ICS entries cause EnableSharing 0x80040201 on Win10/11."""
    ps = r"""
$ErrorActionPreference = 'SilentlyContinue'
Get-CimInstance -Namespace root\Microsoft\HomeNet -ClassName HNet_ConnectionProperties -ErrorAction SilentlyContinue |
  Where-Object { $_.IsIcsPrivate -or $_.IsIcsPublic } |
  ForEach-Object {
    try {
      $_.IsIcsPrivate = $false
      $_.IsIcsPublic = $false
      Set-CimInstance -InputObject $_ -ErrorAction SilentlyContinue
    } catch {}
  }
"""
    run_ps(ps)


def _connections():
    _require_win32()
    hnet = win32com.client.Dispatch("HNetCfg.HNetShare")
    enum = hnet.EnumEveryConnection
    for conn in enum:
        props = hnet.NetConnectionProps(conn)
        cfg = hnet.INetSharingConfigurationForINetConnection(conn)
        yield hnet, conn, props, cfg


def _disable_all_locked() -> None:
    for _hnet, _conn, _props, cfg in _connections():
        try:
            if cfg.SharingEnabled:
                cfg.DisableSharing()
        except Exception:
            pass


def disable_all_sharing() -> None:
    _ensure_shared_access()
    with _com_initialized():
        _disable_all_locked()
    time.sleep(0.6)


def list_shareable_names() -> list[str]:
    names: list[str] = []
    with _com_initialized():
        for _hnet, _conn, props, _cfg in _connections():
            try:
                names.append(str(props.Name))
            except Exception:
                continue
    return names


def current_sharing() -> tuple[str | None, str | None]:
    """Return (public_name, private_name) currently shared, if any."""
    public = None
    private = None
    with _com_initialized():
        for _hnet, _conn, props, cfg in _connections():
            try:
                if not cfg.SharingEnabled:
                    continue
                name = str(props.Name)
                # SharingConnectionType: 0 public, 1 private
                typ = int(getattr(cfg, "SharingConnectionType", -1))
                if typ == ICSS_PUBLIC:
                    public = name
                elif typ == ICSS_PRIVATE:
                    private = name
            except Exception:
                continue
    return public, private


def enable_sharing(public_name: str, private_name: str) -> None:
    """Share public_name (VPN TUN) to private_name (hotspot). Retries around 0x80040201."""
    _ensure_shared_access()

    # Already correct — don't poke ICS (reconnect path)
    cur_pub, cur_priv = current_sharing()
    if cur_pub == public_name and cur_priv == private_name:
        return

    last_exc: Exception | None = None
    for attempt in range(1, 4):
        try:
            _enable_once(public_name, private_name, hard_reset=(attempt > 1))
            # verify
            cur_pub, cur_priv = current_sharing()
            if cur_pub == public_name and cur_priv == private_name:
                return
            # Some Windows builds report type oddly — accept if both enabled names match intent
            if cur_pub and cur_priv:
                if _norm(cur_pub) == _norm(public_name) and _norm(cur_priv) == _norm(private_name):
                    return
            last_exc = RuntimeError(
                f"ICS включился неверно: public={cur_pub!r} private={cur_priv!r}"
            )
        except Exception as exc:
            last_exc = exc
        time.sleep(0.8 * attempt)

    raise RuntimeError(f"ICS EnableSharing failed: {last_exc}")


def _enable_once(public_name: str, private_name: str, hard_reset: bool) -> None:
    if hard_reset:
        _cleanup_wmi_sharing()
        _ensure_shared_access()
        # bounce SharedAccess on stubborn 0x80040201
        run_ps(
            r"""
$ErrorActionPreference='SilentlyContinue'
Restart-Service SharedAccess -Force -ErrorAction SilentlyContinue
Start-Sleep -Seconds 1
"""
        )

    with _com_initialized():
        _disable_all_locked()
        time.sleep(0.8)

        public_cfg = None
        private_cfg = None
        public_conn = None
        private_conn = None
        seen: list[str] = []

        # Re-enumerate after disable (handles stale COM refs)
        items = list(_connections())
        for _hnet, conn, props, cfg in items:
            name = str(props.Name)
            seen.append(name)
            if name == public_name or _norm(name) == _norm(public_name):
                public_cfg = cfg
                public_conn = conn
            if name == private_name or _norm(name) == _norm(private_name):
                private_cfg = cfg
                private_conn = conn
            # star-suffix fallback for private
            if private_cfg is None and _star_suffix(private_name) and _star_suffix(name) == _star_suffix(
                private_name
            ):
                private_cfg = cfg
                private_conn = conn

        if public_cfg is None or private_cfg is None:
            raise RuntimeError(
                "Не найдены адаптеры для ICS.\n"
                f"Искал public='{public_name}', private='{private_name}'.\n"
                f"Доступны: {', '.join(seen)}"
            )

        # Order: private first then public — more reliable on Win11
        try:
            private_cfg.EnableSharing(ICSS_PRIVATE)
            time.sleep(0.35)
            public_cfg.EnableSharing(ICSS_PUBLIC)
        except Exception:
            # fallback: public then private
            time.sleep(0.5)
            _disable_all_locked()
            time.sleep(0.5)
            # refresh configs
            items = list(_connections())
            public_cfg = private_cfg = None
            for _hnet, conn, props, cfg in items:
                name = str(props.Name)
                if name == public_name or _norm(name) == _norm(public_name):
                    public_cfg = cfg
                if name == private_name or _norm(name) == _norm(private_name):
                    private_cfg = cfg
                if private_cfg is None and _star_suffix(private_name) and _star_suffix(name) == _star_suffix(
                    private_name
                ):
                    private_cfg = cfg
            if public_cfg is None or private_cfg is None:
                raise
            public_cfg.EnableSharing(ICSS_PUBLIC)
            time.sleep(0.35)
            private_cfg.EnableSharing(ICSS_PRIVATE)

        _ = (public_conn, private_conn)


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "").strip().lower())


def _star_suffix(name: str) -> str | None:
    m = re.search(r"\*\s*(\d+)\s*$", name or "")
    return m.group(1) if m else None


def find_name(candidates: Iterable[str], available: list[str] | None = None) -> str | None:
    avail = available if available is not None else list_shareable_names()
    lower_map = {_norm(a): a for a in avail}

    for c in candidates:
        if not c:
            continue
        if c in avail:
            return c
        nc = _norm(c)
        if nc in lower_map:
            return lower_map[nc]
        for a in avail:
            na = _norm(a)
            if nc and (nc in na or na in nc):
                return a
        # match "...* 11" even if Cyrillic is mojibake
        suf = _star_suffix(c)
        if suf:
            for a in avail:
                if _star_suffix(a) == suf:
                    return a
    return None


def resolve_public(preferred: Iterable[str] | None = None) -> str:
    avail = list_shareable_names()
    hit = find_name(list(preferred or []) + ["XrayShare"], avail)
    if hit:
        return hit
    for a in avail:
        if "xrayshare" in _norm(a):
            return a
    raise RuntimeError(f"ICS public (XrayShare) не найден. Доступны: {', '.join(avail)}")


def resolve_private(preferred: Iterable[str] | None = None) -> str:
    """Hotspot/ICS private adapter: Local Area Connection* / Подключение по локальной сети*."""
    avail = list_shareable_names()
    hit = find_name(list(preferred or []), avail)
    if hit and _is_hotspot_private(hit):
        return hit

    scored: list[tuple[int, str]] = []
    for a in avail:
        if not _is_hotspot_private(a):
            continue
        score = 0
        n = _norm(a)
        if "*" in a:
            score += 5
        if "локальн" in n or "local area connection" in n:
            score += 5
        if "microsoft" in n or "hosted" in n or "wi-fi direct" in n:
            score += 2
        scored.append((score, a))

    if scored:
        scored.sort(key=lambda x: (-x[0], x[1]))
        return scored[0][1]

    raise RuntimeError(
        "ICS private (точка доступа) не найден.\n"
        f"Доступны: {', '.join(avail)}"
    )


def _is_hotspot_private(name: str) -> bool:
    n = _norm(name)
    if any(x in n for x in ("bluetooth", "radmin", "happ-xray", "xrayshare", "ethernet", "беспровод")):
        # "Беспроводная сеть" is the USB Wi-Fi NIC itself, not the hosted AP vNIC
        if "беспровод" in n or n == "ethernet":
            return False
        if "bluetooth" in n or "radmin" in n or "happ-xray" in n or "xrayshare" in n:
            return False
    if "*" in name:
        return True
    if "локальн" in n or "local area connection" in n:
        return True
    if "hosted" in n or "wi-fi direct" in n:
        return True
    return False
