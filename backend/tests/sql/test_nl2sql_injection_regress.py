# -*- coding: utf-8 -*-
"""NL2SQL 注入渗透回归（G-04 修复锁定，2026-10-06）。

来源：2026-10-03 只读渗透复验（材料#14，脚本存档 D:/tmp/nl2sql_pentest_readonly.py）
——38 类 payload 拦截 37，7 个非查询系统函数穿透第④层函数黑名单（P1）：
set_config 在行级隔离开启后可篡改会话级安全上下文，构成真实提权路径。

本文件把渗透 payload 固化为永久回归：
- 7 个新封禁函数：必须 ValidationError layer=4 / dangerous_function（修复本体）
- 存量 payload：必须维持被拦；层位取当前实现实测值（探针取证于 2026-10-06），
  其中 B05/E01 等未限定表名形态在渗透当期放行、现行列字典校验更严后改为拒绝
  ——安全口径只紧不松，若未来放开表名解析需同步复核这些断言。
- 放行对照组：shared 域合法查询必须通过 guard——防「全拦」式假阳。

纯函数直调 validator/guard，零 DB 副作用（conftest 已断审计库）。
"""
import pytest

from backend.sql.policy import SQLPolicyError
from backend.sql.schema_loader import schema_loader
from backend.sql.sql_validator import ValidationError
from tests.sql.conftest import build_ctx

# 渗透当期确认穿透、本次修复封禁的 7 个系统函数
G04_NEW_BANNED = (
    "setval", "nextval", "set_config", "current_setting",
    "pg_terminate_backend", "pg_cancel_backend", "lo_get",
)

EDITOR_CTX = dict(
    user_id="pentest_probe", department="general",
    tenant_id="default", roles=("editor",),
)


class TestG04SevenBannedSystemFunctions:
    """修复本体：7 个系统函数任何出现形态都必须在第④层被拦。"""

    def test_config_contains_all_seven(self):
        """黑名单单一源必须含全部 7 函数——防配置被无声回退。"""
        assert set(f.upper() for f in G04_NEW_BANNED) <= schema_loader.banned_functions

    @pytest.mark.parametrize("sql", [
        "SELECT setval('order_orders_id_seq', 99999)",
        "SELECT nextval('order_orders_id_seq')",
        "SELECT set_config('app.user_id', '1', false)",
        "SELECT current_setting('app.user_id')",
        # WHERE 谓词形态（渗透 G01 原型：行级 scope 绕过探测）
        "SELECT id FROM order.orders "
        "WHERE customer_id <> current_setting('app.user_id') LIMIT 1",
        "SELECT pg_terminate_backend(12345)",
        "SELECT pg_cancel_backend(12345)",
        "SELECT lo_get(12345)",
    ])
    def test_blocked_at_layer4(self, guard, sql):
        ctx = build_ctx(**EDITOR_CTX)
        with pytest.raises(ValidationError) as ei:
            guard.validate_and_rewrite(sql, ctx)
        assert ei.value.layer == 4
        assert ei.value.reason == "dangerous_function"

    def test_set_config_inside_cte_blocked(self, guard):
        """CTE 内藏 set_config：CTE 别名先被第②层表白名单拦下（实测 layer=2）。
        安全语义=任一层拦下即拦截成立；层位锁定当前实现，若 L2 放开
        CTE 别名解析，此用例必须转为 layer=4 断言而不是删除。"""
        ctx = build_ctx(**EDITOR_CTX)
        with pytest.raises(ValidationError) as ei:
            guard.validate_and_rewrite(
                "WITH s AS (SELECT set_config('app.user_id','1',false)) "
                "SELECT * FROM s LIMIT 1", ctx)
        assert ei.value.layer == 2
        assert ei.value.reason == "table_forbidden"


