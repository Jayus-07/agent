"""customer_service/experts/base.py — Expert 基础设施

Expert 统一输出类型 + 安全执行包装器。
所有 Expert 共享此契约，Supervisor 通过 run_expert_safely 调度。

设计参考: docs/customer-service/langgraph-multi-expert-design.md §6
STOP F（2026-09-29）：执行生命周期内部收敛到 core/node_runtime（六段公共
生命周期 + THREAD_ISOLATED 超时唯一实现）；本模块公开契约（函数签名/
ExpertResult 字段/status 枚举/日志前缀/metrics 名）逐字节冻结不变。
"""
from __future__ import annotations

from enum import Enum
from typing import Any, Callable, TypedDict

from backend.core.node_runtime import (
    CsExpertHooks,
    ErrorPolicy,
    ExecutionContext,
    NodeRunner,
    TimeoutStrategy,
)


class ExpertStatus(str, Enum):
    """Expert 执行状态"""
    SUCCESS = "success"
    FAILED = "failed"
    TIMEOUT = "timeout"
    SKIPPED = "skipped"


class ExpertType(str, Enum):
    """5 个 Expert Agent 类型"""
    KNOWLEDGE = "knowledge"
    QUERY = "query"
    ACTION = "action"
    COMPLAINT = "complaint"
    HANDOFF = "handoff"


class ExpertResult(TypedDict, total=False):
    """Expert 统一输出契约

    Supervisor 读取此结构写入 CSGraphState.last_expert_result，
    Reporter 读取此结构组装最终回复。
    """
    expert: str
    status: str
    response_draft: str
    evidence: list[dict]
    action_result: dict
    data: dict
    error: str
    duration_ms: int


def run_expert_safely(
    expert_name: str,
    fn: Callable[..., dict],
    state: dict[str, Any],
    timeout_s: float | None = None,
) -> dict[str, Any]:
    """安全执行 Expert，异常不穿透。

    职责（内部经 core/node_runtime 六段生命周期实现，行为与迁移前手写
    实现一致）:
    1. 记录执行耗时
    2. 捕获异常 → 转为 status=failed 的 ExpertResult
    3. 可选显式超时（timeout_s）→ THREAD_ISOLATED 线程级限时 →
       status=timeout（P2.3）：LLM invoke 的 config={"timeout"} 在当前
       ChatOpenAI 版本实测不生效，线程级限时是唯一可靠手段；
       per-call 独立单 worker 池 + contextvars 拷贝（P2.3 防共享池饿死
       误判超时 / B4 防线程丢上下文）锁定为 core/node_runtime 的
       TimeoutStrategy.THREAD_ISOLATED 唯一实现
    4. 埋点 metrics + 日志 + span（经 CsExpertHooks，全部软失败；span 为
       M14 治理台账 D14 补齐，kind=SpanKind.CS_EXPERT）

    Args:
        expert_name: Expert 标识（用于 metrics/日志）
        fn: Expert 核心函数，签名 fn(state) -> dict
        state: CSGraphState 当前状态
        timeout_s: 显式超时秒数；None 表示不限时

    Returns:
        ExpertResult — 保证包含 expert + status 字段
    """
    deadline = timeout_s if timeout_s is not None and timeout_s > 0 else None
    ctx = ExecutionContext(node_name=expert_name, domain="cs", deadline=deadline)
    node_result = NodeRunner().run(
        ctx,
        fn,
        state,
        policy=ErrorPolicy.SWALLOW_TO_STATUS,
        hooks=CsExpertHooks(),
        timeout_strategy=(
            TimeoutStrategy.THREAD_ISOLATED if deadline is not None
            else TimeoutStrategy.NONE
        ),
    )

    if node_result.status == ExpertStatus.TIMEOUT.value:
        return {
            "expert": expert_name,
            "status": ExpertStatus.TIMEOUT.value,
            "response_draft": "",
            "error": f"expert timed out after {timeout_s}s",
            "duration_ms": node_result.duration_ms,
        }
    if node_result.status == ExpertStatus.FAILED.value:
        return {
            "expert": expert_name,
            "status": ExpertStatus.FAILED.value,
            "response_draft": "",
            "error": node_result.error or "",
            "duration_ms": node_result.duration_ms,
        }

    # 成功包装刻意留在 runner 之外（与旧实现同构）：包装阶段异常
    # （fn 返回非 dict 时 .get 抛 AttributeError）与旧实现一样向外抛。
    result = node_result.data
    status = result.get("status", ExpertStatus.SUCCESS.value)
    result.setdefault("expert", expert_name)
    result.setdefault("status", status)
    result["duration_ms"] = node_result.duration_ms
    return result
