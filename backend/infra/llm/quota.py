"""Q4 用户/租户日月预算的策略解析、周期计算和本地契约实现。

2026-10-01 治理口径（P0）：
- **记账主体 = 真实身份**。账本键一律为 ``("user", user_id)`` /
  ``("tenant", tenant_id)``；``platform``/``tenant_default`` 策略只是
  **模板**（提供上限/强制级别/时区），绝不再作为账本键——否则无显式
  策略的租户/用户会共用同一本账，额度隔离失效。
- **金额单位 = 记账本位币 CNY**（config/budget.py），列名以 ``_cny``
  结尾；供应商报价的原生币种在定价出口折算。
- 预占三态 reserved/settled/released 之外新增 **needs_review（待对账）**：
  调用可能已计费但用量未知（流式缺 usage / 结算失败 / 滞留超龄）时转入
  该态，占额保守保留到周期结束，不当作零成本放走。
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal, ROUND_HALF_UP
from typing import Any
import os
from contextlib import contextmanager
import uuid
from zoneinfo import ZoneInfo

from backend.shared.logger import logger


_SIX_PLACES = Decimal("0.000001")
_VALID_SCOPES = frozenset({"user", "tenant", "tenant_default", "platform"})
_VALID_ENFORCEMENT = frozenset({"hard", "soft", "audit"})
# 账本/预占里允许出现的记账主体作用域（模板作用域禁止落账本）
_IDENTITY_SCOPES = frozenset({"user", "tenant"})


def _record_quota_metric(
    metric_name: str,
    *,
    scope_type: str = "unknown",
    period_type: str = "unknown",
    result: str,
    threshold: str = "",
) -> None:
    """记录预算指标；指标链路异常不得改变预算结算语义。"""
    try:
        from backend.observability import metrics

        if metric_name == "budget_threshold_total":
            metrics.budget_threshold_total.labels(
                scope_type=scope_type,
                period_type=period_type,
                threshold=threshold,
            ).inc()
        else:
            metrics.budget_quota_total.labels(
                scope_type=scope_type,
                period_type=period_type,
                result=result,
            ).inc()
    except Exception as exc:
        # 预算本身已经由 PG 事务保护；Prometheus 不可用不能改变业务结果。
        logger.debug("[Quota] budget metric write failed: %s", exc)


def _insert_threshold_events(
    cur,
    events_table: str,
    *,
    scope_type: str,
    scope_id: str,
    period_type: str,
    period_start,
    used,
    reserved,
    limit,
) -> None:
    ratio = (Decimal(str(used)) + Decimal(str(reserved))) / Decimal(str(limit))
    for threshold in (Decimal("0.8000"), Decimal("1.0000")):
        if ratio >= threshold:
            cur.execute(
                f"""INSERT INTO {events_table} (
                            scope_type, scope_id, period_type, period_start, threshold
                        ) VALUES (%s, %s, %s, %s, %s)
                        ON CONFLICT DO NOTHING""",
                (scope_type, scope_id, period_type, period_start, threshold),
            )
            if cur.rowcount == 1:
                _record_quota_metric(
                    "budget_threshold_total",
                    scope_type=scope_type,
                    period_type=period_type,
                    result="created",
                    threshold=f"{threshold:.4f}",
                )


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

    def __post_init__(self):
        if self.scope_type not in _VALID_SCOPES:
            raise ValueError(f"非法预算作用域: {self.scope_type}")
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


@dataclass(frozen=True)
class BudgetPeriods:
    day_start_utc: datetime
    month_start_utc: datetime


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


@dataclass(frozen=True)
class QuotaReservation:
    blocked: bool
    audit_exempt: bool = False
    daily_remaining_cny: Decimal = Decimal("0")
    monthly_remaining_cny: Decimal = Decimal("0")


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


@dataclass(frozen=True)
class PostgresQuotaReservation:
    reservation_id: str
    user_id: str
    tenant_id: str
    reserved_cny: Decimal


#: 继承来源 → 中文展示名（2026-10-08 #1）：「额度来源」此前只回
#: scope_type:scope_id 技术串，前端恒走英文回退。label 是纯展示字段，
#: scope 两键保持原样（既有消费方兼容）；命名与 AGENTS.md 继承链口径一致。
_POLICY_SOURCE_LABELS = {
    "user": "用户策略",
    "tenant": "租户策略",
    "tenant_default": "租户默认策略",
    "platform": "平台默认策略",
}


def policy_source_label(scope_type: str, scope_id: str) -> str:
    """策略继承来源的中文展示名（纯函数；未知 scope 回退「继承策略」）。"""
    if not scope_type or scope_type == "unknown":
        return "未知"
    label = _POLICY_SOURCE_LABELS.get(scope_type, "继承策略")
    if scope_type in ("user", "tenant") and scope_id:
        return f"{label}（{scope_id}）"
    return label


class PostgresQuotaStore:
    """PG 原子预占/结算存储；额度表是跨进程权威。"""

    def __init__(self, connection_factory=None):
        self._connection_factory = connection_factory or self._connect
        prefix = os.getenv("BUDGET_PG_TABLE_PREFIX", "")
        self._policies = prefix + "budget_policies"
        self._ledger = prefix + "budget_ledger"
        self._reservations = prefix + "budget_reservations"
        self._events = prefix + "budget_events"
        self._price_table = prefix + "model_price"
        self._policy_audit = prefix + "budget_policy_audit"
        # 第二批：待对账核销动作审计（append-only，144 迁移）
        self._resolution_audit = prefix + "budget_reservation_resolutions"

    @staticmethod
    @contextmanager
    def _connect():
        """池化连接（统一 Engine）：块结束自动 commit、异常 rollback、归还池。

        语义对齐 psycopg2 原生 ``with conn``（块结束 commit / 异常 rollback），
        但连接归还池，不再每次新建。
        """
        from backend.config.database import MEMORY_DB_CONFIG  # noqa: F401  (engine_for 归一)

        from backend.infra.db import engine_for

        conn = engine_for(MEMORY_DB_CONFIG).raw_connection()
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    @staticmethod
    def _policy(row: tuple[Any, ...] | None) -> BudgetPolicy | None:
        if row is None:
            return None
        return BudgetPolicy(
            scope_type=row[0],
            scope_id=row[1],
            daily_limit_cny=Decimal(str(row[2])),
            monthly_limit_cny=Decimal(str(row[3])),
            enforcement=row[4],
            timezone=row[5],
            audit_exempt=bool(row[6]),
        )

    def get_policy(self, scope_type: str, scope_id: str) -> BudgetPolicy | None:
        try:
            with self._connection_factory() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        f"""SELECT scope_type, scope_id, daily_limit_cny,
                                   monthly_limit_cny, enforcement, timezone,
                                   audit_exempt
                            FROM {self._policies}
                            WHERE scope_type = %s AND scope_id = %s""",
                        (scope_type, scope_id),
                    )
                    return self._policy(cur.fetchone())
        except Exception as exc:
            raise QuotaConfigurationError("预算策略表不可用") from exc

    @staticmethod
    def _reset_at(period_type: str, period_start: datetime, timezone_name: str) -> datetime:
        local = period_start.astimezone(ZoneInfo(timezone_name))
        if period_type == "day":
            return (local + timedelta(days=1)).astimezone(timezone.utc)
        if local.month == 12:
            next_month = local.replace(
                year=local.year + 1, month=1, day=1,
            )
        else:
            next_month = local.replace(month=local.month + 1, day=1)
        return next_month.astimezone(timezone.utc)

    @staticmethod
    def _window(
        cur,
        ledger_table: str,
        scope_type: str,
        scope_id: str,
        template: BudgetPolicy,
        period_type: str,
        period_start: datetime,
    ) -> dict[str, Any]:
        limit = (
            template.daily_limit_cny
            if period_type == "day"
            else template.monthly_limit_cny
        )
        cur.execute(
            f"""SELECT used_cny, reserved_cny
                FROM {ledger_table}
                WHERE scope_type = %s AND scope_id = %s
                  AND period_type = %s AND period_start = %s""",
            (scope_type, scope_id, period_type, period_start),
        )
        row = cur.fetchone()
        used = Decimal(str(row[0])) if row else Decimal("0")
        reserved = Decimal(str(row[1])) if row else Decimal("0")
        # 显示口径恒用当前解析策略：执行侧（reserve 超限判定）本来就用
        # fresh policy，账本行的 limit_cny 只是预占时的冻结快照——若显示
        # 读快照，管理员改策略后横幅上限滞后到下次调用才刷新（2026-10-01）。
        from backend.app.api.routes.budget_dto import build_budget_window

        return build_budget_window(
            used=used,
            reserved=reserved,
            limit=limit,
            reset_at=PostgresQuotaStore._reset_at(
                period_type, period_start, template.timezone,
            ),
        )

    def get_budget_status(
        self,
        *,
        user_id: str,
        tenant_id: str,
        now: datetime | None = None,
    ) -> dict[str, Any]:
        """读取当前用户预算及继承来源，不让前端自行解析策略。"""
        policies = self.resolve(user_id, tenant_id)
        explicit_user = self.get_policy("user", user_id)
        explicit_tenant = self.get_policy("tenant", tenant_id)
        tenant_default = self.get_policy("tenant_default", "default")
        platform = self.get_policy("platform", "default")
        source = (
            explicit_user
            or explicit_tenant
            or tenant_default
            or platform
        )
        periods = budget_periods(now, policies.tenant.timezone)
        try:
            with self._connection_factory() as conn:
                with conn.cursor() as cur:
                    # 账本键 = 真实身份；模板策略只提供上限与重置时区
                    user_daily = self._window(
                        cur, self._ledger, "user", user_id,
                        policies.user, "day", periods.day_start_utc,
                    )
                    user_monthly = self._window(
                        cur, self._ledger, "user", user_id,
                        policies.user, "month", periods.month_start_utc,
                    )
                    tenant_daily = self._window(
                        cur, self._ledger, "tenant", tenant_id,
                        policies.tenant, "day", periods.day_start_utc,
                    )
                    tenant_monthly = self._window(
                        cur, self._ledger, "tenant", tenant_id,
                        policies.tenant, "month", periods.month_start_utc,
                    )
        except Exception as exc:
            raise QuotaConfigurationError("预算状态读取失败") from exc

        def blocked(policy: BudgetPolicy, windows: list[dict[str, Any]]) -> bool:
            return (
                policy.enforcement == "hard"
                and not policy.audit_exempt
                and any(window["ratio"] >= 1 for window in windows)
            )

        tenant_blocked = blocked(policies.tenant, [tenant_daily, tenant_monthly])
        user_blocked = blocked(policies.user, [user_daily, user_monthly])
        from backend.config import llm as llm_config
        from backend.config.budget import BUDGET_BASE_CURRENCY, BUDGET_FX_USD_CNY

        # 来源中文名（2026-10-08 #1）：展示字段，scope 两键不变（消费方兼容）
        source_label = policy_source_label(
            source.scope_type if source else "unknown",
            source.scope_id if source else "",
        )

        return {
            "currency": BUDGET_BASE_CURRENCY,
            "fx_usd_cny": str(BUDGET_FX_USD_CNY),
            "mode": llm_config.LLM_BUDGET_MODE,
            "enforcement": policies.user.enforcement,
            "audit_exempt": policies.user.audit_exempt,
            "blocked": user_blocked or tenant_blocked,
            "tenant_blocked": tenant_blocked,
            "policy_source": {
                "scope_type": source.scope_type if source else "unknown",
                "scope_id": source.scope_id if source else "unknown",
                "label": source_label,
            },
            "daily": user_daily,
            "monthly": user_monthly,
            "user": {
                "daily": user_daily,
                "monthly": user_monthly,
                "blocked": user_blocked,
            },
            "tenant": {
                "daily": tenant_daily,
                "monthly": tenant_monthly,
                "blocked": tenant_blocked,
            },
        }

    def list_policies(self) -> list[dict[str, Any]]:
        try:
            with self._connection_factory() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        f"""SELECT scope_type, scope_id, daily_limit_cny,
                                   monthly_limit_cny, enforcement, timezone,
                                   audit_exempt, updated_by, updated_at
                            FROM {self._policies}
                            ORDER BY scope_type, scope_id"""
                    )
                    rows = cur.fetchall()
            keys = (
                "scope_type", "scope_id", "daily_limit_cny", "monthly_limit_cny",
                "enforcement", "timezone", "audit_exempt", "updated_by", "updated_at",
            )
            return [dict(zip(keys, row)) for row in rows]
        except Exception as exc:
            raise QuotaConfigurationError("预算策略读取失败") from exc

    def list_events(self, limit: int = 200) -> list[dict[str, Any]]:
        try:
            with self._connection_factory() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        f"""SELECT scope_type, scope_id, period_type,
                                   threshold, created_at
                            FROM {self._events}
                            ORDER BY created_at DESC LIMIT %s""",
                        (limit,),
                    )
                    rows = cur.fetchall()
            keys = ("scope_type", "scope_id", "period_type", "threshold", "created_at")
            return [dict(zip(keys, row)) for row in rows]
        except Exception as exc:
            raise QuotaConfigurationError("预算事件读取失败") from exc

    def list_policy_audit(self, limit: int = 200) -> list[dict[str, Any]]:
        """读取预算策略修改审计，不允许修改历史记录。"""
        try:
            with self._connection_factory() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        f"""SELECT scope_type, scope_id, before_value,
                                   after_value, reason, updated_by, created_at
                            FROM {self._policy_audit}
                            ORDER BY created_at DESC LIMIT %s""",
                        (max(1, min(limit, 1000)),),
                    )
                    rows = cur.fetchall()
            keys = (
                "scope_type", "scope_id", "before_value", "after_value",
                "reason", "updated_by", "created_at",
            )
            return [dict(zip(keys, row)) for row in rows]
        except Exception as exc:
            raise QuotaConfigurationError("预算策略审计读取失败") from exc

    def list_subjects(
        self, limit: int = 200, *, include_test: bool = False,
    ) -> list[dict[str, Any]]:
        """读取真实主体（user/tenant 身份键）的预算窗口，金额以字符串返回。

        主体集合 = 账本出现过的身份键 ∪ 有显式 user/tenant 策略的身份键。
        模板作用域（platform/tenant_default）不再作为主体展示，只通过
        policy_source/effective_policy 说明继承来源。
        测试/评测主体默认排除（2026-11-10 UX 走查）：scope_id 前缀命中
        BUDGET_TEST_TENANT_PREFIX / BUDGET_TEST_REQUEST_PREFIXES 的主体只在
        include_test=True 时返回，与待对账队列的测试数据开关同口径。
        """
        from backend.config.budget import (
            BUDGET_TEST_REQUEST_PREFIXES,
            BUDGET_TEST_TENANT_PREFIX,
        )

        test_prefixes = tuple(
            str(prefix).lower()
            for prefix in (BUDGET_TEST_TENANT_PREFIX, *BUDGET_TEST_REQUEST_PREFIXES)
        )

        def _is_test_subject(scope_id: str) -> bool:
            return str(scope_id).lower().startswith(test_prefixes)

        try:
            with self._connection_factory() as conn:
                with conn.cursor() as cur:
                    test_filter_sql = (
                        "" if include_test
                        # 注意 NOT LIKE ANY 语义是「至少一个不匹配」（恒真），
                        # 必须整体取反：NOT (scope_id LIKE ANY (...))
                        else " AND NOT (scope_id LIKE ANY(%(test_like)s))"
                    )
                    cur.execute(
                        f"""SELECT scope_type, scope_id FROM {self._ledger}
                            WHERE scope_type IN ('user', 'tenant')
                              {test_filter_sql}
                            GROUP BY scope_type, scope_id
                            ORDER BY scope_type, scope_id LIMIT %(limit)s""",
                        {
                            "limit": limit,
                            "test_like": [p + "%" for p in test_prefixes],
                        },
                    )
                    subjects = [(row[0], row[1]) for row in cur.fetchall()]
                    cur.execute(
                        f"""SELECT scope_type, scope_id, daily_limit_cny,
                                   monthly_limit_cny, enforcement, timezone,
                                   audit_exempt
                            FROM {self._policies}"""
                    )
                    policies = {
                        (row[0], row[1]): BudgetPolicy(
                            scope_type=row[0], scope_id=row[1],
                            daily_limit_cny=Decimal(str(row[2])),
                            monthly_limit_cny=Decimal(str(row[3])), enforcement=row[4],
                            timezone=row[5], audit_exempt=bool(row[6]),
                        )
                        for row in cur.fetchall()
                    }
                    # 用户→租户映射：预算身份的权威来源是预占单（账本的
                    # user/tenant 行都由 reserve 写入）。list_subjects 只有
                    # scope 键，没有请求级 tenant_id，须回查预占单补映射；
                    # 取该用户最近一次预占的租户。
                    cur.execute(
                        f"""SELECT DISTINCT ON (user_id)
                                   user_id, tenant_id
                              FROM {self._reservations}
                             ORDER BY user_id, created_at DESC"""
                    )
                    user_tenant = {row[0]: row[1] for row in cur.fetchall()}

            tenant_default = policies.get(("tenant_default", "default"))
            platform = policies.get(("platform", "default"))
            seen: set[tuple[str, str]] = set()
            result: list[dict[str, Any]] = []
            # 窗口查询复用单连接：此前每主体单独借还连接（N+1），主体
            # 增长后连接建立开销线性放大（2026-11-10 测试库实证超时）。
            with self._connection_factory() as conn_win:
                with conn_win.cursor() as cur_win:
                    for scope_type, scope_id in sorted(set(subjects) | {
                        key for key in policies
                        if key[0] in _IDENTITY_SCOPES
                        and (include_test or not _is_test_subject(key[1]))
                    }):
                        if (scope_type, scope_id) in seen or len(result) >= limit:
                            continue
                        seen.add((scope_type, scope_id))
                        if scope_type == "user":
                            explicit = policies.get(("user", scope_id))
                            # 任务 10（2026-11-10）：旧实现查 ("tenant", "") 空键且
                            # 把用户 id 当租户 id——显式租户策略对用户永不生效，
                            # 页面恒显示 tenant_default。现在经 user→tenant 映射
                            # （预占单回查）找到真实租户再查其显式策略。
                            tenant_scope_id = user_tenant.get(scope_id, "")
                            tenant_of_user = policies.get(("tenant", tenant_scope_id))
                            candidates = [
                                explicit,
                                tenant_of_user,
                                tenant_default,
                                platform,
                            ]
                        else:
                            explicit = policies.get(("tenant", scope_id))
                            candidates = [explicit, tenant_default, platform]
                            tenant_scope_id = scope_id
                        effective = next(
                            (item for item in candidates if item is not None), None,
                        )
                        if effective is None:
                            # 无任何可用模板：无法给出窗口口径，跳过并留待补策略
                            logger.warning(
                                "[Quota] 主体 %s:%s 无生效策略模板，跳过展示",
                                scope_type, scope_id,
                            )
                            continue
                        # 无显式策略的租户，其用户层的租户模板回退链与解析一致
                        if scope_type == "user":
                            tenant_template = policies.get(
                                ("tenant", tenant_scope_id)
                            ) or tenant_default or platform
                        else:
                            tenant_template = effective
                        user_template = explicit or tenant_template or effective

                        def policy_view(item: BudgetPolicy | None) -> dict[str, Any] | None:
                            if item is None:
                                return None
                            return {
                                "scope_type": item.scope_type,
                                "scope_id": item.scope_id,
                                "daily_limit_cny": item.daily_limit_cny,
                                "monthly_limit_cny": item.monthly_limit_cny,
                                "enforcement": item.enforcement,
                                "timezone": item.timezone,
                                "audit_exempt": item.audit_exempt,
                            }

                        periods = budget_periods(timezone_name=effective.timezone)
                        windows = {}
                        for period_type, start in (
                            ("day", periods.day_start_utc),
                            ("month", periods.month_start_utc),
                        ):
                            windows[period_type] = self._window(
                                cur_win, self._ledger, scope_type, scope_id,
                                effective, period_type, start,
                            )
                        ratio = max(windows["day"]["ratio"], windows["month"]["ratio"])
                        result.append({
                            "scope": scope_type,
                            "id": scope_id,
                            "daily": windows["day"],
                            "monthly": windows["month"],
                            "enforcement": effective.enforcement,
                            "ratio": ratio,
                            "explicit_policy": policy_view(explicit),
                            "effective_policy": policy_view(effective),
                            "user_template": policy_view(user_template),
                            "tenant_template": policy_view(tenant_template),
                            "policy_source": {
                                "scope_type": effective.scope_type,
                                "scope_id": effective.scope_id,
                            },
                        })
            return result
        except Exception as exc:
            raise QuotaConfigurationError("预算主体读取失败") from exc

    def summary(self) -> dict[str, Any]:
        """管理端成本总览。

        2026-10-01 口径修复（P0-5）：
        - 总成本从 ``llm_usage`` 用量明细派生（每次调用恰好一行，一笔 $1
          只算一次），不再对 budget_ledger 求和——账本按日/月双行记账，
          直接 SUM 会把同一笔钱按周期数翻倍；
        - 主体占用按 (scope_type, scope_id) 去重计数，且只统计真实身份
          作用域（user/tenant），模板作用域行不算主体；
        - 未结算预留只汇总真实身份账本行。
        """
        periods = budget_periods()
        try:
            with self._connection_factory() as conn:
                with conn.cursor() as cur:
                    # Billing V2（2026-10-07）：总成本 = 记账本位币唯一口径
                    # （V2 行取 billed_cost_cny，legacy 行按旧 currency 逻辑折算），
                    # 不再对 total_cost 做无币种感知的混算直和。
                    from backend.observability.llm_usage_store_pg import (
                        cost_cny_sql_expr,
                    )

                    cur.execute(
                        f"""SELECT COALESCE(SUM({cost_cny_sql_expr()}), 0),
                                  COUNT(*) FILTER (WHERE cost_status IN ('exact', 'estimated')),
                                  COUNT(*) FILTER (WHERE cost_status = 'unpriced'),
                                  COUNT(*) FILTER (WHERE cost_status = 'price_unknown'),
                                  COUNT(*) FILTER (WHERE COALESCE(cost_status, '') = '')
                            FROM llm_usage"""
                    )
                    total_cost, exact_rows, unpriced_rows, unknown_rows, missing_rows = (
                        cur.fetchone()
                    )
                    cur.execute(
                        f"""SELECT COUNT(DISTINCT (scope_type, scope_id))
                                       FILTER (WHERE enforcement = 'hard'
                                               AND used_cny + reserved_cny >= limit_cny
                                               AND ((period_type = 'day'
                                                     AND period_start = %s)
                                                    OR (period_type = 'month'
                                                        AND period_start = %s))),
                                   COUNT(DISTINCT (scope_type, scope_id))
                                       FILTER (WHERE enforcement = 'hard'
                                               AND used_cny + reserved_cny >= limit_cny * 0.8
                                               AND used_cny + reserved_cny < limit_cny
                                               AND ((period_type = 'day'
                                                     AND period_start = %s)
                                                    OR (period_type = 'month'
                                                        AND period_start = %s)))
                            FROM {self._ledger}
                            WHERE scope_type IN ('user', 'tenant')""",
                        (
                            periods.day_start_utc, periods.month_start_utc,
                            periods.day_start_utc, periods.month_start_utc,
                        ),
                    )
                    blocked, near = cur.fetchone()
                    # 未结算预留按预占单去重（2026-11-10 待对账治理 1.4）：
                    # 同一预占在 user/tenant × day/month 的账本上是 4 行同值，
                    # 行级 SUM 会把占额放大数倍（实测 ¥78.60 显示成 ¥228）。
                    # 口径对齐 reconciliation_summary：reserved（在途）+
                    # needs_review（待对账占额）逐单只算一次。
                    cur.execute(
                        f"""SELECT COALESCE(SUM(reserved_cny), 0)
                            FROM (SELECT DISTINCT ON (id) id, reserved_cny
                                  FROM {self._reservations}
                                  WHERE status IN ('reserved', 'needs_review')
                                    AND resolution = ''
                                  ORDER BY id, created_at) r"""
                    )
                    reserved = cur.fetchone()[0]
                    cur.execute(
                        f"""SELECT COALESCE(
                                   SUM(CASE WHEN approval_status = 'approved' THEN 1 ELSE 0 END)::numeric
                                   / NULLIF(COUNT(*), 0), 0)
                            FROM {self._price_table}"""
                    )
                    coverage = cur.fetchone()[0]
            from backend.config.budget import (
                BUDGET_BASE_CURRENCY, BUDGET_FX_USD_CNY,
            )

            return {
                "currency": BUDGET_BASE_CURRENCY,
                "fx_usd_cny": str(BUDGET_FX_USD_CNY),
                "total_cost": str(Decimal(str(total_cost)).quantize(_SIX_PLACES)),
                "cost_status_counts": {
                    "priced": int(exact_rows or 0),
                    "unpriced": int(unpriced_rows or 0),
                    "price_unknown": int(unknown_rows or 0),
                    "missing_status": int(missing_rows or 0),
                },
                "price_coverage_ratio": float(coverage or 0),
                "near_limit_subjects": int(near or 0),
                "blocked_subjects": int(blocked or 0),
                "unsettled_reserved": str(
                    Decimal(str(reserved)).quantize(_SIX_PLACES)
                ),
            }
        except Exception as exc:
            raise QuotaConfigurationError("预算汇总读取失败") from exc

    def upsert_policy(
        self,
        *,
        scope_type: str,
        scope_id: str,
        daily_limit_cny: Decimal,
        monthly_limit_cny: Decimal,
        enforcement: str,
        audit_exempt: bool,
        updated_by: str,
        reason: str,
        expected_updated_at: datetime | None = None,
    ) -> dict[str, Any]:
        """立即生效地写入策略，并追加旧值/新值审计。"""
        if daily_limit_cny > monthly_limit_cny:
            raise ValueError("日额度不能高于月额度")
        policy = BudgetPolicy(
            scope_type=scope_type,
            scope_id=scope_id,
            daily_limit_cny=daily_limit_cny,
            monthly_limit_cny=monthly_limit_cny,
            enforcement=enforcement,
            audit_exempt=audit_exempt,
        )
        if not reason.strip():
            raise ValueError("预算策略变更原因不能为空")
        import json

        try:
            with self._connection_factory() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        f"""SELECT scope_type, scope_id, daily_limit_cny,
                                   monthly_limit_cny, enforcement, timezone,
                                   audit_exempt, updated_by, updated_at
                            FROM {self._policies}
                            WHERE scope_type = %s AND scope_id = %s
                            FOR UPDATE""",
                        (scope_type, scope_id),
                    )
                    old_row = cur.fetchone()
                    if expected_updated_at is not None and (
                        old_row is None or old_row[8] != expected_updated_at
                    ):
                        raise ValueError("预算策略已被修改，请刷新后重试")
                    timezone_name = old_row[5] if old_row else "Asia/Shanghai"
                    cur.execute(
                        f"""INSERT INTO {self._policies} (
                                  scope_type, scope_id, daily_limit_cny,
                                  monthly_limit_cny, enforcement, timezone,
                                  audit_exempt, updated_by
                              ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                              ON CONFLICT (scope_type, scope_id) DO UPDATE SET
                                  daily_limit_cny = EXCLUDED.daily_limit_cny,
                                  monthly_limit_cny = EXCLUDED.monthly_limit_cny,
                                  enforcement = EXCLUDED.enforcement,
                                  audit_exempt = EXCLUDED.audit_exempt,
                                  updated_by = EXCLUDED.updated_by,
                                  updated_at = now()
                              RETURNING scope_type, scope_id, daily_limit_cny,
                                        monthly_limit_cny, enforcement, timezone,
                                        audit_exempt, updated_by, updated_at""",
                        (
                            policy.scope_type, policy.scope_id,
                            policy.daily_limit_cny, policy.monthly_limit_cny,
                            policy.enforcement, timezone_name,
                            policy.audit_exempt, updated_by,
                        ),
                    )
                    row = cur.fetchone()
                    before = dict(zip(
                        ("scope_type", "scope_id", "daily_limit_cny",
                         "monthly_limit_cny", "enforcement", "timezone",
                         "audit_exempt", "updated_by", "updated_at"),
                        old_row,
                    )) if old_row else None
                    after = dict(zip(
                        ("scope_type", "scope_id", "daily_limit_cny",
                         "monthly_limit_cny", "enforcement", "timezone",
                         "audit_exempt", "updated_by", "updated_at"),
                        row,
                    ))
                    cur.execute(
                        """INSERT INTO budget_policy_audit (
                                   scope_type, scope_id, before_value,
                                   after_value, reason, updated_by
                               ) VALUES (%s, %s, %s::jsonb, %s::jsonb, %s, %s)""",
                        (
                            scope_type, scope_id,
                            json.dumps(before, default=str),
                            json.dumps(after, default=str), reason, updated_by,
                        ),
                    )
            return after
        except (ValueError, QuotaConfigurationError):
            raise
        except Exception as exc:
            raise QuotaConfigurationError("预算策略写入失败") from exc

    def resolve(self, user_id: str, tenant_id: str) -> ResolvedBudgetPolicies:
        platform = self.get_policy("platform", "default")
        tenant_default = self.get_policy("tenant_default", "default")
        tenant = self.get_policy("tenant", tenant_id)
        user = self.get_policy("user", user_id)
        return resolve_budget_policies(
            user_id=user_id,
            tenant_id=tenant_id,
            platform_default=platform,
            tenant_default=tenant_default,
            tenant=tenant,
            user=user,
        )

    def reserve(
        self,
        *,
        user_id: str,
        tenant_id: str,
        amount_cny: Decimal,
        request_id: str = "",
        now: datetime | None = None,
        request_limit_cny: Decimal | None = None,
    ) -> PostgresQuotaReservation:
        """按真实身份预占（user/tenant 两层，模板策略只提供上限）。

        2026-10-01 边界修复：金额为 0 的硬门禁（副作用零金额检查）在
        used+reserved 已达上限时同样拒绝——原条件 ``+0 <= limit`` 会让
        打满的账本继续放行零金额调用。
        """
        if not user_id or not tenant_id:
            raise QuotaConfigurationError("硬预算需要可信 user_id 和 tenant_id")
        amount = Decimal(amount_cny).quantize(_SIX_PLACES, rounding=ROUND_HALF_UP)
        if amount < 0:
            raise ValueError("预算预占金额不能为负数")
        request_limit = None
        if request_limit_cny is not None:
            request_limit = Decimal(request_limit_cny)
            if not request_id or not request_limit.is_finite() or request_limit < 0:
                raise ValueError('整批预算必须有请求ID和非负有限人民币上限')
            request_limit = request_limit.quantize(_SIX_PLACES, rounding=ROUND_HALF_UP)
        policies = self.resolve(user_id, tenant_id)
        periods = budget_periods(now, policies.tenant.timezone)
        reservation_id = str(uuid.uuid4())
        try:
            with self._connection_factory() as conn:
                with conn.cursor() as cur:
                    if request_limit is not None:
                        # 同一批次跨进程串行预占；复用既有预占表，不建平行账本。
                        cur.execute(
                            'SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))',
                            (f'budget-request:{request_id}',),
                        )
                        cur.execute(
                            f'''SELECT COALESCE(SUM(CASE WHEN status = 'settled'
                                       THEN settled_cny ELSE reserved_cny END), 0)
                                FROM (SELECT DISTINCT ON (id)
                                             id, status, settled_cny, reserved_cny
                                      FROM {self._reservations}
                                      WHERE request_id = %s
                                        AND status IN ('reserved', 'needs_review', 'settled')
                                      ORDER BY id) reservations''',
                            (request_id,),
                        )
                        committed = Decimal(cur.fetchone()[0])
                        if committed >= request_limit or committed + amount > request_limit:
                            raise QuotaExceeded(f'request:{request_id}', 'run')
                    for scope_type, scope_id, policy in (
                        ("user", user_id, policies.user),
                        ("tenant", tenant_id, policies.tenant),
                    ):
                        for period_type, period_start, limit in (
                            ("day", periods.day_start_utc, policy.daily_limit_cny),
                            ("month", periods.month_start_utc, policy.monthly_limit_cny),
                        ):
                            cur.execute(
                                f"""
                                INSERT INTO {self._ledger} (
                                    scope_type, scope_id, period_type, period_start,
                                    limit_cny, enforcement, reserved_cny
                                )
                                SELECT %s, %s, %s, %s, %s, %s, %s
                                WHERE %s <> 'hard' OR %s <= %s
                                ON CONFLICT (scope_type, scope_id, period_type, period_start)
                                DO UPDATE SET
                                    limit_cny = EXCLUDED.limit_cny,
                                    enforcement = EXCLUDED.enforcement,
                                    reserved_cny = {self._ledger}.reserved_cny
                                        + EXCLUDED.reserved_cny,
                                    updated_at = now()
                                WHERE EXCLUDED.enforcement <> 'hard'
                                   OR {self._ledger}.used_cny
                                      + {self._ledger}.reserved_cny
                                      + EXCLUDED.reserved_cny < EXCLUDED.limit_cny
                                   OR (EXCLUDED.reserved_cny > 0
                                       AND {self._ledger}.used_cny
                                       + {self._ledger}.reserved_cny
                                       + EXCLUDED.reserved_cny <= EXCLUDED.limit_cny)
                                RETURNING scope_type, scope_id, used_cny,
                                          reserved_cny, limit_cny
                                """,
                                (
                                    scope_type, scope_id, period_type,
                                    period_start, limit, policy.enforcement, amount,
                                    policy.enforcement, amount, limit,
                                ),
                            )
                            ledger_row = cur.fetchone()
                            if ledger_row is None:
                                _record_quota_metric(
                                    "budget_quota_total",
                                    scope_type=scope_type,
                                    period_type=period_type,
                                    result="rejected",
                                )
                                raise QuotaExceeded(
                                    f"{scope_type}:{scope_id}", period_type,
                                )
                            _record_quota_metric(
                                "budget_quota_total",
                                scope_type=scope_type,
                                period_type=period_type,
                                result="reserved",
                            )
                            _insert_threshold_events(
                                cur,
                                self._events,
                                scope_type=ledger_row[0],
                                scope_id=ledger_row[1],
                                period_type=period_type,
                                period_start=period_start,
                                used=ledger_row[2],
                                reserved=ledger_row[3],
                                limit=ledger_row[4],
                            )
                            cur.execute(
                                f"""
                                INSERT INTO {self._reservations} (
                                    id, request_id, user_id, tenant_id,
                                    scope_type, scope_id, period_type, period_start,
                                    reserved_cny
                                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                                """,
                                (
                                    reservation_id, request_id, user_id, tenant_id,
                                    scope_type, scope_id, period_type,
                                    period_start, amount,
                                ),
                            )
            return PostgresQuotaReservation(
                reservation_id=reservation_id,
                user_id=user_id,
                tenant_id=tenant_id,
                reserved_cny=amount,
            )
        except (QuotaExceeded, QuotaConfigurationError):
            raise
        except Exception as exc:
            raise QuotaConfigurationError("预算预占存储不可用") from exc

    def settle(
        self,
        reservation: PostgresQuotaReservation,
        actual_cny: Decimal,
        *,
        settle_kind: str = "",
    ) -> None:
        actual = Decimal(actual_cny).quantize(_SIX_PLACES, rounding=ROUND_HALF_UP)
        if actual < 0:
            raise ValueError("结算金额不能为负数")
        if settle_kind not in ("", "estimated"):
            raise ValueError(
                f"非法结算标记: {settle_kind!r}（''=精确结算, 'estimated'=保守估算）"
            )
        try:
            with self._connection_factory() as conn:
                with conn.cursor() as cur:
                    # 账本行锁序必须与 reserve（user→tenant、day→month）一致：
                    # 本查询无 ORDER BY 时按索引返回 tenant 在前，与 reserve
                    # 互持行锁 → AB-BA 死锁（2026-10-29 生产实测 21 次）
                    cur.execute(
                        f"""SELECT scope_type, scope_id, period_type,
                                   period_start, reserved_cny
                            FROM {self._reservations}
                            WHERE id = %s AND status = 'reserved'
                            ORDER BY CASE scope_type WHEN 'user' THEN 0 ELSE 1 END,
                                     CASE period_type WHEN 'day' THEN 0 ELSE 1 END
                            FOR UPDATE""",
                        (reservation.reservation_id,),
                    )
                    rows = cur.fetchall()
                    for scope_type, scope_id, period_type, period_start, reserved in rows:
                        cur.execute(
                            f"""UPDATE {self._ledger}
                                SET reserved_cny = GREATEST(reserved_cny - %s, 0),
                                    used_cny = used_cny + %s,
                                    updated_at = now()
                                WHERE scope_type = %s AND scope_id = %s
                                  AND period_type = %s AND period_start = %s
                                RETURNING used_cny, reserved_cny, limit_cny""",
                            (reserved, actual, scope_type, scope_id,
                             period_type, period_start),
                        )
                        ledger_row = cur.fetchone()
                        if ledger_row is not None:
                            _record_quota_metric(
                                "budget_quota_total",
                                scope_type=scope_type,
                                period_type=period_type,
                                result="settled",
                            )
                            _insert_threshold_events(
                                cur,
                                self._events,
                                scope_type=scope_type,
                                scope_id=scope_id,
                                period_type=period_type,
                                period_start=period_start,
                                used=ledger_row[0],
                                reserved=ledger_row[1],
                                limit=ledger_row[2],
                            )
                    cur.execute(
                        f"""UPDATE {self._reservations}
                            SET settled_cny = %s, status = 'settled', settled_at = now(),
                                settle_kind = %s
                            WHERE id = %s AND status = 'reserved'""",
                        (actual, settle_kind, reservation.reservation_id),
                    )
        except Exception as exc:
            raise QuotaConfigurationError("预算结算存储不可用") from exc

    def release(self, reservation: PostgresQuotaReservation) -> None:
        """模型调用确定未发给供应商时释放预占，不写入 used_cny。"""
        try:
            with self._connection_factory() as conn:
                with conn.cursor() as cur:
                    # 同 settle：账本行锁序必须与 reserve（user→tenant、day→month）
                    # 一致，乱序会与 reserve 的 upsert 互锁（AB-BA 死锁）
                    cur.execute(
                        f"""SELECT scope_type, scope_id, period_type,
                                   period_start, reserved_cny
                            FROM {self._reservations}
                            WHERE id = %s AND status = 'reserved'
                            ORDER BY CASE scope_type WHEN 'user' THEN 0 ELSE 1 END,
                                     CASE period_type WHEN 'day' THEN 0 ELSE 1 END
                            FOR UPDATE""",
                        (reservation.reservation_id,),
                    )
                    rows = cur.fetchall()
                    for scope_type, scope_id, period_type, period_start, reserved in rows:
                        cur.execute(
                            f"""UPDATE {self._ledger}
                                SET reserved_cny = GREATEST(reserved_cny - %s, 0),
                                    updated_at = now()
                                WHERE scope_type = %s AND scope_id = %s
                                  AND period_type = %s AND period_start = %s""",
                            (reserved, scope_type, scope_id, period_type, period_start),
                        )
                        _record_quota_metric(
                            "budget_quota_total",
                            scope_type=scope_type,
                            period_type=period_type,
                            result="released",
                        )
                    cur.execute(
                        f"""UPDATE {self._reservations}
                            SET status = 'released', settled_at = now()
                            WHERE id = %s AND status = 'reserved'""",
                        (reservation.reservation_id,),
                    )
        except Exception as exc:
            raise QuotaConfigurationError("预算预占释放失败") from exc

    def mark_needs_review(
        self,
        reservation: PostgresQuotaReservation,
        reason: str,
    ) -> bool:
        """把预占转入待对账（needs_review），账本占额保守保留。

        用于"调用可能已对供应商计费但用量未知"的场景（流式缺 usage /
        结算失败 / 滞留超龄）：不能按零成本放走，也不能永久卡死当前周期
        额度——占额保留到周期结束，之后由 sweep 释放旧周期占额。
        """
        if not reason.strip():
            raise ValueError("待对账原因不能为空")
        try:
            with self._connection_factory() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        f"""UPDATE {self._reservations}
                            SET status = 'needs_review', review_reason = %s,
                                settled_at = now()
                            WHERE id = %s AND status = 'reserved'""",
                        (reason, reservation.reservation_id),
                    )
                    return cur.rowcount > 0
        except Exception as exc:
            raise QuotaConfigurationError("预算待对账标记失败") from exc

    def sweep_stale_reservations(
        self,
        *,
        now: datetime | None = None,
        threshold_hours: float | None = None,
    ) -> dict[str, int]:
        """滞留预占回收：超过阈值仍 reserved 的预占转入 needs_review。

        周期仍在当前的占额保守保留（调用可能已计费）；周期已结束的占额
        释放——旧周期账本行已无约束意义，保留只会污染未结算汇总。
        幂等：只有 status='reserved' 的行会被处理，重复执行零增量。
        """
        from backend.config.budget import BUDGET_STALE_RESERVATION_HOURS

        hours = (
            float(threshold_hours)
            if threshold_hours is not None
            else float(BUDGET_STALE_RESERVATION_HOURS)
        )
        current = now or datetime.now(timezone.utc)
        if current.tzinfo is None:
            current = current.replace(tzinfo=timezone.utc)
        cutoff = current - timedelta(hours=hours)
        periods = budget_periods(current)
        current_starts = {
            ("day", periods.day_start_utc),
            ("month", periods.month_start_utc),
        }
        reviewed = ledger_released = 0
        try:
            with self._connection_factory() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        f"""SELECT DISTINCT id FROM {self._reservations}
                            WHERE status = 'reserved' AND created_at < %s
                            ORDER BY id LIMIT 500""",
                        (cutoff,),
                    )
                    ids = [row[0] for row in cur.fetchall()]
                    for reservation_pk in ids:
                        # 同 settle：锁序与 reserve 一致，避免互锁
                        cur.execute(
                            f"""SELECT scope_type, scope_id, period_type,
                                       period_start, reserved_cny
                                FROM {self._reservations}
                                WHERE id = %s AND status = 'reserved'
                                ORDER BY CASE scope_type WHEN 'user' THEN 0 ELSE 1 END,
                                         CASE period_type WHEN 'day' THEN 0 ELSE 1 END
                                FOR UPDATE""",
                            (reservation_pk,),
                        )
                        rows = cur.fetchall()
                        if not rows:
                            continue
                        period_open = any(
                            (period_type, period_start) in current_starts
                            for _, _, period_type, period_start, _ in rows
                        )
                        if not period_open:
                            for scope_type, scope_id, period_type, period_start, reserved in rows:
                                cur.execute(
                                    f"""UPDATE {self._ledger}
                                        SET reserved_cny = GREATEST(reserved_cny - %s, 0),
                                            updated_at = now()
                                        WHERE scope_type = %s AND scope_id = %s
                                          AND period_type = %s AND period_start = %s""",
                                    (reserved, scope_type, scope_id,
                                     period_type, period_start),
                                )
                            ledger_released += 1
                        cur.execute(
                            f"""UPDATE {self._reservations}
                                SET status = 'needs_review',
                                    review_reason = %s, settled_at = now()
                                WHERE id = %s AND status = 'reserved'""",
                            (
                                "stale_sweep_period_ended" if not period_open
                                else "stale_sweep_period_open",
                                reservation_pk,
                            ),
                        )
                        reviewed += 1
            if reviewed:
                logger.warning(
                    "[Quota] 滞留预占回收 %s 笔转入待对账（旧周期释放占额 %s 笔，"
                    "阈值 %s 小时）——请到管理端待对账队列核查是否漏结算",
                    reviewed, ledger_released, hours,
                )
            return {"reviewed": reviewed, "ledger_released": ledger_released}
        except Exception as exc:
            raise QuotaConfigurationError("滞留预占回收失败") from exc

    _RESOLUTION_ACTIONS = frozenset(
        {"settled_actual", "released", "written_off", "archived"}
    )

    @staticmethod
    def _test_marker_sql_params() -> dict[str, Any]:
        """测试/评测数据的 SQL 匹配参数（第二批隔离：租户/用户前缀 + 请求前缀）。"""
        from backend.config.budget import (
            BUDGET_TEST_REQUEST_PREFIXES,
            BUDGET_TEST_TENANT_PREFIX,
        )

        return {
            "test_prefix": f"{BUDGET_TEST_TENANT_PREFIX}%",
            "request_markers": [f"{p}%" for p in BUDGET_TEST_REQUEST_PREFIXES],
        }

    @classmethod
    def _test_marker_where(cls, alias: str = "") -> str:
        """测试/评测数据的 WHERE 片段（排除测试主体与测试请求）。"""
        prefix = f"{alias}." if alias else ""
        return (
            f"({prefix}tenant_id LIKE %(test_prefix)s"
            f" OR {prefix}user_id LIKE %(test_prefix)s"
            f" OR {prefix}request_id LIKE ANY(%(request_markers)s))"
        )

    def resolve_needs_review(
        self,
        reservation_id: str,
        *,
        action: str,
        reason: str,
        resolved_by: str,
        actual_cny: Decimal | None = None,
    ) -> dict[str, Any]:
        """核销一笔待对账预占（2026-11-10 第二批闭环）。

        语义：
        - 仅 ``status='needs_review'`` 且 ``resolution=''``（开态）可核销；
          预占不存在或已核销时幂等返回 ``already_resolved``，不二次释放账本；
        - ``released`` / ``written_off`` / ``archived``：仅释放账本占额，
          不记成本——无法确认的成本不得入账（不伪造费用）；
        - ``settled_actual``：按供应商实际账单金额入账（used_cny +=
          actual_cny），actual_cny 必填且 ≥ 0，占额按原 held 释放；
        - 账本行锁序与 reserve/settle/release 一致（user→tenant、day→month），
          避免 AB-BA 死锁（2026-10-29 生产实测教训）；审计写
          ``budget_reservation_resolutions``（append-only），不物理删除。
        """
        action = str(action or "").strip()
        if action not in self._RESOLUTION_ACTIONS:
            raise ValueError(f"非法核销动作: {action}（可选: {sorted(self._RESOLUTION_ACTIONS)}）")
        reason = str(reason or "").strip()
        if not reason:
            raise ValueError("核销原因不能为空")
        resolved_by = str(resolved_by or "").strip()
        if not resolved_by:
            raise ValueError("核销操作人不能为空")
        booked: Decimal | None = None
        if action == "settled_actual":
            if actual_cny is None:
                raise ValueError("settled_actual 必须提供供应商实际账单金额")
            booked = Decimal(str(actual_cny)).quantize(
                _SIX_PLACES, rounding=ROUND_HALF_UP,
            )
            if booked < 0:
                raise ValueError("实际账单金额不能为负数")
        try:
            with self._connection_factory() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        f"""SELECT scope_type, scope_id, period_type,
                                   period_start, reserved_cny, resolution
                            FROM {self._reservations}
                            WHERE id = %s AND status = 'needs_review'
                            ORDER BY CASE scope_type WHEN 'user' THEN 0 ELSE 1 END,
                                     CASE period_type WHEN 'day' THEN 0 ELSE 1 END
                            FOR UPDATE""",
                        (reservation_id,),
                    )
                    rows = cur.fetchall()
                    if not rows:
                        return {
                            "reservation_id": reservation_id, "action": action,
                            "found": False, "already_resolved": True,
                            "released_cny": "0.000000", "booked_cny": None,
                        }
                    held = Decimal(str(rows[0][4])).quantize(_SIX_PLACES)
                    current_resolution = str(rows[0][5] or "")
                    if current_resolution:
                        return {
                            "reservation_id": reservation_id, "action": action,
                            "found": True, "already_resolved": True,
                            "released_cny": "0.000000", "booked_cny": None,
                            "resolution": current_resolution,
                        }
                    for scope_type, scope_id, period_type, period_start, reserved, _ in rows:
                        cur.execute(
                            f"""UPDATE {self._ledger}
                                SET reserved_cny = GREATEST(reserved_cny - %s, 0),
                                    used_cny = used_cny + COALESCE(%s, 0),
                                    updated_at = now()
                                WHERE scope_type = %s AND scope_id = %s
                                  AND period_type = %s AND period_start = %s
                                RETURNING used_cny, reserved_cny, limit_cny""",
                            (reserved, booked, scope_type, scope_id,
                             period_type, period_start),
                        )
                        ledger_row = cur.fetchone()
                        if booked is not None and ledger_row is not None:
                            _record_quota_metric(
                                "budget_quota_total",
                                scope_type=scope_type,
                                period_type=period_type,
                                result="settled",
                            )
                            _insert_threshold_events(
                                cur,
                                self._events,
                                scope_type=scope_type,
                                scope_id=scope_id,
                                period_type=period_type,
                                period_start=period_start,
                                used=ledger_row[0],
                                reserved=ledger_row[1],
                                limit=ledger_row[2],
                            )
                        elif booked is None:
                            _record_quota_metric(
                                "budget_quota_total",
                                scope_type=scope_type,
                                period_type=period_type,
                                result="released",
                            )
                    cur.execute(
                        f"""UPDATE {self._reservations}
                            SET resolution = %s, resolved_by = %s,
                                resolved_at = now(), actual_cny = %s
                            WHERE id = %s AND status = 'needs_review'
                              AND resolution = ''""",
                        (action, resolved_by, booked, reservation_id),
                    )
                    cur.execute(
                        f"""INSERT INTO {self._resolution_audit} (
                                reservation_id, action, reason, resolved_by,
                                actual_cny, released_cny)
                            VALUES (%s, %s, %s, %s, %s, %s)""",
                        (reservation_id, action, reason, resolved_by,
                         booked, held),
                    )
            logger.warning(
                "[Quota] 待对账核销 reservation=%s action=%s held=%s booked=%s "
                "by=%s reason=%s",
                reservation_id, action, held, booked, resolved_by, reason,
            )
            return {
                "reservation_id": reservation_id, "action": action,
                "found": True, "already_resolved": False,
                "released_cny": str(held),
                "booked_cny": str(booked) if booked is not None else None,
            }
        except ValueError:
            raise
        except Exception as exc:
            raise QuotaConfigurationError("待对账核销存储不可用") from exc

    def dry_run_backlog_migration(self) -> dict[str, Any]:
        """存量待对账分类 dry-run（第二批迁移）：只读，不写任何状态。

        分类优先级：test_eval（测试/评测主体或请求标记）→ local_unpriced
        （本地模型未定价审核）→ small_unknown（占额 ≤ 估算结算阈值）→
        large_unknown（其余，需人工逐笔核对）。每类给出单数、占额、建议
        动作与样例单据。
        """
        from backend.config.budget import BUDGET_ESTIMATED_SETTLEMENT_MAX_CNY

        suggested = {
            "test_eval": "archived",
            "local_unpriced": "released",
            "small_unknown": "written_off",
            "large_unknown": "settled_actual 或人工逐笔核对",
        }
        params = self._test_marker_sql_params()
        params["threshold"] = str(BUDGET_ESTIMATED_SETTLEMENT_MAX_CNY)
        try:
            with self._connection_factory() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        f"""SELECT cls, COUNT(*), COALESCE(SUM(reserved_cny), 0)
                            FROM (
                                SELECT CASE
                                    WHEN {self._test_marker_where()} THEN 'test_eval'
                                    WHEN review_reason IN (
                                        'embedding_usage_or_price_unavailable',
                                        'rerank_usage_or_price_unavailable'
                                    ) THEN 'local_unpriced'
                                    WHEN reserved_cny <= %(threshold)s
                                        THEN 'small_unknown'
                                    ELSE 'large_unknown'
                                END AS cls, reserved_cny, id
                                FROM (SELECT DISTINCT ON (id)
                                             id, tenant_id, user_id, request_id,
                                             review_reason, reserved_cny
                                      FROM {self._reservations}
                                      WHERE status = 'needs_review'
                                        AND resolution = ''
                                      ORDER BY id, created_at) open_reviews
                            ) grouped
                            GROUP BY cls""",
                        params,
                    )
                    class_rows = cur.fetchall()
                    cur.execute(
                        f"""SELECT CASE
                                    WHEN {self._test_marker_where()} THEN 'test_eval'
                                    WHEN review_reason IN (
                                        'embedding_usage_or_price_unavailable',
                                        'rerank_usage_or_price_unavailable'
                                    ) THEN 'local_unpriced'
                                    WHEN reserved_cny <= %(threshold)s
                                        THEN 'small_unknown'
                                    ELSE 'large_unknown'
                                END AS cls, id, reserved_cny, review_reason,
                                request_id, user_id, tenant_id
                            FROM (SELECT DISTINCT ON (id)
                                         id, tenant_id, user_id, request_id,
                                         review_reason, reserved_cny, created_at
                                  FROM {self._reservations}
                                  WHERE status = 'needs_review' AND resolution = ''
                                  ORDER BY id, created_at) open_reviews
                            ORDER BY cls, reserved_cny DESC""",
                        params,
                    )
                    all_rows = cur.fetchall()
            classes: dict[str, dict[str, Any]] = {}
            for cls, count, total in class_rows:
                classes[cls] = {
                    "count": int(count),
                    "held_cny": str(Decimal(str(total)).quantize(_SIX_PLACES)),
                    "suggested_action": suggested.get(cls, "人工核对"),
                    "samples": [],
                }
            for cls, rid, reserved, reason, request_id, user_id, tenant_id in all_rows:
                bucket = classes.setdefault(cls, {
                    "count": 0, "held_cny": "0.000000",
                    "suggested_action": suggested.get(cls, "人工核对"),
                    "samples": [],
                })
                if len(bucket["samples"]) < 10:
                    bucket["samples"].append({
                        "reservation_id": str(rid),
                        "reserved_cny": str(Decimal(str(reserved)).quantize(_SIX_PLACES)),
                        "review_reason": reason or "",
                        "request_id": request_id or "",
                        "user_id": user_id or "",
                        "tenant_id": tenant_id or "",
                    })
            total_open = sum(c["count"] for c in classes.values())
            # sum 空序列返回 int 0（0.quantize 会抛 AttributeError），必须显式
            # 以 Decimal("0") 为起点——存量开态清零后 classes 为空，否则 dry-run
            # 稳定 503（2026-11-10 :3200 目检实证）。
            total_held = sum(
                (Decimal(c["held_cny"]) for c in classes.values()), Decimal("0")
            )
            return {
                "open_count": int(total_open),
                "open_held_cny": str(total_held.quantize(_SIX_PLACES)),
                "classes": classes,
                "note": "dry-run 只读；批量核销经 POST /admin/budgets/reconciliation/resolve-batch 执行，写库需人工确认",
            }
        except Exception as exc:
            raise QuotaConfigurationError("存量迁移 dry-run 失败") from exc

    def reconciliation_summary(self) -> dict[str, Any]:
        """待对账总览：队列规模、最老滞留时长、占额与原因分布。"""
        from backend.config.budget import BUDGET_STALE_RESERVATION_HOURS

        cutoff = datetime.now(timezone.utc) - timedelta(
            hours=float(BUDGET_STALE_RESERVATION_HOURS)
        )
        try:
            with self._connection_factory() as conn:
                with conn.cursor() as cur:
                    # 占额按预占单去重：同一 reservation 在 user/tenant ×
                    # day/month 上有多条物理行（金额同值），行级 SUM 会把
                    # 同一笔占额放大数倍，与 list_pending_review 的单据
                    # 口径对不上（2026-10-01 口径修复）。
                    cur.execute(
                        f"""SELECT COUNT(DISTINCT id),
                                   COALESCE(SUM(reserved_cny), 0),
                                   MIN(created_at)
                            FROM (SELECT DISTINCT ON (id) id, reserved_cny, created_at
                                  FROM {self._reservations}
                                  WHERE status = 'needs_review'
                                    AND resolution = ''
                                  ORDER BY id, created_at) d"""
                    )
                    pending, held, oldest = cur.fetchone()
                    cur.execute(
                        f"""SELECT review_reason, COUNT(DISTINCT id)
                            FROM {self._reservations}
                            WHERE status = 'needs_review'
                              AND resolution = ''
                            GROUP BY review_reason ORDER BY 2 DESC"""
                    )
                    by_reason = {row[0] or "unknown": int(row[1]) for row in cur.fetchall()}
                    cur.execute(
                        f"""SELECT COUNT(DISTINCT id)
                            FROM {self._reservations}
                            WHERE status = 'reserved' AND created_at < %s""",
                        (cutoff,),
                    )
                    stale_unswept = cur.fetchone()[0]
                    # 窗口未决率（2026-10-02 企业口径：监控比率不监控单笔）——
                    # 窗口内预占单里 needs_review 的占比，突刺说明结算链路
                    # 或供应商链路出系统性问题；明细队列只是下钻材料。
                    from backend.config.budget import (
                        BUDGET_RECONCILE_WINDOW_HOURS,
                    )
                    cur.execute(
                        f"""SELECT COUNT(DISTINCT id),
                                   COUNT(DISTINCT CASE WHEN status = 'needs_review'
                                                        AND resolution = ''
                                                       THEN id END)
                            FROM (SELECT DISTINCT ON (id) id, status, resolution
                                  FROM {self._reservations}
                                  WHERE created_at >= %s
                                  ORDER BY id, created_at) w""",
                        (datetime.now(timezone.utc)
                         - timedelta(hours=float(BUDGET_RECONCILE_WINDOW_HOURS)),),
                    )
                    window_total, window_reviewed = cur.fetchone()
                    # P1-09 billing_mismatch（2026-10-07 Billing 收口）：窗口内
                    # 已结算预占按 request 聚合后与 V2 用量行（billed_cost_cny）
                    # 逐笔对账——正常恒 0；非 0 = 结算与记账分叉（少结/多结/
                    # 丢 usage 行），是对账日报的新红信号。
                    # 2026-11-10：排除估算结算单（settle_kind='estimated'）——
                    # 估算按预占上限入账而 llm_usage 无同额成本行，必然
                    # "分叉"，属设计内保守口径而非事故信号（迁移 145）。
                    cur.execute(
                        f"""SELECT COUNT(*) FROM (
                                SELECT r.request_id,
                                       SUM(r.settled_cny) AS settled_cny,
                                       COALESCE((SELECT SUM(u.billed_cost_cny)
                                                   FROM llm_usage u
                                                  WHERE u.request_id = r.request_id
                                                    AND u.billing_schema_version >= 2
                                                    AND u.billed_cost_cny IS NOT NULL), 0)
                                           AS billed_cny
                                  FROM (SELECT DISTINCT ON (id) id, request_id,
                                               settled_cny
                                          FROM {self._reservations}
                                         WHERE status = 'settled'
                                           AND COALESCE(settle_kind, '') = ''
                                           AND COALESCE(settled_at, created_at) >= %s
                                         ORDER BY id, created_at) r
                                 GROUP BY r.request_id
                            ) m
                            WHERE ABS(m.settled_cny - m.billed_cny) > 0.000001""",
                        (datetime.now(timezone.utc)
                         - timedelta(hours=float(BUDGET_RECONCILE_WINDOW_HOURS)),),
                    )
                    billing_mismatch = cur.fetchone()[0]
            oldest_age_hours = (
                round(
                    (datetime.now(timezone.utc) - oldest).total_seconds() / 3600, 1,
                ) if oldest else 0.0
            )
            return {
                "pending_count": int(pending or 0),
                "held_cny": str(Decimal(str(held)).quantize(_SIX_PLACES)),
                "oldest_age_hours": oldest_age_hours,
                "by_reason": by_reason,
                "stale_unswept_count": int(stale_unswept or 0),
                "stale_threshold_hours": float(BUDGET_STALE_RESERVATION_HOURS),
                # 窗口未决率（needs_review / 全部预占单）
                "window_hours": float(BUDGET_RECONCILE_WINDOW_HOURS),
                "reservations_total_window": int(window_total or 0),
                "needs_review_window": int(window_reviewed or 0),
                "needs_review_ratio_window": (
                    round(int(window_reviewed or 0) / int(window_total), 4)
                    if window_total else 0.0
                ),
                # P1-09：结算 vs V2 用量对账分叉计数（正常 0）
                "billing_mismatch_count": int(billing_mismatch or 0),
            }
        except Exception as exc:
            raise QuotaConfigurationError("待对账汇总读取失败") from exc

    def monthly_reconciliation(self, months: int = 6) -> dict[str, Any]:
        """月度对账聚合（路线图第 4 块 PR-A，2026-11-10）：三段只读口径。

        - usage：llm_usage 按月 × 供应商 × 模型 × 租户 × 产品域的 CNY 成本——
          ``cost_cny_sql_expr()`` 全仓唯一折算口径（G2），读层不二次换汇；
          域取 agent_domain（任务 9-B 域成本可见性），空归
          '(unattributed)' 暴露归因覆盖缺口；
        - ledger：budget_ledger 月行（period_type='month'）按月 × scope 汇总
          used_cny——预算口径的「入账」，与主体页月用量同源；
        - reservations：预占单按结算月（COALESCE(settled_at, created_at)）
          的 status × settle_kind × resolution 分布——估算/精确、核销形态
          一眼可分，供与供应商账单核对。
        月标签一律按 ``BUDGET_TIMEZONE``（默认 Asia/Shanghai）渲染——预算
        period_start 即该时区的自然月，UTC 会话直接 to_char 会把月初 8 小时
        归到上月（对供应商账单错月）。usage 的 ts 为 ISO-8601 text（统一
        ``YYYY-MM-DDTHH:MM:SS.mmmZ``，写入方 proxy/usage store 同一格式），
        截前 19 位补 'Z' 后转 timestamptz 再换时区。金额 Decimal 量化 6 位
        字符串；单据级金额一律 DISTINCT ON (id) 去重（与待对账口径一致）。
        """
        from zoneinfo import ZoneInfo

        from backend.config.budget import BUDGET_TIMEZONE
        from backend.observability.llm_usage_store_pg import cost_cny_sql_expr

        months = max(1, min(int(months), 24))
        tz = BUDGET_TIMEZONE
        now = datetime.now(timezone.utc).astimezone(ZoneInfo(tz))
        # 自然月窗口起点（含当月）：预算时区当月往前 months-1 个月的 1 号
        start_year, start_month = now.year, now.month - (months - 1)
        while start_month <= 0:
            start_month += 12
            start_year -= 1
        cutoff_iso = f"{start_year:04d}-{start_month:02d}-01"
        cutoff_ts = datetime(start_year, start_month, 1, tzinfo=ZoneInfo(tz))
        try:
            with self._connection_factory() as conn:
                with conn.cursor() as cur:
                    # ① usage：月 × provider × model × tenant 成本聚合
                    cur.execute(
                        f"""SELECT to_char(
                                        ((substring(ts, 1, 19) || 'Z')
                                         ::timestamptz) AT TIME ZONE %s,
                                        'YYYY-MM') AS month,
                                   provider, model, tenant_id,
                                   SUM({cost_cny_sql_expr()}), COUNT(*)
                              FROM llm_usage
                             WHERE ts >= %s
                             GROUP BY 1, 2, 3, 4""",
                        (tz, cutoff_iso,),
                    )
                    usage_rows = cur.fetchall()
                    # ①b usage：月 × agent_domain 成本聚合（域成本可见性，
                    # 任务 9-B 前置）——执行化（域硬限）的决策依据；空域归
                    # '(unattributed)'，提示归因覆盖缺口
                    cur.execute(
                        f"""SELECT to_char(
                                        ((substring(ts, 1, 19) || 'Z')
                                         ::timestamptz) AT TIME ZONE %s,
                                        'YYYY-MM') AS month,
                                   COALESCE(NULLIF(agent_domain, ''),
                                            '(unattributed)') AS domain,
                                   SUM({cost_cny_sql_expr()}), COUNT(*)
                              FROM llm_usage
                             WHERE ts >= %s
                             GROUP BY 1, 2""",
                        (tz, cutoff_iso,),
                    )
                    domain_rows = cur.fetchall()
                    # ② ledger 月行：月 × scope 汇总
                    cur.execute(
                        f"""SELECT to_char(period_start AT TIME ZONE %s,
                                           'YYYY-MM') AS month,
                                   scope_type, scope_id,
                                   SUM(used_cny), SUM(reserved_cny)
                              FROM {self._ledger}
                             WHERE period_type = 'month'
                               AND period_start >= %s
                             GROUP BY 1, 2, 3""",
                        (tz, cutoff_ts,),
                    )
                    ledger_rows = cur.fetchall()
                    # ③ 预占单结算分布：月 × status × settle_kind × resolution
                    cur.execute(
                        f"""SELECT to_char(
                                        COALESCE(settled_at, created_at)
                                        AT TIME ZONE %s,
                                        'YYYY-MM') AS month,
                                   status, COALESCE(settle_kind, ''),
                                   COALESCE(resolution, ''),
                                   COUNT(*), SUM(reserved_cny), SUM(settled_cny)
                              FROM (SELECT DISTINCT ON (id) id, status,
                                           settle_kind, resolution, reserved_cny,
                                           settled_cny, settled_at, created_at
                                      FROM {self._reservations}
                                     WHERE COALESCE(settled_at, created_at) >= %s
                                     ORDER BY id, created_at) d
                             GROUP BY 1, 2, 3, 4""",
                        (tz, cutoff_ts,),
                    )
                    reservation_rows = cur.fetchall()

            def _dec(value: Any) -> str:
                return str(Decimal(str(value)).quantize(_SIX_PLACES))

            usage_by_month: dict[str, dict[str, Any]] = {}
            for month, provider, model, tenant_id, cost, calls in usage_rows:
                bucket = usage_by_month.setdefault(
                    month,
                    {"total_cny": "0.000000", "calls": 0,
                     "by_provider": {}, "by_model": {}, "by_tenant": {},
                     "by_domain": {}},
                )
                cost_d = Decimal(str(cost or 0))
                calls_n = int(calls)
                bucket["calls"] += calls_n
                for target, key in (
                    ("by_provider", provider or ""),
                    ("by_model", model or ""),
                    ("by_tenant", tenant_id or ""),
                ):
                    slot = bucket[target].setdefault(
                        key, {"calls": 0, "cost_cny": "0.000000"}
                    )
                    slot["calls"] += calls_n
                    slot["cost_cny"] = _dec(
                        Decimal(slot["cost_cny"]) + cost_d
                    )
                bucket["total_cny"] = _dec(
                    Decimal(bucket["total_cny"]) + cost_d
                )

            for month, domain, cost, calls in domain_rows:
                bucket = usage_by_month.setdefault(
                    month,
                    {"total_cny": "0.000000", "calls": 0,
                     "by_provider": {}, "by_model": {}, "by_tenant": {},
                     "by_domain": {}},
                )
                slot = bucket["by_domain"].setdefault(
                    domain, {"calls": 0, "cost_cny": "0.000000"}
                )
                slot["calls"] += int(calls)
                slot["cost_cny"] = _dec(
                    Decimal(slot["cost_cny"]) + Decimal(str(cost or 0))
                )

            ledger_by_month: dict[str, dict[str, Any]] = {}
            for month, scope_type, scope_id, used, reserved in ledger_rows:
                bucket = ledger_by_month.setdefault(
                    month, {"by_scope": {}}
                )
                slot = bucket["by_scope"].setdefault(
                    f"{scope_type}:{scope_id}",
                    {"used_cny": "0.000000", "reserved_cny": "0.000000"},
                )
                slot["used_cny"] = _dec(Decimal(slot["used_cny"]) + Decimal(str(used or 0)))
                slot["reserved_cny"] = _dec(
                    Decimal(slot["reserved_cny"]) + Decimal(str(reserved or 0))
                )

            reservations_by_month: dict[str, dict[str, Any]] = {}
            for month, status, settle_kind, resolution, count, reserved, settled in reservation_rows:
                bucket = reservations_by_month.setdefault(
                    month, {"by_shape": [], "total": 0}
                )
                bucket["by_shape"].append({
                    "status": status or "",
                    "settle_kind": settle_kind,
                    "resolution": resolution,
                    "count": int(count),
                    "reserved_cny": _dec(reserved),
                    "settled_cny": _dec(settled or 0),
                })
                bucket["total"] += int(count)

            month_keys = sorted(
                set(usage_by_month) | set(ledger_by_month)
                | set(reservations_by_month),
                reverse=True,
            )[:months]
            return {
                "months": month_keys,
                "cutoff_month": cutoff_iso[:7],
                "usage_by_month": usage_by_month,
                "ledger_by_month": ledger_by_month,
                "reservations_by_month": reservations_by_month,
                "note": (
                    "usage 成本为 llm_usage CNY 口径（cost_cny_sql_expr 唯一折算）；"
                    "ledger 为预算月行入账；reservations 按结算月分布"
                    "（settle_kind='estimated' 为保守估算结算）。CSV 导出"
                    " /admin/budgets/reconciliation/monthly.csv。"
                ),
            }
        except Exception as exc:
            raise QuotaConfigurationError("月度对账聚合读取失败") from exc

    def monthly_reconciliation_csv(self, months: int = 6) -> str:
        """月度对账 CSV 导出：usage 月 × 维度（供应商/模型/租户/合计）行。

        这是与供应商月账单核对的核心材料（PR-B 录入后的差异对照基线）。
        转义走标准 csv 模块（RFC 4180），维度 key 来自 DB 聚合结果仍不裸拼。
        """
        import csv as _csv
        import io as _io

        report = self.monthly_reconciliation(months=months)
        buf = _io.StringIO()
        writer = _csv.writer(buf, lineterminator="\n")
        writer.writerow(["month", "dimension", "key", "calls", "cost_cny"])
        for month in report["months"]:
            bucket = report["usage_by_month"].get(month) or {}
            for dimension, group in (
                ("provider", bucket.get("by_provider") or {}),
                ("model", bucket.get("by_model") or {}),
                ("tenant", bucket.get("by_tenant") or {}),
                ("domain", bucket.get("by_domain") or {}),
            ):
                for key in sorted(group):
                    writer.writerow(
                        [month, dimension, key,
                         group[key]["calls"], group[key]["cost_cny"]]
                    )
            writer.writerow(
                [month, "total", "", bucket.get("calls", 0),
                 bucket.get("total_cny", "0.000000")]
            )
        return buf.getvalue()

    def list_pending_review(
        self,
        limit: int = 100,
        *,
        include_closed: bool = False,
        include_test: bool = False,
    ) -> list[dict[str, Any]]:
        """待对账明细（needs_review + 超龄未回收的 reserved），按最老优先。

        默认只显示开态（resolution=''）且非测试/评测数据；管理端筛选可
        打开已核销（include_closed）与测试数据（include_test）视图。
        """
        from backend.config.budget import BUDGET_STALE_RESERVATION_HOURS

        capped = max(1, min(limit, 500))
        cutoff = datetime.now(timezone.utc) - timedelta(
            hours=float(BUDGET_STALE_RESERVATION_HOURS)
        )
        closed_clause = ""
        if not include_closed:
            closed_clause = " AND resolution = ''"
        test_clause = ""
        if not include_test:
            test_clause = f" AND NOT {self._test_marker_where()}"
        try:
            with self._connection_factory() as conn:
                with conn.cursor() as cur:
                    query_params: dict[str, Any] = {
                        "cutoff": cutoff,
                        **self._test_marker_sql_params(),
                    }
                    cur.execute(
                        f"""SELECT DISTINCT ON (id) id, request_id, user_id, tenant_id,
                                   scope_type, scope_id, period_type, period_start,
                                   reserved_cny, status, review_reason, created_at,
                                   resolution, resolved_by, resolved_at, actual_cny
                            FROM {self._reservations}
                            WHERE (status = 'needs_review'{closed_clause}
                                   OR (status = 'reserved' AND created_at < %(cutoff)s)){test_clause}
                            ORDER BY id, created_at""",
                        query_params,
                    )
                    rows = cur.fetchall()
            items: dict[str, dict[str, Any]] = {}
            for (rid, request_id, user_id, tenant_id, scope_type, scope_id,
                 period_type, period_start, reserved, status, reason, created_at,
                 resolution, resolved_by, resolved_at, actual_cny) in rows:
                item = items.setdefault(rid, {
                    "reservation_id": str(rid),
                    "request_id": request_id,
                    "user_id": user_id,
                    "tenant_id": tenant_id,
                    "reserved_cny": str(Decimal(str(reserved)).quantize(_SIX_PLACES)),
                    "status": status,
                    "review_reason": reason or "",
                    "created_at": created_at.isoformat(),
                    "resolution": resolution or "",
                    "resolved_by": resolved_by or "",
                    "resolved_at": resolved_at.isoformat() if resolved_at else None,
                    "actual_cny": (
                        str(Decimal(str(actual_cny)).quantize(_SIX_PLACES))
                        if actual_cny is not None else None
                    ),
                    "periods": [],
                })
                item["periods"].append(
                    {"period_type": period_type, "period_start": period_start.isoformat()}
                )
                # 同一预占里只要还有未决周期，队列状态以更严重者为准
                if status == "reserved":
                    item["status"] = "reserved"
            return sorted(items.values(), key=lambda item: item["created_at"])[:capped]
        except Exception as exc:
            raise QuotaConfigurationError("待对账明细读取失败") from exc


def enforce_side_effect_budget(*, user_id: str, tenant_id: str) -> None:
    """在写副作用真正发生前做零金额硬门禁。"""
    from backend.config.llm import LLM_BUDGET_MODE

    if LLM_BUDGET_MODE != "enforce":
        return
    store = PostgresQuotaStore()
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


__all__ = [
    "BudgetPeriods",
    "BudgetPolicy",
    "QuotaConfigurationError",
    "QuotaExceeded",
    "QuotaManager",
    "QuotaReservation",
    "PostgresQuotaReservation",
    "PostgresQuotaStore",
    "ResolvedBudgetPolicies",
    "budget_periods",
    "enforce_side_effect_budget",
    "resolve_budget_policies",
]