class TestLegacyPentestPosture:
    """存量渗透 payload 必须维持被拦（层位=2026-10-06 探针实测）。"""

    @pytest.mark.parametrize("sql,layer,reason", [
        # ── 第①层：仅 SELECT ──
        ("UPDATE customer SET name='x' WHERE id=1", 1, "non_select"),
        ("DELETE FROM orders WHERE id=1", 1, "non_select"),
        ("INSERT INTO orders(id) VALUES (999)", 1, "non_select"),
        ("DROP TABLE orders", 1, "non_select"),
        ("TRUNCATE TABLE orders", 1, "non_select"),
        ("ALTER TABLE orders ADD COLUMN hack text", 1, "non_select"),
        ("SELECT id FROM orders LIMIT 1; DROP TABLE orders", 1, "multi_statement"),
        ("WITH del AS (DELETE FROM orders RETURNING *) SELECT * FROM del",
         1, "write_in_subquery"),
        # UNION 裸 SELECT 分支按非 SELECT 语句拒绝
        ("SELECT id FROM orders UNION ALL SELECT 1 LIMIT 1", 1, "non_select"),
        # ── 第②层：表白名单 ──
        ("SELECT * FROM information_schema.tables LIMIT 1", 2, "table_forbidden"),
        ("SELECT * FROM pg_catalog.pg_tables LIMIT 1", 2, "table_forbidden"),
        ("SELECT usename, passwd FROM pg_user LIMIT 1", 2, "table_forbidden"),
        ("SELECT * FROM finance.internal_ledger LIMIT 1", 2, "table_forbidden"),
        # 未限定表名 customer/orders 不在登记表 → 第②层拒
        ("SELECT password FROM customer LIMIT 1", 2, "table_forbidden"),
        ("SELECT * FROM customer LIMIT 1", 2, "table_forbidden"),
        ("SELECT phone FROM customer LIMIT 1", 2, "table_forbidden"),
        # 函数表 pg_ls_dir() 被按表名解析 → 第②层拒
        ("SELECT * FROM pg_ls_dir()", 2, "table_forbidden"),
        ("SELECT * FROM dblink('host=x dbname=y','SELECT 1')", 2, "table_forbidden"),
        # 子查询逃逸读系统表
        ("SELECT (SELECT count(*) FROM pg_user) FROM orders LIMIT 1",
         2, "table_forbidden"),
        # ── 第③层：列字典（未限定表名当前不可解析 → 拒绝）──
        ("SELECT id FROM orders LIMIT 1", 3, "column_undefined"),
        ("SELECT id FROM orders", 3, "column_undefined"),
        ("SELECT id FROM orders LIMIT 999999", 3, "column_undefined"),
        ("SELECT id FROM orders /*x*/ WHERE 1=1 LIMIT 1", 3, "column_undefined"),
        ("SELECT id FROM orders LIMIT 1 -- DROP TABLE orders", 3, "column_undefined"),
        ("SELECT id::text FROM orders LIMIT 1", 3, "column_undefined"),
        # ── 第④层：函数黑名单 ──
        ("SELECT pg_sleep(10)", 4, "dangerous_function"),
        ("SELECT pg_read_file('/etc/passwd')", 4, "dangerous_function"),
        ("SELECT lo_import('/etc/passwd')", 4, "dangerous_function"),
    ])
    def test_blocked(self, guard, sql, layer, reason):
        ctx = build_ctx(**EDITOR_CTX)
        with pytest.raises(ValidationError) as ei:
            guard.validate_and_rewrite(sql, ctx)
        assert ei.value.layer == layer
        assert ei.value.reason == reason

    @pytest.mark.parametrize("sql", [
        # validator 全通过（chr/除零无害），guard 表域策略拒绝未登记表名
        "SELECT chr(65) FROM orders LIMIT 1",
        "SELECT 1/0 FROM orders LIMIT 1",
    ])
    def test_blocked_by_guard_table_domain(self, guard, sql):
        ctx = build_ctx(**EDITOR_CTX)
        with pytest.raises(SQLPolicyError) as ei:
            guard.validate_and_rewrite(sql, ctx)
        assert ei.value.code == "SQL_TABLE_NOT_ALLOWED"


