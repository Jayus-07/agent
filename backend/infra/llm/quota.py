"""Q4 用户/租户日月预算的策略解析、周期计算和本地契约实现。"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal, ROUND_HALF_UP
from typing import Any
import os
import uuid
from zoneinfo import ZoneInfo

from backend.shared.logger import logger


_SIX_PLACES = Decimal("0.000001")
_VALID_SCOPES = frozenset({"user", "tenant", "tenant_default", "platform"})
_VALID_ENFORCEMENT = frozenset({"hard", "soft", "audit"})


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


class QuotaExceeded(RuntimeError):
    """日/月额度硬阻断。"""

    code = "BUDGET_EXCEEDED"
    retryable = False

    def __init__(self, subject_id: str, period: str):
        self.subject_id = subject_id
        self.period = period
        super().__init__(f"预算已达到上限: {subject_id}/{period}")


@dataclass(frozen=True)
class BudgetPolicy:
    scope_type: str
    scope_id: str
    daily_limit_usd: Decimal
    monthly_limit_usd: Decimal
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
        if self.daily_limit_usd <= 0 or self.monthly_limit_usd <= 0:
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
    daily_remaining_usd: Decimal = Decimal("0")
    monthly_remaining_usd: Decimal = Decimal("0")


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

    def record(self, tenant_id: str, user_id: str, amount_usd: Decimal) -> None:
        policies = self._resolve(tenant_id, user_id)
        for policy in {policies.user, policies.tenant}:
            self._used_for(policy, tenant_id, user_id, amount_usd)

    def reserve(
        self,
        tenant_id: str,
        user_id: str,
        amount_usd: Decimal,
        now: datetime | None = None,
    ) -> QuotaReservation:
        amount = Decimal(amount_usd).quantize(_SIX_PLACES, rounding=ROUND_HALF_UP)
        if amount < 0:
            raise ValueError("预算预占金额不能为负数")
        policies = self._resolve(tenant_id, user_id)
        audit_exempt = False
        daily_remaining = Decimal("999999999")
        monthly_remaining = Decimal("999999999")
        periods = budget_periods(now, policies.tenant.timezone)
        for policy in {policies.user, policies.tenant}:
            daily_used = self._used.get(
                (policy.scope_type, policy.scope_id, periods.day_start_utc.isoformat()),
                Decimal("0"),
            )
            monthly_used = self._used.get(
                (policy.scope_type, policy.scope_id, periods.month_start_utc.isoformat()),
                Decimal("0"),
            )
            daily_remaining = min(daily_remaining, policy.daily_limit_usd - daily_used)
            monthly_remaining = min(monthly_remaining, policy.monthly_limit_usd - monthly_used)
            audit_exempt = audit_exempt or policy.audit_exempt
            if (
                policy.enforcement == "hard"
                and not policy.audit_exempt
                and (daily_used + amount > policy.daily_limit_usd
                     or monthly_used + amount > policy.monthly_limit_usd)
            ):
                raise QuotaExceeded(policy.scope_id, "day_or_month")
        return QuotaReservation(
            blocked=False,
            audit_exempt=audit_exempt,
            daily_remaining_usd=max(daily_remaining, Decimal("0")),
            monthly_remaining_usd=max(monthly_remaining, Decimal("0")),
        )

    def _used_for(
        self,
        policy: BudgetPolicy,
        tenant_id: str,
        user_id: str,
        amount_usd: Decimal,
    ) -> None:
        periods = budget_periods(timezone_name=policy.timezone)
        for period_start in (periods.day_start_utc, periods.month_start_utc):
            key = (policy.scope_type, policy.scope_id, period_start.isoformat())
            self._used[key] = self._used.get(key, Decimal("0")) + amount_usd


@dataclass(frozen=True)
class PostgresQuotaReservation:
    reservation_id: str
    user_id: str
    tenant_id: str
    reserved_usd: Decimal


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

    @staticmethod
    def _connect():
        import psycopg2

        from backend.config.database import MEMORY_DB_CONFIG

        return psycopg2.connect(**MEMORY_DB_CONFIG)

    @staticmethod
    def _policy(row: tuple[Any, ...] | None) -> BudgetPolicy | None:
        if row is None:
            return None
        return BudgetPolicy(
            scope_type=row[0],
            scope_id=row[1],
            daily_limit_usd=Decimal(str(row[2])),
            monthly_limit_usd=Decimal(str(row[3])),
            enforcement=row[4],
            timezone=row[5],
            audit_exempt=bool(row[6]),
        )

    def get_policy(self, scope_type: str, scope_id: str) -> BudgetPolicy | None:
        try:
            with self._connection_factory() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        f"""SELECT scope_type, scope_id, daily_limit_usd,
                                   monthly_limit_usd, enforcement, timezone,
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
        policy: BudgetPolicy,
        period_type: str,
        period_start: datetime,
    ) -> dict[str, Any]:
        limit = (
            policy.daily_limit_usd
            if period_type == "day"
            else policy.monthly_limit_usd
        )
        cur.execute(
            f"""SELECT used_usd, reserved_usd, limit_usd
                FROM {ledger_table}
                WHERE scope_type = %s AND scope_id = %s
                  AND period_type = %s AND period_start = %s""",
            (policy.scope_type, policy.scope_id, period_type, period_start),
        )
        row = cur.fetchone()
        used = Decimal(str(row[0])) if row else Decimal("0")
        reserved = Decimal(str(row[1])) if row else Decimal("0")
        if row and row[2] is not None:
            limit = Decimal(str(row[2]))
        from backend.app.api.routes.budget_dto import build_budget_window

        return build_budget_window(
            used=used,
            reserved=reserved,
            limit=limit,
            reset_at=PostgresQuotaStore._reset_at(
                period_type, period_start, policy.timezone,
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
                    user_daily = self._window(
                        cur, self._ledger, policies.user, "day", periods.day_start_utc,
                    )
                    user_monthly = self._window(
                        cur, self._ledger, policies.user, "month", periods.month_start_utc,
                    )
                    tenant_daily = self._window(
                        cur, self._ledger, policies.tenant, "day", periods.day_start_utc,
                    )
                    tenant_monthly = self._window(
                        cur, self._ledger, policies.tenant, "month", periods.month_start_utc,
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

        return {
            "currency": "USD",
            "mode": llm_config.LLM_BUDGET_MODE,
            "enforcement": policies.user.enforcement,
            "audit_exempt": policies.user.audit_exempt,
            "blocked": user_blocked or tenant_blocked,
            "tenant_blocked": tenant_blocked,
            "policy_source": {
                "scope_type": source.scope_type if source else "unknown",
                "scope_id": source.scope_id if source else "unknown",
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
                        f"""SELECT scope_type, scope_id, daily_limit_usd,
                                   monthly_limit_usd, enforcement, timezone,
                                   audit_exempt, updated_by, updated_at
                            FROM {self._policies}
                            ORDER BY scope_type, scope_id"""
                    )
                    rows = cur.fetchall()
            keys = (
                "scope_type", "scope_id", "daily_limit_usd", "monthly_limit_usd",
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

    def list_subjects(self, limit: int = 200) -> list[dict[str, Any]]:
        """读取有策略或有账本记录的主体，金额仍以字符串返回。"""
        try:
            with self._connection_factory() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        f"""SELECT scope_type, scope_id, daily_limit_usd,
                                   monthly_limit_usd, enforcement, timezone,
                                   audit_exempt
                            FROM {self._policies}
                            ORDER BY scope_type, scope_id LIMIT %s""",
                        (limit,),
                    )
                    policy_rows = cur.fetchall()
                    cur.execute(
                        f"""SELECT scope_type, scope_id, period_type,
                                   used_usd, reserved_usd, limit_usd, period_start
                            FROM {self._ledger}
                            WHERE scope_type IN ('user', 'tenant')
                            ORDER BY updated_at DESC"""
                    )
                    ledger_rows = cur.fetchall()
            policies = {
                (row[0], row[1]): BudgetPolicy(
                    scope_type=row[0], scope_id=row[1],
                    daily_limit_usd=Decimal(str(row[2])),
                    monthly_limit_usd=Decimal(str(row[3])), enforcement=row[4],
                    timezone=row[5], audit_exempt=bool(row[6]),
                )
                for row in policy_rows
            }
            ledgers = {
                (row[0], row[1], row[2]): row for row in ledger_rows
            }
            result = []
            for policy in policies.values():
                if policy.scope_type not in {"user", "tenant"}:
                    continue
                if policy.scope_type == "user":
                    candidates = [
                        policies.get(("user", policy.scope_id)),
                        policies.get(("tenant", "")),
                        policies.get(("tenant_default", "default")),
                        policies.get(("platform", "default")),
                    ]
                else:
                    candidates = [
                        policies.get(("tenant", policy.scope_id)),
                        policies.get(("tenant_default", "default")),
                        policies.get(("platform", "default")),
                    ]
                effective = next((item for item in candidates if item is not None), policy)

                def policy_view(item: BudgetPolicy | None) -> dict[str, Any] | None:
                    if item is None:
                        return None
                    return {
                        "scope_type": item.scope_type,
                        "scope_id": item.scope_id,
                        "daily_limit_usd": item.daily_limit_usd,
                        "monthly_limit_usd": item.monthly_limit_usd,
                        "enforcement": item.enforcement,
                        "timezone": item.timezone,
                        "audit_exempt": item.audit_exempt,
                    }

                periods = budget_periods(timezone_name=policy.timezone)
                windows = {}
                for period_type, start in (
                    ("day", periods.day_start_utc),
                    ("month", periods.month_start_utc),
                ):
                    ledger = ledgers.get((policy.scope_type, policy.scope_id, period_type))
                    if ledger and ledger[6] == start:
                        from backend.app.api.routes.budget_dto import build_budget_window

                        windows[period_type] = build_budget_window(
                            used=Decimal(str(ledger[3])),
                            reserved=Decimal(str(ledger[4])),
                            limit=Decimal(str(ledger[5])),
                            reset_at=self._reset_at(period_type, start, policy.timezone),
                        )
                    else:
                        from backend.app.api.routes.budget_dto import build_budget_window

                        windows[period_type] = build_budget_window(
                            used=Decimal("0"), reserved=Decimal("0"),
                            limit=(policy.daily_limit_usd if period_type == "day"
                                   else policy.monthly_limit_usd),
                            reset_at=self._reset_at(period_type, start, policy.timezone),
                        )
                ratio = max(windows["day"]["ratio"], windows["month"]["ratio"])
                result.append({
                    "scope": policy.scope_type,
                    "id": policy.scope_id,
                    "daily": windows["day"],
                    "monthly": windows["month"],
                    "enforcement": policy.enforcement,
                    "ratio": ratio,
                    "explicit_policy": policy_view(policy),
                    "effective_policy": policy_view(effective),
                    "policy_source": {
                        "scope_type": effective.scope_type,
                        "scope_id": effective.scope_id,
                    },
                })
            return result
        except Exception as exc:
            raise QuotaConfigurationError("预算主体读取失败") from exc

    def summary(self) -> dict[str, Any]:
        try:
            with self._connection_factory() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        f"""SELECT COALESCE(SUM(used_usd), 0),
                                   COALESCE(SUM(reserved_usd), 0),
                                   COUNT(*) FILTER (WHERE used_usd + reserved_usd >= limit_usd),
                                   COUNT(*) FILTER (WHERE used_usd + reserved_usd >= limit_usd * 0.8)
                            FROM {self._ledger}"""
                    )
                    total_cost, reserved, blocked, near = cur.fetchone()
                    cur.execute(
                        f"""SELECT COALESCE(
                                   SUM(CASE WHEN approval_status = 'approved' THEN 1 ELSE 0 END)::numeric
                                   / NULLIF(COUNT(*), 0), 0)
                            FROM {self._price_table}"""
                    )
                    coverage = cur.fetchone()[0]
            return {
                "currency": "USD",
                "total_cost_usd": str(Decimal(str(total_cost)).quantize(_SIX_PLACES)),
                "price_coverage_ratio": float(coverage or 0),
                "near_limit_subjects": int(near or 0),
                "blocked_subjects": int(blocked or 0),
                "unsettled_reserved_usd": str(Decimal(str(reserved)).quantize(_SIX_PLACES)),
            }
        except Exception as exc:
            raise QuotaConfigurationError("预算汇总读取失败") from exc

    def upsert_policy(
        self,
        *,
        scope_type: str,
        scope_id: str,
        daily_limit_usd: Decimal,
        monthly_limit_usd: Decimal,
        enforcement: str,
        audit_exempt: bool,
        updated_by: str,
        reason: str,
        expected_updated_at: datetime | None = None,
    ) -> dict[str, Any]:
        """立即生效地写入策略，并追加旧值/新值审计。"""
        if daily_limit_usd > monthly_limit_usd:
            raise ValueError("日额度不能高于月额度")
        policy = BudgetPolicy(
            scope_type=scope_type,
            scope_id=scope_id,
            daily_limit_usd=daily_limit_usd,
            monthly_limit_usd=monthly_limit_usd,
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
                        f"""SELECT scope_type, scope_id, daily_limit_usd,
                                   monthly_limit_usd, enforcement, timezone,
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
                                  scope_type, scope_id, daily_limit_usd,
                                  monthly_limit_usd, enforcement, timezone,
                                  audit_exempt, updated_by
                              ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                              ON CONFLICT (scope_type, scope_id) DO UPDATE SET
                                  daily_limit_usd = EXCLUDED.daily_limit_usd,
                                  monthly_limit_usd = EXCLUDED.monthly_limit_usd,
                                  enforcement = EXCLUDED.enforcement,
                                  audit_exempt = EXCLUDED.audit_exempt,
                                  updated_by = EXCLUDED.updated_by,
                                  updated_at = now()
                              RETURNING scope_type, scope_id, daily_limit_usd,
                                        monthly_limit_usd, enforcement, timezone,
                                        audit_exempt, updated_by, updated_at""",
                        (
                            policy.scope_type, policy.scope_id,
                            policy.daily_limit_usd, policy.monthly_limit_usd,
                            policy.enforcement, timezone_name,
                            policy.audit_exempt, updated_by,
                        ),
                    )
                    row = cur.fetchone()
                    before = dict(zip(
                        ("scope_type", "scope_id", "daily_limit_usd",
                         "monthly_limit_usd", "enforcement", "timezone",
                         "audit_exempt", "updated_by", "updated_at"),
                        old_row,
                    )) if old_row else None
                    after = dict(zip(
                        ("scope_type", "scope_id", "daily_limit_usd",
                         "monthly_limit_usd", "enforcement", "timezone",
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
        amount_usd: Decimal,
        request_id: str = "",
        now: datetime | None = None,
    ) -> PostgresQuotaReservation:
        if not user_id or not tenant_id:
            raise QuotaConfigurationError("硬预算需要可信 user_id 和 tenant_id")
        amount = Decimal(amount_usd).quantize(_SIX_PLACES, rounding=ROUND_HALF_UP)
        if amount < 0:
            raise ValueError("预算预占金额不能为负数")
        policies = self.resolve(user_id, tenant_id)
        periods = budget_periods(now, policies.tenant.timezone)
        unique_policies = {
            (policy.scope_type, policy.scope_id): policy
            for policy in (policies.user, policies.tenant)
        }
        reservation_id = str(uuid.uuid4())
        try:
            with self._connection_factory() as conn:
                with conn.cursor() as cur:
                    for policy in unique_policies.values():
                        for period_type, period_start, limit in (
                            ("day", periods.day_start_utc, policy.daily_limit_usd),
                            ("month", periods.month_start_utc, policy.monthly_limit_usd),
                        ):
                            cur.execute(
                                f"""
                                INSERT INTO {self._ledger} (
                                    scope_type, scope_id, period_type, period_start,
                                    limit_usd, enforcement, reserved_usd
                                )
                                SELECT %s, %s, %s, %s, %s, %s, %s
                                WHERE %s <> 'hard' OR %s <= %s
                                ON CONFLICT (scope_type, scope_id, period_type, period_start)
                                DO UPDATE SET
                                    limit_usd = EXCLUDED.limit_usd,
                                    enforcement = EXCLUDED.enforcement,
                                    reserved_usd = {self._ledger}.reserved_usd
                                        + EXCLUDED.reserved_usd,
                                    updated_at = now()
                                WHERE EXCLUDED.enforcement <> 'hard'
                                   OR ({self._ledger}.used_usd
                                       + {self._ledger}.reserved_usd
                                       + EXCLUDED.reserved_usd <= EXCLUDED.limit_usd)
                                RETURNING scope_type, scope_id, used_usd,
                                          reserved_usd, limit_usd
                                """,
                                (
                                    policy.scope_type, policy.scope_id, period_type,
                                    period_start, limit, policy.enforcement, amount,
                                    policy.enforcement, amount, limit,
                                ),
                            )
                            ledger_row = cur.fetchone()
                            if ledger_row is None:
                                _record_quota_metric(
                                    "budget_quota_total",
                                    scope_type=policy.scope_type,
                                    period_type=period_type,
                                    result="rejected",
                                )
                                raise QuotaExceeded(policy.scope_id, period_type)
                            _record_quota_metric(
                                "budget_quota_total",
                                scope_type=policy.scope_type,
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
                                    reserved_usd
                                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                                """,
                                (
                                    reservation_id, request_id, user_id, tenant_id,
                                    policy.scope_type, policy.scope_id, period_type,
                                    period_start, amount,
                                ),
                            )
            return PostgresQuotaReservation(
                reservation_id=reservation_id,
                user_id=user_id,
                tenant_id=tenant_id,
                reserved_usd=amount,
            )
        except (QuotaExceeded, QuotaConfigurationError):
            raise
        except Exception as exc:
            raise QuotaConfigurationError("预算预占存储不可用") from exc

    def settle(self, reservation: PostgresQuotaReservation, actual_usd: Decimal) -> None:
        actual = Decimal(actual_usd).quantize(_SIX_PLACES, rounding=ROUND_HALF_UP)
        if actual < 0:
            raise ValueError("结算金额不能为负数")
        try:
            with self._connection_factory() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        f"""SELECT scope_type, scope_id, period_type,
                                   period_start, reserved_usd
                            FROM {self._reservations}
                            WHERE id = %s AND status = 'reserved'
                            FOR UPDATE""",
                        (reservation.reservation_id,),
                    )
                    rows = cur.fetchall()
                    for scope_type, scope_id, period_type, period_start, reserved in rows:
                        cur.execute(
                            f"""UPDATE {self._ledger}
                                SET reserved_usd = GREATEST(reserved_usd - %s, 0),
                                    used_usd = used_usd + %s,
                                    updated_at = now()
                                WHERE scope_type = %s AND scope_id = %s
                                  AND period_type = %s AND period_start = %s
                                RETURNING used_usd, reserved_usd, limit_usd""",
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
                            SET settled_usd = %s, status = 'settled', settled_at = now()
                            WHERE id = %s AND status = 'reserved'""",
                        (actual, reservation.reservation_id),
                    )
        except Exception as exc:
            raise QuotaConfigurationError("预算结算存储不可用") from exc

    def release(self, reservation: PostgresQuotaReservation) -> None:
        """模型调用未产生用量时释放预占，不写入 used_usd。"""
        try:
            with self._connection_factory() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        f"""SELECT scope_type, scope_id, period_type,
                                   period_start, reserved_usd
                            FROM {self._reservations}
                            WHERE id = %s AND status = 'reserved'
                            FOR UPDATE""",
                        (reservation.reservation_id,),
                    )
                    rows = cur.fetchall()
                    for scope_type, scope_id, period_type, period_start, reserved in rows:
                        cur.execute(
                            f"""UPDATE {self._ledger}
                                SET reserved_usd = GREATEST(reserved_usd - %s, 0),
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
            amount_usd=Decimal("0"),
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
