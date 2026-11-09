"""summary() 未结算预留口径（2026-11-10 待对账治理 1.4）：按预占单去重。

同一笔预占在 user/tenant × day/month 的 budget_ledger 上是 4 行同值，
旧实现行级 SUM 把占额放大数倍（实测 ¥78.60 显示成 ¥228）。修复后
`unsettled_reserved` = 在途预占（reserved）+ 待对账占额（needs_review），
逐单只算一次，与 reconciliation_summary 口径一致。

断言一律用前后增量：隔离测试库可能有历史会话留下的占额残留，
绝对断言会被无关数据污染。
"""
from contextlib import contextmanager
from decimal import Decimal
from uuid import uuid4

import psycopg2
import pytest

from backend.infra.llm.quota import (
    BudgetPolicy,
    PostgresQuotaStore,
    ResolvedBudgetPolicies,
)


@pytest.fixture
def store(monkeypatch):
    from backend.config.database import MEMORY_DB_CONFIG

    parameters = dict(MEMORY_DB_CONFIG)
    database = parameters.get('dbname', '')
    assert database == 'cs_q3' or database.endswith('_test'), '必须使用隔离测试库'

    @contextmanager
    def connect():
        connection = psycopg2.connect(**parameters)
        try:
            with connection:
                yield connection
        finally:
            connection.close()

    instance = PostgresQuotaStore(connection_factory=connect)
    policy = BudgetPolicy('platform', 'default', Decimal('100'), Decimal('1000'))
    monkeypatch.setattr(
        instance, 'resolve', lambda *args: ResolvedBudgetPolicies(policy, policy),
    )
    return instance


def test_summary_counts_reservation_once_across_ledger_rows(store):
    """T9：一笔预占写 4 行账本（user/tenant × day/month），summary 只算 ¥1。"""
    run = f'sum-dedup-{uuid4().hex}'

    def unsettled() -> Decimal:
        return Decimal(store.summary()['unsettled_reserved'])

    before = unsettled()
    reservation = store.reserve(
        user_id=run, tenant_id=run, amount_cny=Decimal('1.00'),
    )
    in_flight = unsettled() - before
    assert in_flight == Decimal('1.00')

    store.settle(reservation, Decimal('0'))
    assert unsettled() - before == Decimal('0')


def test_summary_includes_needs_review_held_amount(store):
    """T9 补充：待对账（needs_review）占额同样逐单只算一次。"""
    run = f'sum-review-{uuid4().hex}'

    def unsettled() -> Decimal:
        return Decimal(store.summary()['unsettled_reserved'])

    before = unsettled()
    reservation = store.reserve(
        user_id=run, tenant_id=run, amount_cny=Decimal('2.00'),
    )
    store.mark_needs_review(reservation, 'unknown_provider_cost')
    assert unsettled() - before == Decimal('2.00')


def test_summary_matches_reconciliation_held_amount(store):
    """T10：summary 未结算预留增量 == reconciliation 待对账占额增量。

    静息增量下两者应严格相等（同一去重口径的两个出口）。
    """
    run = f'sum-recon-{uuid4().hex}'

    def unsettled() -> Decimal:
        return Decimal(store.summary()['unsettled_reserved'])

    def recon_held() -> Decimal:
        return Decimal(store.reconciliation_summary()['held_cny'])

    summary_before, recon_before = unsettled(), recon_held()
    reservation = store.reserve(
        user_id=run, tenant_id=run, amount_cny=Decimal('3.50'),
    )
    store.mark_needs_review(reservation, 'unknown_provider_cost')
    assert unsettled() - summary_before == Decimal('3.50')
    assert recon_held() - recon_before == Decimal('3.50')


def _open_reservation(store: PostgresQuotaStore, run: str, amount: str):
    reservation = store.reserve(
        user_id=run, tenant_id=run, amount_cny=Decimal(amount),
    )
    assert store.mark_needs_review(reservation, 'unknown_provider_cost') is True
    return reservation


