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
    def test_travel_weak_hit_no_longer_clarifies(self):
        """路由入口重构（2026-09-22）：旅游弱命中不再全局追问——

        「帮我做个行程」直接进旅游域图，由 brief 节点在域内追问目的地/天数
        （参数缺失由域内处理）；入口追问只保留选品类目分支。
        """
        from backend.orchestration.graph import clarify_content

        assert clarify_content.build_entry_clarify(
            "帮我做个行程", domain_hint="") is None
        assert clarify_content.build_entry_clarify(
            "帮我规划个行程", domain_hint="") is None
        # 同时保证这类请求确实能进旅游域（预过滤命中 → 域图 brief 追问）
        assert is_travel_request("帮我规划个行程")

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

    def test_sql_lean_gets_sql_slot_card(self):
        """实机缺口回归（2026-10-03）：「查一下经营数据」被向量路由拦成
        clarify 后，拒答兜底必须给 SQL 槽位卡（宽词表识别数据诉求），
        不能落通用导航——SQL 根本没执行，reporter 的 executed 卡触达不了。"""
        from backend.orchestration.graph import clarify_content

        result = clarify_content.build_refusal_clarify(
            "查一下经营数据", domain_hint="")
        assert result["source"] == "refusal_sql_empty"
        # 路由层语义（还没执行查询）：引导语不含「没有查到」
        assert "没有查到" not in result["question"]
        from backend.orchestration.router.rule_router import RuleRouter
        for label in result["options"]:
            decision = RuleRouter().route(label)
            assert decision is not None and decision.confidence >= 0.85, label

    def test_travel_lean_outranks_sql_lean(self):
        """既有优先级不破坏：旅游倾向优先于 SQL 倾向。"""
        from backend.orchestration.graph import clarify_content

        result = clarify_content.build_refusal_clarify(
            "查一下福州的旅游攻略数据", domain_hint="")
        assert result["source"] == "refusal_travel_lean"


# =====================================================
# 防循环守卫（2026-10-03 企业口径：问题级去重 + 会话封顶）
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

            def delete(self, key):
                self._store.pop(key, None)

            def incr(self, key, ttl=None):
                """与 InMemoryCache.incr 同语义：首建设窗，续增不重置。"""
                self._store[key] = (self._store.get(key) or 0) + 1
                return self._store[key]

        fake = _FakeCache()
        monkeypatch.setattr(clarify_content, "_get_guard_cache", lambda: fake)
        return clarify_content, fake

    def test_same_question_dedup_different_question_allowed(self, monkeypatch):
        """企业口径核心：同一问题不重复追问；换个问法允许再问。

        旧「会话 10 分钟一次性」语义把同句重问变成裸拒答（行为翻转，
        实测 2026-10-03 会话），本用例即该缺陷的回归门。
        """
        cc, _ = self._fresh_guard(monkeypatch)
        assert cc.clarify_allowed("sess-1", "什么时候放假") is True
        cc.mark_clarified("sess-1", "什么时候放假",
                          options=["查一下经营数据"], source="refusal_generic")
        assert cc.clarify_allowed("sess-1", "什么时候放假") is False
        # 换个问法（或空白/大小写差异之外的新问法）不被去重拦截
        assert cc.clarify_allowed("sess-1", "查一下经营数据") is True

    def test_question_normalization_ignores_whitespace_case(self, monkeypatch):
        cc, _ = self._fresh_guard(monkeypatch)
        cc.mark_clarified("sess-1", "什么时候 放假")
        assert cc.clarify_allowed("sess-1", "  什么时候放假 ") is False

    def test_session_cap_blocks_after_limit(self, monkeypatch):
        """会话封顶：窗口内第 3 次起一律不再追问（降级裸拒答）。"""
        cc, fake = self._fresh_guard(monkeypatch)
        cc.mark_clarified("sess-1", "问题一")
        cc.mark_clarified("sess-1", "问题二")
        assert cc.clarify_allowed("sess-1", "问题一") is False  # 去重
        assert cc.clarify_allowed("sess-1", "问题三") is False  # 封顶
        # 计数原子递增且达到上限值
        assert fake._store["count:sess-1"] == 2

    def test_other_session_unaffected(self, monkeypatch):
        cc, _ = self._fresh_guard(monkeypatch)
        cc.mark_clarified("sess-1", "问题一")
        assert cc.clarify_allowed("sess-2", "问题一") is True

    def test_guard_error_fails_open(self, monkeypatch):
        """守卫故障时放行追问（宁可多问一次，不吞掉正常拒答转追问）。"""
        from backend.orchestration.graph import clarify_content

        class _BoomCache:
            def get_json(self, key):
                raise RuntimeError("cache down")

            def set_json(self, key, value, ttl=None):
                raise RuntimeError("cache down")

        monkeypatch.setattr(clarify_content, "_get_guard_cache", lambda: _BoomCache())
        assert clarify_content.clarify_allowed("sess-1", "任意问题") is True  # 不抛异常即放行

    def test_click_detection_consumes_once(self, monkeypatch):
        """选项点击检测：命中（含归一化）消费一次；未命中不消费。"""
        cc, _ = self._fresh_guard(monkeypatch)
        cc.mark_clarified("sess-1", "想出去玩",
                          options=["查一下经营数据", "帮我规划一份旅游行程"],
                          source="refusal_generic")
        # 命中：空白差异归一化后仍匹配
        hit = cc.consume_clarify_click("sess-1", "查一下 经营数据 ")
        assert hit == {"source": "refusal_generic"}
        # 消费后同一选项不再命中
        assert cc.consume_clarify_click("sess-1", "查一下经营数据") is None
        # 未命中不消费暂存
        cc.mark_clarified("sess-1", "x", options=["选项A"], source="s")
        assert cc.consume_clarify_click("sess-1", "自由发言") is None
        assert cc.consume_clarify_click("sess-1", "选项A") == {"source": "s"}


