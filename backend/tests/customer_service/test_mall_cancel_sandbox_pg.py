"""B0 回归：MallService sandbox 直连库取消订单全链路（隔离 PostgreSQL）。

此前 ``_cancel_order_local`` 签名未接收 ``expected_status``（调用方已传参、
函数体内也引用了它），sandbox 模式取消订单直接抛 TypeError/NameError——
既有测试全走 http_wire（http 网关 + 业务 Mock），未覆盖 sandbox 直查路径。

本文件用隔离 PG 按真实迁移（003 业务表 + 125 钱包）建表，钉 sandbox 网关
模式，覆盖三条链路：

1. 直取消通道：pending 未付款单直接取消，钱包不动；
2. 确认执行通道（全链路）：CancelOrderService 提案（OrderService 只读池
   重查事实）→ execute_confirmed_cancel（MallService 写池取消 + 全额
   退款回钱包 + cancel_refund 流水）；
3. 越权防护：直取消通道对已支付订单 expected_status 冲突拒绝。

只允许指向名称明确标记为 test / sandbox 的隔离数据库（同
test_refund_request_real_pg.py 的守卫纪律），绝不触碰共享 agent_business。
"""
from __future__ import annotations

import os
import uuid
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import psycopg2
import pytest
from sqlalchemy.engine import make_url

pytestmark = pytest.mark.pg

# 与 sandbox 链路相关的最小迁移集：业务表（orders）+ 钱包余额/流水
_MIGRATIONS = ("003_agent_business_schema.sql", "125_mall_wallet.sql")

_WALLET_SEED_BALANCE = "100.00"
_PAID_AMOUNT = "157.00"
_PENDING_AMOUNT = "88.00"


def _resolve_conn_kwargs() -> dict[str, Any] | None:
    """解析隔离库连接参数：DSN 优先，其次 PG* 环境变量 + 专属库名。"""
    dsn = os.getenv("MALL_SANDBOX_PG_TEST_DSN", "").strip()
    if dsn:
        url = make_url(dsn)
        return {
            "host": url.host,
            "port": url.port,
            "dbname": url.database,
            "user": url.username,
            "password": url.password,
        }
    dbname = os.getenv("MALL_SANDBOX_PG_DBNAME", "").strip()
    if dbname:
        return {
            "host": os.getenv("PGHOST", "localhost"),
            "port": int(os.getenv("PGPORT", "5432")),
            "dbname": dbname,
            "user": os.getenv("PGUSER", "postgres"),
            "password": os.getenv("PGPASSWORD", ""),
        }
    return None


@pytest.fixture(scope="module")
def sandbox_admin():
    """隔离库连接 + 真实迁移建表（全部 IF NOT EXISTS，可重复执行）。

    连接来源二选一（CI 用后者，避免 workflow 内嵌带凭据的 URL）：
    - MALL_SANDBOX_PG_TEST_DSN：完整连接串（本地隔离容器）；
    - MALL_SANDBOX_PG_DBNAME + 标准 PGHOST/PGPORT/PGUSER/PGPASSWORD。
    两者都未设置时跳过；库名守卫确保只碰 test/sandbox 标记库。
    """
    conn_kwargs = _resolve_conn_kwargs()
    if conn_kwargs is None:
        pytest.skip(
            "设置 MALL_SANDBOX_PG_TEST_DSN（或 MALL_SANDBOX_PG_DBNAME + PG* 环境）"
            "后运行隔离 PostgreSQL sandbox 验收",
        )

    admin = psycopg2.connect(**conn_kwargs, connect_timeout=10)
    admin.autocommit = True
    try:
        with admin.cursor() as cursor:
            cursor.execute("SELECT current_database()")
            database_name = str(cursor.fetchone()[0]).lower()
        if not (
            database_name.endswith("_test")
            or database_name.startswith("test_")
            or "sandbox" in database_name
        ):
            pytest.fail(
                "取消订单 sandbox 验收只允许名称明确标记为 test 或 sandbox 的隔离数据库"
            )

        migrations_dir = Path(__file__).resolve().parents[2] / "sql" / "migrations"
        with admin.cursor() as cursor:
            cursor.execute(
                "SELECT to_regclass('\"order\".orders') IS NOT NULL"
                " AND to_regclass('finance.wallet_balances') IS NOT NULL",
            )
            schema_ready = bool(cursor.fetchone()[0])
        # 迁移 003 含非幂等 CREATE INDEX，只在目标表缺失时重放（重跑安全）
        if not schema_ready:
            with admin.cursor() as cursor:
                for name in _MIGRATIONS:
                    cursor.execute(
                        (migrations_dir / name).read_text(encoding="utf-8"),
                    )
        yield admin
    finally:
        admin.close()


