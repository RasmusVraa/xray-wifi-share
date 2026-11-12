from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any
from urllib.parse import parse_qs, unquote, urlparse


@dataclass
class ParsedLink:
    kind: str  # vless | hysteria2
    outbound: dict[str, Any]
    remark: str = ""
    server_host: str = ""
    server_port: int = 0


def _server_from_outbound(ob: dict[str, Any]) -> tuple[str, int]:
    settings = ob.get("settings") or {}
    if "vnext" in settings and settings["vnext"]:
        node = settings["vnext"][0]
        return str(node.get("address") or ""), int(node.get("port") or 0)
    if settings.get("address"):
        return str(settings.get("address") or ""), int(settings.get("port") or 0)
    return "", 0


def parse_proxy_input(text: str) -> ParsedLink:
    raw = (text or "").strip()
    if not raw:
        raise ValueError("Вставь VLESS или Hysteria2 ссылку")

    if raw.startswith("{"):
        data = json.loads(raw)
        if "outbounds" in data and isinstance(data["outbounds"], list) and data["outbounds"]:
            ob = data["outbounds"][0]
        else:
            ob = data
        kind = str(ob.get("protocol", "custom"))
        host, port = _server_from_outbound(ob)
        return ParsedLink(
            kind=kind,
            outbound=ob,
            remark=str(ob.get("tag") or "json"),
            server_host=host,
            server_port=port,
        )

    # allow multi-line share lists — take first supported uri
    for line in re.split(r"[\s]+", raw):
        line = line.strip()
        if not line:
            continue
        lower = line.lower()
        if lower.startswith("vless://"):
            return _parse_vless(line)
        if lower.startswith("hysteria2://") or lower.startswith("hy2://"):
            return _parse_hysteria2(line)

    raise ValueError("Нужна ссылка vless:// или hysteria2:// (hy2://)")


def _parse_vless(uri: str) -> ParsedLink:
    parsed = urlparse(uri)
    uuid = unquote(parsed.username or "")
    host = parsed.hostname
    port = parsed.port
    if not uuid or not host or not port:
        raise ValueError("Некорректная VLESS ссылка")

    q = {k: v[0] for k, v in parse_qs(parsed.query).items()}
    remark = unquote(parsed.fragment or "") or f"{host}:{port}"

    network = q.get("type") or q.get("network") or "tcp"
    security = q.get("security") or "none"
    flow = q.get("flow") or ""
    encryption = q.get("encryption") or "none"

    stream: dict[str, Any] = {"network": network, "security": security}

    if security in ("reality", "tls"):
        tls_key = "realitySettings" if security == "reality" else "tlsSettings"
        tls: dict[str, Any] = {
            "serverName": q.get("sni") or q.get("host") or host,
            "fingerprint": q.get("fp") or "chrome",
        }
        if security == "reality":
            pbk = q.get("pbk") or ""
            if not pbk:
                raise ValueError("VLESS Reality: нет pbk")
            tls["publicKey"] = pbk
            tls["shortId"] = q.get("sid") or ""
            if q.get("spx"):
                tls["spiderX"] = q.get("spx")
        elif q.get("alpn"):
            tls["alpn"] = [x.strip() for x in q["alpn"].split(",") if x.strip()]
        if q.get("insecure") in ("1", "true", "True"):
            tls["allowInsecure"] = True
        stream[tls_key] = tls

    if network == "ws":
        stream["wsSettings"] = {
            "path": q.get("path") or "/",
            "headers": {"Host": q.get("host") or q.get("sni") or host},
        }
    elif network == "grpc":
        stream["grpcSettings"] = {
            "serviceName": q.get("serviceName") or q.get("path") or "",
            "multiMode": q.get("mode") == "multi",
        }
    elif network == "tcp" and (q.get("headerType") == "http" or q.get("host")):
        stream["tcpSettings"] = {
            "header": {
                "type": q.get("headerType") or "none",
                "request": {
                    "headers": {
                        "Host": [q.get("host") or q.get("sni") or host],
                    }
                },
            }
        }

    user: dict[str, Any] = {"id": uuid, "encryption": encryption}
    if flow:
        user["flow"] = flow

    outbound = {
        "tag": "proxy",
        "protocol": "vless",
        "settings": {
            "vnext": [
                {
                    "address": host,
                    "port": int(port),
                    "users": [user],
                }
            ]
        },
        "streamSettings": stream,
    }
    return ParsedLink(
        kind="vless",
        outbound=outbound,
        remark=remark,
        server_host=host,
        server_port=int(port),
    )


def _parse_hysteria2(uri: str) -> ParsedLink:
    # hysteria2://password@host:port?sni=...&insecure=0&obfs=salamander&obfs-password=...
    normalized = uri
    if normalized.lower().startswith("hy2://"):
        normalized = "hysteria2://" + normalized[6:]

    parsed = urlparse(normalized)
    password = unquote(parsed.username or "")
    host = parsed.hostname
    port = parsed.port
    if not password or not host or not port:
        raise ValueError("Некорректная Hysteria2 ссылка")

    q = {k: v[0] for k, v in parse_qs(parsed.query).items()}
    remark = unquote(parsed.fragment or "") or f"{host}:{port}"
    sni = q.get("sni") or q.get("peer") or host
    insecure = q.get("insecure") in ("1", "true", "True")

    hysteria_settings: dict[str, Any] = {
        "version": 2,
        "auth": password,
        "udpIdleTimeout": 120,
    }

    finalmask: dict[str, Any] | None = None
    obfs = (q.get("obfs") or "").lower()
    obfs_password = q.get("obfs-password") or q.get("obfsPassword") or ""
    if obfs == "salamander" and obfs_password:
        finalmask = {
            "udp": [
                {
                    "type": "salamander",
                    "settings": {"password": obfs_password},
                }
            ]
        }

    stream: dict[str, Any] = {
        "network": "hysteria",
        "security": "tls",
        "tlsSettings": {
            "serverName": sni,
            "alpn": ["h3"],
            "allowInsecure": insecure,
        },
        "hysteriaSettings": hysteria_settings,
    }
    if finalmask:
        stream["finalmask"] = finalmask

    # optional brutal bandwidth hints from query
    up = q.get("up") or q.get("upmbps")
    down = q.get("down") or q.get("downmbps")
    if up or down:
        stream.setdefault("finalmask", {})
        qp = stream["finalmask"].setdefault("quicParams", {})
        if up:
            qp["brutalUp"] = up if str(up).endswith("bps") else f"{up}mbps"
        if down:
            qp["brutalDown"] = down if str(down).endswith("bps") else f"{down}mbps"

    outbound = {
        "tag": "proxy",
        "protocol": "hysteria",
        "settings": {
            "version": 2,
            "address": host,
            "port": int(port),
        },
        "streamSettings": stream,
    }
    return ParsedLink(
        kind="hysteria2",
        outbound=outbound,
        remark=remark,
        server_host=host,
        server_port=int(port),
    )
