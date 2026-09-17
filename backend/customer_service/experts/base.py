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
    timeout_s: float | None = None,
) -> dict[str, Any]:
    """安全执行 Expert，异常不穿透。

    职责:
    1. 记录执行耗时
    2. 捕获异常 → 转为 status=failed 的 ExpertResult
    3. 可选显式超时（timeout_s）→ 转为 status=timeout（P2.3）：
       LLM invoke 的 config={"timeout"} 在当前 ChatOpenAI 版本实测不生效，
       线程级限时是唯一可靠手段（复用 infra.async_utils 的共享线程池）
    4. 埋点 metrics + 记录 trace span

    Args:
        expert_name: Expert 标识（用于 metrics/日志）
        fn: Expert 核心函数，签名 fn(state) -> dict
        state: CSGraphState 当前状态
        timeout_s: 显式超时秒数；None 表示不限时

    Returns:
        ExpertResult — 保证包含 expert + status 字段
    """
    from backend.observability.metrics import record_cs_expert_result

    t0 = time.monotonic()
    logger.info("[CS Expert] start expert=%s", expert_name)

    if timeout_s is not None and timeout_s > 0:
        # P2.3：per-call 独立线程限时——不用 infra 的共享池（max_workers=2），
        # 超时孤儿任务会长期占用共享池 worker，后续调用在队列里排队，
        # future.result(timeout) 会在任务开跑前误判超时（2026-09-17 全量回归实证）。
        import concurrent.futures

        pool = concurrent.futures.ThreadPoolExecutor(
            max_workers=1, thread_name_prefix=f"cs-expert-{expert_name}",
        )
        try:
            future = pool.submit(fn, state)
            result = future.result(timeout=timeout_s)
        except concurrent.futures.TimeoutError:
            duration_ms = int((time.monotonic() - t0) * 1000)
            future.cancel()  # 未开跑则取消；已开跑的孤儿线程无法强杀，自行结束
            logger.error(
                "[CS Expert] timeout expert=%s after %.1fs",
                expert_name, timeout_s,
            )
            record_cs_expert_result(expert_name, "timeout")
            return {
                "expert": expert_name,
                "status": ExpertStatus.TIMEOUT.value,
                "response_draft": "",
                "error": f"expert timed out after {timeout_s}s",
                "duration_ms": duration_ms,
            }
        except Exception as e:
            duration_ms = int((time.monotonic() - t0) * 1000)
            logger.error(
                "[CS Expert] exception expert=%s: %s", expert_name, e,
                exc_info=True,
            )
            record_cs_expert_result(expert_name, "failed")
            return {
                "expert": expert_name,
                "status": ExpertStatus.FAILED.value,
                "response_draft": "",
                "error": str(e),
                "duration_ms": duration_ms,
            }
        finally:
            pool.shutdown(wait=False)
    else:
        try:
            result = fn(state)
        except Exception as e:
            duration_ms = int((time.monotonic() - t0) * 1000)
            logger.error(
                "[CS Expert] exception expert=%s: %s", expert_name, e,
                exc_info=True,
            )
            record_cs_expert_result(expert_name, "failed")
            return {
                "expert": expert_name,
                "status": ExpertStatus.FAILED.value,
                "response_draft": "",
                "error": str(e),
                "duration_ms": duration_ms,
            }

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
