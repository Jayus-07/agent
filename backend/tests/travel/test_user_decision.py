"""tests/travel/test_user_decision.py — 用户决策中断（Phase 6，任务书 §13）

覆盖：
1. interrupt 触发：开关开 + 有持久化 + 必去闭馆 → 图暂停，payload 结构化
2. Command(resume) 恢复：keep → degraded + 留档；drop → 移除 + 需求同步解除
3. 回退语义：开关关走 Phase 3 软处理；开关开但无持久化也回退（不悬空卡人）
4. 决策归一化：三种合法形态 + 非法输入保守按 keep
5. 编排适配器：interrupt 透传（final_answer + pending_decision）与 resume 通道
"""
from __future__ import annotations

from uuid import uuid4

import pytest

import backend.config.travel as T
import backend.travel.graph_builder as gb
from backend.travel.graph_state import new_travel_graph_input
from backend.travel.validator import _normalize_decision
from backend.travel.validator import check_itinerary  # noqa: F401 — 保证模块可导入

MONDAY_QUESTION = "9月21日福州一日游，1个人，必去福建博物院"


@pytest.fixture
def interrupt_graph(monkeypatch):
    """带 memory checkpointer + interrupt 开关的域图（每例 fresh graph）。"""
    monkeypatch.setattr(T, "TRAVEL_USER_DECISION_INTERRUPT", True)
    monkeypatch.setattr(T, "TRAVEL_CHECKPOINTER_ENABLED", True)
    monkeypatch.setattr(T, "TRAVEL_CHECKPOINTER_BACKEND", "memory")
    monkeypatch.setattr(gb, "_travel_graph", None)
    yield gb.get_travel_graph()
    monkeypatch.setattr(T, "TRAVEL_USER_DECISION_INTERRUPT", False)
    monkeypatch.setattr(gb, "_travel_graph", None)


def _tid(tag: str) -> str:
    return f"t-p6-{tag}-{uuid4().hex[:8]}"


def _invoke(graph, message: str, tid: str):
    return graph.invoke(
        new_travel_graph_input(message, session_id="s-p6"),
        config={"configurable": {"thread_id": tid}},
    )


def _resume(graph, decision: dict, tid: str):
    from langgraph.types import Command
    return graph.invoke(
        Command(resume=decision),
        config={"configurable": {"thread_id": tid}},
    )


def _interrupt_payload(final: dict) -> dict:
    for it in final.get("__interrupt__") or []:
        value = getattr(it, "value", None)
        if isinstance(value, dict) and value.get("items"):
            return value
    return {}


# =============================================
# 一、interrupt 触发与恢复
# =============================================
class TestInterruptFlow:
    def test_interrupt_fires_with_payload(self, interrupt_graph):
        tid = _tid("fire")
        final = _invoke(interrupt_graph, MONDAY_QUESTION, tid)
        payload = _interrupt_payload(final)
        assert payload, "应产生 interrupt 暂停"
        names = [i["poi_name"] for i in payload["items"]]
        assert "福建博物院" in names
        assert set(payload["options"]) == {"keep", "drop"}

    def test_resume_keep_degrades_with_record(self, interrupt_graph):
        tid = _tid("keep")
        _invoke(interrupt_graph, MONDAY_QUESTION, tid)
        final = _resume(interrupt_graph, {"action": "keep"}, tid)

        it = final["itinerary"]
        assert it["status"] == "degraded"  # 保留风险如实降级
        assert it["confidence"] < 1.0
        assert any("保留" in n for n in final["notes"])
        # decision_required 已全部转为 warning（不再重复询问）
        levels = {v["level"] for v in final["validation"]["violations"]}
        assert "decision_required" not in levels
        assert "warning" in levels
        # 出单：图继续走完 reporter
        assert "行程 v" in (final.get("final_answer") or "")

    def test_resume_drop_removes_and_unblocks(self, interrupt_graph):
        tid = _tid("drop")
        _invoke(interrupt_graph, MONDAY_QUESTION, tid)
        final = _resume(interrupt_graph, {"drop": ["福建博物院"]}, tid)

        it = final["itinerary"]
        titles = [i["title"] for d in it["days"] for i in d["items"]]
        assert "福建博物院" not in titles
        # 需求同步解除（用户决策结构化落库）
        assert "福建博物院" not in (final["brief"].get("must_go") or [])
        assert any("已按你的决定移除" in n for n in final["notes"])
        assert it["status"] == "ready"
        assert "行程 v" in (final.get("final_answer") or "")

    def test_resume_replans_from_same_thread(self, interrupt_graph):
        """恢复轮与新需求轮共用 thread：checkpointer 恢复暂停态而非重开。"""
        tid = _tid("same")
        _invoke(interrupt_graph, MONDAY_QUESTION, tid)
        final = _resume(interrupt_graph, {"action": "keep"}, tid)
        # 保留的福建博物院仍在行程里（不是重新规划丢了）
        titles = [i["title"] for d in final["itinerary"]["days"] for i in d["items"]]
        assert "福建博物院" in titles


