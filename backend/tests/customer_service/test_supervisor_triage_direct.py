# -*- coding: utf-8 -*-
"""test_supervisor_triage_direct.py — 分诊直出出口（T6，2026-10-04 对话体验改造）

锁定四条语义：
  1. 顺序铁律：带客服规则信号的消息不得被出域/寒暄词表截胡（落业务漏斗）；
  2. 出域出口：无信号 + 出域词表命中 → FINISH + direct_reply 固定话术，
     零 LLM（CS_WINDOW_STANDALONE 控制）；
  3. 寒暄出口：无信号 + 寒暄词表命中 → chat_fallback 一次 LLM
     （CS_CHAT_FALLBACK_ENABLED 控制，关闭落回旧决策链）；
  4. 守卫优先：handoff/pending/风险/循环四类守卫先于出口。
reporter 侧锁定 direct_reply 直出与 expert_result 优先级。
"""
from __future__ import annotations

import pytest

import backend.config.customer_service as cs_config
from backend.customer_service.reporter import _assemble_answer
from backend.customer_service.supervisor import (
    ExpertAction,
    make_supervisor_decision,
)


def _state(user_message: str, cs_route: dict | None = None, **overrides) -> dict:
    """CS 图状态基座：无客服信号（confidence 低、无显式 route_path）。"""
    base = {
        "user_message": user_message,
        "cs_route": cs_route if cs_route is not None else {
            "domain": "UNKNOWN",
            "route_path": "",
            "intent": "",
            "confidence": 0.10,
        },
        "handoff_state": "ai_active",
        "confirmation_state": "not_required",
        "expert_loop_count": 0,
        "expert_history": [],
        "user_id": "u-t6",
        "session_id": "s-t6",
        "tenant_id": "default",
    }
    base.update(overrides)
    return base


@pytest.fixture(autouse=True)
def _switches_on(monkeypatch):
    """两开关显式置 true（config 默认即 true，防环境漂移影响判定）。"""
    monkeypatch.setattr(cs_config, "CS_WINDOW_STANDALONE", True)
    monkeypatch.setattr(cs_config, "CS_CHAT_FALLBACK_ENABLED", True)


@pytest.fixture(autouse=True)
def _stub_llm(monkeypatch):
    """寒暄出口只 mock LLM 外部边界：chat_fallback 的 get_llm 替换为桩。"""
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