class TestSqlEmptyClarify:
    def test_sql_empty_card_content(self):
        from backend.orchestration.graph import clarify_content

        marker = clarify_content.build_sql_empty_clarify("查一下经营数据")
        assert marker["source"] == "refusal_sql_empty"
        assert marker["handoff_available"] is False
        assert marker["options"]

    def test_sql_options_route_strong_to_sql(self):
        """契约：SQL 追问选项必须被 RuleRouter 以强信号直拍 sql.query。

        选项文案=用户话术（点击即重发），路由不可达的选项是死胡同卡片。
        强信号门槛 = confidence≥0.85（3 个以上 rule_keywords 命中），
        不允许依赖向量层兜底。
        """
        from backend.orchestration.graph.clarify_content import _SQL_EMPTY_OPTIONS
        from backend.orchestration.router.rule_router import RuleRouter

        router = RuleRouter()
        for label in _SQL_EMPTY_OPTIONS:
            decision = router.route(label)
            assert decision is not None, f"选项未被规则路由命中: {label}"
            assert decision.execution_mode.value == "direct", label
            top = decision.candidates[0]
            assert top.name == "sql.query", f"选项被拍给 {top.name}: {label}"
            assert decision.confidence >= 0.85, f"非强信号({decision.confidence}): {label}"


# =====================================================
# router_node L1 接线
# =====================================================

def test_router_weak_hit_short_circuits_to_clarify(monkeypatch):
    """选品类目弱命中 query → route_mode="clarify"，不进主 Router。

    路由入口重构（2026-09-22）后 L1 追问仅剩选品类目分支；旅游弱命中
    改为进旅游域图由 brief 节点追问（见 TestEntryClarify）。
    """
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

        def delete(self, key):
            pass

        def incr(self, key, ttl=None):
            return 1

    monkeypatch.setattr(clarify_content, "_get_guard_cache", lambda: _FreshCache())
    # CS 兜底在 L1 之前执行（检测器已无向量通道），测试中必须屏蔽
    monkeypatch.setattr(cs_prefilter, "try_cs_prefilter", lambda *a, **k: None)
    # 选品预过滤不命中（类目暗示无选品动词）才会走到 L1 追问，屏蔽之
    monkeypatch.setattr(sel_prefilter, "try_selection_funnel_prefilter",
                        lambda *a, **k: None)

    state = {"question": "宠物零食这个品类", "session_id": "s1", "domain_hint": ""}
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

        monkeypatch.setattr(clarify_content, "clarify_allowed",
                            lambda sid, q="": allowed)
        monkeypatch.setattr(clarify_content, "mark_clarified",
                            lambda sid, q="", **k: None)
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

    def test_guard_clarify_preserves_security_message(self, monkeypatch):
        """CSInputGuard 短路时不能被通用追问文案覆盖。"""
        from backend.agents.reporter import reporter as reporter_mod

        out = reporter_mod.reporter_node({
            "route_mode": "clarify",
            "final_answer": "您只能查询和操作自己的数据。",
        })

        assert out["final_answer"] == "您只能查询和操作自己的数据。"

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


