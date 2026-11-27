from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal, ROUND_HALF_UP
from typing import Any
from zoneinfo import ZoneInfo

from backend.shared.logger import logger
from .models import (
    _IDENTITY_SCOPES,
    _SIX_PLACES,
    BudgetPolicy,
    PostgresQuotaReservation,
    QuotaConfigurationError,
    QuotaExceeded,
    ResolvedBudgetPolicies,
)
from .dynamic import (
    current_insert_threshold_events as _insert_threshold_events,
    current_record_quota_metric as _record_quota_metric,
)
from .dynamic import (
    current_budget_periods as budget_periods,
    current_resolve_budget_policies as resolve_budget_policies,
)


class QuotaReservationStoreMixin:
    """PG 原子预占与结算混入。"""

    def reserve(
        self,
        *,
        user_id: str,
        tenant_id: str,
        amount_cny: Decimal,
        request_id: str = "",
        now: datetime | None = None,
        request_limit_cny: Decimal | None = None,
        domain: str = "",
    ) -> PostgresQuotaReservation:
        """按真实身份预占（user/tenant 两层，模板策略只提供上限）。

        2026-10-01 边界修复：金额为 0 的硬门禁（副作用零金额检查）在
        used+reserved 已达上限时同样拒绝——原条件 ``+0 <= limit`` 会让
        打满的账本继续放行零金额调用。
        2026-11-11 域额度（任务 9，迁移 147）：domain 非空时在总额度闸之外
        叠加同域闸（用户域 > 租户域，继承链同构），一笔域调用预占 8 行
        （user/tenant × 总/域 × 日/月）；domain='' 行为与历史完全一致。
        """
        if not user_id or not tenant_id:
            raise QuotaConfigurationError("硬预算需要可信 user_id 和 tenant_id")
        amount = Decimal(amount_cny).quantize(_SIX_PLACES, rounding=ROUND_HALF_UP)
        if amount < 0:
            raise ValueError("预算预占金额不能为负数")
        if domain:
            from backend.config.budget import BUDGET_DOMAIN_GATES

            if domain not in BUDGET_DOMAIN_GATES:
                raise ValueError(f"非法预算域: {domain!r}")
        request_limit = None
        if request_limit_cny is not None:
            request_limit = Decimal(request_limit_cny)
            if not request_id or not request_limit.is_finite() or request_limit < 0:
                raise ValueError('整批预算必须有请求ID和非负有限人民币上限')
            request_limit = request_limit.quantize(_SIX_PLACES, rounding=ROUND_HALF_UP)
        policies = self.resolve(user_id, tenant_id)
        domain_policy = (
            self.resolve_domain_policy(user_id, tenant_id, domain)
            if domain else None
        )
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
                    def _reserve_window(
                        scope_type: str,
                        scope_id: str,
                        policy: BudgetPolicy,
                        axis_domain: str,
                        period_type: str,
                        period_start: datetime,
                    ) -> None:
                        """单一窗口原子预占：账本硬闸 upsert + 预占行落库。

                        域窗口（axis_domain 非空）不写阈值事件——budget_events
                        无域维度，域告警由 QuotaExceeded 拒绝日志与月度对账
                        域成本段承载。
                        """
                        limit = (
                            policy.daily_limit_cny
                            if period_type == "day"
                            else policy.monthly_limit_cny
                        )
                        cur.execute(
                            f"""
                            INSERT INTO {self._ledger} (
                                scope_type, scope_id, period_type, period_start,
                                domain, limit_cny, enforcement, reserved_cny
                            )
                            SELECT %s, %s, %s, %s, %s, %s, %s, %s
                            WHERE %s <> 'hard' OR %s <= %s
                            ON CONFLICT (scope_type, scope_id, period_type,
                                         period_start, domain)
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
                                period_start, axis_domain, limit,
                                policy.enforcement, amount,
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
                                f"{scope_type}:{scope_id}:{axis_domain}",
                                period_type,
                            )
                        _record_quota_metric(
                            "budget_quota_total",
                            scope_type=scope_type,
                            period_type=period_type,
                            result="reserved",
                        )
                        if not axis_domain:
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
                                domain, reserved_cny
                            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                            """,
                            (
                                reservation_id, request_id, user_id, tenant_id,
                                scope_type, scope_id, period_type,
                                period_start, axis_domain, amount,
                            ),
                        )

                    # 域策略两级解析：用户域闸走完整继承链（用户域覆盖 >
                    # 租户域覆盖 > 全租户域默认 > 平台域兜底），租户域闸跳过
                    # 用户层——与总额度闸的解析结构完全同构
                    domain_policy_tenant = None
                    if domain:
                        domain_policy_tenant = (
                            self.get_policy("tenant", tenant_id, domain)
                            or self.get_policy("tenant_default", "default", domain)
                            or self.get_policy("platform", "default", domain)
                        )
                    for scope_type, scope_id, policy, subject_domain_policy in (
                        ("user", user_id, policies.user, domain_policy),
                        ("tenant", tenant_id, policies.tenant, domain_policy_tenant),
                    ):
                        for period_type, period_start in (
                            ("day", periods.day_start_utc),
                            ("month", periods.month_start_utc),
                        ):
                            _reserve_window(
                                scope_type, scope_id, policy, "",
                                period_type, period_start,
                            )
                            if subject_domain_policy is not None:
                                _reserve_window(
                                    scope_type, scope_id, subject_domain_policy,
                                    domain, period_type, period_start,
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
                    # 账本行锁序必须与 reserve（user→tenant、day→month、总→域）
                    # 一致：本查询无 ORDER BY 时按索引返回 tenant 在前，与
                    # reserve 互持行锁 → AB-BA 死锁（2026-10-29 生产实测 21 次）
                    cur.execute(
                        f"""SELECT scope_type, scope_id, period_type,
                                   period_start, domain, reserved_cny
                            FROM {self._reservations}
                            WHERE id = %s AND status = 'reserved'
                            ORDER BY CASE scope_type WHEN 'user' THEN 0 ELSE 1 END,
                                     CASE period_type WHEN 'day' THEN 0 ELSE 1 END,
                                     domain
                            FOR UPDATE""",
                        (reservation.reservation_id,),
                    )
                    rows = cur.fetchall()
                    for scope_type, scope_id, period_type, period_start, row_domain, reserved in rows:
                        cur.execute(
                            f"""UPDATE {self._ledger}
                                SET reserved_cny = GREATEST(reserved_cny - %s, 0),
                                    used_cny = used_cny + %s,
                                    updated_at = now()
                                WHERE scope_type = %s AND scope_id = %s
                                  AND period_type = %s AND period_start = %s
                                  AND domain = %s
                                RETURNING used_cny, reserved_cny, limit_cny""",
                            (reserved, actual, scope_type, scope_id,
                             period_type, period_start, row_domain),
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
                    # 同 settle：账本行锁序必须与 reserve（user→tenant、
                    # day→month、总→域）一致，乱序会与 reserve 的 upsert
                    # 互锁（AB-BA 死锁）
                    cur.execute(
                        f"""SELECT scope_type, scope_id, period_type,
                                   period_start, domain, reserved_cny
                            FROM {self._reservations}
                            WHERE id = %s AND status = 'reserved'
                            ORDER BY CASE scope_type WHEN 'user' THEN 0 ELSE 1 END,
                                     CASE period_type WHEN 'day' THEN 0 ELSE 1 END,
                                     domain
                            FOR UPDATE""",
                        (reservation.reservation_id,),
                    )
                    rows = cur.fetchall()
                    for scope_type, scope_id, period_type, period_start, row_domain, reserved in rows:
                        cur.execute(
                            f"""UPDATE {self._ledger}
                                SET reserved_cny = GREATEST(reserved_cny - %s, 0),
                                    updated_at = now()
                                WHERE scope_type = %s AND scope_id = %s
                                  AND period_type = %s AND period_start = %s
                                  AND domain = %s""",
                            (reserved, scope_type, scope_id, period_type,
                             period_start, row_domain),
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
                                   period_start, domain, reserved_cny
                                FROM {self._reservations}
                                WHERE id = %s AND status = 'reserved'
                                ORDER BY CASE scope_type WHEN 'user' THEN 0 ELSE 1 END,
                                         CASE period_type WHEN 'day' THEN 0 ELSE 1 END,
                                         domain
                                FOR UPDATE""",
                            (reservation_pk,),
                        )
                        rows = cur.fetchall()
                        if not rows:
                            continue
                        period_open = any(
                            (period_type, period_start) in current_starts
                            for _, _, period_type, period_start, _, _ in rows
                        )
                        if not period_open:
                            for scope_type, scope_id, period_type, period_start, row_domain, reserved in rows:
                                cur.execute(
                                    f"""UPDATE {self._ledger}
                                        SET reserved_cny = GREATEST(reserved_cny - %s, 0),
                                            updated_at = now()
                                        WHERE scope_type = %s AND scope_id = %s
                                          AND period_type = %s AND period_start = %s
                                          AND domain = %s""",
                                    (reserved, scope_type, scope_id,
                                     period_type, period_start, row_domain),
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