@pytest.fixture
def sandbox_pg(sandbox_admin, monkeypatch):
    """钉 sandbox 直查链路到隔离库，并种一客（订单 ×2 + 钱包）独立数据。"""
    from backend.config import customer_service as cs_cfg
    from backend.infra import db as infra_db
    from backend.sql import executor

    # 写池（MallService→infra.db）与只读池（OrderService→executor）都指向隔离库
    # （sandbox_admin 已解析过连接参数，此处必非 None）
    test_cfg = _resolve_conn_kwargs()
    # 写池（MallService→infra.db）与只读池（OrderService→executor）都指向隔离库
    monkeypatch.setattr(infra_db, "BUSINESS_DB_CONFIG", dict(test_cfg))
    import backend.config as config_pkg
    from backend.config import database as database_cfg

    for target in (config_pkg, database_cfg):
        monkeypatch.setattr(target, "BUSINESS_DB_READONLY_CONFIG", dict(test_cfg))
    # 只读池是模块级单例：先关掉，迫使 _get_pool() 用测试配置重建
    executor._close_pool()

    # 钉死模式：sandbox 直查（非 http 网关）+ 非 demo（身份原样透传）
    monkeypatch.setattr(cs_cfg, "CS_BUSINESS_GATEWAY_MODE", "sandbox")
    monkeypatch.setattr(cs_cfg, "CS_DEMO_MODE", False)
    # 取消闸放行（dev .env 可能置 disabled，与本文件无关）
    monkeypatch.setattr(cs_cfg, "CS_ORDER_CANCEL_MODE", "http")

    # 每次运行独立的客户/订单号：重跑安全，断言不被历史数据污染
    suffix = uuid.uuid4().hex[:8]
    customer_id = 9_000_000 + (int(suffix, 16) % 999_999)
    user_id = str(customer_id)
    pending_no = f"SBOX-P-{suffix}"
    paid_no = f"SBOX-D-{suffix}"
    with sandbox_admin.cursor() as cursor:
        cursor.execute(
            'INSERT INTO "order".orders'
            " (order_no, customer_id, total_amount, status, payment_status)"
            " VALUES (%s, %s, %s, 'pending', 'unpaid')",
            (pending_no, customer_id, _PENDING_AMOUNT),
        )
        cursor.execute(
            'INSERT INTO "order".orders'
            " (order_no, customer_id, total_amount, status, payment_status)"
            " VALUES (%s, %s, %s, 'paid', 'paid')",
            (paid_no, customer_id, _PAID_AMOUNT),
        )
        cursor.execute(
            "INSERT INTO finance.wallet_balances (customer_id, balance)"
            " VALUES (%s, %s) ON CONFLICT (customer_id) DO NOTHING",
            (customer_id, _WALLET_SEED_BALANCE),
        )

    yield SimpleNamespace(
        user_id=user_id,
        tenant_id="sandbox-b0",
        pending_no=pending_no,
        paid_no=paid_no,
        admin=sandbox_admin,
    )

    executor._close_pool()
    infra_db.reset_engines()


def _wallet_balance(admin, customer_id: int) -> float:
    with admin.cursor() as cursor:
        cursor.execute(
            "SELECT balance FROM finance.wallet_balances WHERE customer_id = %s",
            (customer_id,),
        )
        row = cursor.fetchone()
    return float(row[0]) if row else 0.0


