"""tasks/admission/models.py — Admission 决策与令牌数据结构（Phase2 Step4）。

Admission Token 不是 auth token / fencing token / execution lease /
幂等键——它是"本次 execution 当前占用一个 admission slot 的证明"。
"""
from __future__ import annotations

from dataclasses import dataclass, field

# 拒绝/放行原因词表（可观测口径：区分"容量满"与"系统故障"）
REASON_ADMITTED = "admitted"
REASON_DISABLED = "disabled"               # admission 开关关闭（显式）
REASON_REDIS_UNAVAILABLE = "redis_unavailable"  # store 不可达（fail_mode 裁决）
REASON_INTERNAL_ERROR = "internal_error"   # store 异常（fail_mode 裁决）
REASON_GLOBAL_LIMIT = "global_limit"
REASON_TENANT_LIMIT = "tenant_limit"
REASON_USER_LIMIT = "user_limit"
REASON_WORKFLOW_LIMIT = "workflow_limit"

# scope 类型（拒绝时定位是哪一层满）
SCOPE_GLOBAL = "global"
SCOPE_TENANT = "tenant"
SCOPE_USER = "user"
SCOPE_WORKFLOW = "workflow"

# 容量满类原因（fail-open 放行时不得归入此类——那是降级不是拒绝）
_CAPACITY_REASONS = frozenset({
    REASON_GLOBAL_LIMIT, REASON_TENANT_LIMIT,
    REASON_USER_LIMIT, REASON_WORKFLOW_LIMIT,
})


@dataclass(frozen=True)
class AdmissionDecision:
    """一次 acquire 的完整裁决快照（进结构化日志/指标）。

    allowed=True  时 token_id 非空（takeover 时继承既有 token_id）；
    allowed=False 时 scope/current/limit 说明是哪层限制、当前水位与限额；
    fallback=True 表示 store 故障时按 fail-mode=open 放行（可观测的降级）。
    """

    allowed: bool
    reason: str
    scope: str = ""
    current: int | None = None
    limit: int | None = None
    token_id: str = ""
    takeover: bool = False
    fallback: bool = False
    error: str = ""

    @property
    def capacity_rejected(self) -> bool:
        """是否为正常容量满（区别于 disabled / store 故障）。"""
        return self.reason in _CAPACITY_REASONS


@dataclass(frozen=True)
class AdmissionToken:
    """token 元数据（token:{task_id} hash 的 Python 形态，观测/审计用）。"""

    task_id: str
    token_id: str
    owner_execution_id: str
    tenant_id: str
    user_id: str
    workflow: str
    workload_class: str
    acquired_at: str = ""
    dispatch_stage: str = "execute"
    extra: dict = field(default_factory=dict)
