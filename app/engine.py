from __future__ import annotations

import json
import subprocess
import threading
import time
from dataclasses import dataclass, field
from typing import Callable

from . import hotspot, ics
from .deps import ensure_runtime
from .links import ParsedLink, parse_proxy_input
from .netutil import (
    Adapter,
    Uplink,
    add_host_routes,
    find_adapter_by_fragment,
    get_physical_uplink,
    list_adapters,
    list_wifi_adapters,
    prepare_tun_for_ics,
    remove_host_routes,
    resolve_ipv4,
)
from .healthcheck import socks5_http_204
from .paths import BIN, STATE_FILE, TUN2SOCKS_EXE, XRAY_CONFIG, XRAY_EXE, ensure_dirs
from .procutil import kill_our_share_processes, kill_supervisor
from .remnawave import parse_servers
from .xray_config import SOCKS_HOST, SOCKS_PORT, build_xray_config

LogFn = Callable[[str], None]

TUN_NAME = "XrayShare"
TUN_IP = "10.8.0.2"
TUN_GW = "10.8.0.1"


@dataclass
class StartRequest:
    link: str
    wifi_adapter: str
    ssid: str
    password: str
    outbound: dict | None = None
    remark: str = ""
    kind: str = ""
    server_host: str = ""
    server_port: int = 0


@dataclass
class _RuntimeState:
    uplink: Uplink | None = None
    bypass_ips: list[str] = field(default_factory=list)
    tun_adapter: str | None = None
    private_adapter: str | None = None
    ssid: str = ""
    password: str = ""
    parsed: ParsedLink | None = None


