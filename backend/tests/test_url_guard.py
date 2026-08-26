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

    def test_trusted_suffix_matching(self):
        assert _is_trusted_host("taobao.com")
        assert _is_trusted_host("item.taobao.com")
        assert _is_trusted_host("sub.item.jd.com")
        assert not _is_trusted_host("evil-taobao.com")
        assert not _is_trusted_host("taobao.com.evil.com")
        assert not _is_trusted_host("evil.example.com")
