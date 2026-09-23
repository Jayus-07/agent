# -*- coding: utf-8 -*-
"""test_continuation_routing.py — 路由入口重构回归测试（2026-09-22）

覆盖目标架构 Guard → Context Assembler → ContinuationResolver → Coarse
Domain Router 的三个新环节：
  - ContinuationResolver：跨轮短指令判定（改单/反馈/续聊/序数）与切域放行
  - Context Assembler：routing_context 组装 + ConversationContext 回写
  - router_node：延续分派（travel）与 general_chat 分流（禁 RAG）
  - follow_up_resolver overwrite：天数短语不再被当目的地（D2 前置修复）
全部离线：不碰 embedding/pgvector/LLM/Redis。
"""
import pytest


# =====================================================
# ContinuationResolver：信号识别
# =====================================================

class TestIsContinuationQuery:
    @pytest.mark.parametrize("q", [
        "改成3天", "太赶了", "换一个", "便宜点", "继续", "第二个",
        "换成轻松节奏", "多排一天", "再便宜一点", "重新排",
    ])
    def test_signals_hit(self, q):
        from backend.orchestration.context.continuation_resolver import (
            is_continuation_query,
        )

        assert is_continuation_query(q), f"延续信号漏判: {q}"

    @pytest.mark.parametrize("q", [
        "统计本月订单金额", "查询今天库存不足的商品", "帮我排福州2天行程",
        "你好", "",
    ])
    def test_no_signal(self, q):
        from backend.orchestration.context.continuation_resolver import (
            is_continuation_query,
        )

        assert not is_continuation_query(q), f"误判延续信号: {q}"

    def test_overlong_query_not_continuation(self):
        from backend.orchestration.context.continuation_resolver import (
            is_continuation_query,
        )

        assert not is_continuation_query("继续" * 30)


# =====================================================
# ContinuationResolver：判定 + 切域放行
# =====================================================

class TestResolveContinuation:
    def test_no_active_domain_never_continuation(self):
        from backend.orchestration.context.continuation_resolver import (
            resolve_continuation,
        )

        r = resolve_continuation("改成3天", {"active_domain": ""})
        assert r["is_continuation"] is False
        assert r["reason"] == "no_active_domain"

    def test_travel_continuation_hit(self):
        from backend.orchestration.context.continuation_resolver import (
            resolve_continuation,
        )

        for q in ("改成3天", "太赶了", "换一个", "便宜点", "继续", "第二个"):
            r = resolve_continuation(q, {"active_domain": "travel"})
            assert r["is_continuation"] is True, f"{q} 应判定为延续"
            assert r["domain"] == "travel"
            assert r["reason"] == "continuation_hit"

    def test_domain_switch_allowed_on_new_business_signal(self):
        """新强业务信号 → 放行正常路由（允许切域）。"""
        from backend.orchestration.context.continuation_resolver import (
            resolve_continuation,
        )

        # 数据/SQL 强意图（活跃域是 travel）
        r = resolve_continuation("太赶了，先统计本月订单金额", {"active_domain": "travel"})
        assert r["is_continuation"] is False
        assert r["reason"] == "domain_switch_allowed"

    def test_same_domain_signal_not_foreign(self):
        """活跃域是 data 时，数据类词不算外部信号，延续仍生效。"""
        from backend.orchestration.context.continuation_resolver import (
            resolve_continuation,
        )

        r = resolve_continuation("继续", {"active_domain": "data"})
        assert r["is_continuation"] is True

    def test_disabled_flag(self, monkeypatch):
        import backend.orchestration.context.continuation_resolver as mod

        monkeypatch.setattr(mod, "CONTINUATION_RESOLVER_ENABLED", False)
        r = mod.resolve_continuation("改成3天", {"active_domain": "travel"})
        assert r["is_continuation"] is False
        assert r["reason"] == "disabled"


# =====================================================
# Context Assembler：组装 + 回写
# =====================================================

class TestRoutingContextAssembler:
    def test_empty_context_when_no_session(self):
        from backend.orchestration.context.routing_context import (
            assemble_routing_context,
        )

        ctx = assemble_routing_context("t", "u", "")
        assert ctx["active_domain"] == ""
        assert ctx["brief_summary"] == {}

    def test_mark_and_assemble_roundtrip(self, _memory_context_repo):
        from backend.orchestration.context.routing_context import (
            assemble_routing_context,
            mark_domain_turn,
        )

        key = ("t1", "u1", "cont-test-1")
        _memory_context_repo.delete(*key)
        mark_domain_turn(*key, domain="travel", intent="travel",
                         action="travel", pending_question="玩几天？")
        ctx = assemble_routing_context(*key)
        assert ctx["active_domain"] == "travel"
        assert ctx["last_intent"] == "travel"
        assert ctx["pending_question"] == "玩几天？"
        assert "destination" in ctx["brief_summary"]
        _memory_context_repo.delete(*key)

    def test_pending_question_kept_when_none(self, _memory_context_repo):
        from backend.orchestration.context.routing_context import mark_domain_turn

        key = ("t1", "u1", "cont-test-2")
        _memory_context_repo.delete(*key)
        mark_domain_turn(*key, domain="travel", pending_question="玩几天？")
        # 后续正常路由（无新追问）不清空旧待答问题
        mark_domain_turn(*key, domain="travel", intent="travel")
        snap = _memory_context_repo.get(*key).snapshot()
        assert snap["pending_question"] == "玩几天？"
        _memory_context_repo.delete(*key)


# =====================================================
# follow_up_resolver：overwrite 不吃天数短语（D2 前置修复）
# =====================================================