def test_resolve_released_releases_ledger_and_audits(store):
    """B2：released 核销释放账本占额、写审计、队列与汇总同步闭合。"""
    run = f'resol-rel-{uuid4().hex}'

    def unsettled() -> Decimal:
        return Decimal(store.summary()['unsettled_reserved'])

    before = unsettled()
    reservation = _open_reservation(store, run, '2.00')
    assert unsettled() - before == Decimal('2.00')

    result = store.resolve_needs_review(
        reservation.reservation_id, action='released',
        reason='确认供应商未计费', resolved_by='user:test-admin',
    )
    assert result['already_resolved'] is False
    assert Decimal(result['released_cny']) == Decimal('2.00')
    assert unsettled() - before == Decimal('0')

    with store._connection_factory() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """SELECT DISTINCT resolution, resolved_by FROM budget_reservations
                   WHERE id = %s""", (reservation.reservation_id,))
            resolution, by = cur.fetchone()
            cur.execute(
                """SELECT action, reason FROM budget_reservation_resolutions
                   WHERE reservation_id = %s""", (reservation.reservation_id,))
            audit = cur.fetchall()
    assert (resolution, by) == ('released', 'user:test-admin')
    assert audit and audit[0][0] == 'released'

    items = store.list_pending_review(100)
    assert all(i['reservation_id'] != reservation.reservation_id for i in items)
    closed = store.list_pending_review(100, include_closed=True)
    matched = [i for i in closed if i['reservation_id'] == reservation.reservation_id]
    assert matched and matched[0]['resolution'] == 'released'


def test_resolve_settled_actual_books_real_cost(store):
    """B2：settled_actual 按实际账单入账（used 增加），占额释放。"""
    run = f'resol-act-{uuid4().hex}'
    reservation = _open_reservation(store, run, '2.00')
    result = store.resolve_needs_review(
        reservation.reservation_id, action='settled_actual',
        reason='供应商账单确认', resolved_by='user:test-admin',
        actual_cny=Decimal('1.50'),
    )
    assert result['booked_cny'] == '1.500000'
    with store._connection_factory() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """SELECT used_cny, reserved_cny FROM budget_ledger
                   WHERE scope_type='tenant' AND scope_id=%s AND period_type='day'""",
                (run,))
            used, held = cur.fetchone()
    assert used == Decimal('1.500000')
    assert held == Decimal('0')


def test_resolve_written_off_never_books_cost(store):
    """B2：written_off 仅释放占额——无法确认的成本不得入账（不伪造费用）。"""
    run = f'resol-wo-{uuid4().hex}'
    reservation = _open_reservation(store, run, '1.00')
    store.resolve_needs_review(
        reservation.reservation_id, action='written_off',
        reason='小额无法确认', resolved_by='user:test-admin',
    )
    with store._connection_factory() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """SELECT used_cny, reserved_cny FROM budget_ledger
                   WHERE scope_type='tenant' AND scope_id=%s AND period_type='day'""",
                (run,))
            used, held = cur.fetchone()
    assert used == Decimal('0')
    assert held == Decimal('0')


def test_resolve_is_idempotent_never_double_releases(store):
    """B2：重复核销幂等返回 already_resolved，账本不二次释放。"""
    run = f'resol-idem-{uuid4().hex}'
    reservation = _open_reservation(store, run, '3.00')
    first = store.resolve_needs_review(
        reservation.reservation_id, action='released',
        reason='第一次核销', resolved_by='user:test-admin',
    )
    second = store.resolve_needs_review(
        reservation.reservation_id, action='released',
        reason='重复调用', resolved_by='user:test-admin',
    )
    assert first['already_resolved'] is False
    assert second['already_resolved'] is True
    assert Decimal(second['released_cny']) == Decimal('0')
    with store._connection_factory() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """SELECT COUNT(*) FROM budget_reservation_resolutions
                   WHERE reservation_id = %s""", (reservation.reservation_id,))
            assert cur.fetchone()[0] == 1


