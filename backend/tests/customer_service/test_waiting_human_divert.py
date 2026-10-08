# -*- coding: utf-8 -*-
"""test_waiting_human_divert.py — 排队态消息分流（C 案，2026-10-08 服务器验收拍板）

锁定四条语义：
  1. waiting_human + 非客服诉求（出域/寒暄）+ 分流开关开 → 直答
     （next_action=finish），不再被排队话术吞掉；
  2. waiting_human + 客服诉求（业务域词=催单/订单/退款）→ 保持排队
     话术（handoff 拦截），该话术承载「已同步人工」语义；
  3. 分流开关关（config 默认 false）→ 现行为（一律排队话术）；
  4. human_active（人工已接入）不分流——AI 零抢答铁律不动。

全程复用出域/寒暄既有词表（顺序铁律同源），零新词表零新 LLM 面。
"""
from __future__ import annotations

import pytest

import backend.config.customer_service as cs_config
from backend.customer_service.supervisor import (
    ExpertAction,
    make_supervisor_decision,
)


def _state(user_message: str, handoff_state: str = "waiting_human") -> dict:
    return {
        "user_message": user_message,
        "cs_route": {
            "domain": "UNKNOWN",
            "route_path": "",
            "intent": "",
            "confidence": 0.10,
        },
        "handoff_state": handoff_state,
        "confirmation_state": "not_required",
        "expert_loop_count": 0,
        "expert_history": [],
        "user_id": "u-divert",
        "session_id": "s-divert",
    }


@pytest.fixture(autouse=True)
def _stub_llm(monkeypatch):
    """寒暄出口只 mock LLM 外部边界（同 triage_direct 测试口径）。"""
    class _StubResp:
        content = "在的呀～有什么可以帮您？"

    class _StubLLM:
        def invoke(self, messages):
            return _StubResp()

    from backend.infra import async_utils
    monkeypatch.setattr(
        async_utils, "sync_call_with_timeout",
        lambda fn, timeout, *a, **kw: fn(*a, **kw),
    )
    import backend.infra.llm as llm_mod
    monkeypatch.setattr(llm_mod, "llm", _StubLLM())


class TestWaitingHumanDivert:
    def test_out_of_scope_diverted(self, _stub_llm, monkeypatch):
        """排队期出域问题 → 直答（finish），不再排队话术。"""
        monkeypatch.setattr(cs_config, "CS_WAITING_HUMAN_DIVERT_ENABLED", True)
        d = make_supervisor_decision(_state("福州有什么好玩的景点"))
        assert d["next_action"] == ExpertAction.FINISH.value
        assert "direct_reply" in d
        assert "L1-divert" in d["reason"]

    def test_out_of_scope_diverted_with_guide(self, _stub_llm, monkeypatch):
        """排队期旅游话题 + 两开关开 → 引导话术直答。"""
        monkeypatch.setattr(cs_config, "CS_WAITING_HUMAN_DIVERT_ENABLED", True)
        monkeypatch.setattr(cs_config, "CS_TRIAGE_GUIDE_ENABLED", True)
        d = make_supervisor_decision(_state("厦门有什么景点"))
        assert d["next_action"] == ExpertAction.FINISH.value
        assert "旅游规划" in d.get("direct_reply", "")

    def test_chitchat_diverted(self, _stub_llm, monkeypatch):
        """排队期寒暄 → chat_fallback 直答。"""
        monkeypatch.setattr(cs_config, "CS_WAITING_HUMAN_DIVERT_ENABLED", True)
        d = make_supervisor_decision(_state("你好"))
        assert d["next_action"] == ExpertAction.FINISH.value
        assert "在的呀" in d.get("direct_reply", "")

    def test_cs_demand_keeps_queue(self, _stub_llm, monkeypatch):
        """排队期催单/订单诉求（业务域词命中）→ 保持排队话术。"""
        monkeypatch.setattr(cs_config, "CS_WAITING_HUMAN_DIVERT_ENABLED", True)
        d = make_supervisor_decision(_state("我的订单到底什么时候发货，催一下"))
        assert d["next_action"] == ExpertAction.HANDOFF.value

    def test_switch_off_keeps_queue(self, _stub_llm, monkeypatch):
        """开关关（config 默认 false）→ 现行为：出域也排队话术。"""
        monkeypatch.setattr(cs_config, "CS_WAITING_HUMAN_DIVERT_ENABLED", False)
        d = make_supervisor_decision(_state("福州有什么好玩的景点"))
        assert d["next_action"] == ExpertAction.HANDOFF.value

    def test_human_active_never_diverts(self, _stub_llm, monkeypatch):
        """human_active（人工已接入）不分流——AI 零抢答铁律。"""
        monkeypatch.setattr(cs_config, "CS_WAITING_HUMAN_DIVERT_ENABLED", True)
        d = make_supervisor_decision(
            _state("福州有什么好玩的景点", handoff_state="human_active")
        )
        assert d["next_action"] == ExpertAction.HANDOFF.value

    def test_refund_demand_keeps_queue(self, _stub_llm, monkeypatch):
        """排队期退款诉求 → 排队话术（涉资金/售后必须人工线）。"""
        monkeypatch.setattr(cs_config, "CS_WAITING_HUMAN_DIVERT_ENABLED", True)
        d = make_supervisor_decision(_state("顺便帮我退了那个订单"))
        assert d["next_action"] == ExpertAction.HANDOFF.value