# =============================================
# 二、回退语义
# =============================================
class TestFallback:
    def test_switch_off_keeps_soft_path(self, monkeypatch):
        """开关关：Phase 3 软处理原样保留（出单+请决定，不暂停）。"""
        monkeypatch.setattr(T, "TRAVEL_USER_DECISION_INTERRUPT", False)
        monkeypatch.setattr(T, "TRAVEL_CHECKPOINTER_ENABLED", True)
        monkeypatch.setattr(T, "TRAVEL_CHECKPOINTER_BACKEND", "memory")
        monkeypatch.setattr(gb, "_travel_graph", None)
        graph = gb.get_travel_graph()
        try:
            final = _invoke(graph, MONDAY_QUESTION, _tid("off"))
            assert not final.get("__interrupt__")
            assert final["itinerary"]["status"] == "needs_user_decision"
            assert "需要你决定" in (final.get("final_answer") or "")
        finally:
            monkeypatch.setattr(gb, "_travel_graph", None)

    def test_no_persistence_falls_back(self, monkeypatch):
        """开关开但无持久化：回退软处理——中断无处悬挂，不能卡死用户。"""
        monkeypatch.setattr(T, "TRAVEL_USER_DECISION_INTERRUPT", True)
        monkeypatch.setattr(T, "TRAVEL_CHECKPOINTER_ENABLED", False)
        monkeypatch.setattr(gb, "_travel_graph", None)
        graph = gb.get_travel_graph()
        try:
            final = _invoke(graph, MONDAY_QUESTION, _tid("nopersist"))
            assert not final.get("__interrupt__")
            assert final["itinerary"]["status"] == "needs_user_decision"
        finally:
            monkeypatch.setattr(T, "TRAVEL_USER_DECISION_INTERRUPT", False)
            monkeypatch.setattr(gb, "_travel_graph", None)


# =============================================
# 三、决策归一化
# =============================================
def _mini_report(poi_names) -> "ValidationReport":
    """只含 decision_required 违反的最小 report（_normalize_decision 仅读 detail）。"""
    from backend.travel.models.validation import (
        LEVEL_DECISION_REQUIRED, ValidationReport, Violation,
    )
    return ValidationReport(violations=[
        Violation(code="TIME_CLOSED_WEEKDAY", level=LEVEL_DECISION_REQUIRED,
                  message="m", detail={"poi_name": n})
        for n in poi_names
    ])


class TestNormalizeDecision:
    NAMES = {"福建博物院", "鼓岭"}

    def test_action_keep(self):
        drops, keeps = _normalize_decision({"action": "keep"}, _mini_report(self.NAMES))
        assert drops == set() and keeps == self.NAMES

    def test_action_drop_all(self):
        drops, keeps = _normalize_decision({"action": "drop"}, _mini_report(self.NAMES))
        assert drops == self.NAMES and keeps == set()

    def test_drop_named_subset(self):
        drops, keeps = _normalize_decision(
            {"drop": ["福建博物院"]}, _mini_report(self.NAMES))
        assert drops == {"福建博物院"} and keeps == {"鼓岭"}

    def test_invalid_decision_conservative_keep(self):
        for bad in (None, "保留", {}, {"action": "??"}, {"drop": ["不存在"]}):
            drops, keeps = _normalize_decision(bad, _mini_report(self.NAMES))
            assert drops == set() and keeps == self.NAMES, f"{bad!r} 应保守按 keep"


# =============================================
# 四、编排适配器透传与 resume 通道
# =============================================
class TestAdapter:
    def test_transmits_interrupt_and_pending_decision(self, interrupt_graph, monkeypatch):
        from backend.orchestration.graph.travel_graph_node import travel_graph_node

        update = travel_graph_node({
            "question": MONDAY_QUESTION,
            "session_id": "s-p6-adapter",
            "travel_context": {"conversation_id": _tid("adapter")},
        })
        assert "需要你决定" in (update["final_answer"] or "")
        pending = update["travel_context"].get("pending_decision") or {}
        assert any(i["poi_name"] == "福建博物院" for i in pending.get("items", []))

    def test_resume_channel_completes_plan(self, interrupt_graph):
        from backend.orchestration.graph.travel_graph_node import travel_graph_node

        cid = _tid("adapter-resume")
        travel_graph_node({
            "question": MONDAY_QUESTION,
            "session_id": "s-p6-adapter",
            "travel_context": {"conversation_id": cid},
        })
        update = travel_graph_node({
            "question": "",
            "session_id": "s-p6-adapter",
            "travel_context": {"conversation_id": cid,
                               "resume_decision": {"action": "keep"}},
        })
        assert "行程 v" in (update["final_answer"] or "")