class TestLayer5LimitEnforcement:
    """L5 用可放行的 shared 表直测 validator（order.* 会在 guard 表域先被拒）。"""

    def test_missing_limit_auto_added(self):
        safe_sql, _tables, _stmt = _validate("SELECT product_name FROM product.products")
        assert safe_sql.upper().endswith("LIMIT 100")

    def test_limit_over_max_rejected(self):
        with pytest.raises(ValidationError) as ei:
            _validate("SELECT product_name FROM product.products LIMIT 999999")
        assert ei.value.layer == 5
        assert ei.value.reason == "limit_exceeded"

    def test_limit_all_force_rewritten(self):
        """LIMIT ALL / 非数字字面量不允许绕过 max_limit（安全修复锁定）。"""
        safe_sql, _t, _s = _validate(
            "SELECT product_name FROM product.products LIMIT ALL")
        assert "LIMIT 100" in safe_sql.upper()


class TestGuardControlPath:
    """放行对照：合法 shared 域查询必须通过——防「全拦」式假阳。"""

    def test_shared_domain_query_passes(self, guard):
        ctx = build_ctx(**EDITOR_CTX)
        guarded = guard.validate_and_rewrite(
            "SELECT product_name FROM product.products LIMIT 1", ctx)
        assert guarded.referenced_tables == ("product.products",)
        assert "product_name" in guarded.executable_sql


class TestG17SystemFunctionFailClosed:
    """G-17：pg_* 系统函数族 fail-closed——黑名单外默认拒绝，防新型系统函数。"""

    def test_allowed_functions_default_empty(self):
        """白名单默认空 = 未批准即拒绝；放行必须显式配置。"""
        assert schema_loader.allowed_functions == set()

    def test_pg_prefix_function_denied(self, guard):
        ctx = build_ctx(**EDITOR_CTX)
        with pytest.raises(ValidationError) as ei:
            guard.validate_and_rewrite("SELECT pg_current_user()", ctx)
        assert ei.value.layer == 4
        assert ei.value.reason == "unapproved_system_function"

    def test_pg_catalog_qualified_call_denied(self, guard):
        """限定符在 exp.Dot 父节点（func.name 已丢失）——Dot 遍历兜住。"""
        ctx = build_ctx(**EDITOR_CTX)
        with pytest.raises(ValidationError) as ei:
            guard.validate_and_rewrite("SELECT pg_catalog.current_user()", ctx)
        assert ei.value.layer == 4
        assert ei.value.reason == "unapproved_system_function"

    @pytest.mark.parametrize("sql", [
        # 限定名不改变函数名解析——黑名单函数换限定符形态仍被拦
        "SELECT pg_catalog.pg_sleep(1)",
        # 信息探测类（非 pg_ 前缀）走显式黑名单
        "SELECT has_table_privilege('u', 't', 'SELECT')",
        "SELECT txid_current()",
    ])
    def test_dangerous_variants_denied(self, guard, sql):
        ctx = build_ctx(**EDITOR_CTX)
        with pytest.raises(ValidationError) as ei:
            guard.validate_and_rewrite(sql, ctx)
        assert ei.value.layer == 4
        assert ei.value.reason == "dangerous_function"

    def test_harmless_nonpg_function_allowed(self, guard):
        """策略边界：非 pg_ 前缀且无危害的函数不误伤（fail-closed 只针对
        系统函数族，不是全函数白名单——全白名单需黄金集配合另立项）。"""
        ctx = build_ctx(**EDITOR_CTX)
        guarded = guard.validate_and_rewrite("SELECT gen_random_uuid()", ctx)
        # sqlglot 渲染会把函数名规范化为大写
        assert "GEN_RANDOM_UUID()" in guarded.executable_sql.upper()

    def test_column_qualifier_dot_unaffected(self, guard):
        """Dot 检查不得误伤普通列限定（p.product_name）。"""
        ctx = build_ctx(**EDITOR_CTX)
        guarded = guard.validate_and_rewrite(
            "SELECT p.product_name FROM product.products p LIMIT 1", ctx)
        assert "product_name" in guarded.executable_sql


