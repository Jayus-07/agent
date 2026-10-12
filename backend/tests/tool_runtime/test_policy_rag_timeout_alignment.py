# -*- coding: utf-8 -*-
"""P0-1 超时对齐定向测试（2026-10-12 全链路审计批 1-B）。

审计结论（docs/reports/2026-10-12-RAG-P0-1超时对齐与取消传播审计.md）：
  - rag.search 旧 15s 线与 RAG 端到端常态（P90=16.3s / P95=19.1s + 前置
    排队 ~4s）结构性相撞：父判 timeout、子实际成功，答案丢弃、费用白烧。
  - rag_agent workflow 路径（skill_adapter._build_state 无 RequestContext）
    未接 RequestDeadline，policy 超时是唯一约束——放宽到 45s 在该路径
    全额生效。
  - 主图路径（runner 注入 RequestDeadline 30/25/5s）有效超时被预算压缩，
    45s 只是名义上限——该联动属批 3，本轮仅用测试锁定这一事实防漂移。

回滚路径：TOOL_POLICY_JSON={"rag.search": {"timeout_ms": 15000}} 单值
回退，无代码变更、无迁移。
"""
import asyncio
import time
from dataclasses import replace

import pytest

from backend.core.tool_runtime.bulkhead import bulkhead_registry
from backend.core.tool_runtime.circuit_breaker import circuit_registry
from backend.core.tool_runtime.deadline import RequestDeadline
from backend.core.tool_runtime import policy as policy_mod
from backend.core.tool_runtime.policy import (
    DEFAULT_POLICIES,
    get_policy,
    is_registered,
    reset_policy_cache,
)


@pytest.fixture(autouse=True)
def _clean():
    circuit_registry.reset()
    bulkhead_registry.reset()
    reset_policy_cache()
    yield
    circuit_registry.reset()
    bulkhead_registry.reset()
    reset_policy_cache()


class TestRagSearchTimeoutAlignment:
    def test_default_policy_aligned_45s(self):
        """默认注册表 rag.search=45s：修复主体断言（旧值 15000 时此测试红）。"""
        pol = get_policy("rag.search")
        assert pol.timeout_ms == 45_000.0
        # 治理语义不得被顺手改动：零重试（183.7s 事故回归面）、隔离舱、
        # 降级策略、重要级全部保持审计前形态
        assert pol.retries == 0
        assert pol.bulkhead_limit == 20
        assert pol.fallback == "rag_degraded"
        from backend.core.tool_runtime.models import ToolCriticality

        assert pol.criticality is ToolCriticality.IMPORTANT

    def test_registry_declares_rag_search(self):
        """rag.search 必须是显式注册策略（非 DEFAULT_POLICY 兜底）：
        兜底默认 15s/retry 1，一旦退化会同时复现 P0-1 与重试叠加形态。"""
        assert is_registered("rag.search")
        assert "rag.search" in DEFAULT_POLICIES

    def test_rollback_via_tool_policy_json(self, monkeypatch):
        """回滚路径：TOOL_POLICY_JSON 单值回退 15s 生效，清除后恢复 45s。"""
        monkeypatch.setenv(
            "TOOL_POLICY_JSON", '{"rag.search": {"timeout_ms": 15000}}'
        )
        reset_policy_cache()
        assert get_policy("rag.search").timeout_ms == 15_000.0

        monkeypatch.delenv("TOOL_POLICY_JSON")
        reset_policy_cache()
        assert get_policy("rag.search").timeout_ms == 45_000.0


class TestDeadlineInteraction:
    def test_workflow_path_deadline_absent_policy_is_sole_bound(self):
        """workflow 路径（rag_agent）：deadline=None，policy 超时唯一约束。

        skill_adapter._build_state 不含 RequestContext（审计实证），executor
        走 effective_timeout_s = pol.timeout_ms / 1000 分支——45s 全额生效。
        """
        pol = get_policy("rag.search")
        assert pol.timeout_ms == 45_000.0
        # executor 同形态：deadline 为 None 时不做预算压缩（executor.py
        # `else: effective_timeout_s = pol.timeout_ms / 1000`）
        deadline = None
        effective_timeout_s = pol.timeout_ms / 1000 if deadline is None else None
        assert effective_timeout_s == 45.0

    def test_main_graph_deadline_compresses_policy_timeout(self):
        """主图路径：RequestDeadline(30/25/5s) 把 45s 压到剩余预算内。

        锁定审计结论「主图路径 45s 只是名义上限」：显式构造默认预算
        （不依赖进程 env），tool_budget = min(45000, 25000−5000−250)。
        该压缩是否应当放宽属批 3 Deadline 联动，不在本轮。
        """
        deadline = RequestDeadline(
            request_id="p01-test",
            total_budget_ms=30_000.0,
            workflow_budget_ms=25_000.0,
            reporter_reserved_ms=5_000.0,
        )
        allow, tool_budget_ms, decision, reason = deadline.check_tool_execution(
            45_000.0, margin_ms=250.0,
        )
        assert allow is True
        assert decision == "allow"
        assert tool_budget_ms < 45_000.0, (
            "主图路径 45s 应被 workflow 预算压缩（审计结论漂移）"
        )
        # 但压缩后仍宽于旧 15s 线：预算充足时 P95 形态在主图路径也被覆盖
        assert tool_budget_ms >= 15_000.0


class TestFortyFiveSecondFlow:
    def test_slow_but_bounded_tool_succeeds_under_new_policy(self, monkeypatch):
        """机制形态：耗时超过旧 15s 线比例、处在新 45s 线内的 Tool 正常成功。

        按比例缩放（45000:15000 → 1.35s:0.45s，超旧线 3 倍时长、在新线内），
        避免真实 16s 等待；只动 timeout 一项，其余治理语义与线上一致。
        """
        from backend.core.tool_runtime.executor import safe_tool_executor
        from backend.core.tool_runtime.models import ToolStatus

        monkeypatch.setattr(
            policy_mod, "DEFAULT_POLICIES",
            {"rag.search": replace(
                DEFAULT_POLICIES["rag.search"], timeout_ms=1_350.0,
            )},
        )
        reset_policy_cache()

        class _SlowRagTool:
            def invoke(self, params):
                time.sleep(0.45)
                return "客服的审核时间为 1-2 个工作日 [E1]"

        result = asyncio.run(safe_tool_executor.run(
            tool_key="rag.search",
            call=lambda: asyncio.to_thread(_SlowRagTool().invoke, {}),
        ))
        assert result.status is ToolStatus.SUCCESS
        assert result.latency_ms >= 450
        assert "审核时间" in result.data
