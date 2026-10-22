"""test_url_guard.py — SSRF 防护单测（P1-6）"""
import socket
from unittest.mock import patch

import pytest

from backend.tools.url_guard import (
    UrlBlockedError, assert_url_allowed, is_url_allowed,
    _is_forbidden_ip, _is_trusted_host,
)


class TestForbiddenIps:
    def test_loopback_blocked(self):
        assert _is_forbidden_ip(_ip("127.0.0.1"))
        assert _is_forbidden_ip(_ip("::1"))

    def test_private_blocked(self):
        for ip in ("10.0.0.1", "192.168.1.1", "172.16.0.1", "172.31.255.255",
                   "fd00::1"):
            assert _is_forbidden_ip(_ip(ip)), ip

    def test_cloud_metadata_blocked(self):
        # AWS / GCP / Azure 元数据地址
        assert _is_forbidden_ip(_ip("169.254.169.254"))
        assert _is_forbidden_ip(_ip("metadata.google.internal".replace("metadata.google.internal", "169.254.169.254")))

    def test_cgn_and_zero_blocked(self):
        assert _is_forbidden_ip(_ip("100.64.0.1"))
        assert _is_forbidden_ip(_ip("0.0.0.1"))

    def test_public_allowed(self):
        for ip in ("8.8.8.8", "1.1.1.1", "93.184.216.34", "2606:4700::1111"):
            assert not _is_forbidden_ip(_ip(ip)), ip


def _ip(s):
    import ipaddress
    return ipaddress.ip_address(s)


class TestAssertUrl:
    def test_scheme_whitelist(self):
        for url in ("file:///etc/passwd", "ftp://x.com", "gopher://x.com",
                    "javascript:alert(1)", "data:text/html,x"):
            with pytest.raises(UrlBlockedError, match="scheme"):
                assert_url_allowed(url)

    def test_http_https_allowed_literal_public_ip(self):
        assert assert_url_allowed("http://8.8.8.8/") == "http://8.8.8.8/"
        assert assert_url_allowed("https://93.184.216.34/path?q=1")

    def test_literal_private_ip_blocked(self):
        for url in ("http://127.0.0.1:8000/api", "https://192.168.1.1/admin",
                    "http://10.0.0.5", "http://169.254.169.254/latest/meta-data",
                    "http://[::1]:8080/", "http://[fd00::5]/"):
            with pytest.raises(UrlBlockedError):
                assert_url_allowed(url)

    def test_domain_resolving_to_private_blocked(self):
        # 域名解析到内网 IP（DNS rebinding 初级变种）
        fake = [("AF_INET", 1, 6, "", ("10.1.2.3", 0))]
        with patch.object(socket, "getaddrinfo", return_value=fake):
            with pytest.raises(UrlBlockedError, match="内网/保留地址"):
                assert_url_allowed("http://internal.example.com/secret")

    def test_domain_resolving_to_public_allowed(self):
        fake = [("AF_INET", 1, 6, "", ("93.184.216.34", 0))]
        with patch.object(socket, "getaddrinfo", return_value=fake):
            assert assert_url_allowed("https://example.com/") == "https://example.com/"

    def test_domain_mixed_resolution_blocked(self):
        # 多个 A 记录中只要有一个内网 IP 即拦截
        fake = [("AF_INET", 1, 6, "", ("93.184.216.34", 0)),
                ("AF_INET", 1, 6, "", ("192.168.0.1", 0))]
        with patch.object(socket, "getaddrinfo", return_value=fake):
            with pytest.raises(UrlBlockedError):
                assert_url_allowed("https://mixed.example.com/")

    def test_empty_and_control_chars(self):
        with pytest.raises(UrlBlockedError):
            assert_url_allowed("")
        with pytest.raises(UrlBlockedError):
            assert_url_allowed("https://ok.com/\r\nX-Injected: 1")

    def test_is_url_allowed_no_raise(self):
        fake = [("AF_INET", 1, 6, "", ("93.184.216.34", 0))]
        with patch.object(socket, "getaddrinfo", return_value=fake):
            assert is_url_allowed("https://example.com") is True
        assert is_url_allowed("http://127.0.0.1/x") is False

    # ── 可信域名白名单旁路 ───────────────────────────

    def test_trusted_domain_bypasses_dns_hijack(self):
        """可信电商域名 DNS 被劫持到保留 IP 段时不应拦截"""
        # 模拟 taobao.com 被本地 VPN/广告拦截器劫持到 198.18.x.x
        fake = [("AF_INET", 1, 6, "", ("198.18.4.53", 0))]
        with patch.object(socket, "getaddrinfo", return_value=fake):
            assert assert_url_allowed(
                "https://item.taobao.com/item.htm?id=815673415507"
            ) == "https://item.taobao.com/item.htm?id=815673415507"

    def test_trusted_domain_still_checks_scheme(self):
        """可信域名仍校验 scheme 白名单"""
        with pytest.raises(UrlBlockedError, match="scheme"):
            assert_url_allowed("file://item.taobao.com/secret")

    def test_strict_check_does_not_bypass_trusted_host_dns(self):
        fake = [("AF_INET", 1, 6, "", ("10.1.2.3", 0))]
        with patch.object(socket, "getaddrinfo", return_value=fake):
            with pytest.raises(UrlBlockedError, match="内网/保留地址"):
                assert_url_allowed(
                    "https://item.taobao.com/", allow_trusted_hosts=False,
                )

    def test_trusted_suffix_matching(self):
        assert _is_trusted_host("taobao.com")
        assert _is_trusted_host("item.taobao.com")
        assert _is_trusted_host("sub.item.jd.com")
        assert not _is_trusted_host("evil-taobao.com")
        assert not _is_trusted_host("taobao.com.evil.com")
        assert not _is_trusted_host("evil.example.com")


