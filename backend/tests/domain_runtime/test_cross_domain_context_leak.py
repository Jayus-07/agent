# -*- coding: utf-8 -*-
"""test_cross_domain_context_leak.py — 跨域上下文泄漏（STOP B B8）

三个 B8 靶点 + 适配器边界实测：
1. CS order_id → 切 travel：travel_graph_node 构造的域图输入只含 5 个白名单
   键，CS 上下文（含订单）不得混入（FakeGraph 捕获真实 invoke 入参）。
2. Travel destination → 切 CS：cs_graph_node 构造的输入不含 travel/itinerary
   任何字段（同样捕获真实入参）。
3. Selection category / Travel destination → General：general_chat 不读任何
   域上下文（模块源不含域上下文引用， tripwire）。
4. SQL 不自动继承旅游实体：SQL skill 模块源不含域上下文引用（无注入通道
   的结构性证明；行为面由 sql.read 权限门 + 六层校验兜底）。
"""
from __future__ import annotations

import inspect

import backend.orchestration.graph.cs_graph_node as cs_adapter
import backend.orchestration.graph.travel_graph_node as travel_adapter
from backend.customer_service.graph_state import new_cs_graph_input
from backend.selection_funnel.graph_state import new_selection_funnel_graph_input
from backend.travel.graph_state import new_travel_graph_input

_TRAVEL_INPUT_KEYS = {"user_message", "user_id", "session_id",
                      "conversation_id", "travel_route"}


class _FakeTravelGraph:
    def __init__(self):
        self.captured_input: dict | None = None
        self.captured_config: dict | None = None

    def get_state(self, config):
        return None  # 无 checkpoint → resume_mode=fresh（不触 ConversationContext）

    def invoke(self, graph_input, config):
        self.captured_input = dict(graph_input)
        self.captured_config = dict(config)
        return {
            "final_answer": "请告诉我出发日期？",
            "brief": {},
            "brief_missing": ["start_date"],
            "clarifications": ["请问哪天出发？"],
        }


class _FakeCSGraph:
    def __init__(self):
        self.captured_input: dict | None = None

    def invoke(self, graph_input, config):
        self.captured_input = dict(graph_input)
        return {
            "final_answer": "已为您登记退款申请",
            "conversation_id": "c9",
            "cs_action_result": {},
            "cs_audit_entries": [],
            "handoff_state": "",
            "confirmation_state": "",
            "pending_action": None,
            "supervisor_decision": {"next_action": "finish"},
            "cs_route": {"domain": "AFTER_SALES"},
            "last_expert_result": {"data": {"ok": True}},
            "expert_history": [{"node": "cs_action_expert"}],
        }


class TestCSOrderDoesNotLeakIntoTravel:
    def test_travel_input_whitelist_ignores_cs_context(
        self, monkeypatch,
    ):
        fake = _FakeTravelGraph()
        monkeypatch.setattr(travel_adapter, "get_travel_graph", lambda: fake)
        monkeypatch.setattr(
            "backend.orchestration.context.context_repository."
            "get_conversation_context_repository",
            lambda: type("_Repo", (), {"peek": lambda self, *a: None})(),
        )
        monkeypatch.setattr(
            "backend.orchestration.context.conversation_context."
            "sync_travel_run_to_context", lambda *a, **k: "")
        monkeypatch.setattr(
            "backend.orchestration.context.conversation_context."
            "mark_travel_run_completed", lambda *a, **k: None)

        state = {
            "question": "东京玩三天，两个人",
            "session_id": "s-leak",
            "user_id": "u9",
            "tenant_id": "t9",
            # 上一轮 CS 留下的订单上下文（B8 靶点的污染源）
            "cs_context": {
                "conversation_id": "s-leak",
                "cs_route": {"metadata": {"order_id": "MO-1001"}},
                "confirmation_state": "pending_confirmation",
                "pending_action": {"action_type": "apply_refund",
                                   "target_id": "MO-1001"},
            },
            "travel_context": {"conversation_id": "s-leak",
                               "travel_route": {"source": "prefilter"}},
        }
        travel_adapter.travel_graph_node(state)

        assert fake.captured_input is not None
        assert set(fake.captured_input) == _TRAVEL_INPUT_KEYS
        blob = repr(fake.captured_input)
        assert "MO-1001" not in blob          # CS 订单号不得进入旅游域图
        assert "order_id" not in blob
        assert "apply_refund" not in blob     # CS 待确认动作不得进入


class TestTravelDestinationDoesNotLeakIntoCS:
    def test_cs_input_contains_no_travel_fields(self, monkeypatch):
        fake = _FakeCSGraph()
        monkeypatch.setattr(cs_adapter, "get_cs_graph", lambda: fake)

        state = {
            "question": "退款怎么处理",
            "session_id": "s-leak",
            "user_id": "u9",
            "tenant_id": "t9",
            # 上一轮旅游留下的行程（B8 靶点的污染源）
            "travel_context": {
                "conversation_id": "s-leak",
                "brief": {"destination": "东京", "days": 3},
                "itinerary": {"days": [{"items": [{"poi_id": "p1"}]}]},
            },
            "cs_context": {
                "authenticated_user_id": "u9",
                "session_id": "s-leak",
                "conversation_id": "s-leak",
                "cs_route": {"domain": "AFTER_SALES"},
                "tenant_id": "t9",
            },
        }
        cs_adapter.cs_graph_node(state)

        assert fake.captured_input is not None
        blob = repr(fake.captured_input)
        assert "东京" not in blob             # 旅游目的地不得成为 CS 业务实体
        assert "itinerary" not in blob
        assert "travel_context" not in blob
        # 输入键必须落在新域图声明的输入契约内
        contract = set(new_cs_graph_input(
            user_message="", user_id="", session_id="", conversation_id="",
            cs_route={}))
        assert set(fake.captured_input) <= contract


class TestGeneralAndSQLHaveNoDomainContextChannel:
    def test_general_chat_never_reads_domain_contexts(self):
        """B8：Selection category → General 不成为 system-level state。"""
        from backend.orchestration.graph import general_chat_node
        src = inspect.getsource(general_chat_node)
        for key in ("cs_context", "travel_context", "funnel_context"):
            assert key not in src, key

    def test_sql_skill_never_reads_domain_contexts(self):
        """B8：Travel destination 不被 SQL 当业务实体——无注入通道的结构性
        证明（SQL 只吃 question/plan 产物；行为面另有权限门兜底）。"""
        from backend.skills.sql import skill as sql_skill
        src = inspect.getsource(sql_skill)
        for key in ("travel_context", "cs_context", "funnel_context"):
            assert key not in src, key

    def test_funnel_input_keys_are_whitelisted(self):
        out = new_selection_funnel_graph_input(
            user_message="q", user_id="u", session_id="s",
            conversation_id="c", funnel_context={"category": "耳机"},
            run_id="r1")
        assert set(out) == {"user_message", "user_id", "session_id",
                            "conversation_id", "funnel_context", "run_id"}

    def test_travel_input_builder_keys_exact(self):
        out = new_travel_graph_input(
            user_message="q", user_id="u", session_id="s",
            conversation_id="c", travel_route={"source": "prefilter"})
        assert set(out) == _TRAVEL_INPUT_KEYS
