# -*- coding: utf-8 -*-
"""send_email_tool 幂等测试。

背景: BaseSkill 用 to_thread+wait_for 执行 Tool，超时判重试时线程不可
取消——SMTP 实际已发出而 Skill 层判超时重试会重复发信。同内容指纹在
窗口内只允许发出一次。
"""
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

    class _FakeSMTP:
        def __init__(self, host, port, timeout=None):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def starttls(self):
            pass

        def login(self, user, password):
            pass

        def sendmail(self, from_addr, to_addrs, msg):
            sent.append(msg)

    monkeypatch.setattr(smtplib, "SMTP", _FakeSMTP)

    from backend.tools import email as email_mod
    email_mod._SENT_FINGERPRINTS.clear()
    yield sent
    email_mod._SENT_FINGERPRINTS.clear()


class TestEmailIdempotency:
    ARGS = {"to": "a@example.com", "subject": "周报", "body": "正文内容"}

    def test_first_send_succeeds(self, smtp_env):
        from backend.tools.email import send_email_tool

        result = send_email_tool.invoke(dict(self.ARGS))
        assert "已发送" in result
        assert len(smtp_env) == 1

    def test_duplicate_within_window_blocked(self, smtp_env):
        """同指纹窗口内重发被拦截，SMTP 只收到一次"""
        from backend.tools.email import send_email_tool

        assert "已发送" in send_email_tool.invoke(dict(self.ARGS))
        result = send_email_tool.invoke(dict(self.ARGS))
        assert "EMAIL DUPLICATE" in result
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
        with monkeypatch.context() as m:
            m.setattr(smtplib, "SMTP", _boom)
            with pytest.raises(ConnectionError):
                send_email_tool.invoke(dict(self.ARGS))

        # 恢复正常 SMTP 后重试可达
        result = send_email_tool.invoke(dict(self.ARGS))
        assert "已发送" in result
