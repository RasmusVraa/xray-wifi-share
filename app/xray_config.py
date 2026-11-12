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

    return {
        "log": {"loglevel": "warning"},
        "inbounds": [
            {
                "tag": "socks-in",
                "listen": SOCKS_HOST,
                "port": SOCKS_PORT,
                "protocol": "socks",
                "settings": {
                    "auth": "noauth",
                    "udp": True,
                    "ip": SOCKS_HOST,
                },
                "sniffing": {
                    "enabled": True,
                    "destOverride": ["http", "tls", "quic"],
                    "routeOnly": True,
                },
            }
        ],
        "outbounds": [
            outbound,
            {"tag": "direct", "protocol": "freedom", **({"sendThrough": send_through} if send_through else {})},
            {"tag": "block", "protocol": "blackhole"},
        ],
        "routing": {
            "domainStrategy": "AsIs",
            "rules": [
                {
                    "type": "field",
                    "inboundTag": ["socks-in"],
                    "outboundTag": outbound.get("tag") or "proxy",
                }
            ],
        },
    }
