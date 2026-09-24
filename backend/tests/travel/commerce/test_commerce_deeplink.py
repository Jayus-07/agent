"""STOP K Deep Link 安全校验（K5：G18，任务书 §十二全矩阵）。

fail-closed：白名单空 = 全拒；任何拒绝都不得把链接透给用户。
"""
from __future__ import annotations

import pytest

from backend.travel.commerce import deeplink

ALLOW = ("fake-commerce.example.com",)


@pytest.mark.parametrize("url", [
    "https://fake-commerce.example.com/hotel?id=1",
    "https://sub.fake-commerce.example.com/hotel?id=1",  # 白名单域子域
])
def test_deeplink_accepts_valid(url):
    ok, reason = deeplink.validate_deeplink(url, ALLOW)
    assert ok, reason
    assert deeplink.sanitize(url, ALLOW) == url


@pytest.mark.parametrize("url,reason_part", [
    ("http://fake-commerce.example.com/hotel", "not-https"),
    ("javascript:alert(1)", "scheme-forbidden"),
    ("data:text/html;base64,xxx", "scheme-forbidden"),
    ("file:///etc/passwd", "scheme-forbidden"),
    ("vbscript:msgbox", "scheme-forbidden"),
    ("https://127.0.0.1/x", "blocked-host"),
    ("https://localhost/x", "blocked-host"),
    ("https://10.1.2.3/x", "blocked-host"),
    ("https://172.16.0.9/x", "blocked-host"),
    ("https://172.31.255.255/x", "blocked-host"),
    ("https://192.168.1.1/x", "blocked-host"),
    ("https://169.254.1.1/x", "blocked-host"),
    ("https://0.0.0.0/x", "blocked-host"),
    ("https://user:pass@fake-commerce.example.com/x", "credential-in-url"),
    ("https://evil.example.com/hotel", "host-not-allowed"),
    ("https://fake-commerce.example.com/" + "a" * 3000, "too-long"),
])
def test_deeplink_rejects_matrix(url, reason_part):
    ok, reason = deeplink.validate_deeplink(url, ALLOW)
    assert not ok
    assert reason_part in reason


def test_deeplink_absent_is_not_error_but_none():
    """Provider 未提供链接：不是校验失败，但 deep_link 必须 None。"""
    ok, reason = deeplink.validate_deeplink(None, ALLOW)
    assert not ok and reason == "absent"
    assert deeplink.sanitize(None, ALLOW) is None
    assert deeplink.sanitize("  ", ALLOW) is None


def test_deeplink_empty_allowlist_fails_closed():
    """白名单未配置 = 全拒（fail-closed，G18）。"""
    ok, reason = deeplink.validate_deeplink(
        "https://fake-commerce.example.com/x", ())
    assert not ok and reason == "allowlist-empty"


def test_deeplink_rejection_log_never_contains_url(caplog):
    """拒绝路径不得把完整 URL 写日志（query 可能带签名）。"""
    import logging

    with caplog.at_level(logging.DEBUG):
        deeplink.sanitize(
            "https://evil.example.com/x?token=SECRET", ALLOW)
    assert "SECRET" not in caplog.text
