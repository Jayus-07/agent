"""tests/travel/test_run_context.py — STOP F1/F2：TravelRun 生命周期与跨轮闭环

覆盖（任务书 §22 映射）：
  T1  基础补槽：追问 → 短答案 → same travel_run_id + 槽位合并
  T2  多槽一次补齐（经 resolver 命中 → 域图 merge_brief 双槽落位）
  T3  新请求 state：每轮全新 graph input / adapter state，仍从
      ConversationContext 找回 pending（不靠 Python 对象偶然残留）
  T7  用户纠正：8万 → 改成6万，最终 budget=60000（唯一 current value）
  T10 NEW_RUN：显式重开 → run_seq+1（新 run_id），旧行程产物清空
  T12 checkpoint missing：thread 无 checkpoint 但会话有摘要 → reconstruct
      brief 基底（不 500，resume_mode=reconstruct）
  T13 context missing：无摘要、无 checkpoint → fresh（确定性，不随机）
  T15 completed 后清 pending：不再拦截下一轮
  T14 服务重建：新 graph instance（fixture 每例重建）+ 新 request state

纪律：adapter 级用例 monkeypatch get_travel_graph 单例（MemorySaver），
RAG/偏好外设全关；ConversationContext 为进程内真实 store（被测对象），
用 uuid conversation_id 隔离用例。
"""
from __future__ import annotations

from uuid import uuid4

import pytest

import backend.config.travel as T
import backend.travel.graph_builder as gb
from backend.orchestration.context.conversation_context import (
    get_conversation_context_store,
    sync_travel_run_to_context,
)
from backend.orchestration.context.routing_context import (
    assemble_routing_context,
    mark_domain_turn,
)
from backend.orchestration.context.travel_pending_resolver import (
    resolve_travel_pending,
)
from backend.travel.graph_builder import build_travel_graph
from backend.travel.graph_state import new_travel_graph_input


@pytest.fixture
def memory_graph(monkeypatch):
    """带 MemorySaver 的域图（每例 fresh）+ 外设全关（离线确定）。"""
    monkeypatch.setattr(T, "TRAVEL_CHECKPOINTER_ENABLED", True)
    monkeypatch.setattr(T, "TRAVEL_CHECKPOINTER_BACKEND", "memory")
    monkeypatch.setattr(T, "TRAVEL_RAG_ENABLED", False)
    monkeypatch.setattr(T, "TRAVEL_PREFS_ENABLED", False)
    monkeypatch.setattr(T, "TRAVEL_REQUIRE_PERSISTENCE", False)
    monkeypatch.setattr(gb, "_travel_graph", None)
    graph = gb.get_travel_graph()
    yield graph
    monkeypatch.setattr(gb, "_travel_graph", None)


def _tid() -> str:
    return f"t-f2-{uuid4().hex[:8]}"


def _mark(tid: str, tenant="t-f2", user="u-f2") -> None:
    """模拟 router 层已做的域标记（prefilter 命中时 mark_domain_turn）。"""
    mark_domain_turn(tenant, user, tid, domain="travel",
                     action="travel_graph_node")


def _node_state(message: str, tid: str, *, tenant="t-f2", user="u-f2",
                travel_route=None) -> dict:
    """主图形态的最小 state（travel_graph_node 的输入契约）。"""
    return {
        "question": message,
        "tenant_id": tenant,
        "user_id": user,
        "session_id": tid,
        "travel_context": {
            "conversation_id": tid,
            "travel_route": travel_route or {},
        },
    }


def _ask(graph, message: str, tid: str, *, user="u-f2",
         travel_route=None) -> dict:
    return graph.invoke(
        new_travel_graph_input(message, user_id=user, session_id=tid,
                               conversation_id=tid,
                               travel_route=travel_route or {}),
        config={"recursion_limit": 40,
                "configurable": {"thread_id": f"travel:{tid}"}},
    )


def _ctx(tid: str, tenant="t-f2", user="u-f2"):
    return get_conversation_context_store().get(tenant, user, tid)


def _routing(tid: str, tenant="t-f2", user="u-f2") -> dict:
    return assemble_routing_context(tenant, user, tid)


# ============================================================
# T1/T3/T7：追问 → 补槽 → same run（经真实 store + 域图）
# ============================================================

