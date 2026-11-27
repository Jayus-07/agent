"""Q4 用户/租户日月预算的策略解析、周期计算和本地契约实现。

2026-10-01 治理口径（P0）与 SRP 架构解耦：
- 真实记账主体 = ("user", user_id) / ("tenant", tenant_id)。
- 预占三态扩展为四态（reserved, settled, released, needs_review）。
- 本模块现作为轻量门面（Facade），具体实现已解耦至 backend.infra.llm.quota_service/：
  * models.py: 数据结构（BudgetPolicy, QuotaReservation 等）与异常类
  * metrics.py: 监控与指标上报
  * policies.py: 预算策略解析与周期运算、副作用阻断门禁
  * memory_manager.py: 纯内存 QuotaManager
  * store_base.py: PostgresQuotaStore 基础连接管理
  * store_policies.py: 策略配置 CRUD 与继承查询 Mixin
  * store_reservations.py: 原子预占与结算 Mixin
  * store_reconciliation.py: 待对账、月度对账与回收核销 Mixin
  * pg_store.py: 组合式权威 PostgresQuotaStore
"""
from __future__ import annotations

from .quota_service import (
    BudgetPeriods,
    BudgetPolicy,
    PostgresQuotaReservation,
    PostgresQuotaStore,
    QuotaConfigurationError,
    QuotaExceeded,
    QuotaManager,
    QuotaPolicyStoreMixin,
    QuotaReconciliationStoreMixin,
    QuotaReservation,
    QuotaReservationStoreMixin,
    QuotaStoreBase,
    ResolvedBudgetPolicies,
    _IDENTITY_SCOPES,
    _POLICY_SOURCE_LABELS,
    _SIX_PLACES,
    _VALID_ENFORCEMENT,
    _VALID_SCOPES,
    _insert_threshold_events,
    _record_quota_metric,
    budget_periods,
    enforce_side_effect_budget,
    policy_source_label,
    resolve_budget_policies,
)

__all__ = [
    "BudgetPeriods",
    "BudgetPolicy",
    "PostgresQuotaReservation",
    "PostgresQuotaStore",
    "QuotaConfigurationError",
    "QuotaExceeded",
    "QuotaManager",
    "QuotaPolicyStoreMixin",
    "QuotaReconciliationStoreMixin",
    "QuotaReservation",
    "QuotaReservationStoreMixin",
    "QuotaStoreBase",
    "ResolvedBudgetPolicies",
    "_IDENTITY_SCOPES",
    "_POLICY_SOURCE_LABELS",
    "_SIX_PLACES",
    "_VALID_ENFORCEMENT",
    "_VALID_SCOPES",
    "_insert_threshold_events",
    "_record_quota_metric",
    "budget_periods",
    "enforce_side_effect_budget",
    "policy_source_label",
    "resolve_budget_policies",
]
