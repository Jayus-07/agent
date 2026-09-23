"""STOP G3 — Cancel 识别与 Travel Run Lifecycle 收口（任务书 §15~§19）。

覆盖：
- is_cancel_run_query 保守词表：整体取消命中；「不去海游馆了」类局部
  排除句必须排除（T16，不得误判 CANCEL）
- resolver cancel 分支：活跃 run 期间取消表达 → route_mode=travel +
  resume_mode=cancel；completed/cancelled 后不再拦
- travel_graph_node 短路：CANCEL_TRAVEL_RUN 原子 mutation——stage=cancelled、
  pending 清、run 身份清、seq 单调保留（§18：cancel 后新规划 run_002）、
  摘要槽位保留（STOP F clear 契约）
- T15/§15 生命周期：NONE → slot → planned → completed / cancelled
"""
from __future__ import annotations

from uuid import uuid4

import pytest

import backend.config.travel as T
import backend.travel.graph_builder as gb
from backend.orchestration.context.context_repository import (
    get_conversation_context_repository,
)
from backend.orchestration.context.conversation_context import (
    sync_travel_run_to_context,
)
from backend.orchestration.context.routing_context import (
    assemble_routing_context,
    mark_domain_turn,
)
from backend.orchestration.context.travel_pending_resolver import (
    resolve_travel_pending,
)
from backend.travel.slot_filler import is_cancel_run_query


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
    return f"t-g3-{uuid4().hex[:8]}"


def _node_state(message: str, tid: str, *, tenant="t-g3", user="u-g3",
                travel_route=None) -> dict:
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


# ============================================================
# is_cancel_run_query：保守词表（T16）
# ============================================================

class TestCancelQueryRules:
    @pytest.mark.parametrize("msg", [
        "不规划了", "先不规划了", "取消规划", "取消这次行程", "取消本次行程",
        "取消行程", "取消整个规划", "别规划了", "不用规划了", "先不去了",
        "这次旅行不规划了", "不想做攻略了",
    ])
    def test_whole_plan_cancel_phrases_hit(self, msg):
        assert is_cancel_run_query(msg) is True, msg

    @pytest.mark.parametrize("msg", [
        "不去海游馆了",            # T16：局部排除句 = PATCH avoid
        "不想去海游馆了",
        "不打算去环球影城了",
        "帮我去杭州两天",          # 正常规划诉求
        "预算改成6万",             # PATCH
        "三天",                    # 补槽
        "取消收藏",                # 「取消」但对象不是规划
        "我想去大阪",
    ])
    def test_partial_or_unrelated_phrases_do_not_cancel(self, msg):
        assert is_cancel_run_query(msg) is False, msg


# ============================================================
# resolver cancel 分支
# ============================================================

class TestResolverCancel:
    def test_active_run_cancel_short_circuits_to_travel(self):
        tid = _tid()
        sync_travel_run_to_context("t-g3", "u-g3", tid,
                                   brief={"destination": "大阪"},
                                   missing_slots=["days"])
        mark_domain_turn("t-g3", "u-g3", tid, domain="travel",
                         action="travel_graph_node")

        update = resolve_travel_pending("不规划了", assemble_routing_context(
            "t-g3", "u-g3", tid))
        assert update is not None
        route = update["travel_context"]["travel_route"]
        assert route["resume_mode"] == "cancel"
        assert update["route_mode"] == "travel"

    def test_completed_run_no_longer_cancellable_via_route(self):
        tid = _tid()
        sync_travel_run_to_context("t-g3", "u-g3", tid,
                                   brief={"destination": "大阪", "days": 2},
                                   missing_slots=[])
        from backend.orchestration.context.conversation_context import (
            mark_travel_run_completed,
        )
        mark_travel_run_completed("t-g3", "u-g3", tid)
        mark_domain_turn("t-g3", "u-g3", tid, domain="travel")

        # completed 后 run 不再是活跃取消对象（保守：放行正常路由）
        assert resolve_travel_pending("不规划了", assemble_routing_context(
            "t-g3", "u-g3", tid)) is None

    def test_local_exclude_phrase_not_routed_as_cancel(self):
        tid = _tid()
        sync_travel_run_to_context("t-g3", "u-g3", tid,
                                   brief={"destination": "大阪"},
                                   missing_slots=["days"])
        mark_domain_turn("t-g3", "u-g3", tid, domain="travel")

        # T16：「不去海游馆了」不得作为 cancel 短路路由
        update = resolve_travel_pending("不去海游馆了", assemble_routing_context(
            "t-g3", "u-g3", tid))
        if update is not None:  # 可能被补槽/其它规则接住，但绝不能是 cancel
            assert update["travel_context"]["travel_route"][
                "resume_mode"] != "cancel"


# ============================================================
# travel_graph_node 短路：CANCEL_TRAVEL_RUN 原子收口（§15~§18）
# ============================================================