class TestTriageDirectExits:
    """出口命中与铁律。"""

    def test_out_of_scope_fixed_reply_zero_llm(self, _stub_llm):
        """旅游话题（无客服信号）→ FINISH + 固定话术，LLM 零调用。"""
        from backend.infra import async_utils
        called = {"n": 0}
        orig = async_utils.sync_call_with_timeout

        def _spy(fn, timeout, *a, **kw):
            called["n"] += 1
            return orig(fn, timeout, *a, **kw)
        async_utils.sync_call_with_timeout = _spy
        try:
            d = make_supervisor_decision(_state("下周去大理旅游有什么攻略"))
        finally:
            async_utils.sync_call_with_timeout = orig
        assert d["next_action"] == ExpertAction.FINISH.value
        assert d.get("direct_reply")
        assert "旅游" in d["reason"]  # topic = 词表命中片段
        assert called["n"] == 0  # 零 LLM（V1 口径）

    def test_platform_external_topic_fixed_reply(self, _stub_llm):
        """平台外话题（今天天气怎么样）→ 出域固定话术。"""
        d = make_supervisor_decision(_state("今天天气怎么样"))
        assert d["next_action"] == ExpertAction.FINISH.value
        assert "天气" in d.get("direct_reply", "")

    def test_chitchat_one_llm_call(self):
        """寒暄 → chat_fallback 一次 LLM，direct_reply 为人设回复。"""
        d = make_supervisor_decision(_state("你好"))
        assert d["next_action"] == ExpertAction.FINISH.value
        assert d.get("direct_reply") == "在的呀～有什么可以帮您？"
        assert "[v2·L4.5]" in d["reason"]

    def test_chitchat_switch_off_falls_back(self, monkeypatch):
        """寒暄开关关闭 → 出口不生效，落回原决策链（低置信兜底）。"""
        monkeypatch.setattr(cs_config, "CS_CHAT_FALLBACK_ENABLED", False)
        d = make_supervisor_decision(_state("你好"))
        assert "direct_reply" not in d

    def test_out_of_scope_guide_travel(self, _stub_llm, monkeypatch):
        """A 案：旅游话题出域 + 引导开关开 → 指路旅游页而非固定话术。"""
        monkeypatch.setattr(cs_config, "CS_TRIAGE_GUIDE_ENABLED", True)
        d = make_supervisor_decision(_state("福州有什么好玩的景点"))
        assert d["next_action"] == ExpertAction.FINISH.value
        assert "旅游规划" in d.get("direct_reply", "")
        assert "不在本窗口的服务范围内" not in d.get("direct_reply", "")
        assert "出域引导" in d["reason"]

    def test_out_of_scope_guide_off_keeps_fixed(self, _stub_llm, monkeypatch):
        """引导开关关（config 默认 false）→ 维持固定话术现行为。"""
        monkeypatch.setattr(cs_config, "CS_TRIAGE_GUIDE_ENABLED", False)
        d = make_supervisor_decision(_state("福州有什么好玩的景点"))
        assert d["next_action"] == ExpertAction.FINISH.value
        assert "不在本窗口的服务范围内" in d.get("direct_reply", "")

    def test_out_of_scope_platform_never_guides(self, _stub_llm, monkeypatch):
        """平台外话题（天气等）不在引导组，开关开也走固定话术。"""
        monkeypatch.setattr(cs_config, "CS_TRIAGE_GUIDE_ENABLED", True)
        d = make_supervisor_decision(_state("今天天气怎么样"))
        assert "不在本窗口的服务范围内" in d.get("direct_reply", "")

    def test_out_of_scope_guide_selection(self, _stub_llm, monkeypatch):
        """选品话题 → 指路选品工作台。"""
        monkeypatch.setattr(cs_config, "CS_TRIAGE_GUIDE_ENABLED", True)
        d = make_supervisor_decision(_state("帮我做一下竞品分析"))
        assert "选品工作台" in d.get("direct_reply", "")

    def test_cs_signal_beats_out_of_scope(self):
        """顺序铁律：客服规则信号命中 → 出域词表不截胡，落业务漏斗。"""
        d = make_supervisor_decision(_state("订单里的行程单丢了怎么办"))
        assert "direct_reply" not in d
        assert d["next_action"] != ExpertAction.FINISH.value or "[v2·L4.5]" not in d["reason"]

    def test_cs_signal_beats_chitchat(self):
        """顺序铁律：含客服信号的句子即使带寒暄词也走业务漏斗。"""
        d = make_supervisor_decision(_state("你好，我谢谢你们，但是我的订单还没退款"))
        assert "direct_reply" not in d

    def test_guards_before_direct_exit(self):
        """守卫优先：pending 确认等待中发寒暄不被出口截走。"""
        d = make_supervisor_decision(
            _state("好的", confirmation_state="pending"))
        assert d["next_action"] == ExpertAction.PENDING.value
        assert "direct_reply" not in d

    def test_handoff_before_direct_exit(self):
        """守卫优先：人工接管中发寒暄照旧 handoff 拦截。"""
        d = make_supervisor_decision(
            _state("你好", handoff_state="human_active"))
        assert d["next_action"] == ExpertAction.HANDOFF.value

    def test_confident_route_skips_direct_exit(self):
        """高置信客服意图先于出口：confidence 达阈值直接走 L5 路由。"""
        d = make_supervisor_decision(_state(
            "谢谢，另外我的订单什么时候发货",
            cs_route={"domain": "QUERY", "route_path": "order_query",
                      "intent": "q_order", "confidence": 0.90},
        ))
        assert "direct_reply" not in d
        assert d["next_action"] == ExpertAction.RUN_EXPERT.value

    def test_empty_message_no_exit(self):
        """空消息不出口。"""
        d = make_supervisor_decision(_state(""))
        assert "direct_reply" not in d


class TestReporterDirectReply:
    """reporter 直出分支与优先级。"""

    def test_direct_reply_passthrough(self):
        answer = _assemble_answer(
            {"next_action": "finish", "direct_reply": "固定话术"},
            {"response_draft": "不该被采用"}, "ai_active", {},
        )
        assert answer == "固定话术"

    def test_expert_result_wins_without_direct_reply(self):
        answer = _assemble_answer(
            {"next_action": "finish"},
            {"response_draft": "业务回复"}, "ai_active", {},
        )
        assert answer == "业务回复"

    def test_pending_beats_direct_reply(self):
        """pending 语义优先（防御性：出口决策不带 direct_reply 之外的 action）。"""
        answer = _assemble_answer(
            {"next_action": "pending", "direct_reply": "不该直出"},
            {}, "ai_active", {},
        )
        assert answer != "不该直出"
