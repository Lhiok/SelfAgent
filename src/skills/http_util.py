"""HTTP 类 Skill 共用：超时截断与基础 SSRF 防护。"""

from __future__ import annotations

import ipaddress
import socket
from urllib.parse import urlparse


def truncate(text: str, max_chars: int) -> str:
    if max_chars <= 0 or len(text) <= max_chars:
        return text
    return text[: max_chars - 1].rstrip() + "…"


def host_is_private(host: str) -> bool:
    """解析主机名；若指向私网/环回/链路本地则视为不安全。"""
    name = (host or "").strip().lower().strip("[]")
    if not name:
        return True
    if name in {"localhost", "localhost.localdomain"}:
        return True
    try:
        ip = ipaddress.ip_address(name)
        return bool(
            ip.is_private
            or ip.is_loopback
            or ip.is_link_local
            or ip.is_reserved
            or ip.is_multicast
        )
    except ValueError:
        pass
    try:
        infos = socket.getaddrinfo(name, None)
    except OSError:
        return True
    for info in infos:
        addr = info[4][0]
        try:
            ip = ipaddress.ip_address(addr)
        except ValueError:
            continue
        if (
            ip.is_private
            or ip.is_loopback
            or ip.is_link_local
            or ip.is_reserved
            or ip.is_multicast
        ):
            return True
    return False


def validate_http_url(url: str, *, allow_private: bool = False) -> str | None:
    """合法则返回 None，否则返回错误信息。"""
    text = (url or "").strip()
    if not text:
        return "url 不能为空"
    parsed = urlparse(text)
    if parsed.scheme not in {"http", "https"}:
        return "仅允许 http/https"
    if not parsed.netloc:
        return "url 缺少主机名"
    host = parsed.hostname or ""
    if not allow_private and host_is_private(host):
        return f"禁止访问内网/本机地址: {host}"
    return None