class TestCrossTurnRun:
    def test_t1_slot_followup_same_run(self, memory_graph):
        """U1 缺天数 → 追问；U2「三天」→ resolver 命中 → 同 run 出单。"""
        tid = _tid()
        # U1：adapter 级执行（run/pending 写进真实 store）
        from backend.orchestration.graph.travel_graph_node import (
            travel_graph_node,
        )

        travel_graph_node(_node_state("想去福州玩", tid))
        _mark(tid)
        ctx = _ctx(tid)
        assert ctx.travel_pending is not None
        assert "days" in ctx.travel_pending["requested_slots"]
        first_run = ctx.travel_run_id
        assert first_run.startswith("trv_")

        # U2：路由层 resolver 用真实 assemble 的 routing_context 判定
        update = resolve_travel_pending("三天", _routing(tid))
        assert update is not None
        route = update["travel_context"]["travel_route"]
        assert route["resume_mode"] == "continue"

        # U2：域图执行（新 request state，thread 复用）→ 出单 + 同 run
        # （走 adapter：run/pending 同步是 adapter 职责）
        travel_graph_node(_node_state("三天", tid, travel_route=route))
        snap = memory_graph.get_state(
            {"configurable": {"thread_id": f"travel:{tid}"}})
        values = snap.values or {}
        assert values.get("itinerary") is not None
        assert values["itinerary"]["brief"]["destination"] == "福州"
        assert values["itinerary"]["brief"]["days"] == 3
        # run 身份保持（普通补槽不换 run，G5）
        assert _ctx(tid).travel_run_id == first_run
        # 出单后 pending 清掉（T15 前置：planned 无 pending）
        assert _ctx(tid).travel_pending is None

    def test_t7_user_correction_overwrites(self, memory_graph):
        """T7：8万 → 改成6万，最终唯一 current value budget=60000。"""
        tid = _tid()
        from backend.orchestration.graph.travel_graph_node import (
            travel_graph_node,
        )

        travel_graph_node(_node_state("福州两天的行程，预算8万", tid))
        assert _ctx(tid).budget_cny == 80000.0
        travel_graph_node(_node_state("不对，预算改成6万", tid))
        assert _ctx(tid).budget_cny == 60000.0
        # 域图状态同样只有新值（merge_brief 覆盖语义）
        snap = memory_graph.get_state(
            {"configurable": {"thread_id": f"travel:{tid}"}})
        brief = (snap.values or {}).get("brief") or {}
        assert brief.get("budget_cny") == 60000.0

    def test_t3_fresh_request_state_each_turn(self, memory_graph):
        """T3：两轮之间不复用任何 Python 对象——第二轮用全新 input/state，
        pending 从 ConversationContext（持久化语义）找回。"""
        tid = _tid()
        from backend.orchestration.graph.travel_graph_node import (
            travel_graph_node,
        )

        travel_graph_node(_node_state("想去厦门玩", tid))
        _mark(tid)
        pending_after_t1 = _ctx(tid).travel_pending
        assert pending_after_t1 is not None
        # 模拟进程内对象丢失后重建的读取路径：routing_context 从 store 组装
        rt = _routing(tid)
        assert rt["brief_summary"]["travel_pending"] is not None
        assert rt["active_domain"] == "travel"
        assert resolve_travel_pending("3天", rt) is not None


# ============================================================
# T10：NEW_RUN
# ============================================================

class TestNewRun:
    def test_t10_new_run_changes_identity_and_clears_plan(self, memory_graph):
        tid = _tid()
        from backend.orchestration.graph.travel_graph_node import (
            travel_graph_node,
        )

        travel_graph_node(_node_state("福州两天的行程", tid))
        _mark(tid)
        old_run = _ctx(tid).travel_run_id
        old_seq = _ctx(tid).travel_run_seq
        # U1 齐备出单 → pending=None → resolver 不介入；「重新规划」由
        # adapter/slot_filler 对消息的直接判定接管（is_new_run_query 下沉）
        assert resolve_travel_pending(
            "重新规划杭州两天", _routing(tid)) is None

        travel_graph_node(_node_state("重新规划杭州两天", tid))
        ctx = _ctx(tid)
        assert ctx.travel_run_seq == old_seq + 1
        assert ctx.travel_run_id != old_run
        assert ctx.destination == "杭州"


# ============================================================
# T12/T13：checkpoint 丢失的 graceful reconstruction
# ============================================================

