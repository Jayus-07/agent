"""test_llm_cascade.py — 投诉严重度 LLM 兜底 + 复合问题意图分解 单测

级联原则: 规则强命中直接用（不付 LLM 延迟），规则盲区/复合问题时才调 LLM，
LLM 失败一律确定性回退。
"""
from __future__ import annotations

import json
from unittest.mock import patch

import pytest

from backend.customer_service.experts import query as query_mod
from backend.customer_service.experts.query import (
    _compound_suspected,
    _llm_decompose_intents,
    execute_query,
)
from backend.customer_service.service.complaint_service import ComplaintService


class _FakeResp:
    def __init__(self, content: str):
        self.content = content


class _FakeLLM:
    def __init__(self, content: str | None = None, exc: Exception | None = None):
        self._content = content
        self._exc = exc
        self.calls = 0

    def invoke(self, messages, config=None):
        self.calls += 1
        if self._exc:
            raise self._exc
        return _FakeResp(self._content or "")


# =====================================================
# 投诉严重度级联
# =====================================================

class TestComplaintDetectCascade:
    def test_rule_hit_skips_llm(self):
        svc = ComplaintService()
        with patch.object(svc, "_llm_assess") as mock_assess:
            d = svc.detect_with_llm_fallback("我要投诉你们的服务！")
        assert d.is_complaint is True
        assert d.matched_patterns != ["llm_fallback"]
        mock_assess.assert_not_called()

    def test_rule_miss_llm_says_high(self):
        svc = ComplaintService()
        with patch.object(svc, "_llm_assess",
                          return_value={"is_complaint": True, "severity": "high"}):
            d = svc.detect_with_llm_fallback("再不处理这个东西真的没法用了")
        assert d.is_complaint is True
        assert d.severity == "high"
        assert d.matched_patterns == ["llm_fallback"]

    def test_rule_miss_llm_says_not_complaint(self):
        svc = ComplaintService()
        with patch.object(svc, "_llm_assess",
                          return_value={"is_complaint": False, "severity": "low"}):
            d = svc.detect_with_llm_fallback("随便聊聊，今天天气不错")
        assert d.is_complaint is False
        assert d.severity == "low"

    def test_rule_miss_llm_exception_falls_back(self):
        svc = ComplaintService()
        with patch.object(svc, "_llm_assess", return_value=None):
            d = svc.detect_with_llm_fallback("东西用起来不太顺手")
        assert d.is_complaint is False
        assert d.severity == "low"

    def test_llm_invalid_json_returns_none(self):
        svc = ComplaintService()
        llm = _FakeLLM("这不是 JSON")
        with patch("backend.infra.llm.get_llm", return_value=llm):
            assert svc._llm_assess("随便什么") is None
        assert llm.calls == 1


# =====================================================
# 复合问题意图分解
# =====================================================

class TestCompoundSuspected:
    def test_single_domain_not_suspected(self):
        # 注意: "订单…发货" 这类跨域措辞会命中两个域、进入 LLM 裁定，
        # 这是有意设计（预判宁可宽松，最终由 LLM 分解结果裁定）
        assert _compound_suspected("帮我查下订单状态") is False

    def test_two_domains_suspected(self):
        assert _compound_suspected("帮我查下订单状态，顺便看看物流到哪了") is True


class TestLLMDecompose:
    def test_valid_multi_intent(self):
        llm = _FakeLLM('["t_order_status", "t_logistics"]')
        with patch("backend.infra.llm.get_llm", return_value=llm):
            intents = _llm_decompose_intents("查订单和物流")
        assert intents == ["t_order_status", "t_logistics"]

    def test_invalid_intent_filtered(self):
        llm = _FakeLLM('["t_order_status", "fake_intent"]')
        with patch("backend.infra.llm.get_llm", return_value=llm):
            intents = _llm_decompose_intents("查订单")
        assert intents == ["t_order_status"]

    def test_llm_failure_returns_none(self):
        llm = _FakeLLM(exc=RuntimeError("LLM 不可用"))
        with patch("backend.infra.llm.get_llm", return_value=llm):
            assert _llm_decompose_intents("查订单和物流") is None

    def test_non_array_returns_none(self):
        llm = _FakeLLM('{"intent": "t_order_status"}')
        with patch("backend.infra.llm.get_llm", return_value=llm):
            assert _llm_decompose_intents("查订单和物流") is None


class TestExecuteQueryCompound:
    def test_compound_dispatches_multiple_services(self):
        state = {"cs_context": {"authenticated_user_id": "u1"}}
        cs_route = {"intent": "t_order_status"}
        calls = []

        def fake_dispatch(user_id, intent, question, route):
            calls.append(intent)
            return f"结果-{intent}"

        with patch.object(query_mod, "_dispatch_service", fake_dispatch), \
             patch.object(query_mod, "_llm_decompose_intents",
                          return_value=["t_order_status", "t_logistics", "as_repair"]):
            result = execute_query("查订单和物流", cs_route, state)

        # as_repair 与 t_order_status 同属 order 服务，去重后只查 2 次
        assert calls == ["t_order_status", "t_logistics"]
        assert result["data"]["decomposed"] is True
        assert "结果-t_order_status" in result["response_draft"]
        assert "结果-t_logistics" in result["response_draft"]

    def test_single_intent_no_llm(self):
        state = {"cs_context": {"authenticated_user_id": "u1"}}
        cs_route = {"intent": "t_order_status"}
        with patch.object(query_mod, "_llm_decompose_intents") as mock_decompose, \
             patch.object(query_mod, "_dispatch_service",
                          return_value="订单列表") as mock_dispatch:
            result = execute_query("我的订单什么状态", cs_route, state)
        mock_decompose.assert_not_called()
        mock_dispatch.assert_called_once()
        assert result["data"].get("decomposed") is None
