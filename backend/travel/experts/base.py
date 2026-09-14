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
    """
    t0 = time.monotonic()
    logger.info("[Travel Expert] start expert=%s", expert_name)
    try:
        result = fn(state)
        duration_ms = int((time.monotonic() - t0) * 1000)
        result.setdefault("expert", expert_name)
        result.setdefault("status", TravelExpertStatus.SUCCESS.value)
        result["duration_ms"] = duration_ms
        logger.info("[Travel Expert] done expert=%s status=%s duration_ms=%d",
                    expert_name, result["status"], duration_ms)
        return result
    except Exception as e:
        duration_ms = int((time.monotonic() - t0) * 1000)
        logger.exception("[Travel Expert] exception expert=%s", expert_name)
        return TravelExpertResult(
            expert=expert_name,
            status=TravelExpertStatus.FAILED.value,
            data={},
            notes=[],
            error=str(e),
            duration_ms=duration_ms,
        )