def test_dry_run_classifies_backlog_with_suggested_actions(store):
    """B2：dry-run 分类（test_eval 优先）+ 建议动作；只读不改状态。"""
    run = f'dry-{uuid4().hex}'
    test_run = f'test-dry-{uuid4().hex}'
    _open_reservation(store, run, '0.50')          # small_unknown
    _open_reservation(store, test_run, '1.20')     # test_eval（test 前缀租户）

    report = store.dry_run_backlog_migration()
    classes = report['classes']
    assert classes.get('small_unknown', {}).get('suggested_action') == 'written_off'
    assert classes.get('test_eval', {}).get('suggested_action') == 'archived'
    small_ids = {s['reservation_id'] for s in classes.get('small_unknown', {}).get('samples', [])}
    assert small_ids, 'small_unknown 应至少包含本用例的小额单据'
    test_ids = {s['reservation_id'] for s in classes.get('test_eval', {}).get('samples', [])}
    assert test_ids, 'test_eval 应包含 test 前缀单据'

    # 只读：dry-run 后队列不变
    items = store.list_pending_review(100)
    assert any(i['reservation_id'] in small_ids for i in items)


def test_queue_filters_test_data_by_default(store):
    """B2：测试/评测数据默认不进队列，include_test 打开可见。"""
    run = f'vis-{uuid4().hex}'
    test_run = f'test-vis-{uuid4().hex}'
    _open_reservation(store, run, '0.40')
    _open_reservation(store, test_run, '0.60')

    visible = {i['tenant_id'] for i in store.list_pending_review(200)}
    assert run in visible
    assert test_run not in visible
    with_test = {i['tenant_id'] for i in store.list_pending_review(200, include_test=True)}
    assert test_run in with_test


def test_billing_mismatch_excludes_estimated_settlement(store):
    """迁移 145：估算结算（settle_kind='estimated'）不计入 billing_mismatch——
    按预占上限入账而 llm_usage 无同额成本行属设计内保守口径，不是结算分叉；
    精确结算（settle_kind=''）与 usage 分叉仍触发，守卫不被稀释。"""
    estimated_run = f'mism-est-{uuid4().hex}'
    exact_run = f'mism-exact-{uuid4().hex}'

    def mismatch_count() -> int:
        return int(store.reconciliation_summary()['billing_mismatch_count'])

    before = mismatch_count()
    # ① 估算结算：settle 带 estimated 标记 → 不进 mismatch 计数。
    estimated = store.reserve(
        user_id=estimated_run, tenant_id=estimated_run,
        amount_cny=Decimal('0.50'), request_id=estimated_run,
    )
    store.settle(estimated, Decimal('0.50'), settle_kind='estimated')
    assert mismatch_count() - before == 0, (
        '估算结算（无 usage 成本行）不应触发 billing_mismatch'
    )
    # ② 精确结算：无 usage 成本行 → 真实分叉，计数 +1。
    exact = store.reserve(
        user_id=exact_run, tenant_id=exact_run,
        amount_cny=Decimal('0.30'), request_id=exact_run,
    )
    store.settle(exact, Decimal('0.30'))
    assert mismatch_count() - before == 1, (
        '精确结算与 usage 分叉仍必须触发 billing_mismatch'
    )
    with store._connection_factory() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """SELECT DISTINCT settle_kind FROM budget_reservations
                   WHERE id = %s""", (estimated.reservation_id,))
            assert cur.fetchone()[0] == 'estimated'


def test_resolution_audit_table_is_append_only(store):
    """迁移 146:审计行 UPDATE/DELETE 被 DB 触发器拒绝——append-only
    从应用层纪律升级为物理约束(仿 model_price 053 先例)。"""
    run = f'audit-apx-{uuid4().hex}'
    reservation = _open_reservation(store, run, '0.20')
    store.resolve_needs_review(
        reservation.reservation_id, action='released',
        reason='确认供应商未计费', resolved_by='user:test-admin',
    )
    with store._connection_factory() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """SELECT id FROM budget_reservation_resolutions
                   WHERE reservation_id = %s""", (reservation.reservation_id,))
            audit_id = cur.fetchone()[0]
            with pytest.raises(psycopg2.Error):
                cur.execute(
                    """UPDATE budget_reservation_resolutions
                       SET reason = 'attempted-tamper' WHERE id = %s""",
                    (audit_id,))
            conn.rollback()
