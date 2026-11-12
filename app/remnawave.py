from __future__ import annotations

import base64
import json
import re
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from .hwid import device_hwid
from .links import ParsedLink, parse_proxy_input


def _http_get(url: str, headers: dict[str, str] | None = None) -> tuple[str, str, int]:
    hdrs = {
        "User-Agent": "v2rayN/6.40",
        "Accept": "*/*",
        "x-hwid": device_hwid(),
        "x-device-os": "Windows",
        "x-ver-os": "10",
        "x-device-model": "XrayWifiShare",
    }
    if headers:
        hdrs.update(headers)
    req = Request(url, headers=hdrs)
    with urlopen(req, timeout=45) as resp:
        ctype = (resp.headers.get("Content-Type") or "").lower()
        status = getattr(resp, "status", 200) or 200
        raw = resp.read()
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        text = raw.decode("latin-1", errors="replace")
    return text, ctype, int(status)


def _candidate_urls(url: str) -> list[str]:
    base = url.rstrip("/")
    out = [base]
    # Remnawave optional JSON path
    if not base.lower().endswith(("/json", "/info")):
        out.append(base + "/json")
    return out


def _fetch_subscription(url: str) -> str:
    """Download subscription body. Prefer Happ JSON, then classic Base64."""
    attempts: list[dict[str, str]] = [
        {
            "User-Agent": "Happ/3.5.0",
            "Accept": "application/json",
        },
        {
            "User-Agent": "v2rayN/6.40",
            "Accept": "text/plain, */*",
        },
        {
            "User-Agent": "v2rayNG/1.8.19",
            "Accept": "*/*",
        },
        {
            "User-Agent": "XrayWifiShare/1.1",
            "Accept": "*/*",
        },
    ]
    last_err: Exception | None = None
    bodies: list[tuple[str, str]] = []

    for candidate in _candidate_urls(url):
        for hdrs in attempts:
            try:
                body, ctype, status = _http_get(candidate, hdrs)
            except HTTPError as exc:
                last_err = exc
                continue
            except URLError as exc:
                last_err = exc
                continue
            except Exception as exc:
                last_err = exc
                continue
            body = (body or "").strip()
            if not body or status >= 400:
                continue
            # Skip HTML subscription page
            low = body[:200].lower()
            if "<html" in low or "<!doctype html" in low:
                continue
            bodies.append((body, ctype))
            if body.startswith("{") or body.startswith("["):
                return body
            if "json" in ctype:
                return body

    if bodies:
        return bodies[0][0]
    if last_err:
        raise ValueError(f"Не удалось скачать подписку: {last_err}")
    raise ValueError("Подписка пустая или вернула HTML/403 — вставь Raw JSON")


def _looks_like_url(text: str) -> bool:
    t = text.strip().lower()
    return t.startswith("http://") or t.startswith("https://")


def _decode_maybe_base64(text: str) -> str:
    s = "".join(text.strip().split())
    if not s or s.startswith("{") or s.startswith("[") or "://" in s[:48]:
        return text
    try:
        pad = "=" * (-len(s) % 4)
        decoded = base64.b64decode(s + pad)
        out = decoded.decode("utf-8", errors="replace")
        if "://" in out or out.lstrip().startswith("{") or out.lstrip().startswith("["):
            return out
    except Exception:
        pass
    try:
        pad = "=" * (-len(s) % 4)
        decoded = base64.urlsafe_b64decode(s + pad)
        out = decoded.decode("utf-8", errors="replace")
        if "://" in out or out.lstrip().startswith("{") or out.lstrip().startswith("["):
            return out
    except Exception:
        pass
    return text


