from __future__ import annotations

import csv
import io
import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal, ROUND_HALF_UP
from typing import Any

from backend.shared.logger import logger
from .models import (
    _IDENTITY_SCOPES,
    _SIX_PLACES,
    QuotaConfigurationError,
)
from .dynamic import (
    current_record_quota_metric as _record_quota_metric,
)
from .dynamic import (
    current_budget_periods as budget_periods,
)


class QuotaReconciliationStoreMixin:
    """PG 待对账、月度对账与回收核销混入。"""

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
                                   period_start, domain, reserved_cny, resolution
                            FROM {self._reservations}
                            WHERE id = %s AND status = 'needs_review'
                            ORDER BY CASE scope_type WHEN 'user' THEN 0 ELSE 1 END,
                                     CASE period_type WHEN 'day' THEN 0 ELSE 1 END,
                                     domain
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
                    held = Decimal(str(rows[0][5])).quantize(_SIX_PLACES)
                    current_resolution = str(rows[0][6] or "")
                    if current_resolution:
                        return {
                            "reservation_id": reservation_id, "action": action,
                            "found": True, "already_resolved": True,
                            "released_cny": "0.000000", "booked_cny": None,
                            "resolution": current_resolution,
                        }
                    for scope_type, scope_id, period_type, period_start, row_domain, reserved, _ in rows:
                        cur.execute(
                            f"""UPDATE {self._ledger}
                                SET reserved_cny = GREATEST(reserved_cny - %s, 0),
                                    used_cny = used_cny + COALESCE(%s, 0),
                                    updated_at = now()
                                WHERE scope_type = %s AND scope_id = %s
                                  AND period_type = %s AND period_start = %s
                                  AND domain = %s
                                RETURNING used_cny, reserved_cny, limit_cny""",
                            (reserved, booked, scope_type, scope_id,
                             period_type, period_start, row_domain),
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
                    # ② ledger 月行：月 × scope 汇总（只算总额度轴——域行是
                    # 同一笔钱的域维度镜像，不滤会把入账翻倍；域拆分见
                    # by_domain usage 段）
                    cur.execute(
                        f"""SELECT to_char(period_start AT TIME ZONE %s,
                                           'YYYY-MM') AS month,
                                   scope_type, scope_id,
                                   SUM(used_cny), SUM(reserved_cny)
                              FROM {self._ledger}
                             WHERE period_type = 'month'
                               AND domain = ''
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