def test_sandbox_direct_cancel_pending_order_keeps_wallet(sandbox_pg):
    """直取消通道：pending 单直接取消，无资金变动，钱包余额不动。"""
    from backend.customer_service.service.mall_service import MallService

    result = MallService().cancel_order(
        sandbox_pg.user_id, sandbox_pg.tenant_id, sandbox_pg.pending_no,
        reason="不想要了", operation_key=str(uuid.uuid4()),
    )
    assert result["order"]["status"] == "cancelled"
    assert result["order"]["payment_status"] == "unpaid"
    assert result["wallet"]["balance"] == float(_WALLET_SEED_BALANCE)

    customer_id = int(sandbox_pg.user_id)
    assert _wallet_balance(sandbox_pg.admin, customer_id) == float(
        _WALLET_SEED_BALANCE,
    )
    with sandbox_pg.admin.cursor() as cursor:
        cursor.execute(
            "SELECT count(*) FROM finance.wallet_ledger"
            " WHERE customer_id = %s AND ref_no = %s AND kind = 'cancel_refund'",
            (customer_id, sandbox_pg.pending_no),
        )
        assert cursor.fetchone()[0] == 0


def test_sandbox_confirmed_cancel_paid_refunds_wallet(sandbox_pg):
    """确认执行通道全链路：提案（只读池）→ 执行（写池）退款回钱包+流水。"""
    from backend.customer_service.service.cancel_order_service import (
        CancelOrderService,
    )

    service = CancelOrderService()
    proposal = service.build_cancel_order_proposal(
        sandbox_pg.user_id, sandbox_pg.paid_no, "不想要了",
    )
    assert proposal.before_state["status"] == "paid"

    record = service.execute_confirmed_cancel(
        {
            "action_type": proposal.action_type,
            "target_id": proposal.target_id,
            "before_state": proposal.before_state,
            "after_state": proposal.after_state,
        },
        confirmation_id=str(uuid.uuid4()),
        user_id=sandbox_pg.user_id,
        tenant_id=sandbox_pg.tenant_id,
        conversation_id=str(uuid.uuid4()),
    )
    assert record.status == "executed"

    expected_after = float(_WALLET_SEED_BALANCE) + float(_PAID_AMOUNT)
    assert record.data["wallet"]["balance"] == expected_after

    customer_id = int(sandbox_pg.user_id)
    assert _wallet_balance(sandbox_pg.admin, customer_id) == expected_after
    with sandbox_pg.admin.cursor() as cursor:
        cursor.execute(
            "SELECT direction, amount, balance_after FROM finance.wallet_ledger"
            " WHERE customer_id = %s AND ref_no = %s AND kind = 'cancel_refund'",
            (customer_id, sandbox_pg.paid_no),
        )
        ledger = cursor.fetchone()
    assert ledger is not None
    assert ledger[0] == "in"
    assert float(ledger[1]) == float(_PAID_AMOUNT)
    assert float(ledger[2]) == expected_after


def test_sandbox_direct_cancel_paid_order_rejected(sandbox_pg):
    """越权防护：直取消通道只接受 pending，paid 单 expected_status 冲突拒绝。"""
    from backend.customer_service.service.mall_service import MallError, MallService

    with pytest.raises(MallError) as exc_info:
        MallService().cancel_order(
            sandbox_pg.user_id, sandbox_pg.tenant_id, sandbox_pg.paid_no,
            reason="不想要了", operation_key=str(uuid.uuid4()),
        )
    assert exc_info.value.code == "conflict"

    # 订单与钱包均未被改动
    with sandbox_pg.admin.cursor() as cursor:
        cursor.execute(
            'SELECT status, payment_status FROM "order".orders'
            " WHERE order_no = %s",
            (sandbox_pg.paid_no,),
        )
        status, payment_status = cursor.fetchone()
    assert (status, payment_status) == ("paid", "paid")
    assert _wallet_balance(sandbox_pg.admin, int(sandbox_pg.user_id)) == float(
        _WALLET_SEED_BALANCE,
    )