class TestFakeIpAware:
    """fake-ip 感知开关（SSRF_FAKEIP_AWARE）：Clash TUN 环境全域名解析到 198.18.0.0/15"""

    def test_fakeip_blocked_by_default(self, monkeypatch):
        monkeypatch.delenv("SSRF_FAKEIP_AWARE", raising=False)
        assert _is_forbidden_ip(_ip("198.18.1.144"))
        fake = [("AF_INET", 1, 6, "", ("198.18.1.144", 0))]
        with patch.object(socket, "getaddrinfo", return_value=fake):
            with pytest.raises(UrlBlockedError, match="SSRF"):
                assert_url_allowed("https://zh.wikipedia.org/wiki/Main_Page")

    def test_fakeip_allowed_when_aware(self, monkeypatch):
        monkeypatch.setenv("SSRF_FAKEIP_AWARE", "true")
        assert not _is_forbidden_ip(_ip("198.18.1.144"))
        assert not _is_forbidden_ip(_ip("198.19.255.255"))
        fake = [("AF_INET", 1, 6, "", ("198.18.1.144", 0))]
        with patch.object(socket, "getaddrinfo", return_value=fake):
            assert assert_url_allowed("https://zh.wikipedia.org/wiki/Main_Page") == \
                "https://zh.wikipedia.org/wiki/Main_Page"

    def test_fakeip_v6_blocked_by_default(self, monkeypatch):
        """TUN 开 IPv6 后 AAAA 返回 2001:2::/48（mihomo IPv6 fake-ip 池），默认照拦"""
        monkeypatch.delenv("SSRF_FAKEIP_AWARE", raising=False)
        assert _is_forbidden_ip(_ip("2001:2::60"))
        # AAAA fake-ip（复现 2026-10-10 事故：volces.com 解析出 2001:2::60 被拦）
        fake = [
            ("AF_INET", 1, 6, "", ("198.18.0.97", 0)),
            ("AF_INET6", 1, 6, "", ("2001:2::60", 443, 0, 0)),
        ]
        with patch.object(socket, "getaddrinfo", return_value=fake):
            with pytest.raises(UrlBlockedError, match="SSRF"):
                assert_url_allowed("https://ark.cn-beijing.volces.com/api/v3")

    def test_fakeip_v6_allowed_when_aware(self, monkeypatch):
        monkeypatch.setenv("SSRF_FAKEIP_AWARE", "true")
        assert not _is_forbidden_ip(_ip("2001:2::60"))
        assert not _is_forbidden_ip(_ip("2001:2::ffff:ffff:ffff:ffff"))
        # v4+v6 fake-ip 混合解析（getaddrinfo 真实返回形态）整体放行
        fake = [
            ("AF_INET", 1, 6, "", ("198.18.0.97", 0)),
            ("AF_INET6", 1, 6, "", ("2001:2::60", 443, 0, 0)),
        ]
        with patch.object(socket, "getaddrinfo", return_value=fake):
            assert assert_url_allowed("https://ark.cn-beijing.volces.com/api/v3") == \
                "https://ark.cn-beijing.volces.com/api/v3"

    def test_fakeip_aware_does_not_overallow_v6(self, monkeypatch):
        """开关只放行两个 fake-ip 池，真实 v6 内网/保留段照拦"""
        monkeypatch.setenv("SSRF_FAKEIP_AWARE", "true")
        assert _is_forbidden_ip(_ip("fd00::1"))
        assert _is_forbidden_ip(_ip("fe80::1"))
        assert _is_forbidden_ip(_ip("2001:db8::1"))

    def test_fakeip_aware_still_blocks_private_and_loopback(self, monkeypatch):
        """开关只放行 fake-ip 段，真实内网/环照拦"""
        monkeypatch.setenv("SSRF_FAKEIP_AWARE", "true")
        assert _is_forbidden_ip(_ip("127.0.0.1"))
        assert _is_forbidden_ip(_ip("192.168.1.1"))
        assert _is_forbidden_ip(_ip("10.0.0.5"))
        assert _is_forbidden_ip(_ip("169.254.169.254"))
        with pytest.raises(UrlBlockedError):
            assert_url_allowed("http://192.168.1.1/admin")


