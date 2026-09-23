# -*- coding: utf-8 -*-
"""test_domain_state_isolation.py — 域状态 schema 隔离契约（STOP B B2/B3）

锁定三层隔离事实：
1. OrchestratorState：域上下文字段已全部登记（cs_context / travel_context /
   funnel_context / cs_pending_action）——LangGraph updates 流会剥离 schema
   外键，未登记 = 静默丢；SQL/RAG 无平铺域上下文（SQL 走 step_results，
   RAG 产物不进图 state）。
2. 域图私有 State 互不含他域字段：CSGraphState / TravelGraphState /
   SelectionFunnelState 是独立 TypedDict，跨域字段一旦出现即失守。
3. 域图输出契约（build_*_result / merge_cs_context / build_travel_context）
   是主图唯一入口：喂入被污染的 final_state，输出也不得携带他域键。
"""
from __future__ import annotations

from backend.customer_service.context import build_cs_context, merge_cs_context
from backend.customer_service.graph_state import CSGraphState
from backend.customer_service.models.graph_result import build_cs_graph_result
from backend.orchestration.state import AgentState, OrchestratorState
from backend.selection_funnel.graph_state import SelectionFunnelState
from backend.travel.graph_state import TravelGraphState, build_travel_context
from backend.travel.models.graph_result import build_travel_graph_result

_CS_ONLY = {"cs_context", "cs_route", "cs_action_result", "cs_audit_entries",
            "cs_pending_action", "pending_action", "confirmation_state",
            "handoff_state"}
# 只取旅游特有键（brief/stage/status 是各域状态机的通用命名，出现在
# 独立 TypedDict 中不构成泄漏——泄漏判据是「他域命名空间」字段）
_TRAVEL_ONLY = {"travel_context", "travel_route", "itinerary",
                "brief_fingerprint", "day_plan"}
_FUNNEL_ONLY = {"funnel_context", "pool", "run_id", "config_snapshot"}


class TestSharedSchemaRegistration:
    def test_domain_contexts_registered_in_orchestrator_state(self):
        """域上下文 + pending 透传字段必须在主图 schema 内（剥离坑防线）。"""
        for key in ("cs_context", "travel_context", "funnel_context",
                    "cs_pending_action"):
            assert key in OrchestratorState.__annotations__, key

    def test_identity_keys_registered(self):
        """身份平铺键（tenant_id 曾因未登记恒 None）必须持续在 schema。"""
        for key in ("user_id", "tenant_id", "session_id", "department",
                    "request_context", "routing_context"):
            assert key in AgentState.__annotations__, key

    def test_sql_and_selection_have_no_flat_context(self):
        """SQL/RAG 无平铺域上下文；选品域登记名是 funnel_context——
        sql_context / selection_context 出现即说明有人绕开登记纪律。"""
        for key in ("sql_context", "selection_context"):
            assert key not in AgentState.__annotations__
            assert key not in OrchestratorState.__annotations__


class TestDomainGraphStatePrivacy:
    def test_cs_graph_state_has_no_foreign_fields(self):
        leaked = _CS_ONLY & (
            set(TravelGraphState.__annotations__)
            | set(SelectionFunnelState.__annotations__))
        assert leaked == set()

    def test_travel_graph_state_has_no_foreign_fields(self):
        leaked = _TRAVEL_ONLY & (
            set(CSGraphState.__annotations__)
            | set(SelectionFunnelState.__annotations__))
        assert leaked == set()

    def test_funnel_graph_state_has_no_foreign_fields(self):
        leaked = _FUNNEL_ONLY & (
            set(CSGraphState.__annotations__)
            | set(TravelGraphState.__annotations__))
        assert leaked == set()


class TestAdapterContractPurity:
    """域图输出契约是主图唯一入口：喂入带他域键的 final_state，输出必须干净。"""

    def test_travel_result_drops_cs_fields(self):
        final_state = {
            "final_answer": "行程已生成",
            "brief": {"destination": "东京"},
            "brief_missing": [],
            "candidates": [{"poi_id": "p1"}],
            "itinerary": {"days": []},
            # 模拟域图内部被污染 / 上游残留
            "cs_context": {"cs_route": {"metadata": {"order_id": "A-123"}}},
            "cs_audit_entries": [{"detail": "should not leak"}],
        }
        result = build_travel_graph_result(final_state)
        blob = repr(result)
        assert "order_id" not in blob
        assert "cs_context" not in blob
        assert "cs_audit_entries" not in blob

    def test_cs_result_drops_travel_fields(self):
        final_state = {
            "final_answer": "已为您处理",
            "conversation_id": "c1",
            "cs_route": {"domain": "AFTER_SALES"},
            "cs_audit_entries": [],
            # 域图内部不应出现的旅游字段
            "itinerary": {"days": [{"items": []}]},
            "brief": {"destination": "东京"},
        }
        result = build_cs_graph_result(final_state)
        blob = repr(result)
        assert "itinerary" not in blob
        assert "destination" not in blob

    def test_travel_context_snapshot_has_no_cs_keys(self):
        state = {
            "brief": {"destination": "东京"},
            "brief_missing": [],
            "stage": "report",
            "cs_context": {"cs_route": {"metadata": {"order_id": "A-123"}}},
        }
        ctx = build_travel_context(state)
        assert not (set(ctx) & _CS_ONLY)

    def test_merge_cs_context_preserves_cs_namespace(self):
        original = build_cs_context(
            cs_route={"domain": "AFTER_SALES"},
            cs_target="action_expert",
            authenticated_user_id="u1",
            session_id="s1",
            conversation_id="c1",
            tenant_id="t1",
        )
        merged = merge_cs_context(original, {
            "conversation_id": "c2",
            "confirmation_state": "pending",
            # result 里混入的他域键不得进入 cs_context
            "travel_context": {"brief": {"destination": "东京"}},
            "funnel_context": {"category": "耳机"},
        })
        assert merged["conversation_id"] == "c2"
        assert merged["confirmation_state"] == "pending"
        assert "travel_context" not in merged
        assert "funnel_context" not in merged
