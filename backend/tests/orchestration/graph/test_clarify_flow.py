"""test_clarify_flow.py — 拒答转追问（L1 入口弱命中 + L2 拒答兜底）回归测试

覆盖：
  - clarify_content：弱命中判定、三语境追问内容、选项文案必命中对应域预过滤、
    防循环守卫（会话级连续追问上限）
  - router_node：L1 弱命中 → route_mode="clarify" 短路
  - route_selector / builder edge_map：clarify → reporter
  - reporter：L1 短文案（零 LLM）+ L2 拒答打标（技术错误不打标）
  - events：_clarify 标记 → clarification SSE 事件
"""
import pytest

from backend.config import REFUSAL_CLARIFY_ENABLED
from backend.orchestration.graph.travel_prefilter import is_travel_request
from backend.orchestration.graph.selection_funnel_prefilter import (
    is_selection_funnel_request,
)


# =====================================================
# clarify_content：L1 入口弱命中
# =====================================================

class TestEntryClarify:
    def test_weak_hit_single_signal_no_city(self, monkeypatch):
        """「帮我做个行程」：1 个信号词无城市 → 追问城市，选项必命中旅游域。"""
        from backend.orchestration.graph import clarify_content

        result = clarify_content.build_entry_clarify("帮我做个行程", domain_hint="")
        assert result is not None
        assert result["handoff_available"] is False
        assert result["options"], "必须提供可点选项"
        # 选项文案 = 用户话术，点击后原样重发，必须能进对应域
        for label in result["options"]:
            assert is_travel_request(label), f"选项未命中旅游域: {label}"

    def test_no_clarify_when_strong_hit(self):
        """强命中（≥2 信号词）走正常旅游域，不追问。"""
        from backend.orchestration.graph import clarify_content

        assert clarify_content.build_entry_clarify(
            "帮我规划一份福州2天的旅游行程", domain_hint="") is None

    def test_no_clarify_city_only_query(self):
        """「福州天气怎么样」：有城市无信号词，意图发散，不追问。"""
        from backend.orchestration.graph import clarify_content

        assert clarify_content.build_entry_clarify(
            "福州天气怎么样", domain_hint="") is None

    def test_no_clarify_no_signal(self):
        """完全无信号：交给 L2 兜底，入口不追问。"""
        from backend.orchestration.graph import clarify_content

        assert clarify_content.build_entry_clarify(
            "今天天气如何", domain_hint="") is None

    def test_no_clarify_cs_locked(self):
        """客服域锁下不做入口追问（设计约束：客服窗口不被业务追问打断）。"""
        from backend.orchestration.graph import clarify_content

        assert clarify_content.build_entry_clarify(
            "帮我做个行程", domain_hint="customer_service") is None

    def test_no_clarify_when_disabled(self, monkeypatch):
        """总开关关闭 → 全部回滚为原行为。"""
        from backend.orchestration.graph import clarify_content

        monkeypatch.setattr(clarify_content, "REFUSAL_CLARIFY_ENABLED", False)
        assert clarify_content.build_entry_clarify(
            "帮我做个行程", domain_hint="") is None

    def test_selection_weak_hit(self):
        """「宠物零食这个品类」：类目暗示无选品动词 → 追问。"""
        from backend.orchestration.graph import clarify_content

        result = clarify_content.build_entry_clarify(
            "宠物零食这个品类", domain_hint="")
        assert result is not None
        for label in result["options"]:
            assert is_selection_funnel_request(label), f"选项未命中选品域: {label}"


# =====================================================
# clarify_content：L2 拒答兜底
# =====================================================

