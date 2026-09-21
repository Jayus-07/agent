# -*- coding: utf-8 -*-
"""回归测试：rag-service 无响应时不再出现 60s × 3 ≈ 180s（§23 事故回归）。

旧行为：DEFAULT_TIMEOUT=60 + retries=2 → 最坏 ~183.7s，root trace = error。
新行为：rag.search 策略 timeout + retries=0 → 单次预算内快速确认不可用
       → fallback → root trace = degraded（Reporter 正常给出降级回答）。
"""
import asyncio
import threading
import time

import pytest

from backend.core.tool_runtime.bulkhead import bulkhead_registry
from backend.core.tool_runtime.circuit_breaker import circuit_registry
from backend.core.tool_runtime.policy import reset_policy_cache


@pytest.fixture(autouse=True)
def _clean():
    circuit_registry.reset()
    bulkhead_registry.reset()
    reset_policy_cache()
    yield
    circuit_registry.reset()
    bulkhead_registry.reset()
    reset_policy_cache()


def _state(cap="rag.search", step_id="step_1"):
    return {
        "current_step_id": step_id,
        "step_results": {},
        "plan": {"nodes": {step_id: {
            "capability": cap,
            "description": "检索企业知识库",
            "params": {"question": "FBA 发货 SOP"},
        }}, "edges": {}},
    }


class _HangingTool:
    """模拟 rag-service 完全无响应（连接挂起、永不返回）。"""

    def __init__(self, release: threading.Event):
        self.release = release

    def invoke(self, params):
        # 事件可中断：测试结束时立即释放，不给 pytest 进程留悬挂线程
        self.release.wait(timeout=120)
        return "unreachable"


class _OkTool:
    def invoke(self, params):
        return "知识库答案（含引用）"


class TestRagServiceUnresponsiveRegression:
    def test_rag_hang_fails_fast_within_budget(self, monkeypatch):
        """rag.search 无响应：策略超时内快速失败，绝不 3×60s。"""
        from backend.skills.base import _CompatSkill
        from backend.core.tool_runtime.policy import ToolPolicy
        from backend.core.tool_runtime import policy as policy_mod
        from backend.core.tool_runtime.models import ToolCriticality

        # 压缩 rag.search 策略到 0.6s（保持 retries=0 / important 语义不变），
        # 让回归测试秒级跑完；线上默认 8s，机制完全一致
        monkeypatch.setattr(
            policy_mod, "DEFAULT_POLICIES",
            {"rag.search": ToolPolicy(
                timeout_ms=600, retries=0, bulkhead_limit=20,
                criticality=ToolCriticality.IMPORTANT, fallback="rag_degraded")},
        )
        reset_policy_cache()

        release = threading.Event()
        skill = _CompatSkill(_HangingTool(release))
        try:
            t0 = time.monotonic()
            # 裸 loop（不用 asyncio.run）：run 收尾会 join 默认线程池，
            # 会把悬挂的 to_thread 线程等满 120s，污染耗时测量；
            # 生产常驻 loop 无此语义（超时后线程自然结束或被 I/O 超时释放）
            loop = asyncio.new_event_loop()
            try:
                out = loop.run_until_complete(
                    skill.execute(_state(), step_capability="rag.search"))
            finally:
                loop.close()
            elapsed = time.monotonic() - t0

            sr = out["step_results"]["step_1"]
            assert sr["status"] == "failed"
            assert sr["tool_status"] == "timeout"
            assert sr["criticality"] == "important"
            # 核心断言：远小于旧 183.7s（< 5s，含测试环境余量）
            assert elapsed < 5.0, f"回归！rag.search 耗时 {elapsed:.1f}s"
            assert not (60 * 3 <= elapsed), "发生了 3×60s"
        finally:
            release.set()

    def test_rag_ok_flow_unaffected(self, monkeypatch):
        """RAG 正常返回：不破坏现有检索流程（验收标准 12）。"""
        from backend.skills.base import _CompatSkill
        from backend.core.tool_runtime.policy import ToolPolicy
        from backend.core.tool_runtime import policy as policy_mod
        from backend.core.tool_runtime.models import ToolCriticality

        monkeypatch.setattr(
            policy_mod, "DEFAULT_POLICIES",
            {"rag.search": ToolPolicy(
                timeout_ms=5_000, retries=0, bulkhead_limit=20,
                criticality=ToolCriticality.IMPORTANT, fallback="rag_degraded")},
        )
        reset_policy_cache()

        from backend.skills.base import _CompatSkill as CS
        out = asyncio.run(CS(_OkTool()).execute(
            _state(), step_capability="rag.search"))
        sr = out["step_results"]["step_1"]
        assert sr["status"] == "success"
        assert sr["output"] == "知识库答案（含引用）"


class TestBusinessOutcome:
    def test_important_failure_marks_degraded_not_error(self, monkeypatch):
        """§16：rag.search span=error 但业务结果= degraded（非 root error）。"""
        from backend.skills.base import _CompatSkill
        from backend.core.tool_runtime.policy import ToolPolicy
        from backend.core.tool_runtime import policy as policy_mod
        from backend.core.tool_runtime.models import ToolCriticality
        from backend.observability.tracer import trace_collector

        monkeypatch.setattr(
            policy_mod, "DEFAULT_POLICIES",
            {"rag.search": ToolPolicy(
                timeout_ms=300, retries=0, bulkhead_limit=20,
                criticality=ToolCriticality.IMPORTANT, fallback="rag_degraded")},
        )
        reset_policy_cache()

        trace = trace_collector.start(question="回归测试", session_id="test")
        release = threading.Event()
        loop = asyncio.new_event_loop()
        try:
            out = loop.run_until_complete(
                _CompatSkill(_HangingTool(release)).execute(
                    _state(), step_capability="rag.search"))
        finally:
            release.set()
            loop.close()

        # tool span = error（技术层如实记录）
        tool_spans = [s for s in trace.spans if s.type == "tool_call"]
        assert tool_spans and tool_spans[-1].status == "error"

        # 业务层 = degraded（metadata 驱动聚合）
        assert trace.metadata.get("business_outcome") == "degraded"
        trace_collector.finish(trace, "知识库暂不可用的降级回答", 1500, "", "")
        assert trace.status == "degraded"

    def test_optional_failure_keeps_workflow_success(self, monkeypatch):
        """§8/§15：optional tool 失败 → skipped，root trace 仍是 success。"""
        from backend.skills.base import _CompatSkill
        from backend.core.tool_runtime.policy import ToolPolicy
        from backend.core.tool_runtime import policy as policy_mod
        from backend.core.tool_runtime.models import ToolCriticality
        from backend.observability.tracer import trace_collector

        monkeypatch.setattr(
            policy_mod, "DEFAULT_POLICIES",
            {"web.search": ToolPolicy(
                timeout_ms=300, retries=0, bulkhead_limit=10,
                criticality=ToolCriticality.OPTIONAL, fallback="skip")},
        )
        reset_policy_cache()

        trace = trace_collector.start(question="回归测试", session_id="test")
        release = threading.Event()
        loop = asyncio.new_event_loop()
        try:
            out = loop.run_until_complete(
                _CompatSkill(_HangingTool(release)).execute(
                    _state(cap="web.search"), step_capability="web.search"))
        finally:
            release.set()
            loop.close()
        sr = out["step_results"]["step_1"]
        assert sr["status"] == "skipped"  # 跳过而非 failed
        trace_collector.finish(trace, "正常回答（无补充检索）", 500, "", "")
        assert trace.status == "success"  # optional 失败不拖垮 workflow
