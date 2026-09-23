"""tasks/admission/policy.py — AdmissionPolicy（限额唯一事实源，Phase2 Step4）。

配置集中 backend/config/tasks.py（env），本模块只做解析与按 workflow
取值；禁止业务代码散落 if workflow == ... 手写限额。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Mapping

from backend.config.tasks import (
    TASK_ADMISSION_ENABLED,
    TASK_ADMISSION_FAIL_MODE,
    TASK_ADMISSION_GLOBAL_LIMIT,
    TASK_ADMISSION_TENANT_LIMIT,
    TASK_ADMISSION_USER_LIMIT,
    TASK_ADMISSION_WORKFLOW_LIMITS,
    TASK_ADMISSION_TOKEN_TTL_SECONDS,
)

FAIL_MODE_CLOSED = "closed"
FAIL_MODE_OPEN = "open"


@dataclass(frozen=True)
class AdmissionPolicy:
    """四层并发限额 + fail 行为的不可变快照（单测可整体替换模拟配置）。"""

    enabled: bool = TASK_ADMISSION_ENABLED
    fail_mode: str = TASK_ADMISSION_FAIL_MODE
    token_ttl_seconds: int = TASK_ADMISSION_TOKEN_TTL_SECONDS
    global_limit: int | None = TASK_ADMISSION_GLOBAL_LIMIT
    tenant_limit: int | None = TASK_ADMISSION_TENANT_LIMIT
    user_limit: int | None = TASK_ADMISSION_USER_LIMIT
    workflow_limits: Mapping[str, int | None] = field(
        default_factory=lambda: dict(TASK_ADMISSION_WORKFLOW_LIMITS))

    def workflow_limit(self, workflow: str) -> int | None:
        """workflow 层限额：未登记 workflow = 不启用该层限制（None）。"""
        return self.workflow_limits.get(workflow)

    def limits_for(self, workflow: str) -> tuple[int | None, int | None,
                                                 int | None, int | None]:
        """(global, tenant, user, workflow) 四层限额一次取齐。"""
        return (self.global_limit, self.tenant_limit,
                self.user_limit, self.workflow_limit(workflow))


def get_policy() -> AdmissionPolicy:
    """当前生效 policy（每次调用即取 env 快照；测试可 monkeypatch config）。"""
    return AdmissionPolicy()
