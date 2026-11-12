from __future__ import annotations

import json
import os
import re
import tempfile
import time
from pathlib import Path
from typing import Iterable

from .netutil import run_cmd

try:
    import pythoncom  # type: ignore
    import win32com.client  # type: ignore
except ImportError:  # pragma: no cover
    pythoncom = None  # type: ignore
    win32com = None  # type: ignore


ICSS_PUBLIC = 0
ICSS_PRIVATE = 1

# HNetCfg EnableSharing is notoriously unreliable from 64-bit processes (0x80040201).
# Prefer 32-bit PowerShell STA — same bitness as working ICS Manager tools.
_PS32 = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "SysWOW64" / "WindowsPowerShell" / "v1.0" / "powershell.exe"
_PS64 = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32" / "WindowsPowerShell" / "v1.0" / "powershell.exe"


def _powershell_exe() -> str:
    if _PS32.is_file():
        return str(_PS32)
    if _PS64.is_file():
        return str(_PS64)
    return "powershell"


# Out-of-process STA script
_ICS_PS = r"""
param(
  [Parameter(Mandatory=$true)][string]$PublicName,
  [Parameter(Mandatory=$true)][string]$PrivateName,
  [ValidateSet('pub-priv','priv-pub')][string]$Order = 'pub-priv'
)
$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)

function Write-Result($ok, $msg) {
  $obj = @{ ok = [bool]$ok; message = [string]$msg; bitness = [IntPtr]::Size * 8 }
  $obj | ConvertTo-Json -Compress
}

function Ensure-SharedAccess {
  $svc = Get-Service SharedAccess -ErrorAction SilentlyContinue
  if ($null -eq $svc) { return }
  if ($svc.StartType -eq 'Disabled') {
    Set-Service SharedAccess -StartupType Manual -ErrorAction SilentlyContinue
  }
  if ($svc.Status -ne 'Running') {
    Start-Service SharedAccess -ErrorAction SilentlyContinue
    Start-Sleep -Milliseconds 1200
  }
}

function Cleanup-HomeNet {
  try {
    $opts = New-Object System.Management.PutOptions
    $opts.Type = [System.Management.PutType]::UpdateOnly
    $scope = New-Object System.Management.ManagementScope('root\Microsoft\HomeNet')
    $scope.Connect()
    $q = New-Object System.Management.ObjectQuery('SELECT * FROM HNet_ConnectionProperties')
    $searcher = New-Object System.Management.ManagementObjectSearcher($scope, $q)
    foreach ($entry in $searcher.Get()) {
      $changed = $false
      if ($entry['IsIcsPrivate']) { $entry['IsIcsPrivate'] = $false; $changed = $true }
      if ($entry['IsIcsPublic'])  { $entry['IsIcsPublic']  = $false; $changed = $true }
      if ($changed) { $entry.Put($opts) | Out-Null }
    }
  } catch {}
}

function Norm([string]$s) {
  return (($s -replace '\s+', ' ').Trim().ToLower())
}

function StarSuffix([string]$s) {
  $m = [regex]::Match($s, '\*\s*(\d+)\s*$')
  if ($m.Success) { return $m.Groups[1].Value }
  return $null
}

function NameMatch([string]$a, [string]$b) {
  if ($a -eq $b) { return $true }
  if ((Norm $a) -eq (Norm $b)) { return $true }
  $sa = StarSuffix $a; $sb = StarSuffix $b
  if ($sa -and $sb -and $sa -eq $sb) { return $true }
  return $false
}

function Disable-All([object]$mgr) {
  foreach ($conn in $mgr.EnumEveryConnection) {
    try {
      $cfg = $mgr.INetSharingConfigurationForINetConnection($conn)
      if ($cfg.SharingEnabled) { $cfg.DisableSharing() }
    } catch {}
  }
}

function Find-Cfgs([object]$mgr, [string]$pubName, [string]$privName) {
  $publicCfg = $null
  $privateCfg = $null
  $seen = New-Object System.Collections.Generic.List[string]
  foreach ($conn in $mgr.EnumEveryConnection) {
    $props = $mgr.NetConnectionProps($conn)
    $name = [string]$props.Name
    $seen.Add($name) | Out-Null
    $cfg = $mgr.INetSharingConfigurationForINetConnection($conn)
    if (NameMatch $name $pubName) { $publicCfg = $cfg }
    if (NameMatch $name $privName) { $privateCfg = $cfg }
  }
  return @{ public = $publicCfg; private = $privateCfg; seen = $seen }
}

try {
  Ensure-SharedAccess
  try { regsvr32 /s hnetcfg.dll | Out-Null } catch {}
  Cleanup-HomeNet

  $hnet = New-Object -ComObject HNetCfg.HNetShare
  Disable-All $hnet
  Start-Sleep -Milliseconds 800
  Cleanup-HomeNet

  # Soft bounce only if service looks wedged — full Restart can drop SoftAP
  try {
    $svc = Get-Service SharedAccess -ErrorAction SilentlyContinue
    if ($null -ne $svc -and $svc.Status -ne 'Running') {
      Start-Service SharedAccess -ErrorAction SilentlyContinue
      Start-Sleep -Milliseconds 1200
    }
  } catch {}

  $hnet = New-Object -ComObject HNetCfg.HNetShare
  Disable-All $hnet
  Start-Sleep -Milliseconds 500

  $found = Find-Cfgs $hnet $PublicName $PrivateName
  if ($null -eq $found.public -or $null -eq $found.private) {
    Write-Result $false ("adapters not found public='$PublicName' private='$PrivateName' available=$($found.seen -join ', ')")
    exit 2
  }

  $err1 = $null
  try {
    if ($Order -eq 'priv-pub') {
      $found.private.EnableSharing(1)
      Start-Sleep -Milliseconds 500
      $found.public.EnableSharing(0)
    } else {
      $found.public.EnableSharing(0)
      Start-Sleep -Milliseconds 500
      $found.private.EnableSharing(1)
    }
  } catch {
    $err1 = $_.Exception.Message
  }

  if ($null -eq $err1) {
    Write-Result $true ("ok order=$Order")
    exit 0
  }

  # Retry opposite order after fresh cleanup
  Cleanup-HomeNet
  $hnet = New-Object -ComObject HNetCfg.HNetShare
  Disable-All $hnet
  Start-Sleep -Milliseconds 700
  $found = Find-Cfgs $hnet $PublicName $PrivateName
  if ($null -eq $found.public -or $null -eq $found.private) {
    Write-Result $false ("retry adapters missing; first=$err1")
    exit 2
  }

  $alt = if ($Order -eq 'priv-pub') { 'pub-priv' } else { 'priv-pub' }
  try {
    if ($alt -eq 'priv-pub') {
      $found.private.EnableSharing(1)
      Start-Sleep -Milliseconds 500
      $found.public.EnableSharing(0)
    } else {
      $found.public.EnableSharing(0)
      Start-Sleep -Milliseconds 500
      $found.private.EnableSharing(1)
    }
    Write-Result $true ("ok order=$alt after fail=$err1")
    exit 0
  } catch {
    Write-Result $false ("EnableSharing failed ($Order then $alt): $err1 | $($_.Exception.Message)")
    exit 1
  }
} catch {
  Write-Result $false $_.Exception.Message
  exit 1
}
"""


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "").strip().lower())


