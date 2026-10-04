# -*- coding: utf-8 -*-
"""test_e4b_pii_expansion.py — E4b PII 扩面四路径断言（2026-10-05）

E4a（知识主路径）已验收；本文件锁 E4b 扩面：supervisor / query / complaint
三条 LLM 路径的 prompt payload 零明文 PII（mask 后进、不还原）。
action 路径无 LLM 调用（纯规则+业务网关），知识/chat_fallback 已有各自测试。
只 mock LLM 外部边界：捕获入参断言掩码占位符。
"""
from __future__ import annotations

import json

import pytest

PII_PHONE = "13812345678"
# shared/pii_mask 的占位符形态（手机号掩码后应含 [隐私 或 digit 占位）


class _CaptureLLM:
    """捕获 invoke 入参的桩（断言 payload 零明文）。"""

    def __init__(self, content: str):
        self.captured: list[str] = []
        self._content = content

    def invoke(self, messages):
        self.captured.extend(
            str(getattr(m, "content", m)) for m in messages)
        resp = type("R", (), {"content": self._content})()
        return resp


@pytest.fixture()
def _passthrough_timeout(monkeypatch):
    from backend.infra import async_utils
    monkeypatch.setattr(
        async_utils, "sync_call_with_timeout",
        lambda fn, timeout, *a, **kw: fn(*a, **kw),
    )


def _assert_no_plain_phone(captured: list[str]) -> None:
    blob = "\n".join(captured)
    assert PII_PHONE not in blob, f"LLM payload 泄漏明文手机号: {blob[:200]}"


def test_e4b_supervisor_llm_payload_masked(_passthrough_timeout, monkeypatch):
    """supervisor L3 LLM 决策 payload 零明文。"""
    import backend.infra.llm as llm_mod
    import backend.config.customer_service as cs_config
    monkeypatch.setattr(cs_config, "CS_SUPERVISOR_LLM_ENABLED", True)
    stub = _CaptureLLM(json.dumps({"expert": "query"}))
    orig = llm_mod.llm
    llm_mod.llm = stub
    try:
        from backend.customer_service.supervisor import make_supervisor_decision
        make_supervisor_decision({
            "user_message": f"我的订单还没发货，手机号{PII_PHONE}联系",
            "cs_route": {"domain": "QUERY", "route_path": "",
                         "intent": "", "confidence": 0.30},
            "handoff_state": "ai_active",
            "confirmation_state": "not_required",
            "expert_loop_count": 0,
            "expert_history": [{"expert": "knowledge"}],
        })
    finally:
        llm_mod.llm = orig
    assert stub.captured, "supervisor LLM 未被调用（路径未触达）"
    _assert_no_plain_phone(stub.captured)


def test_e4b_query_decompose_payload_masked(_passthrough_timeout):
    """query 复合问题 LLM 分解 payload 零明文。"""
    import backend.infra.llm as llm_mod
    stub = _CaptureLLM(json.dumps(["q_order_status", "q_logistics"]))
    orig = llm_mod.llm
    llm_mod.llm = stub
    try:
        from backend.customer_service.experts.query import (
            _llm_decompose_intents,
        )
        _llm_decompose_intents(
            f"帮我查订单123456的物流并且我的手机号是{PII_PHONE}")
    finally:
        llm_mod.llm = orig
    assert stub.captured, "query 分解 LLM 未被调用"
    _assert_no_plain_phone(stub.captured)


def test_e4b_complaint_assess_payload_masked(_passthrough_timeout):
    """complaint LLM 评估 payload 零明文。"""
    import backend.infra.llm as llm_mod
    stub = _CaptureLLM(json.dumps({"is_complaint": True, "severity": "P1"}))
    orig = llm_mod.llm
    llm_mod.llm = stub
    try:
        from backend.customer_service.service.complaint_service import (
            ComplaintService,
        )
        svc = ComplaintService()
        svc._llm_assess(f"我要投诉！店家骚扰我，一直打我电话{PII_PHONE}")
    finally:
        llm_mod.llm = orig
    assert stub.captured, "complaint 评估 LLM 未被调用"
    _assert_no_plain_phone(stub.captured)
