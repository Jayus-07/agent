"""tool_runtime/retry.py — 重试决策（受 Deadline 约束）

禁止旧式 `for i in range(3): retry()`：每一次重试前都要同时满足
  1. 错误本身可重试（error_mapper 分类）
  2. attempt < policy.retries（策略封顶，写操作恒 0）
  3. 剩余预算 ≥ 本次退避 + 一次完整调用的有效超时（deadline 约束）

退避用 100~300ms 级 jitter，在线请求禁止秒级指数退避。
"""
from __future__ import annotations

import random
import time
from dataclasses import dataclass

from backend.core.tool_runtime.deadline import RequestDeadline
from backend.core.tool_runtime.error_mapper import ErrorClassification, RETRYABLE_STATUSES
from backend.core.tool_runtime.models import ToolStatus
from backend.core.tool_runtime.policy import ToolPolicy


@dataclass
class RetryDecision:
    should_retry: bool
    delay_ms: float = 0.0
    reason: str = ""


def jitter_backoff_ms(policy: ToolPolicy, attempt: int) -> float:
    """attempt 从 0 计。基准 backoff + 最多 200ms 随机抖动，封顶 300ms。"""
    base = policy.retry_backoff_ms * (attempt + 1)
    return min(base + random.uniform(0, 200), 300.0)


def should_retry(
    classification: ErrorClassification,
    attempt: int,
    policy: ToolPolicy,
    deadline: RequestDeadline | None,
    effective_timeout_ms: float,
) -> RetryDecision:
    """综合错误分类 + 策略 + 剩余预算，决定是否重试。"""
    # 1) 错误本身不可重试（400/401/403/404/读超时/业务失败…）
    if classification.status not in RETRYABLE_STATUSES:
        return RetryDecision(False, reason=f"non_retryable:{classification.error_code}")

    # 2) 策略封顶
    if attempt >= policy.retries:
        return RetryDecision(False, reason="retries_exhausted")

    # 3) 429 特判：Retry-After 超过剩余预算就不等了
    if classification.status is ToolStatus.RATE_LIMITED and classification.retry_after_ms:
        remaining = deadline.remaining_workflow_ms() if deadline else float("inf")
        if classification.retry_after_ms + effective_timeout_ms > remaining:
            return RetryDecision(False, reason="rate_limit_exceeds_budget")

    # 4) Deadline 约束：保底预算 + 退避 + 下一次完整调用的预算都在剩余范围内
    #    （P1 阶段 2：每次重试前重算 remaining，低于工具执行保底即禁止）
    if deadline is not None:
        if deadline.remaining_workflow_ms() < deadline.min_tool_execution_ms:
            return RetryDecision(False, reason="below_min_tool_execution")
        need_ms = jitter_backoff_ms(policy, attempt) + effective_timeout_ms
        remaining = deadline.remaining_workflow_ms()
        if remaining < need_ms:
            return RetryDecision(False, reason="deadline_budget_insufficient")

    return RetryDecision(True, delay_ms=jitter_backoff_ms(policy, attempt))


async def sleep_before_retry(delay_ms: float) -> None:
    """异步退避（不阻塞 event loop）；独立入口便于测试 patch。"""
    import asyncio
    await asyncio.sleep(max(delay_ms, 0) / 1000)
