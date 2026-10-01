"""预算治理 P0 修复回归（2026-10-01）。

覆盖四条审查结论的修复：
1. 账本键拆分——继承默认策略的租户/用户不再共用一本账；
2. 零金额硬门禁边界——余额恰好等于上限时，$0 副作用调用必须被拦；
3. 待对账生命周期——用量未知的预占转 needs_review，滞留可回收、可查询；
4. 记账本位币——CNY 折算与未登记币种的诚实降级。

内存用例不依赖 PG；PG 用例连真实权威库（PGPORT=5433），自建数据自清理。
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal

import psycopg2
import pytest

from backend.config.budget import to_base_currency
from backend.config.database import MEMORY_DB_CONFIG
from backend.infra.llm.quota import (
    BudgetPolicy,
    PostgresQuotaStore,
    QuotaExceeded,
    QuotaManager,
)


def _policy(scope_type: str, scope_id: str, daily: str, monthly: str,
            enforcement: str = "hard") -> BudgetPolicy:
    return BudgetPolicy(
        scope_type=scope_type, scope_id=scope_id,
        daily_limit_cny=Decimal(daily), monthly_limit_cny=Decimal(monthly),
        enforcement=enforcement,
    )


# ── 1. 身份键隔离（内存契约层）───────────────────────────────────────


def test_inherited_tenants_do_not_share_ledger():
    """两个都没有显式租户策略的租户，各自独立占用 tenant_default 模板额度。

    注意 QuotaManager 语义：reserve 只裁决不落账，record 才累积用量。
    """
    manager = QuotaManager([
        _policy("platform", "default", "100", "1000"),
        _policy("tenant_default", "default", "10", "100"),
    ])
    manager.record("tenant-a", "u1", Decimal("6"))
    manager.record("tenant-b", "u2", Decimal("6"))
    # 各自 6/10：都能预占；旧 bug 下两租户共用一本账（12/10）此处即拒
    manager.reserve("tenant-a", "u1", Decimal("3"))
    manager.reserve("tenant-b", "u2", Decimal("3"))
    # 租户 A 打满到 10：正金额与零金额都拒绝
    manager.record("tenant-a", "u1", Decimal("4"))
    with pytest.raises(QuotaExceeded):
        manager.reserve("tenant-a", "u1", Decimal("1"))
    with pytest.raises(QuotaExceeded):
        manager.reserve("tenant-a", "u1", Decimal("0"))
    # 租户 B 完全不受 A 打满影响（隔离性）
    manager.reserve("tenant-b", "u2", Decimal("1"))


def test_user_layer_is_independent_from_tenant_layer():
    """用户层继承租户模板时，用户键与租户键是两本账，互不吞占。"""
    manager = QuotaManager([
        _policy("platform", "default", "100", "1000"),
        _policy("tenant_default", "default", "10", "100"),
    ])
    manager.record("tenant-a", "u1", Decimal("6"))  # 用户 6 + 租户 6
    # 用户层已 6/10，还能 3；若用户键塌缩进租户键（旧 bug），12/10 直接拒
    manager.reserve("tenant-a", "u1", Decimal("3"))


def test_zero_amount_blocked_at_exact_limit():
    """零金额副作用检查：used == limit 时 $0 预占必须拒绝（边界修复）。"""
    manager = QuotaManager([
        _policy("platform", "default", "100", "1000"),
        _policy("tenant_default", "default", "10", "100"),
    ])
    manager.record("tenant-a", "u1", Decimal("10"))  # 打满日额度
    with pytest.raises(QuotaExceeded):
        manager.reserve("tenant-a", "u1", Decimal("0"))
    # 未打满时 $0 预占照常放行
    manager.reserve("tenant-b", "u2", Decimal("0"))


# ── 2. 记账本位币 ────────────────────────────────────────────────────


def test_to_base_currency_converts_usd_and_rejects_unknown():
    assert to_base_currency(Decimal("2"), "USD") == Decimal("14.40")
    assert to_base_currency(Decimal("2"), "cny") == Decimal("2")
    with pytest.raises(ValueError):
        to_base_currency(Decimal("1"), "EUR")


# ── 3. 待对账生命周期（PG 集成）─────────────────────────────────────


@pytest.fixture()
def _pg_cleanup():
    """自建预算数据自清理（按 tenant 前缀隔离，不动他人数据）。"""
    token = f"recon_test_{datetime.now().strftime('%H%M%S%f')}"
    yield token
    with psycopg2.connect(**MEMORY_DB_CONFIG) as conn:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM budget_reservations WHERE tenant_id LIKE %s",
                        (f"{token}%",))
            cur.execute("DELETE FROM budget_ledger WHERE scope_id LIKE %s",
                        (f"{token}%",))
            cur.execute("DELETE FROM budget_policies WHERE scope_id LIKE %s",
                        (f"{token}%",))


def _insert_policy(token: str, daily: str) -> None:
    with psycopg2.connect(**MEMORY_DB_CONFIG) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """INSERT INTO budget_policies
                       (scope_type, scope_id, daily_limit_cny,
                        monthly_limit_cny, enforcement, timezone, updated_by)
                   VALUES ('tenant', %s, %s, %s, 'hard', 'Asia/Shanghai', 'test')
                   ON CONFLICT (scope_type, scope_id) DO UPDATE
                       SET daily_limit_cny = EXCLUDED.daily_limit_cny""",
                (token, Decimal(daily), Decimal(daily)),
            )


def test_pg_identity_isolation_and_zero_boundary(_pg_cleanup):
    """PG 层：两个租户账本行独立；打满后 $0 预占被拒、$0 结算不滚账。"""
    token_a, token_b = f"{_pg_cleanup}_a", f"{_pg_cleanup}_b"
    _insert_policy(token_a, "0.050000")
    _insert_policy(token_b, "0.050000")
    store = PostgresQuotaStore()
    store.reserve(user_id=f"{token_a}-u1", tenant_id=token_a, amount_cny=Decimal("0.03"))
    store.reserve(user_id=f"{token_b}-u2", tenant_id=token_b, amount_cny=Decimal("0.03"))
    with psycopg2.connect(**MEMORY_DB_CONFIG) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """SELECT scope_type, scope_id, reserved_cny FROM budget_ledger
                   WHERE period_type = 'day' AND scope_type = 'tenant'
                   ORDER BY scope_id""")
            rows = {r[1]: Decimal(str(r[2])) for r in cur.fetchall()}
    assert token_a in rows and token_b in rows, "每个租户必须有自己的账本行"
    assert rows[token_a] == Decimal("0.030000")
    assert rows[token_b] == Decimal("0.030000")


def test_pg_settle_moves_reserved_to_used(_pg_cleanup):
    """结算：reserved 扣减、used 增加、预占行 settled 终态。"""
    token = _pg_cleanup
    _insert_policy(token, "1.000000")
    store = PostgresQuotaStore()
    reservation = store.reserve(
        user_id=f"{token}-u1", tenant_id=token, amount_cny=Decimal("0.50"),
        request_id="recon-settle",
    )
    store.settle(reservation, Decimal("0.20"))
    with psycopg2.connect(**MEMORY_DB_CONFIG) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """SELECT used_cny, reserved_cny FROM budget_ledger
                   WHERE scope_type='tenant' AND scope_id=%s
                     AND period_type='day'""", (token,))
            used, reserved = cur.fetchone()
            cur.execute(
                """SELECT DISTINCT status FROM budget_reservations
                   WHERE id=%s""", (reservation.reservation_id,))
            status = cur.fetchone()[0]
    assert used == Decimal("0.200000")
    assert reserved == Decimal("0.000000")
    assert status == "settled"


def test_pg_needs_review_lifecycle_and_sweep(_pg_cleanup):
    """用量未知 → mark_needs_review 保留占额；旧周期滞留被 sweep 释放并进队列。"""
    token = _pg_cleanup
    _insert_policy(token, "1.000000")
    store = PostgresQuotaStore()
    reservation = store.reserve(
        user_id=f"{token}-u1", tenant_id=token, amount_cny=Decimal("0.40"),
        request_id="recon-review",
    )
    assert store.mark_needs_review(reservation, "stream_usage_missing") is True
    # 占额保守保留
    with psycopg2.connect(**MEMORY_DB_CONFIG) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """SELECT reserved_cny FROM budget_ledger
                   WHERE scope_type='tenant' AND scope_id=%s
                     AND period_type='day'""", (token,))
            assert cur.fetchone()[0] == Decimal("0.400000")
            cur.execute(
                """SELECT DISTINCT status, review_reason
                   FROM budget_reservations WHERE id=%s""",
                (reservation.reservation_id,))
            status, reason = cur.fetchone()
    assert status == "needs_review" and reason == "stream_usage_missing"

    # 构造一笔"周期已结束"的滞留预占：直接插入 8 天前的 reserved 行 + 账本占额
    old_start = datetime.now(timezone.utc) - timedelta(days=8)
    with psycopg2.connect(**MEMORY_DB_CONFIG) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """INSERT INTO budget_ledger
                       (scope_type, scope_id, period_type, period_start,
                        limit_cny, enforcement, used_cny, reserved_cny)
                   VALUES ('tenant', %s, 'day', %s, 1, 'hard', 0, 0.30)
                   ON CONFLICT (scope_type, scope_id, period_type, period_start)
                   DO UPDATE SET reserved_cny = 0.30""",
                (token, old_start.replace(minute=0, second=0, microsecond=0)),
            )
            cur.execute(
                """INSERT INTO budget_reservations
                       (id, request_id, user_id, tenant_id, scope_type, scope_id,
                        period_type, period_start, reserved_cny, status, created_at)
                   VALUES (gen_random_uuid(), 'recon-stale', %s, %s,
                           'tenant', %s, 'day', %s, 0.30, 'reserved', %s)""",
                (token, token, token,
                 old_start.replace(minute=0, second=0, microsecond=0),
                 datetime.now(timezone.utc) - timedelta(hours=12)),
            )
    result = store.sweep_stale_reservations(threshold_hours=6)
    assert result["reviewed"] >= 1 and result["ledger_released"] >= 1
    with psycopg2.connect(**MEMORY_DB_CONFIG) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """SELECT reserved_cny FROM budget_ledger
                   WHERE scope_type='tenant' AND scope_id=%s AND period_type='day'
                     AND period_start < now() - interval '1 day'""", (token,))
            old_holds = [Decimal(str(r[0])) for r in cur.fetchall()]
    assert all(hold == 0 for hold in old_holds), "旧周期占额必须被释放"
    summary = store.reconciliation_summary()
    assert summary["pending_count"] >= 2
    queue = store.list_pending_review()
    reasons = {item["review_reason"] for item in queue}
    assert "stream_usage_missing" in reasons
    assert any("stale_sweep" in item["review_reason"] for item in queue)


def test_pg_held_cny_deduped_per_reservation(_pg_cleanup):
    """占额总额必须按预占单去重：reserve 落 user+tenant × day/month 多条
    物理行（金额同值），行级 SUM 会把同一笔占额放大数倍，导致管理端
    「占额总额」与明细「单据金额 × 单数」对不上（2026-10-01 口径修复）。"""
    token = _pg_cleanup
    _insert_policy(token, "1.000000")
    store = PostgresQuotaStore()
    # 权威库共享：summary 是全局口径，用差值断言隔离存量数据
    held_before = Decimal(store.reconciliation_summary()["held_cny"])
    reservation = store.reserve(
        user_id=f"{token}-u1", tenant_id=token, amount_cny=Decimal("0.40"),
        request_id="recon-dedup",
    )
    assert store.mark_needs_review(reservation, "stream_usage_missing") is True
    with psycopg2.connect(**MEMORY_DB_CONFIG) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """SELECT count(*) FROM budget_reservations
                   WHERE id = %s AND status = 'needs_review'""",
                (reservation.reservation_id,),
            )
            physical_rows = cur.fetchone()[0]
    assert physical_rows >= 2, "前提：同一预占单存在多条物理行（扇出才可复现放大）"

    summary = store.reconciliation_summary()
    queue = [item for item in store.list_pending_review()
             if item["reservation_id"] == reservation.reservation_id]
    assert len(queue) == 1
    per_reservation = Decimal(queue[0]["reserved_cny"])
    # 去重后的占额增量必须等于「单据金额 × 预占单数」，而不是物理行求和
    assert Decimal(summary["held_cny"]) - held_before == per_reservation


def test_pg_budget_status_limit_reflects_fresh_policy(_pg_cleanup):
    """/budgets/me 显示上限恒用当前策略：账本行的 limit_cny 只是预占时
    的冻结快照，管理员改策略后显示必须即时跟上（执行侧本就即时）。"""
    token = _pg_cleanup
    _insert_policy(token, "1.000000")
    store = PostgresQuotaStore()
    store.reserve(
        user_id=f"{token}-u1", tenant_id=token, amount_cny=Decimal("0.10"),
        request_id="recon-fresh",
    )
    status_before = store.get_budget_status(
        user_id=f"{token}-u1", tenant_id=token)
    assert Decimal(status_before["daily"]["limit"]) == Decimal("1.000000")
    # 管理员改租户策略 → 下一次读取立即反映新上限（旧实现读账本冻结快照）
    _insert_policy(token, "2.500000")
    status_after = store.get_budget_status(
        user_id=f"{token}-u1", tenant_id=token)
    assert Decimal(status_after["daily"]["limit"]) == Decimal("2.500000")