class TestAstEscapeSurface:
    """G-13/G-14/G-15/G-16/G-18/G-19：AST 逃逸面锁定（探针取证 2026-10-06）。

    这批行为由既有 AST 检查承担（写操作递归/表白名单递归/语句类型），
    本类把渗透形态固化为回归——安全实现若重构，此处是行为契约。
    """

    @pytest.mark.parametrize("sql", [
        "WITH x AS (DELETE FROM order.orders RETURNING *) SELECT * FROM x",
        "WITH x AS (UPDATE order.orders SET status='x' RETURNING *) SELECT * FROM x",
        "WITH x AS (INSERT INTO order.orders(id) VALUES (1) RETURNING *) SELECT * FROM x",
    ])
    def test_g13_writable_cte(self, guard, sql):
        ctx = build_ctx(**EDITOR_CTX)
        with pytest.raises(ValidationError) as ei:
            guard.validate_and_rewrite(sql, ctx)
        assert ei.value.layer == 1
        assert ei.value.reason == "write_in_subquery"

    @pytest.mark.parametrize("sql,layer", [
        # 任意深度子查询/CTE 出现非白名单表 → 拒（递归版 G-14）
        ("SELECT * FROM (SELECT * FROM (SELECT * FROM pg_catalog.pg_roles) a) b LIMIT 1", 2),
        ("WITH t AS (SELECT * FROM pg_catalog.pg_authid) SELECT * FROM t LIMIT 1", 2),
        ("SELECT (SELECT 1 FROM information_schema.columns) FROM product.products LIMIT 1", 2),
        # UNION 混入系统表：语句级先拒（多语句形态）
        ("SELECT * FROM product.products UNION ALL SELECT * FROM pg_catalog.pg_shadow LIMIT 1", 1),
    ])
    def test_g14_recursive_whitelist(self, guard, sql, layer):
        ctx = build_ctx(**EDITOR_CTX)
        with pytest.raises(ValidationError) as ei:
            guard.validate_and_rewrite(sql, ctx)
        assert ei.value.layer == layer

    def test_g15_multi_statement_and_obfuscation(self, guard):
        ctx = build_ctx(**EDITOR_CTX)
        with pytest.raises(ValidationError) as ei:
            guard.validate_and_rewrite("SELECT 1; DELETE FROM order.orders", ctx)
        assert ei.value.reason == "multi_statement"
        # 块注释混淆不改语义
        with pytest.raises(ValidationError) as ei:
            guard.validate_and_rewrite(
                "select/**/id/**/from/**/pg_catalog.pg_tables/**/limit/**/1", ctx)
        assert ei.value.layer == 2

    def test_g15_whitespace_form_reaches_guard(self, guard):
        """换行/制表符形态过 validator 后被 guard 表域拒（实测归因 guard）。"""
        ctx = build_ctx(**EDITOR_CTX)
        with pytest.raises(SQLPolicyError) as ei:
            guard.validate_and_rewrite(
                "SELECT\r\nid\tFROM\norder.orders LIMIT 1", ctx)
        assert ei.value.code == "SQL_TABLE_NOT_ALLOWED"

    @pytest.mark.parametrize("sql", [
        "SELECT * FROM pg_catalog.pg_class LIMIT 1",
        "SELECT table_name FROM information_schema.columns LIMIT 1",
        "SELECT * FROM pg_settings LIMIT 1",
    ])
    def test_g16_system_schema(self, guard, sql):
        ctx = build_ctx(**EDITOR_CTX)
        with pytest.raises(ValidationError) as ei:
            guard.validate_and_rewrite(sql, ctx)
        assert ei.value.layer == 2

    @pytest.mark.parametrize("sql", [
        "SET ROLE agent_readonly",
        "SET search_path = pg_catalog",
        "SET SESSION AUTHORIZATION admin",
        "RESET ROLE",
        "BEGIN",
        "COMMIT",
        "ROLLBACK",
        "DISCARD ALL",
        "PREPARE p1 AS SELECT 1",
        "EXECUTE p1",
    ])
    def test_g18_session_transaction_control(self, guard, sql):
        ctx = build_ctx(**EDITOR_CTX)
        with pytest.raises(ValidationError) as ei:
            guard.validate_and_rewrite(sql, ctx)
        assert ei.value.layer == 1
        assert ei.value.reason == "non_select"

    @pytest.mark.parametrize("sql,layer,reason", [
        ("COPY order.orders TO '/tmp/x.csv'", 1, "non_select"),
        ("COPY order.orders FROM '/tmp/x.csv'", 1, "non_select"),
        ("CALL some_proc()", 1, "non_select"),
        ("DO $$ BEGIN RAISE NOTICE 'x'; END $$", 1, "non_select"),
        ("VACUUM order.orders", 1, "non_select"),
        ("ANALYZE order.orders", 1, "non_select"),
        # EXPLAIN ANALYZE 会真实执行语句——必须与其他非查询命令同待遇
        ("EXPLAIN ANALYZE SELECT * FROM order.orders", 1, "non_select"),
        ("EXPLAIN SELECT * FROM order.orders", 1, "non_select"),
        ("LISTEN channel1", 1, "non_select"),
        ("NOTIFY channel1", 1, "non_select"),
        ("CREATE TABLE t2(id int)", 1, "non_select"),
        ("ALTER SYSTEM SET max_connections = 10", 1, "non_select"),
        ("REINDEX INDEX idx1", 1, "non_select"),
        ("CHECKPOINT", 1, "non_select"),
        ("GRANT SELECT ON order.orders TO someone", 1, "non_select"),
        ("SELECT * INTO new_t FROM order.orders", 1, "select_into"),
        # sqlglot 不识别的命令走 parse_error 拒绝（fail-closed 兜底）
        ("LOCK TABLE order.orders IN ACCESS EXCLUSIVE MODE", 0, "parse_error"),
        ("CLUSTER order.orders", 0, "parse_error"),
    ])
    def test_g19_pg_specific_commands(self, guard, sql, layer, reason):
        ctx = build_ctx(**EDITOR_CTX)
        with pytest.raises(ValidationError) as ei:
            guard.validate_and_rewrite(sql, ctx)
        assert ei.value.layer == layer
        assert ei.value.reason == reason


class TestG20IdentityFailClosed:
    """G-20：身份字段缺失不允许默认全局身份。"""

    def test_missing_roles_denied(self, guard):
        ctx = build_ctx(user_id="u1")
        with pytest.raises(SQLPolicyError) as ei:
            guard.validate_and_rewrite(
                "SELECT product_name FROM product.products LIMIT 1", ctx)
        assert ei.value.code == "SQL_PERMISSION_DENIED"

    def test_missing_user_id_denied(self, guard):
        """空 user_id 即使有角色也拒——审计归因与 scope 注入的锚点不可为空。
        归 SCOPE_UNAVAILABLE（与 self-scope 缺 user 既有语义一致；
        权限门先行，guest 无权限仍按 PERMISSION_DENIED 拒）。"""
        ctx = build_ctx(user_id="", roles=("editor",))
        with pytest.raises(SQLPolicyError) as ei:
            guard.validate_and_rewrite(
                "SELECT product_name FROM product.products LIMIT 1", ctx)
        assert ei.value.code == "SQL_SCOPE_UNAVAILABLE"


def _validate(sql: str) -> tuple:
    from backend.sql.sql_validator import sql_validator
    return sql_validator.validate(sql)