class TestRefusalClarify:
    def test_cs_context_options_and_handoff(self):
        """客服窗口拒答 → CS 定向选项 + 转人工可用。"""
        from backend.customer_service.router.domain_detector import cs_rule_hit_count
        from backend.orchestration.graph import clarify_content

        result = clarify_content.build_refusal_clarify(
            "这个问题解决不了", domain_hint="customer_service")
        assert result is not None
        assert result["handoff_available"] is True
        for label in result["options"]:
            assert cs_rule_hit_count(label) > 0, f"CS 选项未命中客服规则: {label}"

    def test_normal_travel_lean(self):
        """普通问答、带旅游倾向的拒答（1 个信号词「攻略」）→ 旅游定向选项。"""
        from backend.orchestration.graph import clarify_content

        result = clarify_content.build_refusal_clarify(
            "给个攻略看看", domain_hint="")
        assert result is not None
        assert result["handoff_available"] is False
        for label in result["options"]:
            assert is_travel_request(label)

    def test_normal_no_lean_generic_navigation(self):
        """普通问答、无业务倾向（「想出去玩」不含任何信号词）→ 通用业务导航。"""
        from backend.orchestration.graph import clarify_content

        result = clarify_content.build_refusal_clarify(
            "想出去玩", domain_hint="")
        assert result is not None
        labels = result["options"]
        # 通用导航必须覆盖旅游/选品入口，且文案可被对应域预过滤命中
        assert any(is_travel_request(label) for label in labels)
        assert any(is_selection_funnel_request(label) for label in labels)


# =====================================================
# 防循环守卫
# =====================================================

class TestClarifyGuard:
    def _fresh_guard(self, monkeypatch):
        from backend.orchestration.graph import clarify_content

        class _FakeCache:
            def __init__(self):
                self._store = {}

            def get_json(self, key):
                return self._store.get(key)

            def set_json(self, key, value, ttl=None):
                self._store[key] = value

        fake = _FakeCache()
        monkeypatch.setattr(clarify_content, "_get_guard_cache", lambda: fake)
        return clarify_content

    def test_first_clarify_allowed_then_blocked(self, monkeypatch):
        cc = self._fresh_guard(monkeypatch)
        assert cc.clarify_allowed("sess-1") is True
        cc.mark_clarified("sess-1")
        assert cc.clarify_allowed("sess-1") is False

    def test_guard_error_fails_open(self, monkeypatch):
        """守卫故障时放行追问（宁可多问一次，不吞掉正常拒答转追问）。"""
        from backend.orchestration.graph import clarify_content

        class _BoomCache:
            def get_json(self, key):
                raise RuntimeError("cache down")

            def set_json(self, key, value, ttl=None):
                raise RuntimeError("cache down")

        monkeypatch.setattr(clarify_content, "_get_guard_cache", lambda: _BoomCache())
        assert clarify_content.clarify_allowed("sess-1") is True  # 不抛异常即放行


# =====================================================
# router_node L1 接线
# =====================================================

def test_router_weak_hit_short_circuits_to_clarify(monkeypatch):
    """弱命中 query → route_mode="clarify"，不进主 Router。"""
    import backend.orchestration.graph.cs_prefilter as cs_prefilter
    import backend.orchestration.graph.router_node as router_node
    import backend.orchestration.graph.selection_funnel_prefilter as sel_prefilter
    from backend.orchestration.graph import clarify_content
    from backend.orchestration.graph.travel_prefilter import try_travel_prefilter

    monkeypatch.setattr(clarify_content, "REFUSAL_CLARIFY_ENABLED", True)

    class _FreshCache:
        """隔离守卫缓存：Redis 是跨进程持久的，同 session 重跑会误判已追问。"""

        def get_json(self, key):
            return None

        def set_json(self, key, value, ttl=None):
            pass

    monkeypatch.setattr(clarify_content, "_get_guard_cache", lambda: _FreshCache())
    # CS 语义兜底（向量通道）在 L1 之前执行，测试中必须屏蔽
    monkeypatch.setattr(cs_prefilter, "try_cs_prefilter", lambda *a, **k: None)
    monkeypatch.setattr(sel_prefilter, "try_selection_funnel_prefilter",
                        lambda *a, **k: None)

    state = {"question": "帮我做个行程", "session_id": "s1", "domain_hint": ""}
    update = router_node.router_node(state)
    assert update["route_mode"] == "clarify"
    assert update["_clarify"]["options"]
    # 预过滤原语义不破坏
    assert try_travel_prefilter("帮我规划一份福州2天的旅游行程", state) is not None