def _sql_empty_step() -> dict:
    """SQL 业务性空结果（查不到）：status=success 但输出是标准空话术。"""
    return {"step_id": "1", "capability": "sql.query", "status": "success",
            "output": "未找到相关信息", "error": ""}


class TestReporterSqlEmptyClarify:
    def _patch_guard(self, monkeypatch, allowed: bool = True):
        from backend.orchestration.graph import clarify_content

        monkeypatch.setattr(clarify_content, "clarify_allowed",
                            lambda sid, q="": allowed)
        monkeypatch.setattr(clarify_content, "mark_clarified",
                            lambda sid, q="", **k: None)
        monkeypatch.setattr(clarify_content, "REFUSAL_CLARIFY_ENABLED", True)

    def test_sql_empty_attaches_sql_slot_clarify(self, monkeypatch):
        """SQL 查不到 → 定向槽位追问卡（时间范围/常用指标），非通用导航。"""
        from backend.agents.reporter import reporter as reporter_mod

        self._patch_guard(monkeypatch)
        out = reporter_mod.reporter_node({
            "question": "查一下经营数据", "route_mode": "direct",
            "domain_hint": "", "session_id": "s1",
            "step_results": {"1": _sql_empty_step()},
        })
        assert "抱歉" in out["final_answer"]
        assert out["_clarify"]["source"] == "refusal_sql_empty"
        assert out["_clarify"]["options"]

    def test_rag_miss_keeps_generic_clarify(self, monkeypatch):
        """纯 RAG 拒答不走 SQL 卡（无 sql.query 步骤）。"""
        from backend.agents.reporter import reporter as reporter_mod

        self._patch_guard(monkeypatch)
        out = reporter_mod.reporter_node({
            "question": "出口退税税率是多少", "route_mode": "plan",
            "domain_hint": "", "session_id": "s1",
            "step_results": {"1": _refusal_step()},
        })
        assert out["_clarify"]["source"] != "refusal_sql_empty"


class TestReporterUnansweredLog:
    """未答问题旁路登记：拒答必留痕（与追问守卫解耦），技术故障不留。"""

    def _patch_recorder(self, monkeypatch) -> list[dict]:
        calls: list[dict] = []

        def _rec(question, *, source, **kwargs):
            calls.append({"question": question, "source": source, **kwargs})
            return True

        import backend.observability.unanswered as unanswered_mod
        monkeypatch.setattr(unanswered_mod, "record_unanswered_question", _rec)
        return calls

    def test_rag_miss_logged(self, monkeypatch):
        from backend.agents.reporter import reporter as reporter_mod

        calls = self._patch_recorder(monkeypatch)
        reporter_mod.reporter_node({
            "question": "出口退税税率是多少", "route_mode": "plan",
            "domain_hint": "", "session_id": "s1",
            "step_results": {"1": _refusal_step()},
        })
        assert len(calls) == 1
        assert calls[0]["source"] == "rag_miss"
        assert calls[0]["question"] == "出口退税税率是多少"

    def test_sql_empty_logged_as_sql_empty(self, monkeypatch):
        from backend.agents.reporter import reporter as reporter_mod

        calls = self._patch_recorder(monkeypatch)
        reporter_mod.reporter_node({
            "question": "查一下经营数据", "route_mode": "direct",
            "domain_hint": "", "session_id": "s1",
            "step_results": {"1": _sql_empty_step()},
        })
        assert calls[0]["source"] == "sql_empty"

    def test_technical_error_not_logged(self, monkeypatch):
        """服务不可用是故障不是知识缺口，不进运营清单。"""
        from backend.agents.reporter import reporter as reporter_mod

        calls = self._patch_recorder(monkeypatch)
        reporter_mod.reporter_node({
            "question": "上月销量", "route_mode": "plan",
            "domain_hint": "", "session_id": "s1",
            "step_results": {"1": _tech_error_step()},
        })
        assert calls == []

    def test_normal_answer_not_logged(self, monkeypatch):
        from backend.agents.reporter import reporter as reporter_mod

        calls = self._patch_recorder(monkeypatch)
        reporter_mod.reporter_node({
            "question": "正常问题", "route_mode": "direct",
            "domain_hint": "", "session_id": "s1",
            "step_results": {"1": {"step_id": "1", "capability": "rag.search",
                                   "status": "success",
                                   "output": "这是一段足够长的正常回答内容。",
                                   "error": ""}},
        })
        assert calls == []


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

        monkeypatch.setattr(clarify_content, "clarify_allowed",
                            lambda sid, q="": allowed)
        monkeypatch.setattr(clarify_content, "mark_clarified",
                            lambda sid, q="", **k: None)
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
