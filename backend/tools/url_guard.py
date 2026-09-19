"""url_guard.py — 出站抓取 SSRF 防护（P1-6）

对 web_crawl 等出站请求的 URL 做两层校验：
  1. scheme 白名单：仅允许 http/https
  2. DNS 解析后逐 IP 检查：拦截私网/环回/链路本地/保留段/云元数据地址
     （防止 `http://localhost`、`http://192.168.x.x`、`http://169.254.169.254`
       以及「公网域名解析到内网 IP」的 DNS rebinding 初级变种）

已知限制（文档化，不做过度承诺）：
  - 浏览器层重定向仍可能跳向内网（crawl4ai/Playwright 控制粒度有限），
    生产环境应配合出口防火墙 / 代理白名单使用。
  - TOCTOU：校验后到真实发起请求之间 DNS 可能变化（高级 DNS rebinding）。
    彻底方案需在代理层 pin IP，超出本次改动范围。

用法:
    from backend.tools.url_guard import assert_url_allowed
    assert_url_allowed("https://example.com")   # 通过
    assert_url_allowed("http://169.254.169.254/latest/meta-data")  # 抛 UrlBlockedError
"""
from __future__ import annotations

import ipaddress
import os
import socket
from urllib.parse import urlparse

from backend.shared.logger import logger

# scheme 白名单
ALLOWED_SCHEMES = frozenset({"http", "https"})

# 需要显式拦截的额外网段（ipaddress 判定之外）
# - 100.64.0.0/10 CGN 共享地址段（is_private 不含）
# - 0.0.0.0/8      "本网络" 段
_EXTRA_BLOCKED_V4_NETS = tuple(ipaddress.ip_network(n) for n in (
    "100.64.0.0/10",
    "0.0.0.0/8",
))

# 可信电商域名后缀白名单
# 这些公网域名的 DNS 可能被本地 VPN / 广告拦截器劫持到保留 IP 段
# （如 198.18.x.x RFC 2544 基准测试段），对可信域名跳过 IP 段检查
# （scheme 白名单仍然生效），避免误拦截合法竞品抓取请求。
_TRUSTED_SUFFIXES = tuple(
    s.strip().lower() for s in os.getenv(
        "SSRF_TRUSTED_DOMAINS",
        "taobao.com,tmall.com,jd.com,amazon.com,amazon.cn,amazon.co.jp,"
        "amazon.de,pinduoduo.com,yangkeduo.com,suning.com,books.toscrape.com"
    ).split(",") if s.strip()
)

# fake-ip 感知（默认关闭）：Clash/mihomo 类 TUN 代理的 fake-ip 模式会把**所有**
# 域名解析到 198.18.0.0/15（RFC 2544 基准测试保留段，真实公网服务绝不落在这段）。
# 本机开发开启 SSRF_FAKEIP_AWARE=true 后，对该段跳过拦截（字面私网 IP 与
# loopback/元数据地址仍照拦；真实出站流量由本地代理接管）。
_FAKEIP_V4_NET = ipaddress.ip_network("198.18.0.0/15")


def _fakeip_aware() -> bool:
    return os.getenv("SSRF_FAKEIP_AWARE", "").strip().lower() in ("1", "true", "yes", "on")


def _is_trusted_host(host: str) -> bool:
    """判断 host 是否匹配可信域名后缀（精确匹配或子域）。"""
    h = host.lower()
    for suffix in _TRUSTED_SUFFIXES:
        if h == suffix or h.endswith("." + suffix):
            return True
    return False


class UrlBlockedError(ValueError):
    """URL 被 SSRF 防护拦截。message 说明拦截原因。"""


def _is_forbidden_ip(ip: ipaddress._BaseAddress) -> bool:
    """判断 IP 是否落在禁止访问的网段。"""
    if ip.is_loopback or ip.is_link_local or ip.is_multicast or ip.is_unspecified:
        return True
    if isinstance(ip, ipaddress.IPv4Address):
        # fake-ip 感知：198.18.0.0/15 在开关开启时放行（见 _FAKEIP_V4_NET 注释）
        if ip in _FAKEIP_V4_NET and _fakeip_aware():
            return False
    if ip.is_private or ip.is_reserved:
        return True
    if isinstance(ip, ipaddress.IPv4Address):
        return any(ip in net for net in _EXTRA_BLOCKED_V4_NETS)
    return False


# 云元数据服务地址（AWS/GCP/Azure/阿里云等共用的链路本地魔法地址）。
# 它是「取实例凭据」的入口，泄露后果与「访问内网 LLM」完全不成比例，
# 故即便调用方显式放行私网也**永远拦截** —— 自建 LLM 服务不可能部署在这里。
_METADATA_IPS = frozenset({"169.254.169.254", "fd00:ec2::254"})


