from __future__ import annotations

from datetime import datetime
from decimal import Decimal, ROUND_HALF_UP

from .models import (
    _SIX_PLACES,
    BudgetPolicy,
    QuotaExceeded,
    QuotaReservation,
    ResolvedBudgetPolicies,
)
from .dynamic import (
    current_budget_periods as budget_periods,
    current_resolve_budget_policies as resolve_budget_policies,
)

class QuotaManager:
    """纯内存预算管理器，供单测和进程内回归使用；生产由 PG 实现替代。"""

    def __init__(self, policies: list[BudgetPolicy]):
        self._policies = {(p.scope_type, p.scope_id): p for p in policies}
        self._used: dict[tuple[str, str, str], Decimal] = {}

    @classmethod
    def from_policies(cls, *policies: BudgetPolicy) -> "QuotaManager":
        return cls(list(policies))

    def _resolve(self, tenant_id: str, user_id: str) -> ResolvedBudgetPolicies:
        platform = self._policies.get(("platform", "default"))
        tenant_default = self._policies.get(("tenant_default", "default"))
        tenant = self._policies.get(("tenant", tenant_id))
        user = self._policies.get(("user", user_id))
        if platform is None:
            # 纯单策略测试的兼容路径；生产 PG 解析不允许省略平台默认。
            platform = tenant or tenant_default
        return resolve_budget_policies(
            user_id=user_id,
            tenant_id=tenant_id,
            platform_default=platform,
            tenant_default=tenant_default,
            tenant=tenant,
            user=user,
        )

    @staticmethod
    def _subjects(
        tenant_id: str, user_id: str, policies: ResolvedBudgetPolicies,
    ) -> list[tuple[str, str, BudgetPolicy]]:
        """记账主体 = 真实身份；策略只作模板（提供上限/强制级别/时区）。"""
        return [
            ("user", user_id, policies.user),
            ("tenant", tenant_id, policies.tenant),
        ]

    def record(self, tenant_id: str, user_id: str, amount_cny: Decimal) -> None:
        policies = self._resolve(tenant_id, user_id)
        for scope_type, scope_id, policy in self._subjects(
            tenant_id, user_id, policies,
        ):
            self._used_for(scope_type, scope_id, policy, amount_cny)

    def reserve(
        self,
        tenant_id: str,
        user_id: str,
        amount_cny: Decimal,
        now: datetime | None = None,
    ) -> QuotaReservation:
        amount = Decimal(amount_cny).quantize(_SIX_PLACES, rounding=ROUND_HALF_UP)
        if amount < 0:
            raise ValueError("预算预占金额不能为负数")
        policies = self._resolve(tenant_id, user_id)
        audit_exempt = False
        daily_remaining = Decimal("999999999")
        monthly_remaining = Decimal("999999999")
        periods = budget_periods(now, policies.tenant.timezone)
        for scope_type, scope_id, policy in self._subjects(
            tenant_id, user_id, policies,
        ):
            # 键必须含 period_type：每月 1 日 day/month 的 period_start 相同，
            # 只用时间戳做键会把当天用量双计进日窗口（2026-10-01 实测踩中）
            daily_used = self._used.get(
                ("day", scope_type, scope_id,
                 periods.day_start_utc.isoformat()),
                Decimal("0"),
            )
            monthly_used = self._used.get(
                ("month", scope_type, scope_id,
                 periods.month_start_utc.isoformat()),
                Decimal("0"),
            )
            daily_remaining = min(daily_remaining, policy.daily_limit_cny - daily_used)
            monthly_remaining = min(
                monthly_remaining, policy.monthly_limit_cny - monthly_used,
            )
            audit_exempt = audit_exempt or policy.audit_exempt
            if (
                policy.enforcement == "hard"
                and not policy.audit_exempt
                and (
                    daily_used + amount > policy.daily_limit_cny
                    or monthly_used + amount > policy.monthly_limit_cny
                    # 零金额门禁（副作用检查）在账本打满时同样拒绝
                    or (amount == 0 and (
                        daily_used >= policy.daily_limit_cny
                        or monthly_used >= policy.monthly_limit_cny
                    ))
                )
            ):
                raise QuotaExceeded(f"{scope_type}:{scope_id}", "day_or_month")
        return QuotaReservation(
            blocked=False,
            audit_exempt=audit_exempt,
            daily_remaining_cny=max(daily_remaining, Decimal("0")),
            monthly_remaining_cny=max(monthly_remaining, Decimal("0")),
        )

    def _used_for(
        self,
        scope_type: str,
        scope_id: str,
        policy: BudgetPolicy,
        amount_cny: Decimal,
    ) -> None:
        periods = budget_periods(timezone_name=policy.timezone)
        for period_type, period_start in (
            ("day", periods.day_start_utc),
            ("month", periods.month_start_utc),
        ):
            # 键含 period_type，避免每月 1 日 day/month 窗口塌缩双计
            key = (period_type, scope_type, scope_id, period_start.isoformat())
            self._used[key] = self._used.get(key, Decimal("0")) + amount_cny
