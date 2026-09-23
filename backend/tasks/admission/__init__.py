"""tasks/admission — 分布式并发准入控制（Phase2 Step4）。

唯一准入入口 = AdmissionController（本包导出的便捷函数均收敛到它）。
禁止业务代码自建并发计数 / redis.incr 限流（规格 §三十四 硬编码扫描口径）。

快速用法（Worker 执行起点）：
    decision = acquire_for_execution(task_id, owner_execution_id=lease_id,
                                     record=record)
    if not decision.allowed:
        ...  defer：释放租约回 PENDING + 消息 countdown 重投 ...
    ... 执行 ...
    release_for_execution(task_id, lease_id, reason="success")  # 终态释放

模块组成：
- models     AdmissionDecision / 词表
- policy     AdmissionPolicy（env 限额快照）
- store      Redis Lua 原子操作（ZSET score=过期时刻，crash 自愈）
- controller 裁决编排 + fail-mode + defer 退避 + reconcile
"""
from __future__ import annotations

from typing import Any

from backend.tasks.admission.controller import (
    AdmissionController,
    get_admission_controller,
)
from backend.tasks.admission.models import AdmissionDecision, AdmissionToken


class AdmissionDeferred(Exception):
    """满载 defer：调用方应按 exception.delay_seconds 重投消息后正常返回。

    携带 task_id 与重投延迟；消息层 countdown 重投不属于业务失败，
    不消耗 retry budget（规格 §十五：区分业务 retry 与 admission retry）。
    """

    def __init__(self, task_id: str, delay_seconds: float, reason: str = ""):
        super().__init__(
            f"admission deferred task={task_id} delay={delay_seconds:.1f}s "
            f"reason={reason}")
        self.task_id = task_id
        self.delay_seconds = delay_seconds
        self.reason = reason


def acquire_for_execution(task_id: str, *, owner_execution_id: str,
                          record: Any,
                          dispatch_stage: str = "execute") -> AdmissionDecision:
    """执行起点准入（便捷代理；语义见 AdmissionController）。"""
    return get_admission_controller().acquire_for_execution(
        task_id, owner_execution_id=owner_execution_id, record=record,
        dispatch_stage=dispatch_stage)


def release_for_execution(task_id: str, owner_execution_id: str, *,
                          reason: str = "terminal") -> bool:
    """终态释放（幂等；owner CAS）。"""
    return get_admission_controller().release_for_execution(
        task_id, owner_execution_id, reason=reason)


def renew_for_execution(task_id: str, owner_execution_id: str) -> bool:
    """heartbeat 续期（lease renew 成功后调用）。"""
    return get_admission_controller().renew_for_execution(
        task_id, owner_execution_id)


def note_deferred(task_id: str, *, workflow: str = "") -> float:
    """满载 defer：登记并返回本次重投延迟（bounded + jitter）。"""
    return get_admission_controller().note_deferred(
        task_id, workflow=workflow)


def defer_budget_exhausted(task_id: str) -> bool:
    """defer 重投 budget 是否耗尽（耗尽 → 调用方落 FAILED 终态）。"""
    return get_admission_controller().defer_budget_exhausted(task_id)


def reconcile_admission_state() -> dict:
    """admission 对账（挂既有 zombie_reconcile beat；不新增调度器）。"""
    return get_admission_controller().reconcile_admission_state()


__all__ = [
    "AdmissionController",
    "AdmissionDecision",
    "AdmissionToken",
    "AdmissionDeferred",
    "get_admission_controller",
    "acquire_for_execution",
    "release_for_execution",
    "renew_for_execution",
    "note_deferred",
    "defer_budget_exhausted",
    "reconcile_admission_state",
]
