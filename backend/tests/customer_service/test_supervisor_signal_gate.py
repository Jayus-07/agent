"""test_supervisor_signal_gate.py — Supervisor understanding 信号门（迁移 B5）

设计方案 §4.2 L1 安全拦截 + §9.1 介入点④的 P0 部分：understanding 规则
信号（零 LLM）接入 Supervisor 决策——风险兜底拦截（拒答）与 P0 投诉直通
（强制 complaint expert）。全部确定性规则，metadata 缺字段时行为与接线前
一致（向后兼容用例覆盖）。
"""
from __future__ import annotations

from backend.config import customer_service as cs_config
from backend.customer_service.supervisor import (
    ExpertAction,
    ExpertType,
    make_supervisor_decision,
)
from backend.customer_service.understanding.signals import is_p0_escalation


def _state(metadata: dict | None = None, **overrides) -> dict:
    cs_route = {
        "domain": "KNOWLEDGE",
        "route_path": "knowledge_query",
        "intent": "k_faq",
        "confidence": 0.90,
    }
    if metadata is not None:
        cs_route["metadata"] = metadata
    base = {
        "cs_route": cs_route,
        "handoff_state": "ai_active",
        "confirmation_state": "not_required",
        "expert_loop_count": 0,
        "expert_history": [],
        "user_message": "test question",
    }
    base.update(overrides)
    return base


class TestIsP0Escalation:

    def test_regulatory_marker_hit(self):
        assert is_p0_escalation(["angry:12315"]) is True

    def test_plain_angry_not_p0(self):
        assert is_p0_escalation(["angry:气死"]) is False

    def test_empty_hits(self):
        assert is_p0_escalation([]) is False

    def test_none_hits(self):
        assert is_p0_escalation(None) is False


class TestRiskBackstopIntercept:
    """越权/注入信号 → 拒答收尾（Input Guard 图前拦截的兜底位）。"""

    def test_risk_hits_finish(self):
        d = make_supervisor_decision(_state({"risk_hits": ["risk:别人的订单"]}))
        assert d["next_action"] == ExpertAction.FINISH.value
        assert d["is_finished"] is True
        assert d["decision_layer"] == 1
        assert "风险" in d["reason"]

    def test_risk_denies_over_p0_direct(self):
        """确定性拒绝优先于执行类分支：越权 + P0 威胁并存时拒答。"""
        d = make_supervisor_decision(_state({
            "risk_hits": ["risk:别人的订单"],
            "sentiment_hits": ["angry:12315"],
        }))
        assert d["next_action"] == ExpertAction.FINISH.value

    def test_pending_wins_over_risk(self):
        """信号门位于 2a pending 之后：确认等待中的会话不被改道。"""
        d = make_supervisor_decision(_state(
            {"risk_hits": ["risk:别人的订单"]},
            confirmation_state="pending_confirmation",
        ))
        assert d["next_action"] == ExpertAction.PENDING.value

    def test_gate_off_restores_route(self, monkeypatch):
        monkeypatch.setattr(cs_config, "CS_SIGNAL_GATE_ENABLED", False)
        d = make_supervisor_decision(_state({"risk_hits": ["risk:别人的订单"]}))
        assert d["next_action"] == ExpertAction.RUN_EXPERT.value
        assert d["next_expert"] == "knowledge"

    def test_no_metadata_unchanged(self):
        """旧状态/直测无 metadata → 整段跳过，行为与接线前一致。"""
        d = make_supervisor_decision(_state())
        assert d["next_action"] == ExpertAction.RUN_EXPERT.value
        assert d["next_expert"] == "knowledge"


class TestP0ComplaintDirect:
    """监管/舆情信号直通 complaint expert，不依赖路由置信度。"""

    def test_p0_forces_complaint(self):
        d = make_supervisor_decision(_state({
            "sentiment_hits": ["angry:12315", "angry:曝光"],
        }))
        assert d["next_action"] == ExpertAction.RUN_EXPERT.value
        assert d["next_expert"] == ExpertType.COMPLAINT.value
        assert d["decision_layer"] == 1
        assert "P0" in d["reason"]

    def test_low_confidence_still_directed(self):
        """直通意义所在：路由低置信时 P0 信号照常生效。"""
        d = make_supervisor_decision(_state(cs_route={
            "domain": "KNOWLEDGE",
            "route_path": "knowledge_query",
            "intent": "k_faq",
            "confidence": 0.30,
            "metadata": {"sentiment_hits": ["angry:报警"]},
        }))
        assert d["next_expert"] == ExpertType.COMPLAINT.value

    def test_plain_angry_not_directed(self):
        """非 P0 的愤怒情绪不走直通（SLA 分级归案件体系，B12 范围）。"""
        d = make_supervisor_decision(_state({
            "sentiment_hits": ["angry:气死"],
        }))
        assert d["next_action"] == ExpertAction.RUN_EXPERT.value
        assert d["next_expert"] == "knowledge"