def _star_suffix(name: str) -> str | None:
    m = re.search(r"\*\s*(\d+)\s*$", name or "")
    return m.group(1) if m else None


def _run_ps_command(ps: str, timeout: int = 60):
    return run_cmd(
        [
            _powershell_exe(),
            "-STA",
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-Command",
            ps,
        ],
        timeout=timeout,
    )


def _parse_json_result(raw: str) -> dict | None:
    for line in reversed((raw or "").splitlines()):
        line = line.strip()
        if line.startswith("{") and "ok" in line:
            try:
                data = json.loads(line)
                if isinstance(data, dict):
                    return data
            except Exception:
                continue
    return None


def _run_ics_sta(public_name: str, private_name: str, order: str = "pub-priv") -> None:
    """Enable ICS in a clean 32-bit PowerShell STA process."""
    with tempfile.TemporaryDirectory() as tmp:
        script = Path(tmp) / "enable_ics.ps1"
        script.write_text(_ICS_PS.lstrip("\n"), encoding="utf-8")
        r = run_cmd(
            [
                _powershell_exe(),
                "-STA",
                "-NoProfile",
                "-ExecutionPolicy",
                "Bypass",
                "-File",
                str(script),
                "-PublicName",
                public_name,
                "-PrivateName",
                private_name,
                "-Order",
                order,
            ],
            timeout=120,
        )
    raw = (r.stdout or "").strip() or (r.stderr or "").strip()
    data = _parse_json_result(raw)
    if isinstance(data, dict) and data.get("ok"):
        return
    msg = ""
    bitness = ""
    if isinstance(data, dict):
        msg = str(data.get("message") or "")
        if data.get("bitness"):
            bitness = f" [ps={data.get('bitness')}-bit]"
    if not msg:
        msg = raw or f"exit={r.returncode}"
    raise RuntimeError(f"ICS STA failed{bitness}: {msg}")


