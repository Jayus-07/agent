"""travel/commerce/deeplink.py — Booking Deep Link 安全校验（STOP K1/K5，任务书 §十二/G18）

STOP K 只到 Deep Link，不下单。**fail-closed**：白名单未配置 = 全拒；
任何校验不过 → deep_link=None（绝不上报「不可用链接」给用户，更不拼假地址）。

清单（§十二全项）：https only；host 白名单；长度限制；禁 javascript:/data:/
file:（https-only 已涵盖，仍显式拒绝以自证）；禁 localhost/127.0.0.1/
RFC1918/链路本地；禁 credential@host；secret/token 不入日志（本模块日志
只记 host+拒绝原因，**绝不记完整 URL**——query 可能带供应商签名）。
"""
from __future__ import annotations

import ipaddress
from urllib.parse import urlsplit

from backend.shared.logger import logger

_REJECT = ""  # 校验通过时 reason 为空串


def _is_blocked_host(hostname: str) -> bool:
    """localhost / 环回 / RFC1918 / 链路本地 / 未指定地址一律拒绝。"""
    host = hostname.strip().lower()
    if not host or host in ("localhost",) or host.endswith(".localhost"):
        return True
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        return False  # 域名，交给白名单
    return (
        ip.is_loopback
        or ip.is_private
        or ip.is_link_local
        or ip.is_unspecified
        or ip.is_reserved
    )


def validate_deeplink(
    url: str | None, allowed_hosts: tuple[str, ...],
) -> tuple[bool, str]:
    """校验 Provider 提供的 booking deep link。

    Returns:
        (ok, reason)：ok=False 时 reason 为可读拒绝原因（不含 URL 本身）。
        url 为 None/空 = Provider 未提供（不是错误，reason=absent）。
    """
    if not url or not url.strip():
        return False, "absent"

    url = url.strip()

    # 危险 scheme 显式拒绝（https-only 的兜底自证；防解析器差异绕过）
    lowered = url.lower()
    for scheme in ("javascript:", "data:", "file:", "vbscript:"):
        if lowered.startswith(scheme):
            return False, f"scheme-forbidden:{scheme[:-1]}"

    if len(url) > _max_len():
        return False, "too-long"

    try:
        parts = urlsplit(url)
    except ValueError:
        return False, "unparseable"

    if parts.scheme.lower() != "https":
        return False, f"scheme:{parts.scheme or 'empty'}-not-https"
    if not parts.hostname:
        return False, "no-host"
    # credential@host（userinfo）拒绝
    if parts.username or parts.password:
        return False, "credential-in-url"
    if _is_blocked_host(parts.hostname):
        return False, f"blocked-host:{parts.hostname}"

    host_allow = {h.strip().lower() for h in allowed_hosts if h.strip()}
    if not host_allow:
        return False, "allowlist-empty"
    hostname = parts.hostname.lower()
    if hostname not in host_allow and not any(
            hostname.endswith("." + h) for h in host_allow):
        return False, f"host-not-allowed:{hostname}"

    return True, _REJECT


def _max_len() -> int:
    from backend.config.travel_commerce import TRAVEL_COMMERCE_DEEPLINK_MAX_LEN

    return TRAVEL_COMMERCE_DEEPLINK_MAX_LEN


def sanitize(url: str | None, allowed_hosts: tuple[str, ...]) -> str | None:
    """校验 → 合法原样返回 / 不合法返回 None（服务层唯一入口）。"""
    ok, reason = validate_deeplink(url, allowed_hosts)
    if ok:
        return url
    if url:
        # 只记 host 与原因；完整 URL（可能含签名 query）绝不入日志
        host = ""
        try:
            host = urlsplit(url.strip()).hostname or ""
        except ValueError:
            pass
        logger.debug("[CommerceDeepLink] 拒绝链接 host=%s reason=%s", host, reason)
    return None
