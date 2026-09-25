"""send_email SMTP TLS 证书校验（2026-09-23 D1-3 回归）。

修复前 `server.starttls()` 无 context 参数 → Python 走
ssl._create_stdlib_context()（CERT_NONE），TLS 握手不校验服务器证书，
可被 MITM 截获 SMTP 口令与邮件内容。锁定：starttls 必须显式传入
验证系统 CA 的默认 SSL context（CERT_REQUIRED + check_hostname）。
"""
import smtplib
import ssl
import uuid

import pytest


@pytest.fixture()
def tls_capture(monkeypatch):
    import backend.config as cfg
    import backend.security.tool_approval as approval

    # SMTP 配置 + 审批自动放行（同 test_email_idempotency 口径）
    monkeypatch.setattr(cfg, "SMTP_USER", "tester")
    monkeypatch.setattr(cfg, "SMTP_PASSWORD", "secret")
    monkeypatch.setattr(cfg, "SMTP_HOST", "smtp.test.local")
    monkeypatch.setattr(cfg, "SMTP_PORT", 25)
    monkeypatch.setattr(cfg, "SMTP_FROM", "bot@example.com")
    monkeypatch.setattr(approval, "ensure_approved", lambda *a, **k: None)

    # 发送邮件生产契约要求可信租户/用户上下文；TLS 断言必须在与真实
    # 网关注入一致的身份边界内执行，而不是走已封死的匿名旁路。
    from backend.tools.session import _current_tenant_id, _current_user_id
    tenant_token = _current_tenant_id.set(
        f"test-tenant-{uuid.uuid4().hex[:8]}")
    user_token = _current_user_id.set("test-user")

    captured = {}
    sent = []

    class _FakeSMTP:
        def __init__(self, host, port, timeout=None):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def starttls(self, context=None):
            captured["context"] = context

        def login(self, user, password):
            captured["login"] = (user, password)

        def sendmail(self, from_addr, to_addrs, msg):
            sent.append(msg)

    monkeypatch.setattr(smtplib, "SMTP", _FakeSMTP)

    from backend.tools import email as email_mod
    email_mod._SENT_FINGERPRINTS.clear()
    yield captured, sent
    email_mod._SENT_FINGERPRINTS.clear()
    _current_tenant_id.reset(tenant_token)
    _current_user_id.reset(user_token)


def test_starttls_receives_verifying_default_context(tls_capture):
    from backend.tools.email import send_email_tool

    out = send_email_tool.invoke({
        "to": "a@example.com", "subject": "TLS 验收", "body": "正文"})
    assert "邮件已发送" in out

    captured, _sent = tls_capture
    ctx = captured.get("context")
    assert isinstance(ctx, ssl.SSLContext), "starttls 必须收到 SSLContext"
    assert ctx.verify_mode == ssl.CERT_REQUIRED, "必须校验服务器证书"
    assert ctx.check_hostname is True, "必须校验主机名"
    assert captured["login"], "TLS 之后才允许 login（顺序语义）"
