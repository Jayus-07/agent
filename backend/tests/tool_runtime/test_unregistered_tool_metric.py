# -*- coding: utf-8 -*-
"""G2 治理对账：未注册 tool_key 调用指标（agent_tool_unregistered_total）

口径（capability-governance TASK §4 G2，2026-11-01 探明）：
- 「未注册」= 策略注册表（DEFAULT_POLICIES）与 MCP 动态快照均无该 key；
  get_policy 走 DEFAULT_POLICY 兜底继续执行——执行器不拒绝，只记账。
- 记账点在 SafeToolExecutor.run 的 domain 归属计算之后（单点，覆盖
  熔断 fast fail / bulkhead / deadline 拒绝 / 成功 / 失败穷尽全部路径）。
- 探针/测试流量（probe./test. 前缀）不计，与 record_tool_result 生产口径一致。
"""
from __future__ import annotations

import asyncio
from uuid import uuid4

from backend.core.tool_runtime.executor import safe_tool_executor
from backend.core.tool_runtime.policy import ToolPolicy

# G2 专用测试键：非 probe./test. 前缀（验证生产口径过滤之外的记账），
# 且不撞 DEFAULT_POLICIES 静态表与 _WRITE_SUFFIXES 后缀启发。
_GHOST_KEY = "ghost.capability"
_REGISTERED_KEY = "sql.query"  # DEFAULT_POLICIES 显式注册表成员


def _unregistered_samples() -> dict[tuple[str, str], float]:
    """读 agent_tool_unregistered_total 当前累计样本 → {(tool, domain): value}。"""
    from backend.observability.metrics import agent_tool_unregistered_total

    out: dict[tuple[str, str], float] = {}
    for metric in agent_tool_unregistered_total.collect():
        for s in metric.samples:
            if s.name == "agent_tool_unregistered_total":
                key = (s.labels["tool"], s.labels["domain"])
                out[key] = out.get(key, 0.0) + s.value
    return out


def _run_once(tool_key: str, domain: str = "ghost") -> None:
    """经真实执行链触发一次调用（成功路径；策略关熔断保单测确定性）。"""
    policy = ToolPolicy(circuit_breaker=False, retries=0, bulkhead_wait_ms=0)

    async def scenario() -> None:
        await safe_tool_executor.run(
            tool_key=tool_key, call=lambda: {"ok": True},
            policy=policy, domain=domain,
        )

    asyncio.run(scenario())


def test_unregistered_call_increments_metric():
    """未注册 key 经执行链调用 → 指标 +1（tool/domain 标签正确）。"""
    key = f"ghost.{uuid4().hex[:8]}"
    before = _unregistered_samples().get((key, "ghost"), 0.0)

    _run_once(key, domain="ghost")

    after = _unregistered_samples().get((key, "ghost"), 0.0)
    assert after - before >= 1, (
        f"未注册调用 {key} 未记账：before={before} after={after}"
    )


def test_registered_call_not_counted():
    """已注册 key（DEFAULT_POLICIES 成员）不产生未注册样本。"""
    before = _unregistered_samples().get((_REGISTERED_KEY, "data"), 0.0)

    _run_once(_REGISTERED_KEY, domain="data")

    after = _unregistered_samples().get((_REGISTERED_KEY, "data"), 0.0)
    assert after == before, "已注册 key 不应计入未注册指标"


def test_probe_prefix_not_counted():
    """probe. 前缀（评测探针流量）不计，防污染生产对账口径。"""
    key = f"probe.{uuid4().hex}"
    before = len(_unregistered_samples())

    _run_once(key, domain="eval")

    assert len(_unregistered_samples()) == before
    assert all(tool != key for tool, _ in _unregistered_samples())
