"""trace 链路修复回归测试（2026-08-21 浏览器实测整改）。

覆盖四组修复：
1. 嵌套 trace：agent 图内嵌 RAGChain.ask() 不再劫持外层 trace ——
   start() 建立父子链，finish() 恢复父 trace 为 current。
2. 同名 span_id 计时独立：timer 绑在 Span 对象上，两个同名 span
   各自计各自时长（此前 dict 按 span_id 覆盖导致一方 duration 归零）。
3. _trace_from_state 合成兜底：中间件已记录真实时长 span 时不再合成
   重复项；未执行节点（direct 模式跳过 planner/critique/supervisor）
   不合成 0ms 占位噪声；失败步骤无错误详情时 metrics.error 有兜底文案。
4. Observability DTO SLA：用 trace 自身 sla_threshold_ms，不再硬编码 10s。
"""
import time

import pytest

# 循环导入规避：先加载 orchestration.graph 入口（server 生产路径同款），
# 避免经 backend.agents.__init__ → planner → tool_registry 的环。
import backend.orchestration.graph  # noqa: F401

from backend.observability.tracer import TraceCollector, TraceRecord, _current_trace_var
from backend.orchestration.graph.system import MultiAgentSystem


@pytest.fixture(autouse=True)
def _clean_ctx():
    _current_trace_var.set(None)
    yield
    _current_trace_var.set(None)


# =====================================================
# 1. 嵌套 trace — 父子链 + finish 恢复外层
# =====================================================

class TestNestedTrace:
    def test_nested_start_links_parent_child(self):
        tc = TraceCollector()
        outer = tc.start("外层问题")
        inner = tc.start("内层问题")  # 模拟 RAGChain.ask() 嵌套
        assert inner.parent_id == outer.id
        assert inner.id in outer.children_ids
        assert tc.current() is inner

    def test_finish_inner_restores_outer(self):
        """内层 finish 后外层 trace 恢复为 current —— 后续 span 不落 noop。"""
        tc = TraceCollector()
        outer = tc.start("外层")
        inner = tc.start("内层")
        tc.finish(inner, "ans", 10, "m")
        assert tc.current() is outer
        # 外层继续记 span 仍是真实 span（非 noop）
        span = tc.start_span("after_inner")
        assert span.sequence >= 0
        tc.end_span(span)
        tc.finish(outer, "final", 100, "m")
        assert tc.current() is None

    def test_top_level_finish_clears_current(self):
        tc = TraceCollector()
        t = tc.start("单层")
        tc.finish(t, "a", 10, "m")
        assert tc.current() is None


# =====================================================
# 2. 同名 span_id 计时独立
# =====================================================

class TestSpanTimerIsolation:
    def test_same_span_id_durations_independent(self):
        """中间件与 router 内部同名 'router' span 不再互相覆盖计时。"""
        tc = TraceCollector()
        t = tc.start("q")
        s1 = tc.start_span("router")       # 模拟中间件外层
        s2 = tc.start_span("router")       # 模拟 router.route() 内部
        time.sleep(0.02)
        tc.end_span(s2)
        time.sleep(0.02)
        tc.end_span(s1)
        tc.finish(t, "a", 100, "m")
        # s2 只含第一段睡眠；s1 含两段 —— 若 timer 被覆盖，s1 会归零
        assert s2.duration_ms >= 15
        assert s1.duration_ms > s2.duration_ms


# =====================================================
# 3. _trace_from_state 去重 + error 兜底
# =====================================================

def _mk_trace():
    return TraceRecord(id="t1", question="q")


def _run_trace_from_state(trace, state):
    # _trace_from_state 不使用 self，传占位对象即可
    MultiAgentSystem._trace_from_state(object(), trace, state)