class TestCancelShortCircuit:
    def test_cancel_current_run_keeps_summary_and_seq(self):
        from backend.orchestration.graph.travel_graph_node import (
            travel_graph_node,
        )

        tid = _tid()
        # 造活跃 run：destination=大阪、缺 days（slot 阶段）
        sync_travel_run_to_context("t-g3", "u-g3", tid,
                                   brief={"destination": "大阪"},
                                   missing_slots=["days"])
        mark_domain_turn("t-g3", "u-g3", tid, domain="travel")
        repo = get_conversation_context_repository()
        run_001 = repo.get("t-g3", "u-g3", tid).travel_run_id
        assert run_001.startswith("trv_")

        out = travel_graph_node(_node_state("不规划了", tid))
        assert "已取消" in out["final_answer"]
        assert out["travel_context"]["travel_route"]["resume_mode"] == "cancel"

        ctx = repo.get("t-g3", "u-g3", tid)
        assert ctx.travel_stage == "cancelled"
        assert ctx.travel_run_id == ""           # active run 清除
        assert ctx.travel_pending is None        # pending 清除
        assert ctx.travel_run_seq == 1           # seq 单调保留（§18 防撞号）
        assert ctx.destination == "大阪"         # 摘要槽位保留（STOP F 契约）

        # 取消后重新规划 → run_002（不得恢复 run_001）
        sync_travel_run_to_context("t-g3", "u-g3", tid,
                                   brief={"destination": "杭州", "days": 2},
                                   missing_slots=[])
        ctx2 = repo.get("t-g3", "u-g3", tid)
        assert ctx2.travel_run_id.endswith("_002")
        assert ctx2.travel_run_id != run_001
        assert ctx2.destination == "杭州"

    def test_partial_avoid_phrase_is_not_cancelled(self, memory_graph):
        """T16：「不去海游馆了」不得触发 CANCEL 短路（走正常域图处理）。"""
        from backend.orchestration.graph.travel_graph_node import (
            travel_graph_node,
        )

        tid = _tid()
        sync_travel_run_to_context("t-g3", "u-g3", tid,
                                   brief={"destination": "大阪"},
                                   missing_slots=["days"])
        repo = get_conversation_context_repository()
        run_001 = repo.get("t-g3", "u-g3", tid).travel_run_id

        out = travel_graph_node(_node_state("不去海游馆了", tid))
        assert "已取消" not in out["final_answer"]  # 未被 cancel 短路
        ctx = repo.get("t-g3", "u-g3", tid)
        assert ctx.travel_stage != "cancelled"

    def test_no_active_run_is_noop(self):
        """无活跃 run 时取消表达不产生**取消**副作用（保守放行正常流程）。"""
        from backend.orchestration.graph.travel_graph_node import (
            travel_graph_node,
        )

        tid = _tid()
        out = travel_graph_node(_node_state("不规划了", tid))
        # 无 run → 不短路：绝无「已取消」文案、run 未被标 cancelled
        assert "已取消" not in (out.get("final_answer") or "")
        ctx = get_conversation_context_repository().get("t-g3", "u-g3", tid)
        assert ctx is None or ctx.travel_stage != "cancelled"


# ============================================================
# 生命周期冻结（§15）：NONE→slot→planned→completed / cancelled
# ============================================================

class TestLifecycleStages:
    def test_full_lifecycle_via_public_sync_api(self):
        tid = _tid()
        repo = get_conversation_context_repository()

        # NONE（无 run）
        assert repo.get("t-g3", "u-g3", tid) is None
        # ACTIVE_SLOT：缺必填槽
        sync_travel_run_to_context("t-g3", "u-g3", tid,
                                   brief={"destination": "福州"},
                                   missing_slots=["days"])
        ctx = repo.get("t-g3", "u-g3", tid)
        assert ctx.travel_stage == "slot"
        assert ctx.travel_pending["requested_slots"] == ["days"]
        # ACTIVE_PLANNING：槽齐备
        sync_travel_run_to_context("t-g3", "u-g3", tid,
                                   brief={"destination": "福州", "days": 3},
                                   missing_slots=[])
        ctx = repo.get("t-g3", "u-g3", tid)
        assert ctx.travel_stage == "planned"
        assert ctx.travel_pending is None
        # COMPLETED
        from backend.orchestration.context.conversation_context import (
            mark_travel_run_completed,
        )
        mark_travel_run_completed("t-g3", "u-g3", tid)
        ctx = repo.get("t-g3", "u-g3", tid)
        assert ctx.travel_stage == "completed"
        assert ctx.travel_run_id  # run_id 保留归因（STOP F §19 契约）
        # CANCELLED（新一轮 run 后取消）
        sync_travel_run_to_context("t-g3", "u-g3", tid,
                                   brief={"destination": "杭州"},
                                   missing_slots=["days"])
        from backend.orchestration.context.context_repository import (
            ContextMutation,
            MutationType,
        )
        run_id = repo.get("t-g3", "u-g3", tid).travel_run_id
        result = repo.mutate("t-g3", "u-g3", tid, ContextMutation(
            MutationType.CANCEL_TRAVEL_RUN, {"expected_run_id": run_id}))
        assert result.status == "applied"
        assert repo.get("t-g3", "u-g3", tid).travel_stage == "cancelled"
