"""travel/experts/base.py — 专家基础设施

复用 CS 域的专家契约形态（ExpertResult + run_expert_safely 安全包装），
但刻意不复用其实现：CS 的 ExpertResult 绑定了 response_draft / evidence 等
客服语义字段，旅游专家之间传递的是结构化 POI/行程对象，硬套会造成
「字段名对不上、只能塞进 data 里当黑盒」的假复用。
"""
from __future__ import annotations

import time
from enum import Enum
from typing import Any, Callable, TypedDict

from backend.shared.logger import logger


class TravelExpertStatus(str, Enum):
    SUCCESS = "success"
    FAILED = "failed"
    SKIPPED = "skipped"


class TravelExpertType(str, Enum):
    """四个规则专家（P0 全部为业务编排，不含 LLM 决策）"""
    POI = "poi"
    TRANSIT = "transit"
    BUDGET = "budget"
    RISK = "risk"


class TravelExpertResult(TypedDict, total=False):
    """专家统一输出契约

    与 CS 的 ExpertResult 的差异：去掉客服语义字段，改为
    data（结构化产物）+ notes（给人看的提示）两个通用出口。
    """
    expert: str
    status: str
    data: dict
    notes: list[str]
    error: str
    duration_ms: int


def run_expert_safely(
    expert_name: str,
    fn: Callable[..., dict],
    state: dict[str, Any],
) -> TravelExpertResult:
    """安全执行专家，异常不穿透（与 CS 同语义）。

    单个专家失败不应让整条旅游链路崩掉：返回 status=failed 的结果，
    由 supervisor 决定是跳过还是终止 —— 决策权在调度器，不在专家。

    Phase 4（任务书 §11）：每个专家调用统一建 span —— 此前专家只有
    duration_ms 日志，与 validator（每轴独立 span）不一致，专家延迟与
    失败率在 trace 里不可见。软失败：无活跃 trace 时为 noop span。
    """
    t0 = time.monotonic()
    logger.info("[Travel Expert] start expert=%s", expert_name)
    span = _start_expert_span(expert_name)
    try:
        result = fn(state)
        duration_ms = int((time.monotonic() - t0) * 1000)
        result.setdefault("expert", expert_name)
        result.setdefault("status", TravelExpertStatus.SUCCESS.value)
        result["duration_ms"] = duration_ms
        _end_expert_span(span, result["status"], duration_ms)
        logger.info("[Travel Expert] done expert=%s status=%s duration_ms=%d",
                    expert_name, result["status"], duration_ms)
        return result
    except Exception as e:
        duration_ms = int((time.monotonic() - t0) * 1000)
        _end_expert_span(span, TravelExpertStatus.FAILED.value, duration_ms,
                         error=str(e))
        logger.exception("[Travel Expert] exception expert=%s", expert_name)
        return TravelExpertResult(
            expert=expert_name,
            status=TravelExpertStatus.FAILED.value,
            data={},
            notes=[],
            error=str(e),
            duration_ms=duration_ms,
        )


def _start_expert_span(expert_name: str):
    """开专家 span（软失败：任何埋点异常都不影响专家执行）。"""
    try:
        from backend.observability.tracer import trace_collector
        return trace_collector.start_span(
            f"travel_expert_{expert_name}", name=f"旅游专家:{expert_name}",
            type="agent", kind="agent", input={},
        )
    except Exception:
        logger.debug("[Travel Expert] span 开启失败（不影响执行）", exc_info=True)
        return None


def _end_expert_span(span, status: str, duration_ms: int,
                     error: str = "") -> None:
    """收口专家 span（软失败；span 为 None 说明开启时已失败，直接跳过）。"""
    if span is None:
        return
    try:
        from backend.observability.tracer import trace_collector
        metrics = {"expert_status": status, "duration_ms": duration_ms}
        if error:
            metrics["error"] = error
        trace_collector.end_span(
            span, output={"status": status}, metrics=metrics,
            status="error" if status == TravelExpertStatus.FAILED.value
            else "success",
        )
    except Exception:
        logger.debug("[Travel Expert] span 收口失败", exc_info=True)