class _FakeResponse:
    def __init__(self, status: int, headers: dict[str, str] | None = None):
        self.status = status
        self.headers = headers or {}
        self.disposed = False

    async def dispose(self):
        self.disposed = True


class _FakeRequest:
    def __init__(self, url: str, *, navigation: bool = False):
        self.url = url
        self.method = "GET"
        self.post_data_buffer = None
        self._navigation = navigation

    async def all_headers(self):
        return {
            "authorization": "Bearer secret",
            "cookie": "session=secret",
        }

    def is_navigation_request(self):
        return self._navigation


class _FakeRoute:
    def __init__(self, url: str, responses: list[_FakeResponse], *, navigation=False):
        self.request = _FakeRequest(url, navigation=navigation)
        self.responses = list(responses)
        self.fetch_calls: list[dict] = []
        self.aborted = None
        self.fulfilled = None

    async def fetch(self, **kwargs):
        self.fetch_calls.append(kwargs)
        return self.responses.pop(0)

    async def abort(self, *, error_code: str):
        self.aborted = error_code

    async def fulfill(self, *, response):
        self.fulfilled = response


@pytest.mark.anyio
async def test_crawler_redirect_to_private_ip_is_blocked(monkeypatch):
    from backend.tools import crawler_runtime

    fake_dns = [("AF_INET", 1, 6, "", ("93.184.216.34", 0))]
    monkeypatch.setattr(socket, "getaddrinfo", lambda *args, **kwargs: fake_dns)
    route = _FakeRoute(
        "https://public.example/",
        [_FakeResponse(302, {"location": "http://127.0.0.1/admin"})],
    )

    await crawler_runtime._guarded_route(route)

    assert len(route.fetch_calls) == 1
    assert route.fetch_calls[0]["max_redirects"] == 0
    assert route.aborted == "blockedbyclient"
    assert route.fulfilled is None


@pytest.mark.anyio
async def test_crawler_cross_origin_redirect_strips_credentials(monkeypatch):
    from backend.tools import crawler_runtime

    fake_dns = [("AF_INET", 1, 6, "", ("93.184.216.34", 0))]
    monkeypatch.setattr(socket, "getaddrinfo", lambda *args, **kwargs: fake_dns)
    route = _FakeRoute(
        "https://public.example/",
        [
            _FakeResponse(302, {"location": "https://other.example/final"}),
            _FakeResponse(200),
        ],
    )

    await crawler_runtime._guarded_route(route)

    assert len(route.fetch_calls) == 2
    headers = route.fetch_calls[1]["headers"]
    assert "authorization" not in headers
    assert "cookie" not in headers
    assert route.fulfilled.status == 200
    assert route.aborted is None


@pytest.mark.anyio
async def test_crawler_http_to_https_redirect_is_retried_only_after_check(monkeypatch):
    from backend.tools import crawler_runtime

    fake_dns = [("AF_INET", 1, 6, "", ("93.184.216.34", 0))]
    monkeypatch.setattr(socket, "getaddrinfo", lambda *args, **kwargs: fake_dns)
    route = _FakeRoute(
        "http://public.example",
        [_FakeResponse(301, {"location": "https://public.example/secure"})],
        navigation=True,
    )

    await crawler_runtime._guarded_route(route)

    assert route.aborted == "blockedbyclient"
    assert crawler_runtime._take_safe_redirect_target("http://public.example") == (
        "https://public.example/secure"
    )


@pytest.mark.anyio
async def test_crawler_cross_host_http_to_https_redirect_is_not_retried(monkeypatch):
    from backend.tools import crawler_runtime

    fake_dns = [("AF_INET", 1, 6, "", ("93.184.216.34", 0))]
    monkeypatch.setattr(socket, "getaddrinfo", lambda *args, **kwargs: fake_dns)
    route = _FakeRoute(
        "http://platform.example/",
        [_FakeResponse(302, {"location": "https://attacker.example/collect"})],
        navigation=True,
    )

    await crawler_runtime._guarded_route(route)

    assert route.aborted == "blockedbyclient"
    assert crawler_runtime._take_safe_redirect_target("http://platform.example/") is None


@pytest.mark.anyio
async def test_crawler_multi_hop_redirect_cannot_retry_cookie_config_on_other_host(monkeypatch):
    from backend.tools import crawler_runtime

    fake_dns = [("AF_INET", 1, 6, "", ("93.184.216.34", 0))]
    monkeypatch.setattr(socket, "getaddrinfo", lambda *args, **kwargs: fake_dns)
    route = _FakeRoute(
        "http://platform.example/",
        [
            _FakeResponse(302, {"location": "http://attacker.example/step2"}),
            _FakeResponse(301, {"location": "https://attacker.example/collect"}),
        ],
        navigation=True,
    )

    await crawler_runtime._guarded_route(route)

    assert route.aborted == "blockedbyclient"
    assert crawler_runtime._take_safe_redirect_target("http://platform.example/") is None