def test_route_selector_clarify_branch():
    from backend.orchestration.graph.router_node import route_selector

    assert route_selector({"route_mode": "clarify"}) == "clarify"


def test_builder_edge_map_contains_clarify():
    """clarify 返回值必须能落到 reporter 节点（条件边显式映射）。"""
    from pathlib import Path

    builder = (Path(__file__).resolve().parents[3]
               / "orchestration" / "graph" / "builder.py")
    src = builder.read_text(encoding="utf-8")
    assert '"clarify": "reporter"' in src, "builder edge_map 缺少 clarify → reporter 映射"


# =====================================================
# reporter：L1 短文案 + L2 拒答打标
# =====================================================

def _refusal_step(output: str = "知识库暂无相关资料。") -> dict:
    return {"step_id": "1", "capability": "rag.search", "status": "success",
            "output": output, "error": ""}


def _tech_error_step() -> dict:
    return {"step_id": "1", "capability": "rag.search", "status": "failed",
            "output": "", "error": "psycopg2 connection timeout"}


class TestReporterClarify:
    def _patch_guard(self, monkeypatch, allowed: bool):
        from backend.orchestration.graph import clarify_content

        monkeypatch.setattr(clarify_content, "clarify_allowed", lambda sid: allowed)
        monkeypatch.setattr(clarify_content, "mark_clarified", lambda sid: None)
        monkeypatch.setattr(clarify_content, "REFUSAL_CLARIFY_ENABLED", True)

    def test_l1_short_text_no_llm(self, monkeypatch):
        """clarify 模式：reporter 输出短文案，绝不调 LLM。"""
        from backend.agents.reporter import reporter as reporter_mod

        def _boom(*a, **k):
            raise AssertionError("clarify 模式不应进入 LLM 汇总")

        monkeypatch.setattr(reporter_mod, "generate_final_answer", _boom)
        out = reporter_mod.reporter_node({"question": "帮我做个行程",
                                          "route_mode": "clarify",
                                          "step_results": {}})
        assert out["final_answer"]
        assert "_clarify" not in out  # 追问事件已由 router 节点发出，不重复

    def test_l2_refusal_attaches_clarify(self, monkeypatch):
        from backend.agents.reporter import reporter as reporter_mod

        self._patch_guard(monkeypatch, allowed=True)
        out = reporter_mod.reporter_node({
            "question": "出口退税税率是多少", "route_mode": "plan",
            "domain_hint": "", "session_id": "s1",
            "step_results": {"1": _refusal_step()},
        })
        assert "抱歉" in out["final_answer"]  # 拒答正文保留
        assert out["_clarify"]["options"]

    def test_l2_technical_error_no_clarify(self, monkeypatch):
        """技术性错误（服务不可用）不是拒答，不打追问标。"""
        from backend.agents.reporter import reporter as reporter_mod

        self._patch_guard(monkeypatch, allowed=True)
        out = reporter_mod.reporter_node({
            "question": "上月销量", "route_mode": "plan",
            "domain_hint": "", "session_id": "s1",
            "step_results": {"1": _tech_error_step()},
        })
        assert "_clarify" not in out

    def test_l2_loop_guard_blocks_second_clarify(self, monkeypatch):
        from backend.agents.reporter import reporter as reporter_mod

        self._patch_guard(monkeypatch, allowed=False)
        out = reporter_mod.reporter_node({
            "question": "出口退税税率是多少", "route_mode": "plan",
            "domain_hint": "", "session_id": "s1",
            "step_results": {"1": _refusal_step()},
        })
        assert "_clarify" not in out


# =====================================================
# events：_clarify 标记 → clarification 事件
# =====================================================

