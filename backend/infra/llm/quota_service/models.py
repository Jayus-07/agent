from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

_SIX_PLACES = Decimal("0.000001")
_VALID_SCOPES = frozenset({"user", "tenant", "tenant_default", "platform"})
_VALID_ENFORCEMENT = frozenset({"hard", "soft", "audit"})
_IDENTITY_SCOPES = frozenset({"user", "tenant"})

_POLICY_SOURCE_LABELS = {
    "user": "用户策略",
    "tenant": "租户策略",
    "tenant_default": "租户默认策略",
    "platform": "平台默认策略",
}


class QuotaConfigurationError(RuntimeError):
    """预算策略缺失或违反继承/审计约束。"""

    code = "BUDGET_EXCEEDED"
    retryable = False
    provider_attempted = False


class QuotaExceeded(RuntimeError):
    """日/月额度硬阻断。"""

    code = "BUDGET_EXCEEDED"
    retryable = False
    provider_attempted = False

    def __init__(self, subject_id: str, period: str):
        self.subject_id = subject_id
        self.period = period
        super().__init__(f"预算已达到上限: {subject_id}/{period}")


@dataclass(frozen=True)
class BudgetPolicy:
    scope_type: str
    scope_id: str
    daily_limit_cny: Decimal
    monthly_limit_cny: Decimal
    enforcement: str = "hard"
    timezone: str = "Asia/Shanghai"
    audit_exempt: bool = False
    # ''=总额度轴；非空=域额度轴（迁移 147，任务 9）
    domain: str = ""

    def __post_init__(self):
        if self.scope_type not in _VALID_SCOPES:
            raise ValueError(f"非法预算作用域: {self.scope_type}")
        if self.domain:
            from backend.config.budget import BUDGET_DOMAIN_GATES

            if self.domain not in BUDGET_DOMAIN_GATES:
                raise ValueError(f"非法预算域: {self.domain!r}")
        if not self.scope_id.strip():
            raise ValueError("预算作用域 ID 不能为空")
        if self.enforcement not in _VALID_ENFORCEMENT:
            raise ValueError(f"非法预算强制级别: {self.enforcement}")
        if self.daily_limit_cny <= 0 or self.monthly_limit_cny <= 0:
            raise ValueError("日/月额度必须大于 0，不允许用 0 表示无限")
        if self.audit_exempt and not (
            self.scope_type == "tenant"
            and self.scope_id.startswith("test")
            and self.enforcement == "audit"
        ):
            raise ValueError("审计豁免只能用于 test 租户的 audit 策略")
        ZoneInfo(self.timezone)


@dataclass(frozen=True)
class ResolvedBudgetPolicies:
    user: BudgetPolicy
    tenant: BudgetPolicy


@dataclass(frozen=True)
class BudgetPeriods:
    day_start_utc: datetime
    month_start_utc: datetime


@dataclass(frozen=True)
class QuotaReservation:
    blocked: bool
    audit_exempt: bool = False
    daily_remaining_cny: Decimal = Decimal("0")
    monthly_remaining_cny: Decimal = Decimal("0")


@dataclass(frozen=True)
class PostgresQuotaReservation:
    reservation_id: str
    user_id: str
    tenant_id: str
    reserved_cny: Decimal


def policy_source_label(scope_type: str, scope_id: str) -> str:
    """策略继承来源的中文展示名（纯函数；未知 scope 回退「继承策略」）。"""
    if not scope_type or scope_type == "unknown":
        return "未知"
    label = _POLICY_SOURCE_LABELS.get(scope_type, "继承策略")
    if scope_type in ("user", "tenant") and scope_id:
        return f"{label}（{scope_id}）"
    return label
