from __future__ import annotations

import re
from typing import Iterable

try:
    import win32com.client  # type: ignore
except ImportError:  # pragma: no cover
    win32com = None  # type: ignore


ICSS_PUBLIC = 0
ICSS_PRIVATE = 1


def _require_win32() -> None:
    if win32com is None:
        raise RuntimeError("Нужен пакет pywin32 (pip install pywin32)")


def _connections():
    _require_win32()
    hnet = win32com.client.Dispatch("HNetCfg.HNetShare")
    enum = hnet.EnumEveryConnection
    for conn in enum:
        props = hnet.NetConnectionProps(conn)
        cfg = hnet.INetSharingConfigurationForINetConnection(conn)
        yield hnet, conn, props, cfg


def disable_all_sharing() -> None:
    for _hnet, _conn, _props, cfg in _connections():
        try:
            if cfg.SharingEnabled:
                cfg.DisableSharing()
        except Exception:
            pass


def list_shareable_names() -> list[str]:
    names: list[str] = []
    for _hnet, _conn, props, _cfg in _connections():
        try:
            names.append(str(props.Name))
        except Exception:
            continue
    return names


def enable_sharing(public_name: str, private_name: str) -> None:
    """Share public_name (VPN TUN) to private_name (hotspot)."""
    _require_win32()
    disable_all_sharing()

    public_cfg = None
    private_cfg = None
    seen: list[str] = []

    for _hnet, _conn, props, cfg in _connections():
        name = str(props.Name)
        seen.append(name)
        if name == public_name:
            public_cfg = cfg
        elif name == private_name:
            private_cfg = cfg

    if public_cfg is None or private_cfg is None:
        raise RuntimeError(
            "Не найдены адаптеры для ICS.\n"
            f"Искал public='{public_name}', private='{private_name}'.\n"
            f"Доступны: {', '.join(seen)}"
        )

    public_cfg.EnableSharing(ICSS_PUBLIC)
    private_cfg.EnableSharing(ICSS_PRIVATE)


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