def test_stream_node_events_emits_clarification():
    from backend.orchestration.graph.events import stream_node_events

    marker = {"source": "entry_travel_city", "question": "想去哪个城市？",
              "options": ["规划福州2天的行程"], "handoff_available": False}
    events = list(stream_node_events(
        "router", {"route_mode": "clarify", "_clarify": marker},
        set(), lambda *a, **k: None, lambda *a, **k: None))
    clarify = [e for e in events if e["event"] == "clarification"]
    assert len(clarify) == 1
    data = clarify[0]["data"]
    assert data["question"] == "想去哪个城市？"
    assert data["options"][0]["label"] == "规划福州2天的行程"
    assert data["handoff_available"] is False


def test_stream_node_events_no_marker_no_event():
    from backend.orchestration.graph.events import stream_node_events

    events = list(stream_node_events(
        "router", {"route_mode": "plan"}, set(),
        lambda *a, **k: None, lambda *a, **k: None))
    assert not [e for e in events if e["event"] == "clarification"]


def test_refusal_clarify_flag_exists():
    """开关必须存在于 config（回滚路径）。"""
    assert REFUSAL_CLARIFY_ENABLED in (True, False)


def test_clarify_key_in_state_schema():
    """_clarify 必须入 state schema：LangGraph updates 流会剥离 schema 外的键，
    不声明则节点输出的追问标记到不了 events.py（实测 2026-09-19）。"""
    from backend.orchestration.state import OrchestratorState

    assert "_clarify" in OrchestratorState.__annotations__


# =====================================================
# cs_graph_node：L2 知识域拒答判定
# =====================================================

class TestCsRefusalClarify:
    def _patch_guard(self, monkeypatch, allowed: bool = True):
        from backend.orchestration.graph import clarify_content

        monkeypatch.setattr(clarify_content, "clarify_allowed", lambda sid: allowed)
        monkeypatch.setattr(clarify_content, "mark_clarified", lambda sid: None)
        monkeypatch.setattr(clarify_content, "REFUSAL_CLARIFY_ENABLED", True)

    def _final_state(self, **overrides) -> dict:
        state = {
            "supervisor_decision": {"next_action": "answer"},
            "cs_route": {"domain": "KNOWLEDGE"},
            "last_expert_result": {},
        }
        state.update(overrides)
        return state

    def test_knowledge_refusal_attaches_cs_options(self, monkeypatch):
        from backend.orchestration.graph.cs_graph_node import _refusal_clarify

        self._patch_guard(monkeypatch)
        marker = _refusal_clarify(self._final_state(), {"question": "保修范围", "session_id": "s1"})
        assert marker is not None
        assert marker["handoff_available"] is True

    def test_non_knowledge_domain_no_clarify(self, monkeypatch):
        from backend.orchestration.graph.cs_graph_node import _refusal_clarify

        self._patch_guard(monkeypatch)
        assert _refusal_clarify(
            self._final_state(cs_route={"domain": "TRANSACTION"}),
            {"question": "x", "session_id": "s1"}) is None

    def test_expert_answered_no_clarify(self, monkeypatch):
        from backend.orchestration.graph.cs_graph_node import _refusal_clarify

        self._patch_guard(monkeypatch)
        assert _refusal_clarify(
            self._final_state(last_expert_result={"response_draft": "答案"}),
            {"question": "x", "session_id": "s1"}) is None

    def test_handoff_pending_no_clarify(self, monkeypatch):
        from backend.orchestration.graph.cs_graph_node import _refusal_clarify

        self._patch_guard(monkeypatch)
        assert _refusal_clarify(
            self._final_state(supervisor_decision={"next_action": "handoff"}),
            {"question": "x", "session_id": "s1"}) is None

    def test_loop_guard_blocks(self, monkeypatch):
        from backend.orchestration.graph.cs_graph_node import _refusal_clarify

        self._patch_guard(monkeypatch, allowed=False)
        assert _refusal_clarify(
            self._final_state(), {"question": "x", "session_id": "s1"}) is None