def _blocked_reason(ip: ipaddress._BaseAddress, *, allow_private: bool) -> str | None:
    """返回该 IP 的拦截原因；None 表示放行。

    `allow_private=True`（显式私网放行，见 `assert_url_allowed`）时跳过
    网段判定，但元数据地址仍拦。
    """
    if str(ip) in _METADATA_IPS:
        return "云元数据地址（始终禁止）"
    if allow_private:
        return None
    if _is_forbidden_ip(ip):
        return "内网/保留地址"
    return None


def _check_resolved_ips(host: str, *, allow_private: bool = False) -> None:
    """解析 host 并校验全部解析结果 IP。

    `allow_private=True` 时跳过网段检查（**只能由调用方依据显式勾选传入**，
    见 assert_url_allowed 的说明），但云元数据地址仍拦。
    """
    try:
        infos = socket.getaddrinfo(host, None)
    except socket.gaierror as e:
        raise UrlBlockedError(f"域名解析失败: {host} ({e})") from e

    seen_ips = {info[4][0] for info in infos}
    for ip_str in seen_ips:
        try:
            ip = ipaddress.ip_address(ip_str)
        except ValueError:
            raise UrlBlockedError(f"非常规地址格式: {ip_str}")
        reason = _blocked_reason(ip, allow_private=allow_private)
        if reason is not None:
            raise UrlBlockedError(f"目标 {host} 解析到{reason} {ip}，已拦截（SSRF 防护）")


def assert_url_allowed(url: str, *, allow_private: bool = False) -> str:
    """校验 URL 是否允许出站抓取。通过则原样返回，否则抛 UrlBlockedError。

    校验内容：
      1. URL 可解析、scheme 在白名单（http/https）
      2. host 为字面 IP 时直接判定网段
      3. host 为域名时 DNS 解析后逐 IP 判定

    可信域名后缀（_TRUSTED_SUFFIXES）跳过 IP 段检查，
    因其 DNS 可能被本地 VPN / 广告拦截器劫持到保留 IP 段。

    `allow_private=True` —— **显式私网放行**，供「管理端勾选『这是内网服务』」
    的 provider 实例使用（自托管 vLLM / Ollama / 内网中转站）。语义边界：

      - 只放开 **IP 网段**，不放开协议：scheme 白名单、控制字符拦截**照常生效**
      - 云元数据地址（`_METADATA_IPS`）**始终拦截**，与本参数无关
      - ⚠️ 调用方必须让本参数来自一个**显式、可审计的选择**（DB 的
        `network_scope='private'`），**绝不可**写成「解析出来是私网就自动放行」——
        那会让「公网域名解析到内网 IP」的 DNS rebinding 直接绕过防护
    """
    if not url or not isinstance(url, str):
        raise UrlBlockedError("URL 为空或类型非法")

    # 防御解析器歧义：先剥掉空白与控制字符
    url = url.strip()
    if any(ord(c) < 0x20 for c in url):
        raise UrlBlockedError("URL 含控制字符")

    try:
        parsed = urlparse(url)
    except ValueError as e:
        raise UrlBlockedError(f"URL 解析失败: {e}") from e

    if parsed.scheme.lower() not in ALLOWED_SCHEMES:
        raise UrlBlockedError(
            f"不允许的 scheme: {parsed.scheme or '(空)'}（白名单: http/https）"
        )

    host = parsed.hostname
    if not host:
        raise UrlBlockedError("URL 缺少 host")

    # 可信域名后缀跳过 IP 段检查（scheme 白名单仍然生效）
    if _is_trusted_host(host):
        return url

    # 字面 IP（含 [IPv6] 已被 urlparse 处理为 hostname）
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        ip = None

    if ip is not None:
        reason = _blocked_reason(ip, allow_private=allow_private)
        if reason is not None:
            raise UrlBlockedError(f"目标 IP {ip} 是{reason}，已拦截（SSRF 防护）")
    else:
        _check_resolved_ips(host, allow_private=allow_private)

    return url


def is_url_allowed(url: str, *, allow_private: bool = False) -> bool:
    """非抛异常版：返回 URL 是否允许（拦截时记录 warning 日志）。"""
    try:
        assert_url_allowed(url, allow_private=allow_private)
        return True
    except UrlBlockedError as e:
        logger.warning(f"[UrlGuard] 拦截出站请求: {url!r} — {e}")
        return False