def parse_servers(text: str) -> list[ParsedLink]:
    """Parse single link, multi-link list, Remnawave JSON, or subscription URL body."""
    raw = (text or "").strip()
    if not raw:
        raise ValueError("Вставь ссылку, URL подписки или JSON")

    if _looks_like_url(raw) and "\n" not in raw and not raw.startswith("{") and not raw.startswith("["):
        body = _fetch_subscription(raw)
        return parse_servers(body)

    # Remnawave / Happ raw JSON
    if raw.startswith("{") or raw.startswith("["):
        data = json.loads(raw)
        servers = _from_remnawave_json(data)
        if servers:
            return servers
        raise ValueError("JSON распознан, но серверов не найдено")

    # classic subscription body (base64 or plain links)
    decoded = _decode_maybe_base64(raw)
    links: list[ParsedLink] = []
    for line in re.split(r"[\r\n]+", decoded):
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        lower = line.lower()
        if lower.startswith(("vless://", "hysteria2://", "hy2://", "ss://", "trojan://")):
            try:
                links.append(parse_proxy_input(line))
            except Exception:
                continue
    if links:
        return links

    # single share link
    return [parse_proxy_input(raw)]


def _is_xray_config(obj: dict[str, Any]) -> bool:
    return isinstance(obj.get("outbounds"), list) and (
        "inbounds" in obj or "routing" in obj or "remarks" in obj or "dns" in obj
    )


def _from_xray_config(cfg: dict[str, Any]) -> ParsedLink | None:
    remark = str(cfg.get("remarks") or cfg.get("remark") or "")
    outbounds = cfg.get("outbounds") or []
    for ob in outbounds:
        if not isinstance(ob, dict):
            continue
        proto = str(ob.get("protocol") or "")
        if proto in ("freedom", "blackhole", "dns", "loopback", ""):
            continue
        host, port = host_port_from_outbound(ob)
        if not host:
            continue
        kind = "hysteria2" if proto == "hysteria" else proto
        tag = str(ob.get("tag") or kind)
        return ParsedLink(
            kind=kind,
            outbound=ob,
            remark=remark or tag,
            server_host=host,
            server_port=port,
        )
    return None


def _from_remnawave_json(data: Any) -> list[ParsedLink]:
    # Happ / XRAY_JSON: array of full configs
    if isinstance(data, list):
        if data and isinstance(data[0], dict) and _is_xray_config(data[0]):
            out: list[ParsedLink] = []
            for cfg in data:
                if not isinstance(cfg, dict):
                    continue
                parsed = _from_xray_config(cfg)
                if parsed:
                    out.append(parsed)
            return out
        items = data
    elif isinstance(data, dict):
        if _is_xray_config(data) and not data.get("resolvedProxyConfigs"):
            parsed = _from_xray_config(data)
            return [parsed] if parsed else []
        if isinstance(data.get("resolvedProxyConfigs"), list):
            items = data["resolvedProxyConfigs"]
        elif isinstance(data.get("proxies"), list):
            items = data["proxies"]
        elif isinstance(data.get("servers"), list):
            items = data["servers"]
        else:
            items = []
    else:
        items = []

    ranked: list[tuple[int, int, ParsedLink]] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        # skip disabled / hidden
        meta = item.get("metadata") if isinstance(item.get("metadata"), dict) else {}
        if meta.get("isDisabled") is True:
            continue
        if meta.get("isHidden") is True:
            continue
        # nested xray config?
        if _is_xray_config(item):
            parsed = _from_xray_config(item)
            if parsed:
                ranked.append((int(meta.get("viewPosition") or 10_000), 10, parsed))
            continue
        try:
            parsed = _convert_resolved_proxy(item)
        except Exception:
            continue
        if not parsed:
            continue
        raw_pos = meta.get("viewPosition")
        view_pos = int(raw_pos) if raw_pos is not None else 10_000
        prefer = prefer_sort_key(parsed)[0]
        ranked.append((view_pos, prefer, parsed))
    ranked.sort(key=lambda x: (x[0], x[1], x[2].remark))
    return [p for _, _, p in ranked]