def disable_all_sharing() -> None:
    ps = r"""
$ErrorActionPreference='SilentlyContinue'
try { Start-Service SharedAccess } catch {}
try {
  $hnet = New-Object -ComObject HNetCfg.HNetShare
  foreach ($conn in $hnet.EnumEveryConnection) {
    try {
      $cfg = $hnet.INetSharingConfigurationForINetConnection($conn)
      if ($cfg.SharingEnabled) { $cfg.DisableSharing() }
    } catch {}
  }
} catch {}
try {
  $opts = New-Object System.Management.PutOptions
  $opts.Type = [System.Management.PutType]::UpdateOnly
  $scope = New-Object System.Management.ManagementScope('root\Microsoft\HomeNet')
  $scope.Connect()
  $q = New-Object System.Management.ObjectQuery('SELECT * FROM HNet_ConnectionProperties')
  $searcher = New-Object System.Management.ManagementObjectSearcher($scope, $q)
  foreach ($entry in $searcher.Get()) {
    $changed = $false
    if ($entry['IsIcsPrivate']) { $entry['IsIcsPrivate'] = $false; $changed = $true }
    if ($entry['IsIcsPublic'])  { $entry['IsIcsPublic']  = $false; $changed = $true }
    if ($changed) { $entry.Put($opts) | Out-Null }
  }
} catch {}
"""
    _run_ps_command(ps, timeout=60)
    time.sleep(0.5)


def list_shareable_names() -> list[str]:
    ps = r"""
$ErrorActionPreference='SilentlyContinue'
[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)
$hnet = New-Object -ComObject HNetCfg.HNetShare
$names = @()
foreach ($conn in $hnet.EnumEveryConnection) {
  try { $names += [string]$hnet.NetConnectionProps($conn).Name } catch {}
}
$names | ConvertTo-Json -Compress
"""
    r = _run_ps_command(ps, timeout=45)
    try:
        data = json.loads((r.stdout or "").strip() or "[]")
        if isinstance(data, str):
            return [data]
        if isinstance(data, list):
            return [str(x) for x in data]
    except Exception:
        pass
    # fallback in-process (names only)
    names: list[str] = []
    if win32com is None:
        return names
    pythoncom.CoInitializeEx(pythoncom.COINIT_APARTMENTTHREADED)
    try:
        hnet = win32com.client.Dispatch("HNetCfg.HNetShare")
        for conn in hnet.EnumEveryConnection:
            try:
                names.append(str(hnet.NetConnectionProps(conn).Name))
            except Exception:
                continue
    finally:
        try:
            pythoncom.CoUninitialize()
        except Exception:
            pass
    return names


def current_sharing() -> tuple[str | None, str | None]:
    ps = r"""
$ErrorActionPreference='SilentlyContinue'
[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)
$hnet = New-Object -ComObject HNetCfg.HNetShare
$pub = $null; $priv = $null
foreach ($conn in $hnet.EnumEveryConnection) {
  try {
    $cfg = $hnet.INetSharingConfigurationForINetConnection($conn)
    if (-not $cfg.SharingEnabled) { continue }
    $name = [string]$hnet.NetConnectionProps($conn).Name
    $t = [int]$cfg.SharingConnectionType
    if ($t -eq 0) { $pub = $name }
    elseif ($t -eq 1) { $priv = $name }
  } catch {}
}
@{ public = $pub; private = $priv } | ConvertTo-Json -Compress
"""
    r = _run_ps_command(ps, timeout=45)
    try:
        data = json.loads((r.stdout or "").strip() or "{}")
        return data.get("public"), data.get("private")
    except Exception:
        return None, None


def enable_sharing(public_name: str, private_name: str) -> None:
    """Share public (TUN) → private (SoftAP). Uses 32-bit STA PowerShell."""
    cur_pub, cur_priv = current_sharing()
    if cur_pub and cur_priv:
        if _norm(cur_pub) == _norm(public_name) and (
            _norm(cur_priv) == _norm(private_name)
            or (
                _star_suffix(cur_priv)
                and _star_suffix(private_name)
                and _star_suffix(cur_priv) == _star_suffix(private_name)
            )
        ):
            return

    last_err: Exception | None = None
    orders = ("pub-priv", "priv-pub")
    for attempt in range(1, 4):
        order = orders[(attempt - 1) % 2]
        try:
            _run_ics_sta(public_name, private_name, order=order)
            cur_pub, cur_priv = current_sharing()
            if cur_pub and cur_priv:
                return
            # EnableSharing didn't throw — accept even if type readout is flaky
            return
        except Exception as exc:
            last_err = exc
            time.sleep(0.9 * attempt)
            try:
                disable_all_sharing()
            except Exception:
                pass
    ps = _powershell_exe()
    raise RuntimeError(f"ICS EnableSharing failed ({ps}): {last_err}")


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