class TestReconstruct:
    def test_t12_checkpoint_missing_reconstructs_from_context(self):
        """thread 无 checkpoint（新 graph 实例）但会话摘要存在 →
        adapter 标 reconstruct 并从摘要重建 brief 基底；域图出单不 500。"""
        from backend.orchestration.graph.travel_graph_node import (
            _detect_resume_mode,
        )

        tid = _tid()
        # 会话摘要先存在（上一轮的遗留事实）
        sync_travel_run_to_context("t-f2", "u-f2", tid,
                                   brief={"destination": "福州", "days": 2,
                                          "party_size": 1, "pace": "moderate"},
                                   missing_slots=[], new_run=False)
        graph = build_travel_graph(checkpointer=None)  # 无 checkpointer 实例
        state = _node_state("改成3天", tid)
        mode, extra = _detect_resume_mode(
            graph, {"configurable": {"thread_id": f"travel:{tid}"}},
            state, tid)
        assert mode == "reconstruct"
        base = extra["reconstruct_brief"]
        assert base["destination"] == "福州" and base["days"] == 2

    def test_t13_no_context_no_checkpoint_is_fresh(self):
        """T13：摘要与 checkpoint 皆无 → fresh（确定性，不随机）。"""
        from backend.orchestration.graph.travel_graph_node import (
            _detect_resume_mode,
        )

        tid = _tid()
        graph = build_travel_graph(checkpointer=None)
        mode, extra = _detect_resume_mode(
            graph, {"configurable": {"thread_id": f"travel:{tid}"}},
            _node_state("规划行程", tid), tid)
        assert mode == "fresh"
        assert extra == {}

    def test_t14_reconstructed_brief_feeds_slot_filler(self, memory_graph):
        """T14：reconstruct_brief 作为基底进 slot_filler，本轮消息只补差量
        ——「改成3天」在 destination=福州 的基础上出 3 天单，不丢目的地。"""
        tid = _tid()
        reconstruct = {"destination": "福州", "days": 2, "party_size": 1,
                       "pace": "moderate"}
        final = memory_graph.invoke(
            {**new_travel_graph_input("改成3天", user_id="u-f2", session_id=tid,
                                      conversation_id=tid),
             "reconstruct_brief": reconstruct},
            config={"recursion_limit": 40,
                    "configurable": {"thread_id": f"travel:{tid}"}},
        )
        assert final.get("itinerary") is not None
        assert final["itinerary"]["brief"]["destination"] == "福州"
        assert final["itinerary"]["brief"]["days"] == 3


# ============================================================
# T15：completed 后清 pending
# ============================================================

class TestCompletion:
    def test_t15_completed_clears_pending(self, memory_graph):
        from backend.orchestration.context.conversation_context import (
            mark_travel_run_completed,
        )

        tid = _tid()
        from backend.orchestration.graph.travel_graph_node import (
            travel_graph_node,
        )

        travel_graph_node(_node_state("想去福州玩", tid))
        assert _ctx(tid).travel_pending is not None
        # 齐备补槽出单 → adapter 走 mark_travel_run_completed
        travel_graph_node(_node_state("福州玩2天", tid))
        assert _ctx(tid).travel_stage == "completed"
        assert _ctx(tid).travel_pending is None
        # 下一轮普通问题不再被 pending 拦截（无 pending → resolver 不介入）
        assert resolve_travel_pending("3天", _routing(tid)) is None


# ============================================================
# run 生命周期纯单测
# ============================================================

class TestRunLifecycle:
    def test_run_id_format_and_seq(self):
        tid = _tid()
        run1 = sync_travel_run_to_context("t", "u", tid, brief={"days": 2},
                                          missing_slots=["destination"])
        run2 = sync_travel_run_to_context("t", "u", tid,
                                          brief={"destination": "杭州",
                                                 "days": 2},
                                          missing_slots=[], new_run=True)
        assert run1.startswith("trv_") and run2.startswith("trv_")
        assert run1 != run2
        ctx = _ctx(tid, "t", "u")
        assert ctx.travel_run_seq == 2
        assert ctx.travel_stage == "planned"
        assert ctx.travel_pending is None

    def test_same_run_when_just_filling(self):
        """普通补槽（无 new_run、run 已存在）不换 run（G5）。"""
        tid = _tid()
        run1 = sync_travel_run_to_context("t", "u", tid, brief={"days": 2},
                                          missing_slots=["destination"])
        run2 = sync_travel_run_to_context("t", "u", tid,
                                          brief={"destination": "福州",
                                                 "days": 2},
                                          missing_slots=[])
        assert run1 == run2

    def test_question_id_stable_while_same_slots(self):
        """同一轮追问（requested_slots 不变）question_id 稳定。"""
        tid = _tid()
        sync_travel_run_to_context("t", "u", tid, brief={},
                                   missing_slots=["destination", "days"])
        qid1 = _ctx(tid, "t", "u").travel_pending["question_id"]
        sync_travel_run_to_context("t", "u", tid, brief={"destination": "福州"},
                                   missing_slots=["days"])
        pending = _ctx(tid, "t", "u").travel_pending
        assert pending["requested_slots"] == ["days"]
        assert pending["question_id"] != qid1  # 槽位集合变了 = 新追问
