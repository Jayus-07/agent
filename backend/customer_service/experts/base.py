"""customer_service/experts/base.py — Expert 基础设施

Expert 统一输出类型 + 安全执行包装器。
所有 Expert 共享此契约，Supervisor 通过 run_expert_safely 调度。

设计参考: docs/customer-service/langgraph-multi-expert-design.md §6
"""
from __future__ import annotations

import time
from enum import Enum
from typing import Any, Callable, TypedDict

from backend.shared.logger import logger


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
) -> dict[str, Any]:
    """安全执行 Expert，异常不穿透。

    职责:
    1. 记录执行耗时
    2. 捕获异常 → 转为 status=failed 的 ExpertResult
    3. 埋点 metrics + 记录 trace span

    Args:
        expert_name: Expert 标识（用于 metrics/日志）
        fn: Expert 核心函数，签名 fn(state) -> dict
        state: CSGraphState 当前状态

    Returns:
        ExpertResult — 保证包含 expert + status 字段
    """
    from backend.observability.metrics import record_cs_expert_result

    t0 = time.monotonic()
    logger.info("[CS Expert] start expert=%s", expert_name)

    try:
        result = fn(state)
        duration_ms = int((time.monotonic() - t0) * 1000)

        status = result.get("status", ExpertStatus.SUCCESS.value)
        result.setdefault("expert", expert_name)
        result.setdefault("status", status)
        result["duration_ms"] = duration_ms

        record_cs_expert_result(expert_name, status)
        logger.info(
            "[CS Expert] done expert=%s status=%s duration_ms=%d",
            expert_name, status, duration_ms,
        )
        return result

    except Exception as e:
        duration_ms = int((time.monotonic() - t0) * 1000)
        logger.error(
            "[CS Expert] exception expert=%s: %s", expert_name, e, exc_info=True,
        )
        record_cs_expert_result(expert_name, "failed")

        return {
            "expert": expert_name,
            "status": ExpertStatus.FAILED.value,
            "response_draft": "",
            "error": str(e),
            "duration_ms": duration_ms,
        }
