from __future__ import annotations

import socket
import struct
from typing import Tuple


def socks5_tcp_reachable(
    proxy_host: str,
    proxy_port: int,
    dest_host: str,
    dest_port: int,
    timeout: float = 6.0,
) -> Tuple[bool, str]:
    """Minimal SOCKS5 CONNECT probe (no extra deps)."""
    try:
        with socket.create_connection((proxy_host, proxy_port), timeout=timeout) as sock:
            sock.settimeout(timeout)
            # greeting: ver=5, 1 method, no-auth
            sock.sendall(b"\x05\x01\x00")
            resp = _recv_exact(sock, 2)
            if resp != b"\x05\x00":
                return False, f"SOCKS auth rejected: {resp!r}"

            host_bytes = dest_host.encode("idna")
            req = b"\x05\x01\x00\x03" + bytes([len(host_bytes)]) + host_bytes + struct.pack(
                "!H", dest_port
            )
            sock.sendall(req)
            hdr = _recv_exact(sock, 4)
            if len(hdr) < 4 or hdr[0] != 5:
                return False, "bad SOCKS reply"
            if hdr[1] != 0:
                return False, f"SOCKS CONNECT failed code={hdr[1]}"
            atyp = hdr[3]
            if atyp == 1:
                _recv_exact(sock, 4 + 2)
            elif atyp == 3:
                ln = _recv_exact(sock, 1)[0]
                _recv_exact(sock, ln + 2)
            elif atyp == 4:
                _recv_exact(sock, 16 + 2)
            else:
                return False, f"unknown SOCKS atyp={atyp}"
            return True, "ok"
    except OSError as exc:
        return False, str(exc)


def socks5_http_204(
    proxy_host: str,
    proxy_port: int,
    timeout: float = 8.0,
) -> Tuple[bool, str]:
    """
    Fetch Google connectivity check through SOCKS5.
    Proves TCP path via VPN works (good enough tunnel heartbeat).
    """
    host = "www.gstatic.com"
    path = "/generate_204"
    try:
        with socket.create_connection((proxy_host, proxy_port), timeout=timeout) as sock:
            sock.settimeout(timeout)
            sock.sendall(b"\x05\x01\x00")
            if _recv_exact(sock, 2) != b"\x05\x00":
                return False, "SOCKS auth failed"

            host_bytes = host.encode("ascii")
            sock.sendall(
                b"\x05\x01\x00\x03"
                + bytes([len(host_bytes)])
                + host_bytes
                + struct.pack("!H", 80)
            )
            hdr = _recv_exact(sock, 4)
            if hdr[1] != 0:
                return False, f"CONNECT failed ({hdr[1]})"
            atyp = hdr[3]
            if atyp == 1:
                _recv_exact(sock, 6)
            elif atyp == 3:
                ln = _recv_exact(sock, 1)[0]
                _recv_exact(sock, ln + 2)
            elif atyp == 4:
                _recv_exact(sock, 18)

            req = (
                f"GET {path} HTTP/1.1\r\n"
                f"Host: {host}\r\n"
                f"Connection: close\r\n"
                f"User-Agent: XrayWifiShare-health\r\n"
                f"\r\n"
            ).encode("ascii")
            sock.sendall(req)
            data = sock.recv(256)
            if not data:
                return False, "empty HTTP response"
            head = data.split(b"\r\n", 1)[0].decode("latin-1", errors="replace")
            if "204" in head or "200" in head or "301" in head or "302" in head:
                return True, head
            return False, head
    except OSError as exc:
        return False, str(exc)


def _recv_exact(sock: socket.socket, n: int) -> bytes:
    buf = b""
    while len(buf) < n:
        chunk = sock.recv(n - len(buf))
        if not chunk:
            raise OSError("connection closed")
        buf += chunk
    return buf