def _convert_resolved_proxy(item: dict[str, Any]) -> ParsedLink | None:
    protocol = str(item.get("protocol") or "").lower()
    address = str(item.get("address") or "")
    port = int(item.get("port") or 0)
    remark = str(item.get("finalRemark") or meta_remark(item) or f"{address}:{port}")
    if not address or not port or not protocol:
        return None

    if protocol == "vless":
        outbound = _vless_outbound(item, address, port, remark)
    elif protocol in ("hysteria", "hysteria2"):
        outbound = _hysteria_outbound(item, address, port, remark)
    elif protocol in ("shadowsocks", "ss"):
        outbound = _ss_outbound(item, address, port, remark)
    elif protocol == "trojan":
        outbound = _trojan_outbound(item, address, port, remark)
    else:
        return None

    return ParsedLink(
        kind="hysteria2" if protocol.startswith("hysteria") else protocol,
        outbound=outbound,
        remark=remark,
        server_host=address,
        server_port=port,
    )


def meta_remark(item: dict[str, Any]) -> str:
    meta = item.get("metadata")
    if isinstance(meta, dict) and meta.get("remark"):
        return str(meta["remark"])
    return ""


def _transport_name(transport: str) -> str:
    t = (transport or "tcp").lower()
    if t in ("raw", "tcp"):
        return "tcp"
    return t


def _apply_stream_overrides(stream: dict[str, Any], item: dict[str, Any]) -> None:
    overrides = item.get("streamOverrides") if isinstance(item.get("streamOverrides"), dict) else {}
    final_mask = overrides.get("finalMask") or overrides.get("finalmask")
    if final_mask:
        stream["finalmask"] = final_mask
    sockopt = overrides.get("sockopt")
    if sockopt:
        stream["sockopt"] = sockopt


def _vless_outbound(item: dict[str, Any], address: str, port: int, remark: str) -> dict[str, Any]:
    opts = item.get("protocolOptions") if isinstance(item.get("protocolOptions"), dict) else {}
    transport = _transport_name(str(item.get("transport") or "tcp"))
    security = str(item.get("security") or "none")
    t_opts = item.get("transportOptions") if isinstance(item.get("transportOptions"), dict) else {}
    s_opts = item.get("securityOptions") if isinstance(item.get("securityOptions"), dict) else {}

    user: dict[str, Any] = {
        "id": str(opts.get("id") or ""),
        "encryption": str(opts.get("encryption") or "none"),
    }
    flow = opts.get("flow") or ""
    if flow:
        user["flow"] = flow

    stream: dict[str, Any] = {"network": transport, "security": security}

    if transport == "xhttp":
        xhttp = {
            "path": t_opts.get("path") or "/",
            "host": t_opts.get("host") or address,
            "mode": t_opts.get("mode") or "auto",
        }
        if t_opts.get("extra"):
            xhttp["extra"] = t_opts["extra"]
        stream["xhttpSettings"] = xhttp
    elif transport == "ws":
        stream["wsSettings"] = {
            "path": t_opts.get("path") or "/",
            "headers": {"Host": t_opts.get("host") or address},
        }
    elif transport == "grpc":
        stream["grpcSettings"] = {
            "serviceName": t_opts.get("serviceName") or t_opts.get("path") or "",
        }
    elif transport == "tcp" and t_opts.get("header"):
        stream["tcpSettings"] = {"header": t_opts["header"]}

    if security == "reality":
        stream["realitySettings"] = {
            "serverName": s_opts.get("serverName") or address,
            "fingerprint": s_opts.get("fingerprint") or "chrome",
            "publicKey": s_opts.get("publicKey") or "",
            "shortId": s_opts.get("shortId") or "",
            "spiderX": s_opts.get("spiderX") or "",
        }
    elif security == "tls":
        tls: dict[str, Any] = {
            "serverName": s_opts.get("serverName") or address,
            "fingerprint": s_opts.get("fingerprint") or "chrome",
        }
        alpn = s_opts.get("alpn")
        if alpn:
            tls["alpn"] = [x.strip() for x in str(alpn).split(",") if x.strip()]
        stream["tlsSettings"] = tls

    _apply_stream_overrides(stream, item)

    return {
        "tag": "proxy",
        "protocol": "vless",
        "settings": {"vnext": [{"address": address, "port": port, "users": [user]}]},
        "streamSettings": stream,
    }


