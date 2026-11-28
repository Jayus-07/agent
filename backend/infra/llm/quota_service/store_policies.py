from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal, ROUND_HALF_UP
from typing import Any
from zoneinfo import ZoneInfo

from backend.shared.logger import logger
from .models import (
    _IDENTITY_SCOPES,
    _SIX_PLACES,
    BudgetPolicy,
    QuotaConfigurationError,
    ResolvedBudgetPolicies,
    policy_source_label,
)
from .dynamic import (
    current_budget_periods as budget_periods,
    current_resolve_budget_policies as resolve_budget_policies,
)


class QuotaPolicyStoreMixin:
    """PG 预算策略查询与变更混入。"""

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
            domain=str(row[7] or ""),
        )

    def get_policy(
        self, scope_type: str, scope_id: str, domain: str = "",
    ) -> BudgetPolicy | None:
        try:
            with self._connection_factory() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        f"""SELECT scope_type, scope_id, daily_limit_cny,
                                   monthly_limit_cny, enforcement, timezone,
                                   audit_exempt, domain
                            FROM {self._policies}
                            WHERE scope_type = %s AND scope_id = %s
                              AND domain = %s""",
                        (scope_type, scope_id, domain),
                    )
                    return self._policy(cur.fetchone())
        except Exception as exc:
            raise QuotaConfigurationError("预算策略表不可用") from exc

    @staticmethod
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
    @staticmethod
    def _window(
        cur,
        ledger_table: str,
        scope_type: str,
        scope_id: str,
        template: BudgetPolicy,
        period_type: str,
        period_start: datetime,
        domain: str = "",
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
                  AND period_type = %s AND period_start = %s
                  AND domain = %s""",
            (scope_type, scope_id, period_type, period_start, domain),
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
            reset_at=QuotaPolicyStoreMixin._reset_at(
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
                                   audit_exempt, updated_by, updated_at, domain
                            FROM {self._policies}
                            ORDER BY scope_type, scope_id, domain"""
                    )
                    rows = cur.fetchall()
            keys = (
                "scope_type", "scope_id", "daily_limit_cny", "monthly_limit_cny",
                "enforcement", "timezone", "audit_exempt", "updated_by", "updated_at",
                "domain",
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
                              AND domain = ''
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
                            WHERE scope_type IN ('user', 'tenant')
                              AND domain = ''""",
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
        domain: str = "",
    ) -> dict[str, Any]:
        """立即生效地写入策略，并追加旧值/新值审计。

        domain='' 为总额度策略；非空为域额度策略（迁移 147，任务 9），
        并发控制（expected_updated_at）按 (scope, id, domain) 三元组判定。
        """
        if daily_limit_cny > monthly_limit_cny:
            raise ValueError("日额度不能高于月额度")
        policy = BudgetPolicy(
            scope_type=scope_type,
            scope_id=scope_id,
            daily_limit_cny=daily_limit_cny,
            monthly_limit_cny=monthly_limit_cny,
            enforcement=enforcement,
            audit_exempt=audit_exempt,
            domain=domain,
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
                                   audit_exempt, updated_by, updated_at, domain
                            FROM {self._policies}
                            WHERE scope_type = %s AND scope_id = %s
                              AND domain = %s
                            FOR UPDATE""",
                        (scope_type, scope_id, domain),
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
                                  audit_exempt, updated_by, domain
                              ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                              ON CONFLICT (scope_type, scope_id, domain) DO UPDATE SET
                                  daily_limit_cny = EXCLUDED.daily_limit_cny,
                                  monthly_limit_cny = EXCLUDED.monthly_limit_cny,
                                  enforcement = EXCLUDED.enforcement,
                                  audit_exempt = EXCLUDED.audit_exempt,
                                  updated_by = EXCLUDED.updated_by,
                                  updated_at = now()
                              RETURNING scope_type, scope_id, daily_limit_cny,
                                        monthly_limit_cny, enforcement, timezone,
                                        audit_exempt, updated_by, updated_at, domain""",
                        (
                            policy.scope_type, policy.scope_id,
                            policy.daily_limit_cny, policy.monthly_limit_cny,
                            policy.enforcement, timezone_name,
                            policy.audit_exempt, updated_by, domain,
                        ),
                    )
                    row = cur.fetchone()
                    before = dict(zip(
                        ("scope_type", "scope_id", "daily_limit_cny",
                         "monthly_limit_cny", "enforcement", "timezone",
                         "audit_exempt", "updated_by", "updated_at", "domain"),
                        old_row,
                    )) if old_row else None
                    after = dict(zip(
                        ("scope_type", "scope_id", "daily_limit_cny",
                         "monthly_limit_cny", "enforcement", "timezone",
                         "audit_exempt", "updated_by", "updated_at", "domain"),
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

    def resolve_domain_policy(
        self, user_id: str, tenant_id: str, domain: str,
    ) -> BudgetPolicy | None:
        """解析某域的策略链：用户域覆盖 > 租户域覆盖 > 全租户域默认 > 平台域兜底。

        四层都没有（如新增域未配策略）返回 None——该域不做域闸（放行），
        由种子配置保证 3 个顶级域始终有默认值；调用方据此跳过域窗口。
        """
        return (
            self.get_policy("user", user_id, domain)
            or self.get_policy("tenant", tenant_id, domain)
            or self.get_policy("tenant_default", "default", domain)
            or self.get_policy("platform", "default", domain)
        )
