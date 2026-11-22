"""回归：MallService sandbox 直写下单的 customer 档案幂等补建（隔离 PostgreSQL）。

此前 ``_create_order_local`` 把 auth user_id 直接当
``"order".orders.customer_id`` 插入，而 fk_orders_customer（迁移 005）要求
customer.customers(id) 存在；注册链路不建业务客户档案（auth 与 business
分库），现场注册账号（如手机号 13900000009）首单必然 500
（ForeignKeyViolation → 裸 INTERNAL_ERROR；充值能成功是因为钱包表无 FK）。

修复：下单事务内幂等补档（对齐迁移 134 的显式 id 建档先例）+ setval
前移对齐 + int4 守卫。本文件按真实迁移（003 业务表 + 005 FK）建库，覆盖：

1. 新用户首单：customer 行自动建档，订单/明细插入满足 FK；
2. 同用户二单：不重复建档；
3. 建档后按默认序列 INSERT customer 不撞主键（显式 id 高于序列时前移）；
4. 非数字 user_id：早拒为 identity_invalid 业务错误，而非裸 500。

独立于 test_mall_cancel_sandbox_pg.py 的隔离库：该套件按 003+125 建库
且直接插无档案订单，与本文件的 FK 断言互斥（一方种子/一方约束，共库
互相破坏），故使用独立环境变量与库名。只允许指向名称明确标记为
test / sandbox 的隔离数据库（同套件守卫纪律），绝不触碰共享 agent_business。
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

# 下单链路的最小迁移集：001 业务仓（本链路所需表与 005 全部前置表——
# refunds、purchase_orders、customer_behavior、ai.agent_tasks——的超集，
# 并自带 1-3 号客户/56 个商品种子，不影响 9M 段自造身份）+ 005 硬化约束
# （fk_orders_customer）。两者索引均 IF NOT EXISTS 可重放；不引入 003
# 是因其 CREATE INDEX 非幂等，且 idx_comp_price_time 与 001 重复（init_db
# 靠逐语句补齐容错，测试 fixture 不复制该逻辑）。
_MIGRATIONS = (
    "001_business_warehouse.sql",
    "005_schema_hardening.sql",
)

_REQUIRED_TABLES = (
    "customer.customers",
    '"order".orders',
    '"order".order_items',
    '"order".refunds',
    "product.products",
    "product.categories",
    "inventory.inventory",
    "inventory.purchase_orders",
    "customer.customer_behavior",
    "ai.agent_tasks",
)

_PRODUCT_PRICE = "12.50"


def _resolve_conn_kwargs() -> dict[str, Any] | None:
    """解析隔离库连接参数：DSN 优先，其次 PG* 环境变量 + 专属库名。"""
    dsn = os.getenv("MALL_ORDER_FK_PG_TEST_DSN", "").strip()
    if dsn:
        url = make_url(dsn)
        return {
            "host": url.host,
            "port": url.port,
            "dbname": url.database,
            "user": url.username,
            "password": url.password,
        }
    dbname = os.getenv("MALL_ORDER_FK_PG_DBNAME", "").strip()
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
def order_fk_admin():
    """隔离库连接 + 真实迁移建表（含 FK；表与约束已齐时跳过重放）。"""
    conn_kwargs = _resolve_conn_kwargs()
    if conn_kwargs is None:
        pytest.skip(
            "设置 MALL_ORDER_FK_PG_TEST_DSN（或 MALL_ORDER_FK_PG_DBNAME + PG* 环境）"
            "后运行隔离 PostgreSQL 验收",
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
                "下单 FK 回归只允许名称明确标记为 test 或 sandbox 的隔离数据库"
            )

        migrations_dir = Path(__file__).resolve().parents[2] / "sql" / "migrations"
        with admin.cursor() as cursor:
            cursor.execute(
                "SELECT " + ", ".join(
                    f"to_regclass('{table}') IS NOT NULL"
                    for table in _REQUIRED_TABLES
                ),
            )
            tables_ready = all(bool(v) for v in cursor.fetchone())
            cursor.execute(
                "SELECT EXISTS ("
                " SELECT 1 FROM pg_constraint WHERE conname = 'fk_orders_customer')",
            )
            fk_ready = bool(cursor.fetchone()[0])
        if not tables_ready:
            # 本 fixture 专属隔离库：表不齐（含上次迁移中断的半建状态）时
            # 整库重建，避免 003 非幂等索引被残留对象卡死。
            with admin.cursor() as cursor:
                for schema in (
                    "ai", "crawler", "customer", "finance",
                    "inventory", "order", "product",
                ):
                    cursor.execute(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE')
            with admin.cursor() as cursor:
                for name in _MIGRATIONS:
                    cursor.execute(
                        (migrations_dir / name).read_text(encoding="utf-8"),
                    )
        elif not fk_ready:
            # 表已齐（其他套件按 003 建过库）但缺 FK：只补幂等的 005。
            with admin.cursor() as cursor:
                cursor.execute(
                    (migrations_dir / _MIGRATIONS[1]).read_text(encoding="utf-8"),
                )
        yield admin
    finally:
        admin.close()


@pytest.fixture
def order_fk_pg(order_fk_admin, monkeypatch):
    """钉 sandbox 直写下单链路到隔离库，并种一个可购买商品。"""
    from backend.config import customer_service as cs_cfg
    from backend.infra import db as infra_db
    from backend.sql import executor

    # 写池（MallService→infra.db）与只读池（executor）都指向隔离库
    test_cfg = _resolve_conn_kwargs()
    monkeypatch.setattr(infra_db, "BUSINESS_DB_CONFIG", dict(test_cfg))
    import backend.config as config_pkg
    from backend.config import database as database_cfg

    for target in (config_pkg, database_cfg):
        monkeypatch.setattr(target, "BUSINESS_DB_READONLY_CONFIG", dict(test_cfg))
    # 池是模块级单例：先清掉，迫使 get_business_engine 用测试配置重建
    infra_db.reset_engines()
    executor._close_pool()

    # 钉死模式：sandbox 直写（非 http 网关）+ 非 demo（身份原样透传）
    monkeypatch.setattr(cs_cfg, "CS_BUSINESS_GATEWAY_MODE", "sandbox")
    monkeypatch.setattr(cs_cfg, "CS_DEMO_MODE", False)

    # 每次运行独立的商品/用户号：重跑安全，断言不被历史数据污染
    suffix = uuid.uuid4().hex[:8]
    product_id = 77_000 + (int(suffix, 16) % 2_000)
    with order_fk_admin.cursor() as cursor:
        cursor.execute(
            "INSERT INTO product.products (id, sku, product_name, sale_price)"
            " VALUES (%s, %s, %s, %s)"
            " ON CONFLICT (id) DO UPDATE SET sku = EXCLUDED.sku,"
            " product_name = EXCLUDED.product_name, sale_price = EXCLUDED.sale_price",
            (product_id, f"FKT-{suffix}", "下单回归测试商品", _PRODUCT_PRICE),
        )

    yield SimpleNamespace(
        admin=order_fk_admin,
        product_id=str(product_id),
        tenant_id="order-fk-regression",
        suffix=suffix,
    )

    executor._close_pool()
    infra_db.reset_engines()


def _customer_count(admin: psycopg2.connection, customer_id: int) -> int:
    with admin.cursor() as cursor:
        cursor.execute(
            "SELECT COUNT(*) FROM customer.customers WHERE id = %s",
            (customer_id,),
        )
        return int(cursor.fetchone()[0])


def _create_order(env: SimpleNamespace, user_id: str) -> dict[str, Any]:
    from backend.customer_service.service.mall_service import MallService

    return MallService().create_order(
        user_id, env.tenant_id,
        [{"product_id": env.product_id, "quantity": 2}],
        operation_key=f"opk-{env.suffix}-{user_id}",
        customer_name=f"回归用户{user_id}",
    )


def test_first_order_creates_customer_profile(order_fk_pg: SimpleNamespace):
    """新用户首单：customer 行自动建档，订单/明细满足 FK，不再 500。"""
    env = order_fk_pg
    suffix = uuid.uuid4().hex[:8]
    customer_id = 9_000_000 + (int(suffix, 16) % 999_999)
    user_id = str(customer_id)

    order = _create_order(env, user_id)

    assert order["order_no"].startswith("WEB-")
    assert order["status"] == "pending"
    assert float(order["total_amount"]) == 25.00  # 12.50 × 2，服务端计价
    with env.admin.cursor() as cursor:
        cursor.execute(
            "SELECT name, level FROM customer.customers WHERE id = %s",
            (customer_id,),
        )
        row = cursor.fetchone()
        assert row is not None, "首单未自动建档 customer.customers"
        assert row[0] == f"回归用户{user_id}"
        assert row[1] == "普通"
        cursor.execute(
            'SELECT customer_id, status, payment_status FROM "order".orders'
            " WHERE order_no = %s",
            (order["order_no"],),
        )
        order_row = cursor.fetchone()
        assert order_row is not None
        assert order_row[0] == customer_id
        assert (order_row[1], order_row[2]) == ("pending", "unpaid")
        cursor.execute(
            'SELECT COUNT(*) FROM "order".order_items oi'
            ' JOIN "order".orders o ON o.id = oi.order_id'
            " WHERE o.order_no = %s AND oi.product_id = %s AND oi.quantity = 2",
            (order["order_no"], int(env.product_id)),
        )
        assert cursor.fetchone()[0] == 1


def test_second_order_does_not_duplicate_profile(order_fk_pg: SimpleNamespace):
    """同用户二单：customer 行不重复（ON CONFLICT DO NOTHING）。"""
    env = order_fk_pg
    suffix = uuid.uuid4().hex[:8]
    customer_id = 9_000_000 + (int(suffix, 16) % 999_999)
    user_id = str(customer_id)

    first = _create_order(env, user_id)
    second = _create_order(env, user_id)

    assert first["order_no"] != second["order_no"]
    assert _customer_count(env.admin, customer_id) == 1


def test_sequence_advances_after_explicit_id(order_fk_pg: SimpleNamespace):
    """显式 id 建档后，默认序列 INSERT 不撞主键且取号高于显式 id。"""
    env = order_fk_pg
    suffix = uuid.uuid4().hex[:8]
    customer_id = 9_000_000 + (int(suffix, 16) % 999_999)
    _create_order(env, str(customer_id))

    with env.admin.cursor() as cursor:
        cursor.execute(
            "INSERT INTO customer.customers (name) VALUES ('序列取号校验')"
            " RETURNING id",
        )
        auto_id = int(cursor.fetchone()[0])
    assert auto_id > customer_id, (
        "建档后 setval 未前移序列，后续默认 INSERT 会撞显式 id 主键"
    )


def test_non_numeric_user_id_rejected_cleanly(order_fk_pg: SimpleNamespace):
    """非数字 user_id：早拒为 identity_invalid，而不是 int() 裸 500。"""
    import pytest as _pytest

    from backend.customer_service.service.mall_service import MallError

    env = order_fk_pg
    with _pytest.raises(MallError) as excinfo:
        _create_order(env, "not-a-number")
    assert excinfo.value.code == "identity_invalid"

    with _pytest.raises(MallError) as excinfo:
        _create_order(env, str(2_147_483_648))  # 超出 PG integer 上限
    assert excinfo.value.code == "identity_invalid"