def _hysteria_outbound(item: dict[str, Any], address: str, port: int, remark: str) -> dict[str, Any]:
    t_opts = item.get("transportOptions") if isinstance(item.get("transportOptions"), dict) else {}
    s_opts = item.get("securityOptions") if isinstance(item.get("securityOptions"), dict) else {}
    auth = str(t_opts.get("auth") or "")
    version = int(t_opts.get("version") or 2)

    stream: dict[str, Any] = {
        "network": "hysteria",
        "security": "tls",
        "tlsSettings": {
            "serverName": s_opts.get("serverName") or address,
            "alpn": [x.strip() for x in str(s_opts.get("alpn") or "h3").split(",") if x.strip()],
            "fingerprint": s_opts.get("fingerprint") or "chrome",
        },
        "hysteriaSettings": {
            "version": version,
            "auth": auth,
            "udpIdleTimeout": 120,
        },
    }
    _apply_stream_overrides(stream, item)

    return {
        "tag": "proxy",
        "protocol": "hysteria",
        "settings": {"version": version, "address": address, "port": port},
        "streamSettings": stream,
    }


def _ss_outbound(item: dict[str, Any], address: str, port: int, remark: str) -> dict[str, Any]:
    opts = item.get("protocolOptions") if isinstance(item.get("protocolOptions"), dict) else {}
    method = str(opts.get("method") or "chacha20-ietf-poly1305")
    password = str(opts.get("password") or "")
    return {
        "tag": "proxy",
        "protocol": "shadowsocks",
        "settings": {
            "servers": [
                {
                    "address": address,
                    "port": port,
                    "method": method,
                    "password": password,
                }
            ]
        },
    }


def _trojan_outbound(item: dict[str, Any], address: str, port: int, remark: str) -> dict[str, Any]:
    opts = item.get("protocolOptions") if isinstance(item.get("protocolOptions"), dict) else {}
    s_opts = item.get("securityOptions") if isinstance(item.get("securityOptions"), dict) else {}
    password = str(opts.get("password") or "")
    stream: dict[str, Any] = {
        "network": _transport_name(str(item.get("transport") or "tcp")),
        "security": str(item.get("security") or "tls"),
    }
    if stream["security"] == "tls":
        stream["tlsSettings"] = {
            "serverName": s_opts.get("serverName") or address,
            "fingerprint": s_opts.get("fingerprint") or "chrome",
        }
    _apply_stream_overrides(stream, item)
    return {
        "tag": "proxy",
        "protocol": "trojan",
        "settings": {"servers": [{"address": address, "port": port, "password": password}]},
        "streamSettings": stream,
    }


def host_port_from_outbound(ob: dict[str, Any]) -> tuple[str, int]:
    settings = ob.get("settings") or {}
    if "vnext" in settings and settings["vnext"]:
        node = settings["vnext"][0]
        return str(node.get("address") or ""), int(node.get("port") or 0)
    if "servers" in settings and settings["servers"]:
        node = settings["servers"][0]
        return str(node.get("address") or ""), int(node.get("port") or 0)
    if settings.get("address"):
        return str(settings.get("address") or ""), int(settings.get("port") or 0)
    return "", 0


def prefer_sort_key(item: ParsedLink) -> tuple[int, str]:
    remark = item.remark.lower()
    kind = item.kind.lower()
    score = 50
    if "своя" in remark or "custom" in remark:
        score = 0
    elif kind in ("vless", "hysteria2", "hysteria"):
        score = 10
    elif "lte" in remark or "авто" in remark:
        score = 80
    elif kind in ("shadowsocks", "ss"):
        score = 90
    return (score, item.remark)
