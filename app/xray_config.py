from __future__ import annotations

import copy
from typing import Any

from .links import ParsedLink


SOCKS_HOST = "127.0.0.1"
SOCKS_PORT = 17890


def build_xray_config(parsed: ParsedLink, send_through: str | None = None) -> dict[str, Any]:
    outbound = copy.deepcopy(parsed.outbound)
    if not outbound.get("tag"):
        outbound["tag"] = "proxy"
    if send_through:
        # Bind dial-out to physical NIC IP so traffic does not enter Happ TUN by mistake
        outbound["sendThrough"] = send_through

    # Keepalive helps long-lived Discord WS / PS5 sessions
    stream = outbound.get("streamSettings")
    if not isinstance(stream, dict):
        stream = {}
        outbound["streamSettings"] = stream
    sockopt = stream.get("sockopt")
    if not isinstance(sockopt, dict):
        sockopt = {}
        stream["sockopt"] = sockopt
    sockopt.setdefault("tcpKeepAliveInterval", 30)
    sockopt.setdefault("tcpKeepAliveIdle", 30)

    direct: dict[str, Any] = {"tag": "direct", "protocol": "freedom"}
    if send_through:
        direct["sendThrough"] = send_through

    return {
        "log": {"loglevel": "warning"},
        # DNS for sniffed domains — Discord / PS5 need reliable resolution through tunnel
        "dns": {
            "servers": [
                "1.1.1.1",
                "8.8.8.8",
                "localhost",
            ],
            "queryStrategy": "UseIPv4",
        },
        "inbounds": [
            {
                "tag": "socks-in",
                "listen": SOCKS_HOST,
                "port": SOCKS_PORT,
                "protocol": "socks",
                "settings": {
                    "auth": "noauth",
                    "udp": True,
                    # Advertise reachable UDP relay address for SOCKS5 ASSOCIATE
                    "ip": SOCKS_HOST,
                },
                "sniffing": {
                    "enabled": True,
                    # Do NOT sniff quic — breaks Discord voice / activity on consoles
                    "destOverride": ["http", "tls"],
                    "routeOnly": True,
                },
            }
        ],
        "outbounds": [
            outbound,
            direct,
            {"tag": "block", "protocol": "blackhole"},
            {
                "tag": "dns-out",
                "protocol": "dns",
            },
        ],
        "routing": {
            "domainStrategy": "AsIs",
            "rules": [
                {
                    "type": "field",
                    "inboundTag": ["socks-in"],
                    "port": 53,
                    "network": "udp",
                    "outboundTag": "dns-out",
                },
                {
                    "type": "field",
                    "inboundTag": ["socks-in"],
                    "outboundTag": outbound.get("tag") or "proxy",
                },
            ],
        },
    }
