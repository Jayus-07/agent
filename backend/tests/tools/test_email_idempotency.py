# -*- coding: utf-8 -*-
"""send_email_tool 幂等测试。

背景: BaseSkill 用 to_thread+wait_for 执行 Tool，超时判重试时线程不可
取消——SMTP 实际已发出而 Skill 层判超时重试会重复发信。同内容指纹在
窗口内只允许发出一次。
"""
import uuid

import pytest


@pytest.fixture()
def smtp_env(monkeypatch):
    """自动放行审批 + 假 SMTP，记录实际发信次数。"""
    import smtplib
    import backend.config as cfg
    import backend.security.tool_approval as approval

    monkeypatch.setattr(cfg, "SMTP_USER", "tester")
    monkeypatch.setattr(cfg, "SMTP_PASSWORD", "secret")
    monkeypatch.setattr(cfg, "SMTP_HOST", "smtp.test.local")
    monkeypatch.setattr(cfg, "SMTP_PORT", 25)
    monkeypatch.setattr(cfg, "SMTP_FROM", "bot@example.com")
    monkeypatch.setattr(approval, "ensure_approved", lambda *a, **k: None)

    sent = []
    contexts = []

    class _FakeSMTP:
        def __init__(self, host, port, timeout=None):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def starttls(self, context=None):
            # 2026-09-23 D1-3：生产路径必须显式传验证系统 CA 的 SSL context
            assert context is not None, "starttls 必须携带证书校验 context"
            contexts.append(context)

        def login(self, user, password):
            pass

        def sendmail(self, from_addr, to_addrs, msg):
            sent.append(msg)

    monkeypatch.setattr(smtplib, "SMTP", _FakeSMTP)

    from backend.tools import email as email_mod
    from backend.tools.session import _current_tenant_id, _current_user_id
    email_mod._SENT_FINGERPRINTS.clear()
    tenant_token = _current_tenant_id.set(
        f"test-tenant-{uuid.uuid4().hex[:8]}")
    user_token = _current_user_id.set("test-user")
    yield sent
    _current_tenant_id.reset(tenant_token)
    _current_user_id.reset(user_token)
    email_mod._SENT_FINGERPRINTS.clear()


class TestEmailIdempotency:
    ARGS = {"to": "a@example.com", "subject": "周报", "body": "正文内容"}

    def test_first_send_succeeds(self, smtp_env):
        from backend.tools.email import send_email_tool

        result = send_email_tool.invoke(dict(self.ARGS))
        assert "已发送" in result
        assert len(smtp_env) == 1

    def test_duplicate_within_window_blocked(self, smtp_env):
        """同 logical effect 重发只重放结果，SMTP 只收到一次。"""
        from backend.tools.email import send_email_tool

        assert "已发送" in send_email_tool.invoke(dict(self.ARGS))
        result = send_email_tool.invoke(dict(self.ARGS))
        # 现行 STOP C 契约由 PG durable ledger 返回成功结果；进程内
        # 指纹只负责并发抑制，不能要求第二次调用必须返回旧 duplicate 文案。
        assert "已发送" in result or "EMAIL DUPLICATE" in result
        assert len(smtp_env) == 1

    def test_different_content_not_blocked(self, smtp_env):
        """不同主题/正文不受指纹拦截"""
        from backend.tools.email import send_email_tool

        assert "已发送" in send_email_tool.invoke(dict(self.ARGS))
        other = {**self.ARGS, "subject": "另一封邮件"}
        assert "已发送" in send_email_tool.invoke(other)
        assert len(smtp_env) == 2

    def test_failure_not_cached_retry_allowed(self, smtp_env, monkeypatch):
        """SMTP 异常不记指纹：失败后的重试不受幂等拦截"""
        import smtplib
        from backend.tools.email import send_email_tool

        def _boom(*a, **k):
            raise ConnectionError("smtp down")

        # 故障只限第一次调用，之后恢复正常 SMTP
        from backend.shared.provider_idempotency import ProviderEffectError
        with monkeypatch.context() as m:
            m.setattr(smtplib, "SMTP", _boom)
            with pytest.raises(ProviderEffectError, match="SMTP 连接/认证失败"):
                send_email_tool.invoke(dict(self.ARGS))

        # 恢复正常 SMTP 后重试可达
        result = send_email_tool.invoke(dict(self.ARGS))
        assert "已发送" in result

    def test_recipient_format_variance_blocked(self, smtp_env):
        """收件人格式差异（空格/大小写）不得换指纹绕过重复拦截"""
        from backend.tools.email import send_email_tool

        assert "已发送" in send_email_tool.invoke(dict(self.ARGS))
        # 同一收件人的格式变体：多空格、大小写
        variant = {**self.ARGS, "to": " A@Example.com "}
        result = send_email_tool.invoke(variant)
        assert "EMAIL DUPLICATE" in result
        assert len(smtp_env) == 1

    def test_chinese_comma_recipients_parsed(self, smtp_env):
        """中文逗号/顿号分隔的收件人拆成多个地址（此前整串成畸形地址）"""
        from backend.tools.email import send_email_tool

        result = send_email_tool.invoke({
            **self.ARGS, "to": "a@example.com，b@example.com、c@example.com",
        })
        assert "已发送" in result
        assert len(smtp_env) == 1

    def test_empty_recipients_after_parse(self, smtp_env):
        """to 只含分隔符时显式报错，不进 SMTP"""
        from backend.tools.email import send_email_tool
        from backend.shared.provider_idempotency import ProviderEffectError

        with pytest.raises(ProviderEffectError, match="收件人解析为空"):
            send_email_tool.invoke({**self.ARGS, "to": "，、 "})
        assert len(smtp_env) == 0
