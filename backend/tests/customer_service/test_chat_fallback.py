"""test_chat_fallback.py — 寒暄分支单元测试（任务卡 T3，V3 验收口径）。

只 mock 外部边界（LLM 调用）；断言：
  - 开关关闭返回 None（调用方落回旧路径）；
  - 进 LLM 的 payload 零明文 PII（手机号掩码），回复经 vault 还原；
  - LLM 故障走固定兜底话术（fail-open），不向用户暴露错误。
"""
from __future__ import annotations

from types import SimpleNamespace

from backend.customer_service import chat_fallback as cf


class TestChatFallback:
    def test_disabled_returns_none(self, monkeypatch):
        monkeypatch.setattr(cf, "CS_CHAT_FALLBACK_ENABLED", False)
        assert cf.run_chat_fallback("你好") is None

    def test_empty_question_fixed_reply(self, monkeypatch):
        monkeypatch.setattr(cf, "CS_CHAT_FALLBACK_ENABLED", True)
        r = cf.run_chat_fallback("   ")
        assert r is not None and r.reply and r.error is None

    def test_pii_masked_before_llm_and_restored_after(self, monkeypatch):
        captured = {}

        def _fake_invoke(messages):
            captured["messages"] = messages
            # 模型把掩码占位符原样复述——回归后应还原为真实手机号
            return SimpleNamespace(content="您好呀，已经记下您的问题了，13800001111 对吧～")

        class _FakeLLM:
            invoke = staticmethod(_fake_invoke)

        monkeypatch.setattr(cf, "CS_CHAT_FALLBACK_ENABLED", True)
        # get_llm() 作为 sync_call_with_timeout 的第一个实参先求值，必须一起 mock
        monkeypatch.setattr("backend.infra.llm.get_llm", lambda: _FakeLLM())

        r = cf.run_chat_fallback("我手机号是13800001111，帮我看看")
        assert r.pii_masked is True
        # 进 LLM 的 payload 零明文手机号（V3）
        import json as _json
        payload = "".join(getattr(m, "content", str(m)) for m in captured["messages"])
        assert "13800001111" not in payload
        # 回复经 unmask 还原（C11 同口径：mask→LLM→unmask 100%）
        assert "13800001111" in r.reply

    def test_llm_failure_fixed_fallback(self, monkeypatch):
        def _boom(fn, timeout, msgs):
            raise RuntimeError("LLM 挂了")

        monkeypatch.setattr(cf, "CS_CHAT_FALLBACK_ENABLED", True)
        monkeypatch.setattr("backend.infra.async_utils.sync_call_with_timeout", _boom)
        r = cf.run_chat_fallback("在吗")
        assert r.reply == cf._FALLBACK_REPLY  # 固定友好话术，不报错给用户
        assert r.error is not None

    def test_persona_prompt_constraints(self):
        """人设红线必须写进 system prompt（V3 不编造/不承诺/不出域）。"""
        assert "不编造" in cf.PERSONA_SYSTEM_PROMPT
        assert "不承诺" in cf.PERSONA_SYSTEM_PROMPT or "不做任何承诺" in cf.PERSONA_SYSTEM_PROMPT
        assert "不回答购物客服以外" in cf.PERSONA_SYSTEM_PROMPT
