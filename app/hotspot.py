from __future__ import annotations

import subprocess
import time
from dataclasses import dataclass

from .netutil import list_adapters, run_ps, run_cmd


@dataclass
class HotspotInfo:
    ssid: str
    password: str
    private_adapter: str


def _netsh(args: list[str]) -> subprocess.CompletedProcess[str]:
    return run_cmd(["netsh", *args])


def configure_and_start(ssid: str, password: str) -> HotspotInfo:
    if len(password) < 8:
        raise ValueError("Пароль Wi-Fi минимум 8 символов")

    before = {a.name for a in list_adapters()}

    # hosted network (works with DWA-140)
    r1 = _netsh(["wlan", "set", "hostednetwork", "mode=allow", f"ssid={ssid}", f"key={password}"])
    if r1.returncode != 0:
        raise RuntimeError(f"hostednetwork set failed:\n{r1.stdout}\n{r1.stderr}")

    r2 = _netsh(["wlan", "start", "hostednetwork"])
    st = _netsh(["wlan", "show", "hostednetwork"])
    status_text = f"{r2.stdout}\n{r2.stderr}\n{st.stdout}\n{st.stderr}".lower()
    started = r2.returncode == 0 and (
        "started" in status_text
        or "запущ" in status_text
        or "состояние              : запущен" in status_text.replace(" ", "")
        or "status                 : started" in status_text.replace(" ", "")
    )
    if not started:
        # fallback: Windows Mobile Hotspot
        _start_mobile_hotspot(ssid, password)

    private = _wait_private_adapter(before, timeout=20)
    # Prefer the exact ICS COM name (avoids PowerShell Cyrillic mojibake)
    try:
        from . import ics

        private = ics.resolve_private([private])
    except Exception:
        pass
    return HotspotInfo(ssid=ssid, password=password, private_adapter=private)


def stop_hotspot() -> None:
    _netsh(["wlan", "stop", "hostednetwork"])
    run_ps(
        r"""
Add-Type -AssemblyName System.Runtime.WindowsRuntime | Out-Null
$asTaskGeneric = ([System.WindowsRuntimeSystemExtensions].GetMethods() | ? {
  $_.Name -eq 'AsTask' -and $_.IsGenericMethod -and $_.GetParameters().Count -eq 1
} | select -First 1)
function Await($WinRtTask, $ResultType) {
  $netTask = $asTaskGeneric.MakeGenericMethod($ResultType).Invoke($null, @($WinRtTask))
  $netTask.Wait(-1) | Out-Null
  $netTask.Result
}
try {
  $null = [Windows.Networking.Connectivity.NetworkInformation,Windows.Networking.Connectivity,ContentType=WindowsRuntime]
  $null = [Windows.Networking.NetworkOperators.NetworkOperatorTetheringManager,Windows.Networking.NetworkOperators,ContentType=WindowsRuntime]
  $profile = [Windows.Networking.Connectivity.NetworkInformation]::GetInternetConnectionProfile()
  if ($null -eq $profile) { return }
  $tm = [Windows.Networking.NetworkOperators.NetworkOperatorTetheringManager]::CreateFromConnectionProfile($profile)
  if ($tm.TetheringOperationalState -ne 0) {
    Await ($tm.StopTetheringAsync()) ([Windows.Networking.NetworkOperators.NetworkOperatorTetheringOperationResult]) | Out-Null
  }
} catch {}
"""
    )


def _wait_private_adapter(before: set[str], timeout: float) -> str:
    deadline = time.time() + timeout
    while time.time() < deadline:
        adapters = list_adapters()
        for a in adapters:
            name = a.name
            desc = (a.description or "").lower()
            if name in before:
                # hosted network often reuses existing "* 12" adapter that was disconnected
                if a.status.lower() == "up" and (
                    "hosted" in desc
                    or "virtual" in desc
                    or "wi-fi direct" in desc
                    or "microsoft hosted" in desc
                    or name.startswith("Local Area Connection*")
                    or "локальн" in name.lower()
                    and "*" in name
                ):
                    return name
            else:
                if a.status.lower() in ("up", "disconnected"):
                    # new adapter appeared
                    if a.status.lower() == "up" or "hosted" in desc or "virtual" in desc:
                        # prefer Up
                        if a.status.lower() == "up":
                            return name
        # second pass: any Local Area Connection* that is Up
        for a in adapters:
            if a.status.lower() == "up" and (
                "Local Area Connection*" in a.name
                or ("локальн" in a.name.lower() and "*" in a.name)
            ):
                return a.name
        time.sleep(0.5)
    raise RuntimeError(
        "Не найден адаптер точки доступа. Проверь, что USB Wi-Fi виден и драйвер поддерживает SoftAP."
    )


def _start_mobile_hotspot(ssid: str, password: str) -> None:
    # Configure + start Win11 Mobile Hotspot
    ps = rf"""
$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName System.Runtime.WindowsRuntime | Out-Null
$asTask = ([System.WindowsRuntimeSystemExtensions].GetMethods() | ? {{
  $_.Name -eq 'AsTask' -and $_.IsGenericMethod -and $_.GetParameters().Count -eq 1
}} | select -First 1)
$asTaskAction = ([System.WindowsRuntimeSystemExtensions].GetMethods() | ? {{
  $_.Name -eq 'AsTask' -and -not $_.IsGenericMethod -and $_.GetParameters().Count -eq 1
}} | select -First 1)
function Await($WinRtTask, $ResultType) {{
  $netTask = $asTask.MakeGenericMethod($ResultType).Invoke($null, @($WinRtTask))
  $netTask.Wait(-1) | Out-Null
  $netTask.Result
}}
function AwaitAction($WinRtAction) {{
  $netTask = $asTaskAction.Invoke($null, @($WinRtAction))
  $netTask.Wait(-1) | Out-Null
}}
$null = [Windows.Networking.Connectivity.NetworkInformation,Windows.Networking.Connectivity,ContentType=WindowsRuntime]
$null = [Windows.Networking.NetworkOperators.NetworkOperatorTetheringManager,Windows.Networking.NetworkOperators,ContentType=WindowsRuntime]
$null = [Windows.Networking.NetworkOperators.NetworkOperatorTetheringAccessPointConfiguration,Windows.Networking.NetworkOperators,ContentType=WindowsRuntime]
$profile = [Windows.Networking.Connectivity.NetworkInformation]::GetInternetConnectionProfile()
if ($null -eq $profile) {{ throw 'Нет активного интернет-профиля' }}
$tm = [Windows.Networking.NetworkOperators.NetworkOperatorTetheringManager]::CreateFromConnectionProfile($profile)
$cfg = $tm.GetCurrentAccessPointConfiguration()
$cfg.Ssid = '{ssid}'
$cfg.Passphrase = '{password}'
AwaitAction ($tm.ConfigureAccessPointAsync($cfg))
$result = Await ($tm.StartTetheringAsync()) ([Windows.Networking.NetworkOperators.NetworkOperatorTetheringOperationResult])
if ([int]$result.Status -ne 0) {{ throw ("Mobile Hotspot start failed: " + $result.Status) }}
"""
    r = run_ps(ps)
    if r.returncode != 0:
        raise RuntimeError(f"Не удалось поднять Wi-Fi точку:\n{r.stdout}\n{r.stderr}")
