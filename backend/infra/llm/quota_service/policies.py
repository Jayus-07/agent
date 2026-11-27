from __future__ import annotations

import sys
from datetime import datetime, timezone
from decimal import Decimal
from zoneinfo import ZoneInfo

from backend.shared.logger import logger
from .models import (
    BudgetPeriods,
    BudgetPolicy,
    QuotaConfigurationError,
    ResolvedBudgetPolicies,
)

def resolve_budget_policies(
    *,
    user_id: str,
    tenant_id: str,
    platform_default: BudgetPolicy | None,
    tenant_default: BudgetPolicy | None,
    tenant: BudgetPolicy | None,
    user: BudgetPolicy | None,
) -> ResolvedBudgetPolicies:
    """按用户覆盖 > 租户覆盖 > 平台默认解析，并保留两层约束。"""
    if platform_default is None:
        raise QuotaConfigurationError("平台默认预算策略不存在")
    effective_tenant = tenant or tenant_default or platform_default
    effective_user = user or effective_tenant
    return ResolvedBudgetPolicies(user=effective_user, tenant=effective_tenant)

def budget_periods(now: datetime | None = None, timezone_name: str = "Asia/Shanghai") -> BudgetPeriods:
    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    local = current.astimezone(ZoneInfo(timezone_name))
    day_local = local.replace(hour=0, minute=0, second=0, microsecond=0)
    month_local = day_local.replace(day=1)
    return BudgetPeriods(
        day_start_utc=day_local.astimezone(timezone.utc),
        month_start_utc=month_local.astimezone(timezone.utc),
    )


def enforce_side_effect_budget(*, user_id: str, tenant_id: str) -> None:
    """在写副作用真正发生前做零金额硬门禁。"""
    from backend.config.llm import LLM_BUDGET_MODE

    if LLM_BUDGET_MODE != "enforce":
        return
    facade = sys.modules.get("backend.infra.llm.quota")
    store_cls = getattr(facade, "PostgresQuotaStore", None)
    if store_cls is None:
        from .pg_store import PostgresQuotaStore
        store_cls = PostgresQuotaStore
    store = store_cls()
    try:
        reservation = store.reserve(
            user_id=user_id,
            tenant_id=tenant_id,
            amount_cny=Decimal("0"),
            request_id="side-effect",
        )
        store.settle(reservation, Decimal("0"))
        try:
            from backend.observability import metrics

            metrics.side_effect_budget_total.labels(result="allowed").inc()
        except Exception as exc:
            logger.debug("[Quota] side-effect metric write failed: %s", exc)
    except Exception:
        try:
            from backend.observability import metrics

            metrics.side_effect_budget_total.labels(result="rejected").inc()
        except Exception as exc:
            logger.debug("[Quota] side-effect rejection metric write failed: %s", exc)
        raise
