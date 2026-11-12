from __future__ import annotations

import json
import socket
import subprocess
from dataclasses import dataclass


VIRTUAL_HINTS = (
    "happ-xray",
    "xrayshare",
    "wintun",
    "wireguard",
    "radmin",
    "vpn",
    "tap-windows",
    "tun",
    "loopback",
    "virtual",
    "hyper-v",
    "vmware",
    "virtualbox",
    "bluetooth",
    "wi-fi direct",
    "hosted",
)


@dataclass
class Adapter:
    name: str
    description: str
    status: str
    mac: str = ""


@dataclass
class Uplink:
    name: str
    ip: str
    gateway: str
    if_index: int


def run_cmd(args: list[str], timeout: int = 60) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        args,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
        creationflags=subprocess.CREATE_NO_WINDOW if hasattr(subprocess, "CREATE_NO_WINDOW") else 0,
    )


def run_ps(script: str, timeout: int = 90) -> subprocess.CompletedProcess[str]:
    # Force UTF-8 so Cyrillic adapter names ("Подключение по локальной сети* 11") survive.
    wrapped = (
        "[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false); "
        "$OutputEncoding = [Console]::OutputEncoding; "
        + script
    )
    return subprocess.run(
        [
            "powershell",
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-Command",
            wrapped,
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
        creationflags=subprocess.CREATE_NO_WINDOW if hasattr(subprocess, "CREATE_NO_WINDOW") else 0,
    )


def list_adapters() -> list[Adapter]:
    ps = r"""
Get-NetAdapter | Select-Object Name, InterfaceDescription, Status, MacAddress |
  ConvertTo-Json -Compress
"""
    r = run_ps(ps)
    if r.returncode != 0 or not (r.stdout or "").strip():
        return []

    data = json.loads(r.stdout)
    if isinstance(data, dict):
        data = [data]
    out: list[Adapter] = []
    for row in data:
        out.append(
            Adapter(
                name=str(row.get("Name") or ""),
                description=str(row.get("InterfaceDescription") or ""),
                status=str(row.get("Status") or ""),
                mac=str(row.get("MacAddress") or ""),
            )
        )
    return out


def list_wifi_adapters() -> list[Adapter]:
    result = []
    for a in list_adapters():
        desc = a.description.lower()
        name = a.name.lower()
        if any(
            x in desc
            for x in (
                "wireless",
                "wi-fi",
                "wifi",
                "802.11",
                "wlan",
                "dwa-",
                "d-link",
                "atheros",
                "realtek rtl8",
                "mediatek",
            )
        ) or "беспровод" in name:
            result.append(a)
    return result


def find_adapter_by_fragment(fragment: str) -> Adapter | None:
    frag = fragment.lower()
    for a in list_adapters():
        if frag in a.name.lower() or frag in a.description.lower():
            return a
    return None


def _is_virtual(name: str, description: str) -> bool:
    blob = f"{name} {description}".lower()
    return any(h in blob for h in VIRTUAL_HINTS)


def get_physical_uplink() -> Uplink:
    """Real LAN uplink for dial-out, never Happ/TUN/VPN."""
    ps = r"""
Get-NetIPConfiguration |
  Where-Object { $_.IPv4Address -and $_.IPv4DefaultGateway } |
  ForEach-Object {
    [pscustomobject]@{
      Name = $_.InterfaceAlias
      Description = $_.InterfaceDescription
      Ip = $_.IPv4Address.IPAddress
      Gateway = $_.IPv4DefaultGateway.NextHop
      IfIndex = $_.InterfaceIndex
      Status = (Get-NetAdapter -InterfaceIndex $_.InterfaceIndex -ErrorAction SilentlyContinue).Status
    }
  } | ConvertTo-Json -Compress
"""
    r = run_ps(ps)
    if r.returncode != 0 or not (r.stdout or "").strip():
        raise RuntimeError("Не найден физический uplink (Ethernet/Wi-Fi с шлюзом)")

    data = json.loads(r.stdout)
    if isinstance(data, dict):
        data = [data]

    candidates: list[Uplink] = []
    for row in data:
        name = str(row.get("Name") or "")
        desc = str(row.get("Description") or "")
        status = str(row.get("Status") or "")
        if status and status.lower() != "up":
            continue
        if _is_virtual(name, desc):
            continue
        ip = str(row.get("Ip") or "")
        gw = str(row.get("Gateway") or "")
        if_index = int(row.get("IfIndex") or 0)
        if not ip or not gw or not if_index:
            continue
        candidates.append(Uplink(name=name, ip=ip, gateway=gw, if_index=if_index))

    if not candidates:
        raise RuntimeError(
            "Нет физического адаптера со шлюзом. Happ/VPN адаптеры игнорируются специально."
        )

    def score(u: Uplink) -> tuple[int, int]:
        name = u.name.lower()
        # Prefer Ethernet over Wi-Fi
        eth = 0 if ("ethernet" in name or " eth" in f" {name}") else 1
        return (eth, u.if_index)

    candidates.sort(key=score)
    return candidates[0]


def resolve_ipv4(host: str) -> list[str]:
    host = (host or "").strip()
    if not host:
        return []
    # already an IP?
    try:
        socket.inet_aton(host)
        return [host]
    except OSError:
        pass
    ips: list[str] = []
    try:
        for info in socket.getaddrinfo(host, None, socket.AF_INET, socket.SOCK_STREAM):
            ip = info[4][0]
            if ip not in ips:
                ips.append(ip)
    except OSError as exc:
        raise RuntimeError(f"Не резолвится {host}: {exc}") from exc
    return ips


def add_host_routes(ips: list[str], uplink: Uplink) -> list[str]:
    """Force proxy server via physical gateway so Happ TUN does not swallow it."""
    added: list[str] = []
    for ip in ips:
        safe_ip = ip.replace("'", "")
        ps = rf"""
$ifIndex = {uplink.if_index}
$gw = '{uplink.gateway}'
$ip = '{safe_ip}'
Get-NetRoute -DestinationPrefix ($ip + '/32') -ErrorAction SilentlyContinue |
  Where-Object {{ $_.NextHop -eq $gw -or $_.InterfaceIndex -eq $ifIndex }} |
  Remove-NetRoute -Confirm:$false -ErrorAction SilentlyContinue
New-NetRoute -DestinationPrefix ($ip + '/32') -InterfaceIndex $ifIndex -NextHop $gw -RouteMetric 1 -ErrorAction Stop | Out-Null
"""
        r = run_ps(ps)
        if r.returncode != 0:
            # fallback netsh
            r2 = run_cmd(
                [
                    "route",
                    "add",
                    ip,
                    "mask",
                    "255.255.255.255",
                    uplink.gateway,
                    "metric",
                    "1",
                    "if",
                    str(uplink.if_index),
                ]
            )
            if r2.returncode != 0:
                raise RuntimeError(
                    f"Не удалось добавить bypass-маршрут {ip}:\n{r.stderr or r.stdout}\n{r2.stderr or r2.stdout}"
                )
        added.append(ip)
    return added


def remove_host_routes(ips: list[str], uplink: Uplink | None = None) -> None:
    for ip in ips:
        safe_ip = ip.replace("'", "")
        if uplink:
            ps = rf"""
Get-NetRoute -DestinationPrefix '{safe_ip}/32' -ErrorAction SilentlyContinue |
  Where-Object {{ $_.InterfaceIndex -eq {uplink.if_index} -or $_.NextHop -eq '{uplink.gateway}' }} |
  Remove-NetRoute -Confirm:$false -ErrorAction SilentlyContinue
"""
        else:
            ps = rf"""
Get-NetRoute -DestinationPrefix '{safe_ip}/32' -ErrorAction SilentlyContinue |
  Remove-NetRoute -Confirm:$false -ErrorAction SilentlyContinue
"""
        run_ps(ps)
        run_cmd(["route", "delete", ip, "mask", "255.255.255.255"])


def set_static_ip(adapter_name: str, ip: str, prefix: int = 24) -> None:
    safe = adapter_name.replace("'", "''")
    ps = rf"""
$a = Get-NetAdapter -Name '{safe}' -ErrorAction Stop
Get-NetIPAddress -InterfaceIndex $a.ifIndex -AddressFamily IPv4 -ErrorAction SilentlyContinue |
  Remove-NetIPAddress -Confirm:$false -ErrorAction SilentlyContinue
New-NetIPAddress -InterfaceIndex $a.ifIndex -IPAddress '{ip}' -PrefixLength {prefix} -ErrorAction Stop | Out-Null
"""
    r = run_ps(ps)
    if r.returncode != 0:
        raise RuntimeError(f"Не удалось задать IP {ip} на {adapter_name}:\n{r.stderr or r.stdout}")


def remove_default_routes(adapter_name: str) -> None:
    """Only strip defaults from OUR share TUN — never touch happ-xray."""
    if adapter_name.lower() in ("happ-xray",) or "happ" in adapter_name.lower():
        return
    safe = adapter_name.replace("'", "''")
    ps = rf"""
$a = Get-NetAdapter -Name '{safe}' -ErrorAction SilentlyContinue
if ($null -eq $a) {{ return }}
Get-NetRoute -InterfaceIndex $a.ifIndex -DestinationPrefix '0.0.0.0/0' -ErrorAction SilentlyContinue |
  Remove-NetRoute -Confirm:$false -ErrorAction SilentlyContinue
"""
    run_ps(ps)


def raise_adapter_metric(adapter_name: str, metric: int = 5000) -> None:
    if "happ" in adapter_name.lower():
        return
    safe = adapter_name.replace("'", "''")
    ps = rf"""
$a = Get-NetAdapter -Name '{safe}' -ErrorAction SilentlyContinue
if ($null -eq $a) {{ return }}
Set-NetIPInterface -InterfaceIndex $a.ifIndex -AddressFamily IPv4 -InterfaceMetric {metric} -ErrorAction SilentlyContinue
"""
    run_ps(ps)


def prepare_tun_for_ics(
    adapter_name: str,
    ip: str = "10.8.0.2",
    gateway: str = "10.8.0.1",
    prefix: int = 24,
    metric: int = 9000,
    mtu: int = 1280,
) -> None:
    """
    Make Wintun look like a usable ICS 'public' uplink without stealing PC default route.
    High interface/route metric keeps Happ/Ethernet preferred for the PC.
    MTU 1280 helps Discord voice / UDP on SoftAP clients (PS5).
    """
    if "happ" in adapter_name.lower():
        return
    safe = adapter_name.replace("'", "''")
    ps = rf"""
$ErrorActionPreference = 'Stop'
$a = Get-NetAdapter -Name '{safe}' -ErrorAction Stop
$ifIndex = $a.ifIndex

Get-NetIPAddress -InterfaceIndex $ifIndex -AddressFamily IPv4 -ErrorAction SilentlyContinue |
  Remove-NetIPAddress -Confirm:$false -ErrorAction SilentlyContinue
Get-NetRoute -InterfaceIndex $ifIndex -DestinationPrefix '0.0.0.0/0' -ErrorAction SilentlyContinue |
  Remove-NetRoute -Confirm:$false -ErrorAction SilentlyContinue

New-NetIPAddress -InterfaceIndex $ifIndex -IPAddress '{ip}' -PrefixLength {prefix} -ErrorAction Stop | Out-Null
Set-NetIPInterface -InterfaceIndex $ifIndex -AddressFamily IPv4 -InterfaceMetric {metric} -Forwarding Enabled -NlMtuBytes {mtu} -ErrorAction SilentlyContinue
try {{ Set-NetIPInterface -InterfaceIndex $ifIndex -NlMtu {mtu} -ErrorAction SilentlyContinue }} catch {{}}
New-NetRoute -DestinationPrefix '0.0.0.0/0' -InterfaceIndex $ifIndex -NextHop '{gateway}' -RouteMetric {metric} -ErrorAction SilentlyContinue | Out-Null

# Fallback if NextHop route rejected (on-link)
if (-not (Get-NetRoute -InterfaceIndex $ifIndex -DestinationPrefix '0.0.0.0/0' -ErrorAction SilentlyContinue)) {{
  route add 0.0.0.0 mask 0.0.0.0 {gateway} metric {metric} if $ifIndex | Out-Null
}}

# Also set adapter MTU (Wintun)
try {{ netsh interface ipv4 set subinterface $ifIndex mtu={mtu} store=active | Out-Null }} catch {{}}
"""
    r = run_ps(ps)
    if r.returncode != 0:
        raise RuntimeError(f"prepare_tun_for_ics failed:\n{r.stderr or r.stdout}")


def happ_detected() -> bool:
    ps = r"""
$procs = Get-Process -ErrorAction SilentlyContinue | Where-Object { $_.ProcessName -match '^(Happ|happd)$' }
$tun = Get-NetAdapter -ErrorAction SilentlyContinue | Where-Object { $_.Name -eq 'happ-xray' -and $_.Status -eq 'Up' }
if ($procs -or $tun) { 'yes' } else { 'no' }
"""
    r = run_ps(ps)
    return (r.stdout or "").strip().lower().startswith("yes")
