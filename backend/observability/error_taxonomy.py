"""observability/error_taxonomy.py — 跨层错误统一分类（M3 / 台账 D3）

为什么存在：平台已有三套正交错误词表，各自落各自的目的地——
  - 模型层 5 类（infra/llm/error_taxonomy.py → llm_failures_total）
  - 任务层 10 类（tasks/error_taxonomy.py → tasks.error_type）
  - Tool 层 ToolStatus 8 值（core/tool_runtime/models.py → agent_tool_*）
三套词表都对，但聚合不出「今天失败里 timeout 占多少」这种统一分布。
本模块是**纯映射出口**：不新造第四套词表、不改变三套源词表的任何
取值，只在展示/聚合边界把三者归一到七类 + unknown。

统一七类（管理端失败分布与告警的消费口径）：
  timeout / network_error / permission_denied / validation_error /
  business_error / contract_error / provider_error

零 IO、永不抛错——所有函数对未知输入返回 "unknown"。
"""
from __future__ import annotations

from backend.core.tool_runtime.models import ToolStatus
from backend.infra.llm.error_taxonomy import MODEL_ERROR_TYPES
from backend.tasks.error_taxonomy import TASK_ERROR_TYPES

#: 统一错误类（对外稳定口径，管理端/API 消费）
UNIFIED_ERROR_CLASSES = (
    "timeout",
    "network_error",
    "permission_denied",
    "validation_error",
    "business_error",
    "contract_error",
    "provider_error",
    "unknown",
)

#: 成功语义哨兵（unify_tool_status 对 SUCCESS 的返回，调用方据此跳过计数）
SUCCESS = "success"

# --- 三套源词表 → 统一类 -----------------------------------------------

_MODEL_MAP = {
    "timeout": "timeout",
    "auth_failed": "permission_denied",
    "rate_limited": "provider_error",
    "quota_exhausted": "provider_error",
    "provider_error": "provider_error",
}

_TASK_MAP = {
    "timeout": "timeout",
    "provider_timeout": "timeout",
    "rate_limited": "provider_error",
    "quota_exhausted": "provider_error",
    "auth_failed": "permission_denied",
    "provider_error": "provider_error",
    "validation_error": "validation_error",
    "permission_denied": "permission_denied",
    "illegal_transition": "validation_error",
    "internal_error": "business_error",
}

_TOOL_MAP = {
    ToolStatus.TIMEOUT: "timeout",
    ToolStatus.UNAVAILABLE: "network_error",
    ToolStatus.UNAUTHORIZED: "permission_denied",
    ToolStatus.INVALID_REQUEST: "validation_error",
    ToolStatus.FAILED: "business_error",
    ToolStatus.DEGRADED: "contract_error",
    ToolStatus.RATE_LIMITED: "provider_error",
    # ToolStatus.SUCCESS 不在映射里：成功不是错误类（见 unify_tool_status）
}


def unify_model_error(error_type: str | None) -> str:
    """模型层五类 → 统一类。未知/None → unknown。"""
    return _MODEL_MAP.get((error_type or "").strip().lower(), "unknown")


def unify_task_error(error_type: str | None) -> str:
    """任务层十类 → 统一类。未知/None → unknown（ZOMBIE_RECONCILED 等
    特殊标记、admission_rejected 拒绝语义落 business_error 由调用方决定，
    本函数不猜）。"""
    return _TASK_MAP.get((error_type or "").strip().lower(), "unknown")


def unify_tool_status(status: ToolStatus | str | None) -> str:
    """ToolStatus → 统一类。SUCCESS → "success"（调用方跳过失败计数）。

    接受 str（ToolStatus 是 str 枚举，DB/JSON 回读常为裸字符串）。
    """
    if isinstance(status, ToolStatus):
        if status is ToolStatus.SUCCESS:
            return SUCCESS
        return _TOOL_MAP.get(status, "unknown")
    key = (status or "").strip().lower()
    if key == ToolStatus.SUCCESS.value:
        return SUCCESS
    try:
        return _TOOL_MAP.get(ToolStatus(key), "unknown")
    except ValueError:
        return "unknown"


# --- 完备性自检（导入期 fail-fast：源词表加枚举漏配映射立刻暴露） ----------

def _ensure_full_coverage() -> None:
    missing = [t for t in MODEL_ERROR_TYPES if t not in _MODEL_MAP]
    missing += [t for t in TASK_ERROR_TYPES if t not in _TASK_MAP]
    missing += [s.name for s in ToolStatus if s is not ToolStatus.SUCCESS
                and s not in _TOOL_MAP]
    if missing:
        raise RuntimeError(
            f"[error_taxonomy] 源词表新增取值未配统一映射: {missing}——"
            f"请同步 _MODEL_MAP/_TASK_MAP/_TOOL_MAP（台账 D3）"
        )


_ensure_full_coverage()


__all__ = [
    "UNIFIED_ERROR_CLASSES",
    "SUCCESS",
    "unify_model_error",
    "unify_task_error",
    "unify_tool_status",
]
