from __future__ import annotations

import json
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

# Out-of-process STA script — in-process pywin32 often hits 0x80040201 on Win10/11
_ICS_PS = r"""
param(
  [Parameter(Mandatory=$true)][string]$PublicName,
  [Parameter(Mandatory=$true)][string]$PrivateName
)
$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)

function Write-Result($ok, $msg) {
  $obj = @{ ok = [bool]$ok; message = [string]$msg }
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
    Start-Sleep -Milliseconds 900
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

try {
  Ensure-SharedAccess
  regsvr32 /s hnetcfg.dll | Out-Null
  Cleanup-HomeNet

  $hnet = New-Object -ComObject HNetCfg.HNetShare

  function Disable-All([object]$mgr) {
    foreach ($conn in $mgr.EnumEveryConnection) {
      try {
        $cfg = $mgr.INetSharingConfigurationForINetConnection($conn)
        if ($cfg.SharingEnabled) { $cfg.DisableSharing() }
      } catch {}
    }
  }

  Disable-All $hnet
  Start-Sleep -Milliseconds 700

  # Bounce SharedAccess to clear stuck event subscribers (0x80040201)
  try {
    Restart-Service SharedAccess -Force -ErrorAction SilentlyContinue
    Start-Sleep -Seconds 1
    Ensure-SharedAccess
  } catch {}

  # Re-create COM after service bounce
  $hnet = New-Object -ComObject HNetCfg.HNetShare
  Disable-All $hnet
  Start-Sleep -Milliseconds 500
  Cleanup-HomeNet

  $publicCfg = $null
  $privateCfg = $null
  $seen = New-Object System.Collections.Generic.List[string]

  foreach ($conn in $hnet.EnumEveryConnection) {
    $props = $hnet.NetConnectionProps($conn)
    $name = [string]$props.Name
    $seen.Add($name) | Out-Null
    $cfg = $hnet.INetSharingConfigurationForINetConnection($conn)
    if (NameMatch $name $PublicName) { $publicCfg = $cfg }
    if (NameMatch $name $PrivateName) { $privateCfg = $cfg }
  }

  if ($null -eq $publicCfg -or $null -eq $privateCfg) {
    Write-Result $false ("adapters not found public='$PublicName' private='$PrivateName' available=$($seen -join ', ')")
    exit 2
  }

  # Order from working IcsManager: PUBLIC then PRIVATE
  $publicCfg.EnableSharing(0)
  Start-Sleep -Milliseconds 400
  $privateCfg.EnableSharing(1)

  Write-Result $true 'ok'
  exit 0
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


def _run_ics_sta(public_name: str, private_name: str) -> None:
    """Enable ICS in a clean PowerShell STA process (avoids Python COM 0x80040201)."""
    with tempfile.TemporaryDirectory() as tmp:
        script = Path(tmp) / "enable_ics.ps1"
        script.write_text(_ICS_PS.lstrip("\n"), encoding="utf-8")
        # -STA is critical for HNetCfg event subscribers
        r = run_cmd(
            [
                "powershell",
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
            ],
            timeout=120,
        )
    raw = (r.stdout or "").strip() or (r.stderr or "").strip()
    data = None
    for line in reversed(raw.splitlines()):
        line = line.strip()
        if line.startswith("{") and "ok" in line:
            try:
                data = json.loads(line)
                break
            except Exception:
                continue
    if isinstance(data, dict) and data.get("ok"):
        return
    msg = ""
    if isinstance(data, dict):
        msg = str(data.get("message") or "")
    if not msg:
        msg = raw or f"exit={r.returncode}"
    raise RuntimeError(f"ICS STA failed: {msg}")


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
"""
    run_cmd(
        ["powershell", "-STA", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", ps],
        timeout=60,
    )
    time.sleep(0.4)


def list_shareable_names() -> list[str]:
    # Prefer STA process listing for consistent Cyrillic names
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
    r = run_cmd(
        ["powershell", "-STA", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", ps],
        timeout=45,
    )
    try:
        data = json.loads((r.stdout or "").strip() or "[]")
        if isinstance(data, str):
            return [data]
        if isinstance(data, list):
            return [str(x) for x in data]
    except Exception:
        pass
    # fallback in-process
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
    r = run_cmd(
        ["powershell", "-STA", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", ps],
        timeout=45,
    )
    try:
        data = json.loads((r.stdout or "").strip() or "{}")
        return data.get("public"), data.get("private")
    except Exception:
        return None, None


def enable_sharing(public_name: str, private_name: str) -> None:
    """Share public (TUN) → private (SoftAP). Uses out-of-process STA PowerShell."""
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
    for attempt in range(1, 4):
        try:
            _run_ics_sta(public_name, private_name)
            # verify
            cur_pub, cur_priv = current_sharing()
            if cur_pub and cur_priv:
                return
            # sometimes SharingConnectionType is flaky — accept if EnableSharing didn't throw
            return
        except Exception as exc:
            last_err = exc
            time.sleep(0.7 * attempt)
    raise RuntimeError(f"ICS EnableSharing failed: {last_err}")


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