class TestOverwriteDayGuard:
    def test_day_count_not_destination(self):
        from backend.orchestration.context.follow_up_resolver import _detect_overwrite

        assert _detect_overwrite("改成3天") is None
        assert _detect_overwrite("换成3天") is None
        assert _detect_overwrite("改成3天，然后加一天") is None

    def test_real_destination_still_overwrites(self):
        from backend.orchestration.context.follow_up_resolver import _detect_overwrite

        assert _detect_overwrite("算了，改成成都") == "成都"
        # 注：「换成厦门吧」捕获「厦门吧」是既有行为（可选「吧」在贪婪
        # 字符类之后），非本次改动范围；这里只验证不带语气词的形态。
        assert _detect_overwrite("换到厦门") == "厦门"


# =====================================================
# router_node：延续分派 + general_chat 分流
# =====================================================

def _base_state(question: str, **extra) -> dict:
    state = {
        "question": question,
        "session_id": "s-cont-test",
        "user_id": "u1",
        "tenant_id": "t1",
        "domain_hint": "",
        "guard_result": {"category": "business_query"},
        "routing_context": {},
    }
    state.update(extra)
    return state


class TestRouterNodeContinuation:
    def test_travel_continuation_dispatch(self):
        """D2/D3 核心：跨轮短指令直接回旅游域图。"""
        import backend.orchestration.graph.router_node as rn

        state = _base_state("太赶了",
                            routing_context={"active_domain": "travel"})
        update = rn.router_node(state)
        assert update["route_mode"] == "travel"
        assert update["travel_context"]["travel_route"]["source"] == "continuation"

    def test_no_continuation_without_active_domain(self, monkeypatch):
        """无活跃任务：同样短指令不触发延续，交正常路由。"""
        import backend.orchestration.graph.cs_prefilter as cs_prefilter
        import backend.orchestration.graph.router_node as rn

        monkeypatch.setattr(cs_prefilter, "try_cs_prefilter", lambda *a, **k: None)
        update = rn.router_node(_base_state("太赶了"))
        assert update.get("route_mode") != "travel"

    def test_general_chat_via_guard_greeting(self):
        """D7：问候/能力咨询 → general_chat 直答（不进 RAG/域图）。"""
        import backend.orchestration.graph.router_node as rn

        state = _base_state("你好，介绍下你自己",
                            guard_result={"category": "greeting"})
        update = rn.router_node(state)
        assert update["route_mode"] == "general_chat"
        assert update["route_decision"] is None

    def test_cs_window_not_intercepted_by_general_chat(self):
        """客服窗口锁域：寒暄仍进 CS 管线，不被 general_chat 抢走。"""
        import backend.orchestration.graph.router_node as rn

        state = _base_state("你好",
                            domain_hint="customer_service",
                            guard_result={"category": "greeting"})
        update = rn.router_node(state)
        # cs_forced 路径：走 CS prefilter 结果（此测试环境未 mock，绝不可能是 general_chat）
        assert update.get("route_mode") != "general_chat"

    def test_route_selector_general_chat(self):
        from backend.orchestration.graph.router_node import route_selector

        assert route_selector({"route_mode": "general_chat"}) == "general_chat"


# =====================================================
# general_chat 节点 + builder 接线
# =====================================================

class TestGeneralChatNode:
    def test_llm_failure_falls_back(self, monkeypatch):
        """LLM 故障 → 静态话术兜底，绝不抛异常拖垮主图。"""
        from backend.orchestration.graph import general_chat_node as g

        class _Boom:
            def stream(self, msgs):
                raise RuntimeError("llm down")

            def invoke(self, msgs):
                raise RuntimeError("llm down")

        import backend.infra.llm as llm_mod

        monkeypatch.setattr(llm_mod, "llm", _Boom())
        out = g.general_chat_node({"question": "你好", "messages": []})
        assert "final_answer" in out and out["final_answer"]

    def test_no_tool_calls_structure(self, monkeypatch):
        """节点输出只有 final_answer，不产出 plan/step_results（禁工具路径）。"""
        from backend.orchestration.graph import general_chat_node as g

        class _FakeLLM:
            def invoke(self, msgs):
                return type("R", (), {"content": "你好！我是企业智能运营助手。"})()

        import backend.infra.llm as llm_mod

        monkeypatch.setattr(llm_mod, "llm", _FakeLLM())
        monkeypatch.setattr(
            "backend.config.ENABLE_TOKEN_STREAMING", False)
        out = g.general_chat_node({"question": "你能做什么", "messages": []})
        assert set(out.keys()) == {"final_answer"}

    def test_builder_wired(self):
        """builder edge_map 与 END 边必须包含 general_chat。"""
        from pathlib import Path

        src = (Path(__file__).resolve().parents[3]
               / "orchestration" / "graph" / "builder.py").read_text(encoding="utf-8")
        assert '"general_chat": "general_chat"' in src
        assert 'wf.add_edge("general_chat", END)' in src


# =====================================================
# hierarchical：general 域 → general_chat
# =====================================================

def test_hierarchical_general_domain_dispatches_general_chat(monkeypatch):
    import backend.orchestration.router.hierarchical as hier
    from backend.orchestration.router.hierarchical import HierarchicalRouter
    from backend.orchestration.router.domain_classifier import DomainPrediction

    pred = DomainPrediction(
        domain="general", confidence=0.9, second_domain="data",
        second_confidence=0.3, margin=0.6,
        source="classifier", reason_code="DOMAIN_CONFIDENT",
    )
    r = HierarchicalRouter()
    r.rule = type("R", (), {"route": staticmethod(lambda q: None)})()
    r.classifier = type("Stub", (), {
        "classify": staticmethod(lambda q, c=None: pred)})()
    d = r.route("你好")
    assert d.routing_meta["domain_action"] == "general_chat"
