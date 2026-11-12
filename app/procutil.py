from __future__ import annotations

import os
from pathlib import Path

from .netutil import run_ps
from .paths import BIN, XRAY_CONFIG, WORK, ensure_dirs
from .xray_config import SOCKS_PORT

SUPERVISOR_PID = WORK / "supervisor.pid"


def write_supervisor_pid() -> None:
    ensure_dirs()
    SUPERVISOR_PID.write_text(str(os.getpid()), encoding="utf-8")


def clear_supervisor_pid() -> None:
    try:
        if SUPERVISOR_PID.exists():
            SUPERVISOR_PID.unlink()
    except Exception:
        pass


def kill_supervisor() -> bool:
    if not SUPERVISOR_PID.exists():
        return False
    try:
        pid = int(SUPERVISOR_PID.read_text(encoding="utf-8").strip())
    except Exception:
        return False
    if pid <= 0 or pid == os.getpid():
        return False
    r = run_ps(rf"Stop-Process -Id {pid} -Force -ErrorAction SilentlyContinue")
    clear_supervisor_pid()
    return r.returncode == 0


def kill_our_share_processes() -> int:
    """
    Kill ONLY our share stack:
    - tun2socks with XrayShare (unique to this app)
    - xray with our config.json path (never Happ's xray)
    - listener on our SOCKS port
    """
    bin_path = str(BIN).replace("'", "''").lower()
    cfg_path = str(XRAY_CONFIG).replace("'", "''").lower()
    root_hint = str(BIN.parent.parent).replace("'", "''").lower()

    ps = rf"""
$ErrorActionPreference = 'SilentlyContinue'
$count = 0
$bin = '{bin_path}'
$cfg = '{cfg_path}'
$root = '{root_hint}'
$port = {SOCKS_PORT}

function Kill-Pid($id) {{
  if (-not $id) {{ return }}
  try {{
    Stop-Process -Id ([int]$id) -Force -ErrorAction Stop
    $script:count++
  }} catch {{}}
}}

# 1) By command line / path
Get-CimInstance Win32_Process | ForEach-Object {{
  $name = $_.Name
  $path = ''
  $cmd = ''
  try {{ $path = [string]$_.ExecutablePath }} catch {{}}
  try {{ $cmd = [string]$_.CommandLine }} catch {{}}
  $pathL = $path.ToLower()
  $cmdL = $cmd.ToLower()
  $isOurs = $false

  if ($name -eq 'tun2socks.exe') {{
    if ($cmdL -match 'xrayshare' -or $pathL.StartsWith($bin) -or $pathL.Contains('xraywifishare')) {{
      $isOurs = $true
    }}
  }}
  elseif ($name -eq 'xray.exe') {{
    if ($cmdL.Contains($cfg) -or ($cmdL.Contains('runtime\\work\\config.json')) -or ($cmdL.Contains('runtime/work/config.json'))) {{
      $isOurs = $true
    }}
    elseif ($pathL.StartsWith($bin)) {{
      $isOurs = $true
    }}
  }}

  if ($isOurs) {{ Kill-Pid $_.ProcessId }}
}}

# 2) Get-Process Path fallback
Get-Process -Name xray,tun2socks -ErrorAction SilentlyContinue | ForEach-Object {{
  try {{
    $p = $_.Path
    if (-not $p) {{ return }}
    $pl = $p.ToLower()
    if ($_.ProcessName -eq 'tun2socks' -and ($pl.StartsWith($bin) -or $pl.Contains('xraywifishare'))) {{
      Kill-Pid $_.Id
    }}
    elseif ($_.ProcessName -eq 'xray' -and $pl.StartsWith($bin)) {{
      Kill-Pid $_.Id
    }}
  }} catch {{}}
}}

# 3) Anyone listening on our SOCKS port (should be our xray)
try {{
  Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue |
    Select-Object -ExpandProperty OwningProcess -Unique |
    ForEach-Object {{ Kill-Pid $_ }}
}} catch {{}}

# 4) Last resort for tun2socks name (Happ does not use it)
Get-Process -Name tun2socks -ErrorAction SilentlyContinue | ForEach-Object {{ Kill-Pid $_.Id }}

$count
"""
    r = run_ps(ps)
    try:
        return int((r.stdout or "0").strip().splitlines()[-1])
    except Exception:
        return 0