class TestTraceFromStateDedup:
    def test_no_duplicate_spans_when_middleware_present(self):
        """中间件已记录 planner/critique/reporter/skill span → 不合成重复项。"""
        trace = _mk_trace()
        from backend.observability.tracer import Span
        # 模拟中间件真实 span
        trace.spans.append(Span(span_id="planner", parent_id="root",
                                name="任务规划", type="agent", duration_ms=50))
        trace.spans.append(Span(span_id="critique", parent_id="root",
                                name="计划审查", type="agent", duration_ms=30))
        trace.spans.append(Span(span_id="reporter", parent_id="root",
                                name="结果汇总", type="agent", duration_ms=20))
        trace.spans.append(Span(span_id="rag_skill:s1", parent_id="root",
                                name="知识库检索", type="agent", duration_ms=800))

        state = {
            "plan": {"nodes": {"s1": {"capability": "rag.search"}}},
            "step_results": {"s1": {"capability": "rag.search", "status": "success",
                                     "description": "查退货"}},
            "_supervisor_loop_count": 1,
            "_degraded_steps": set(),
            "_plan_changed": False,
            "_plan_critiqued": True,
            "final_answer": "答案",
        }
        _run_trace_from_state(trace, state)
        ids = [s.span_id for s in trace.spans]
        assert ids.count("planner") == 1
        assert ids.count("critique") == 1
        assert ids.count("reporter") == 1
        assert "skill-s1" not in ids  # 中间件 skill span 存在 → 不合成

    def test_synthetic_spans_created_when_middleware_missing(self):
        trace = _mk_trace()
        state = {
            "plan": {"nodes": {"s1": {"capability": "sql.query"}}},
            "step_results": {"s1": {"capability": "sql.query", "status": "success",
                                     "description": "查订单"}},
            "_supervisor_loop_count": 1,
            "_degraded_steps": set(),
            "_plan_changed": False,
            "_plan_critiqued": True,
            "final_answer": "答案",
        }
        _run_trace_from_state(trace, state)
        ids = {s.span_id for s in trace.spans}
        assert {"planner", "critique", "reporter", "skill-s1"} <= ids

    def test_direct_mode_no_placeholder_noise(self):
        """direct 模式：planner/critique/supervisor 未执行 → 不合成 0ms 占位；
        中间件带 step_id 后缀的 reporter span 也能被识别（浏览器实测整改）。"""
        trace = _mk_trace()
        from backend.observability.tracer import Span
        # 中间件 span：skill_executor / rag_skill:direct_1 / reporter:direct_1
        trace.spans.append(Span(span_id="skill_executor", parent_id="root",
                                name="直接执行", type="agent", duration_ms=26000))
        trace.spans.append(Span(span_id="rag_skill:direct_1", parent_id="root",
                                name="知识库检索", type="agent", duration_ms=26000))
        trace.spans.append(Span(span_id="reporter:direct_1", parent_id="root",
                                name="结果汇总", type="agent", duration_ms=66))

        state = {
            "plan": {"nodes": {}},
            "step_results": {"direct_1": {"capability": "rag.search", "status": "success",
                                           "description": "查退货"}},
            "_supervisor_loop_count": 1,
            "_degraded_steps": set(),
            "_plan_changed": False,
            "_plan_critiqued": False,
            "final_answer": "答案",
        }
        _run_trace_from_state(trace, state)
        ids = [s.span_id for s in trace.spans]
        assert "planner" not in ids
        assert "critique" not in ids
        assert "reporter" not in ids  # reporter:direct_1 已存在，前缀匹配去重
        assert not any(sid.startswith("supervisor-round-") for sid in ids)
        assert "skill-direct_1" not in ids
        # 中间件 span 原样保留
        assert ids.count("reporter:direct_1") == 1

    def test_failed_step_without_error_gets_fallback_message(self):
        """失败步骤无错误详情 → metrics.error 有可读兜底，不再空字符串。"""
        trace = _mk_trace()
        state = {
            "plan": {"nodes": {}},
            "step_results": {"s1": {"capability": "sql.query", "status": "failed",
                                     "description": "查订单", "error": ""}},
            "_supervisor_loop_count": 0,
            "_degraded_steps": set(),
            "_plan_changed": False,
            "final_answer": "",
        }
        _run_trace_from_state(trace, state)
        skill = next(s for s in trace.spans if s.span_id == "skill-s1")
        assert skill.status == "error"
        assert skill.metrics["error"] != ""
        assert "执行失败" in skill.metrics["error"]


# =====================================================
# 4. Observability DTO SLA 语义
# =====================================================

class TestObservabilitySlaDto:
    def test_trace_dto_uses_record_sla_threshold(self):
        from backend.app.api.routes.observability import _to_trace_dto
        rec = TraceRecord(id="t1", question="q", duration_ms=15000)
        rec.sla_threshold_ms = 30000
        dto = _to_trace_dto(rec)
        assert dto["sla"]["threshold_ms"] == 30000
        assert dto["sla"]["breached"] is False  # 15s < 30s，不再误标超时

    def test_trace_dto_breached_when_over_own_threshold(self):
        from backend.app.api.routes.observability import _to_trace_dto
        rec = TraceRecord(id="t2", question="q", duration_ms=35000)
        rec.sla_threshold_ms = 30000
        dto = _to_trace_dto(rec)
        assert dto["sla"]["breached"] is True

    def test_stored_dict_dto_uses_persisted_threshold(self):
        from backend.app.api.routes.observability import _stored_dict_to_dto
        d = {"id": "t3", "duration_ms": 12000, "sla_threshold_ms": 30000}
        dto = _stored_dict_to_dto(d)
        assert dto["sla"]["threshold_ms"] == 30000
        assert dto["sla"]["breached"] is False

    def test_stored_dict_dto_legacy_defaults_to_10s(self):
        """旧数据无 sla_threshold_ms 字段 → 回落 10s 默认值。"""
        from backend.app.api.routes.observability import _stored_dict_to_dto
        d = {"id": "t4", "duration_ms": 20000}
        dto = _stored_dict_to_dto(d)
        assert dto["sla"]["threshold_ms"] == 10000
        assert dto["sla"]["breached"] is True


# =====================================================
# 5. trace_store 序列化不泄漏内部属性
# =====================================================

class TestSerializeInternalAttrs:
    def test_span_internal_t0_not_serialized(self):
        from backend.observability.trace_store import _serialize_trace
        from backend.observability.tracer import Span
        rec = TraceRecord(id="t5", question="q")
        sp = Span(span_id="s", parent_id=None, name="n", type="agent")
        sp._t0 = time.time()  # 内部计时器属性
        rec.spans.append(sp)
        d = _serialize_trace(rec)
        assert "_t0" not in d["spans"][0]
        assert d["spans"][0]["span_id"] == "s"