class Engine:
    def __init__(self, log: LogFn | None = None) -> None:
        self._log = log or (lambda m: None)
        self._xray: subprocess.Popen[str] | None = None
        self._tun: subprocess.Popen[str] | None = None
        self._running = False
        self._lock = threading.Lock()
        self._state = _RuntimeState()
        self._last_req: StartRequest | None = None
        self._reconnect_count = 0

    @property
    def running(self) -> bool:
        return self._running

    def wifi_adapters(self) -> list[Adapter]:
        return list_wifi_adapters()

    def prepare_binaries(self) -> None:
        ensure_runtime(self._log)

    def start(self, req: StartRequest) -> None:
        with self._lock:
            if self._running:
                raise RuntimeError("Уже запущено")
            try:
                self._start_unlocked(req)
                self._running = True
                self._save_state()
            except Exception:
                self._cleanup_unlocked()
                raise

    def stop(self) -> None:
        with self._lock:
            self._log("Остановка...")
            self._cleanup_unlocked()
            self._log("Остановлено")

    def force_kill(self) -> None:
        """Hard cleanup: our xray/tun2socks by path, ICS, hotspot, bypass routes."""
        with self._lock:
            self._log("FORCE KILL...")
            self._load_state()
            try:
                ics.disable_all_sharing()
            except Exception as exc:
                self._log(f"ICS: {exc}")
            try:
                hotspot.stop_hotspot()
            except Exception as exc:
                self._log(f"Hotspot: {exc}")
            self._kill(self._tun)
            self._tun = None
            self._kill(self._xray)
            self._xray = None
            killed = kill_our_share_processes()
            kill_supervisor()
            self._log(f"Убито процессов: {killed}")
            if self._state.bypass_ips:
                try:
                    remove_host_routes(self._state.bypass_ips, self._state.uplink)
                    self._log("Bypass маршруты сняты")
                except Exception as exc:
                    self._log(f"Bypass: {exc}")
            self._clear_state()
            self._state = _RuntimeState()
            self._running = False
            self._log("FORCE KILL завершён")

    def check_health(self) -> tuple[bool, str]:
        """Return (ok, detail). Safe to call from supervisor loop."""
        if not self._running:
            return False, "not running"
        if self._xray is None or self._xray.poll() is not None:
            return False, "xray process down"
        if self._tun is None or self._tun.poll() is not None:
            return False, "tun2socks process down"
        try:
            import socket

            with socket.create_connection((SOCKS_HOST, SOCKS_PORT), timeout=1.5):
                pass
        except OSError as exc:
            return False, f"socks closed: {exc}"

        ok, detail = socks5_http_204(SOCKS_HOST, SOCKS_PORT, timeout=8.0)
        if not ok:
            return False, f"tunnel probe failed: {detail}"
        return True, "ok"

    def reconnect_tunnel(self) -> None:
        """Restart Xray + tun2socks without tearing down SoftAP when possible."""
        with self._lock:
            if not self._running or not self._last_req:
                raise RuntimeError("Нечего переподключать")
            self._reconnect_unlocked(self._last_req)

    def _start_unlocked(self, req: StartRequest) -> None:
        ensure_dirs()
        self.prepare_binaries()
        self._last_req = req
        self._reconnect_count = 0

        uplink = get_physical_uplink()
        self._state.uplink = uplink
        self._state.ssid = req.ssid
        self._state.password = req.password
        self._log(f"Физический uplink: {uplink.name} {uplink.ip} gw={uplink.gateway}")

        parsed = self._resolve_link(req)
        self._state.parsed = parsed
        self._log(f"Протокол: {parsed.kind} ({parsed.remark})")
        if not parsed.server_host:
            raise RuntimeError("Не удалось вытащить адрес сервера из ссылки")

        ips = resolve_ipv4(parsed.server_host)
        self._state.bypass_ips = add_host_routes(ips, uplink)
        self._log(f"Прямой маршрут к серверу: {', '.join(self._state.bypass_ips)}")

        self._write_xray_config(parsed, uplink.ip)
        self._start_xray()
        self._wait_port(SOCKS_HOST, SOCKS_PORT, timeout=15)
        self._log(f"Xray SOCKS {SOCKS_HOST}:{SOCKS_PORT} sendThrough={uplink.ip}")

        self._start_tun(uplink.name)
        tun_adapter = self._wait_tun_adapter(timeout=20)
        self._state.tun_adapter = tun_adapter
        self._log(f"TUN адаптер: {tun_adapter}")

        # High-metric default on TUN so ICS can NAT Wi-Fi clients through VPN,
        # while PC still prefers Happ/Ethernet (lower metric). MTU 1280 for Discord/UDP.
        prepare_tun_for_ics(tun_adapter, ip=TUN_IP, gateway=TUN_GW, metric=9000, mtu=1280)
        self._log(f"TUN готов к раздаче: {TUN_IP} (ПК продолжает обычный интернет)")

        hs = hotspot.configure_and_start(req.ssid, req.password)
        self._state.private_adapter = hs.private_adapter
        self._log(f"Wi‑Fi точка: SSID={hs.ssid}  адаптер={hs.private_adapter}")

        # SoftAP vNIC needs a moment before HNetCfg accepts EnableSharing
        time.sleep(1.2)
        self._apply_ics(tun_adapter, hs.private_adapter)
        self._log("Готово. Подключай устройство к Wi‑Fi точке.")
        self._log("Ожидание подключений…")

    def _reconnect_unlocked(self, req: StartRequest) -> None:
        self._reconnect_count += 1
        self._log(f"Переподключение туннеля #{self._reconnect_count}…")

        uplink = get_physical_uplink()
        self._state.uplink = uplink
        parsed = self._state.parsed or self._resolve_link(req)
        self._state.parsed = parsed

        try:
            ips = resolve_ipv4(parsed.server_host)
            self._state.bypass_ips = add_host_routes(ips, uplink)
        except Exception as exc:
            self._log(f"Bypass refresh: {exc}")

        self._kill(self._tun)
        self._tun = None
        self._kill(self._xray)
        self._xray = None
        time.sleep(0.4)

        self._write_xray_config(parsed, uplink.ip)
        self._start_xray()
        self._wait_port(SOCKS_HOST, SOCKS_PORT, timeout=15)
        self._start_tun(uplink.name)
        tun_adapter = self._wait_tun_adapter(timeout=20)
        self._state.tun_adapter = tun_adapter
        prepare_tun_for_ics(tun_adapter, ip=TUN_IP, gateway=TUN_GW, metric=9000, mtu=1280)

        private = self._state.private_adapter
        if private:
            try:
                self._apply_ics(tun_adapter, private)
            except Exception as exc:
                # Don't fail reconnect if tunnel probe is OK — ICS may already be up
                self._log(f"WARN ICS re-apply: {exc}")
                try:
                    cur_pub, cur_priv = ics.current_sharing()
                    self._log(f"ICS сейчас: public={cur_pub} private={cur_priv}")
                except Exception:
                    pass

        if self._state.bypass_ips and self._state.uplink:
            add_host_routes(self._state.bypass_ips, self._state.uplink)

        ok, detail = socks5_http_204(SOCKS_HOST, SOCKS_PORT, timeout=10.0)
        if not ok:
            raise RuntimeError(f"После reconnect туннель мёртв: {detail}")
        self._log("Туннель восстановлен")

    def _write_xray_config(self, parsed: ParsedLink, send_through: str) -> None:
        cfg = build_xray_config(parsed, send_through=send_through)
        XRAY_CONFIG.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")

    def _apply_ics(self, tun_adapter: str, private_adapter: str) -> None:
        public = ics.resolve_public([tun_adapter, TUN_NAME])
        private = ics.resolve_private([private_adapter])
        self._log(f"ICS: public={public} | private={private}")
        ics.enable_sharing(public, private)
        prepare_tun_for_ics(tun_adapter, ip=TUN_IP, gateway=TUN_GW, metric=9000, mtu=1280)
        if self._state.bypass_ips and self._state.uplink:
            add_host_routes(self._state.bypass_ips, self._state.uplink)
        self._log(f"Раздача включена: {public} → {private}")

    def _resolve_link(self, req: StartRequest) -> ParsedLink:
        if isinstance(req.outbound, dict) and req.outbound:
            host = req.server_host
            port = int(req.server_port or 0)
            if not host:
                from .remnawave import host_port_from_outbound

                host, port = host_port_from_outbound(req.outbound)
            return ParsedLink(
                kind=req.kind or str(req.outbound.get("protocol") or "custom"),
                outbound=req.outbound,
                remark=req.remark or str(req.outbound.get("tag") or "selected"),
                server_host=host,
                server_port=port,
            )

        text = (req.link or "").strip()
        if not text:
            raise RuntimeError("Нет ссылки / подписки / JSON")

        try:
            servers = parse_servers(text)
        except Exception:
            servers = []
        if servers:
            if len(servers) == 1:
                return servers[0]
            # elevated job without GUI selection: prefer first usable
            return servers[0]

        return parse_proxy_input(text)

    def _cleanup_unlocked(self) -> None:
        self._load_state()
        try:
            ics.disable_all_sharing()
        except Exception:
            pass
        try:
            hotspot.stop_hotspot()
        except Exception:
            pass
        self._kill(self._tun)
        self._tun = None
        self._kill(self._xray)
        self._xray = None
        kill_our_share_processes()
        if self._state.bypass_ips:
            try:
                remove_host_routes(self._state.bypass_ips, self._state.uplink)
            except Exception:
                pass
        self._clear_state()
        self._state = _RuntimeState()
        self._running = False

    def _save_state(self) -> None:
        ensure_dirs()
        payload = {
            "bypass_ips": list(self._state.bypass_ips),
            "uplink": None
            if not self._state.uplink
            else {
                "name": self._state.uplink.name,
                "ip": self._state.uplink.ip,
                "gateway": self._state.uplink.gateway,
                "if_index": self._state.uplink.if_index,
            },
            "tun_adapter": self._state.tun_adapter,
            "private_adapter": self._state.private_adapter,
        }
        STATE_FILE.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    def _load_state(self) -> None:
        if self._state.bypass_ips or self._state.uplink:
            return
        if not STATE_FILE.exists():
            return
        try:
            data = json.loads(STATE_FILE.read_text(encoding="utf-8"))
        except Exception:
            return
        self._state.bypass_ips = list(data.get("bypass_ips") or [])
        up = data.get("uplink")
        if isinstance(up, dict) and up.get("ip"):
            self._state.uplink = Uplink(
                name=str(up.get("name") or ""),
                ip=str(up.get("ip") or ""),
                gateway=str(up.get("gateway") or ""),
                if_index=int(up.get("if_index") or 0),
            )
        self._state.tun_adapter = data.get("tun_adapter")
        self._state.private_adapter = data.get("private_adapter")

    def _clear_state(self) -> None:
        try:
            if STATE_FILE.exists():
                STATE_FILE.unlink()
        except Exception:
            pass

    def _start_xray(self) -> None:
        if not XRAY_EXE.exists():
            raise RuntimeError("xray.exe не найден")
        self._kill(self._xray)
        self._xray = subprocess.Popen(
            [str(XRAY_EXE), "run", "-c", str(XRAY_CONFIG)],
            cwd=str(BIN),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            creationflags=subprocess.CREATE_NO_WINDOW if hasattr(subprocess, "CREATE_NO_WINDOW") else 0,
        )
        threading.Thread(target=self._pump, args=(self._xray, "xray"), daemon=True).start()
        time.sleep(0.4)
        if self._xray.poll() is not None:
            raise RuntimeError("Xray сразу завершился — проверь ссылку/конфиг")

    def _start_tun(self, phys_name: str) -> None:
        if not TUN2SOCKS_EXE.exists():
            raise RuntimeError("tun2socks.exe не найден")
        self._kill(self._tun)
        args = [
            str(TUN2SOCKS_EXE),
            "--device",
            f"tun://{TUN_NAME}",
            "--proxy",
            f"socks5://{SOCKS_HOST}:{SOCKS_PORT}",
            "--loglevel",
            "info",
            "--interface",
            phys_name,
        ]
        self._log(f"tun2socks uplink iface: {phys_name}")
        self._tun = subprocess.Popen(
            args,
            cwd=str(BIN),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            creationflags=subprocess.CREATE_NO_WINDOW if hasattr(subprocess, "CREATE_NO_WINDOW") else 0,
        )
        threading.Thread(target=self._pump, args=(self._tun, "tun2socks"), daemon=True).start()
        time.sleep(0.6)
        if self._tun.poll() is not None:
            raise RuntimeError("tun2socks сразу завершился — нужен админ и wintun.dll")

    def _wait_tun_adapter(self, timeout: float) -> str:
        deadline = time.time() + timeout
        while time.time() < deadline:
            a = find_adapter_by_fragment(TUN_NAME)
            if a:
                return a.name
            for ad in list_adapters():
                if TUN_NAME.lower() in (ad.name + ad.description).lower():
                    return ad.name
            time.sleep(0.4)
        raise RuntimeError(f"Адаптер {TUN_NAME} не появился")

    def _wait_port(self, host: str, port: int, timeout: float) -> None:
        import socket

        deadline = time.time() + timeout
        while time.time() < deadline:
            if self._xray and self._xray.poll() is not None:
                raise RuntimeError("Xray упал при старте")
            try:
                with socket.create_connection((host, port), timeout=0.5):
                    return
            except OSError:
                time.sleep(0.2)
        raise RuntimeError("SOCKS порт Xray не открылся")

    def _pump(self, proc: subprocess.Popen[str], tag: str) -> None:
        if not proc.stdout:
            return
        for line in proc.stdout:
            line = line.rstrip()
            if line:
                self._log(f"[{tag}] {line}")

    @staticmethod
    def _kill(proc: subprocess.Popen[str] | None) -> None:
        if not proc:
            return
        try:
            if proc.poll() is None:
                proc.terminate()
                try:
                    proc.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    proc.kill()
        except Exception:
            pass
