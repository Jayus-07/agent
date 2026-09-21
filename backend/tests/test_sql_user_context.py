"""P1-11 测试 — SQL 数据安全：脱敏修复 + 行级安全开关 + 服务端用户推导

覆盖：
  1. executor._mask_value 的 suffix_len=0 修复（原 bug：value[-0:] 返回整串泄露原文）
  2. schema_loader 行级安全总开关（SQL_ROW_SECURITY_ENABLED）
  3. /sql 路由的用户身份服务端推导（可信网关头，客户端字段废弃）
"""
from unittest.mock import Mock

import pytest
import sqlglot
from sqlglot import exp

from backend.sql.executor import _mask_value, _mask_row
from backend.sql.schema_loader import SchemaLoader


# =====================================================
# 1. 脱敏 _mask_value（P1-11 修复 suffix_len=0 泄露 bug）
# =====================================================

class TestMaskValue:
    def test_suffix_zero_no_leak(self):
        """suffix_len=0 时不得把原值拼回（旧 bug 输出 '张***张三丰'）"""
        assert _mask_value("张三丰", "name") == "张***"

    def test_prefix_and_suffix(self):
        """(2, 2)：保留前后各 2 位，中间打码"""
        # 手工注入配置避免依赖全局 schema_config（当前 name=(1,0)）
        from backend.sql import executor as ex
        original = dict(ex.schema_loader.masked_columns)
        try:
            ex.schema_loader.masked_columns["customer.customers.email"] = (2, 2)
            assert _mask_value("user@example.com", "email") == "us***om"
        finally:
            ex.schema_loader.masked_columns.clear()
            ex.schema_loader.masked_columns.update(original)

    def test_short_value_fully_masked(self):
        """长度不足时整体打码，不泄露任何字符"""
        from backend.sql import executor as ex
        original = dict(ex.schema_loader.masked_columns)
        try:
            # name=(1,0)：len("李四")=2 <= 1+0+1 → 全打码
            assert _mask_value("李四", "name") == "**"
        finally:
            ex.schema_loader.masked_columns.clear()
            ex.schema_loader.masked_columns.update(original)

    def test_non_string_untouched(self):
        assert _mask_value(12345, "name") == 12345
        assert _mask_value(None, "name") is None

    def test_unmasked_column_untouched(self):
        assert _mask_value("普通值", "brand") == "普通值"

    def test_mask_row_masks_configured_column_only(self):
        row = {"name": "王小明", "level": "VIP"}
        masked = _mask_row(row, ["name", "level"])
        assert masked["name"] == "王***"
        assert masked["level"] == "VIP"


# =====================================================
# 1b. 别名脱敏（2026-09-21 审查 #10）
# =====================================================

class TestMaskWithAlias:
    def test_output_lineage_resolves_alias(self):
        from backend.sql.executor import _output_lineage
        lineage = _output_lineage(
            "SELECT name AS n, brand FROM customer.customers"
        )
        assert lineage.get("n") == {"name"}
        assert lineage.get("brand") == {"brand"}

    def test_lineage_parse_failure_degrades_to_empty(self):
        from backend.sql.executor import _output_lineage
        assert _output_lineage("NOT A SQL !!!") == {}

    def test_mask_row_masks_aliased_column(self):
        """`SELECT name AS n` 绕过脱敏的回归：经 lineage 回溯源列名后仍打码"""
        from backend.sql import executor as ex
        original = dict(ex.schema_loader.masked_columns)
        try:
            ex.schema_loader.masked_columns["customer.customers.name"] = (1, 0)
            masked = ex._mask_row({"n": "张三丰"}, ["n"], lineage={"n": {"name"}})
            assert masked["n"] == "张***"
        finally:
            ex.schema_loader.masked_columns.clear()
            ex.schema_loader.masked_columns.update(original)

    def test_mask_row_without_lineage_unchanged(self):
        """无 lineage 时行为与旧版一致（按结果列名匹配）"""
        from backend.sql import executor as ex
        original = dict(ex.schema_loader.masked_columns)
        try:
            ex.schema_loader.masked_columns["customer.customers.name"] = (1, 0)
            masked = ex._mask_row({"name": "张三丰", "level": "VIP"}, ["name", "level"])
            assert masked["name"] == "张***"
            assert masked["level"] == "VIP"
        finally:
            ex.schema_loader.masked_columns.clear()
            ex.schema_loader.masked_columns.update(original)


# =====================================================
# 1c. 数据库错误原文不回显（2026-09-21 审查 #11）
# =====================================================

class TestExecutorErrorSanitization:
    """PG 报错原文可能含 SQL 片段/字面量 → 只进日志，对外只回分类语义。"""

    def _patch_conn_raise(self, monkeypatch, exc):
        from contextlib import contextmanager
        from backend.sql import executor as ex

        @contextmanager
        def fake_conn(timeout=None):
            raise exc
            yield  # pragma: no cover

        monkeypatch.setattr(ex, "_get_conn", fake_conn)

    def test_execute_sql_permission_error_sanitized(self, monkeypatch):
        import psycopg2
        from backend.sql import executor as ex
        self._patch_conn_raise(
            monkeypatch,
            psycopg2.errors.InsufficientPrivilege(
                "permission denied for table customers; literal=secret123"
            ),
        )
        out = ex.execute_sql("SELECT name FROM customer.customers")
        assert "secret123" not in out
        assert "permission denied" not in out.lower()
        assert "安全错误" in out

    def test_execute_sql_timeout_sanitized(self, monkeypatch):
        import psycopg2
        from backend.sql import executor as ex
        self._patch_conn_raise(
            monkeypatch,
            psycopg2.errors.QueryCanceled(
                "canceling statement due to statement timeout"
            ),
        )
        out = ex.execute_sql("SELECT 1", timeout=5)
        assert "canceling" not in out
        assert "查询超时" in out

    def test_struct_error_sanitized(self, monkeypatch):
        import psycopg2
        from backend.sql import executor as ex
        self._patch_conn_raise(
            monkeypatch,
            psycopg2.errors.InsufficientPrivilege(
                "permission denied for table customers; literal=secret123"
            ),
        )
        result = ex.execute_sql_struct("SELECT name FROM customer.customers")
        assert result.status == "permission_denied"
        assert "secret123" not in (result.error or "")
        assert "permission denied" not in (result.error or "").lower()

    def test_pool_exhausted_markdown_rate_limited(self, monkeypatch):
        """连接池打满 → 限流文案，不再裸 500（建议项 2026-09-21）"""
        from backend.sql import executor as ex
        self._patch_conn_raise(monkeypatch, ex.PoolExhaustedError("连接池已满"))
        out = ex.execute_sql("SELECT name FROM customer.customers")
        assert "限流" in out
        assert "稍后重试" in out

    def test_pool_exhausted_struct_rate_limited(self, monkeypatch):
        """连接池打满 → SQLResult(failed, error_type=rate_limited)"""
        from backend.sql import executor as ex
        self._patch_conn_raise(monkeypatch, ex.PoolExhaustedError("连接池已满"))
        result = ex.execute_sql_struct("SELECT name FROM customer.customers")
        assert result.status == "failed"
        assert result.error_type == "rate_limited"
        assert "并发过高" in (result.error or "")


# =====================================================
# 2. 行级安全总开关
# =====================================================

class TestRowSecuritySwitch:
    def test_disabled_by_default(self, monkeypatch):
        """开关关闭（默认）→ row_security 为空，Demo 查询不受影响"""
        monkeypatch.setattr("backend.sql.schema_loader.SQL_ROW_SECURITY_ENABLED", False)
        loader = SchemaLoader()
        assert loader.row_security == {}
        assert loader.get_row_security("order.orders") == {}

    def test_enabled_loads_config(self, monkeypatch):
        """开关开启 → order.orders 强制行级隔离"""
        monkeypatch.setattr("backend.sql.schema_loader.SQL_ROW_SECURITY_ENABLED", True)
        loader = SchemaLoader()
        rs = loader.get_row_security("order.orders")
        assert rs == {"column": "customer_id", "param": "current_user_id"}
        # 未配置的表不受影响
        assert loader.get_row_security("product.products") == {}

    def test_masked_columns_always_loaded(self):
        """脱敏配置不受行级安全开关影响（独立生效）"""
        loader = SchemaLoader()
        assert loader.masked_columns.get("customer.customers.name") == (1, 0)


# =====================================================
# 3. 服务端用户推导（可信网关头）
# =====================================================

from backend.app.api.routes import sql as sql_route


class TestResolveUserId:
    """_resolve_user_id 已收敛到 identity.py（P3）：三模式机 identity_source()，
    网关契约头固定 X-User-Id（不可配）。旧「sql 模块级 TRUST_USER_HEADER/USER_ID_HEADER
    常量」已不存在，这里 patch auth 模式机与 config 旧开关（仅 legacy 分支消费）。"""

    def _make_request(self, headers=None):
        req = Mock()
        req.headers = headers or {}
        return req

    def _to_legacy(self, monkeypatch, trust: bool):
        monkeypatch.setattr("backend.config.auth.IDENTITY_SOURCE", "legacy")
        monkeypatch.setattr("backend.config.TRUST_USER_HEADER", trust)

    def test_untrusted_header_ignored(self, monkeypatch):
        """legacy + TRUST_USER_HEADER=false：即使客户端带 X-User-Id 也不采用"""
        self._to_legacy(monkeypatch, trust=False)
        req = self._make_request({"X-User-Id": "101"})
        assert sql_route._resolve_user_id(req) is None

    def test_trusted_header_parsed(self, monkeypatch):
        self._to_legacy(monkeypatch, trust=True)
        req = self._make_request({"X-User-Id": "101"})
        assert sql_route._resolve_user_id(req) == 101

    def test_trusted_header_missing(self, monkeypatch):
        self._to_legacy(monkeypatch, trust=True)
        req = self._make_request({})
        assert sql_route._resolve_user_id(req) is None

    def test_trusted_header_invalid_int(self, monkeypatch):
        self._to_legacy(monkeypatch, trust=True)
        req = self._make_request({"X-User-Id": "not-a-number"})
        assert sql_route._resolve_user_id(req) is None

    def test_contract_header_name_fixed(self, monkeypatch):
        """只认网关契约头 X-User-Id：USER_ID_HEADER 已固化为网关注入契约
        （与 AuthenticationGlobalFilter 对齐），其他同名语义头不认。"""
        self._to_legacy(monkeypatch, trust=True)
        req = self._make_request({"X-Auth-User": "42", "X-User-Id": "999"})
        assert sql_route._resolve_user_id(req) == 999


# =====================================================
# 4. 行级安全严格模式回归（开关开启 + 缺上下文 → 拒绝）
# =====================================================

class TestRowSecurityStrictMode:
    """严格模式回归。

    注：inject_row_filter 读的是模块级 schema_loader 单例（进程启动时已
    按当时的开关加载），monkeypatch 环境变量只影响新实例。因此这里直接
    patch 单例的 row_security 字典来模拟「开关开启」后的加载结果。
    """

    _RS_CONFIG = {"order.orders": {"column": "customer_id", "param": "current_user_id"}}

    def test_missing_context_rejected(self, monkeypatch):
        from backend.sql.schema_loader import schema_loader
        monkeypatch.setattr(schema_loader, "row_security", dict(self._RS_CONFIG))
        from backend.sql.row_security import inject_row_filter, RowSecurityError
        with pytest.raises(RowSecurityError):
            inject_row_filter(
                "SELECT count(*) FROM order.orders", user_context={}
            )

    def test_injects_filter_with_context(self, monkeypatch):
        from backend.sql.schema_loader import schema_loader
        monkeypatch.setattr(schema_loader, "row_security", dict(self._RS_CONFIG))
        from backend.sql.row_security import inject_row_filter
        new_sql, params = inject_row_filter(
            "SELECT count(*) FROM order.orders",
            user_context={"current_user_id": 101},
        )
        assert "customer_id" in new_sql
        assert "%(" in new_sql  # 参数化占位符
        assert 101 in params.values()

    def test_bare_table_name_also_matched(self, monkeypatch):
        """P1-11 修复：裸表名（不带 schema 前缀）也能匹配限定名配置"""
        from backend.sql.schema_loader import schema_loader
        monkeypatch.setattr(schema_loader, "row_security", dict(self._RS_CONFIG))
        from backend.sql.row_security import inject_row_filter
        new_sql, params = inject_row_filter(
            "SELECT count(*) FROM orders",
            user_context={"current_user_id": 7},
        )
        assert "customer_id" in new_sql
        assert 7 in params.values()


# =====================================================
# 5. 行级安全注入安全回归（自连接 + 作用域）
# =====================================================

class TestRowSecurityInjectionSafety:
    """2026-09-21 审查 #1/#2 回归。

    #1 自连接：同一受保护表多个别名时必须逐别名注入（旧实现按表覆盖写，
    只有最后一个别名被过滤，其余别名裸奔）。
    #2 作用域：条件必须注入**顶层** SELECT 的 WHERE（旧实现 stmt.find 全树
    BFS，外层无 WHERE 而子查询有 WHERE 时条件落入子查询，外层零过滤）。
    """

    _RS_CONFIG = {"order.orders": {"column": "customer_id", "param": "current_user_id"}}

    def _patch(self, monkeypatch):
        from backend.sql.schema_loader import schema_loader
        monkeypatch.setattr(schema_loader, "row_security", dict(self._RS_CONFIG))
        from backend.sql.row_security import inject_row_filter
        return inject_row_filter

    def test_self_join_injects_all_aliases(self, monkeypatch):
        """自连接两个别名都必须被过滤（占位符条件出现两次）"""
        inject = self._patch(monkeypatch)
        new_sql, params = inject(
            "SELECT * FROM order.orders o1 JOIN order.orders o2 "
            "ON o1.customer_id = o2.customer_id",
            user_context={"current_user_id": 101},
        )
        stmt = sqlglot.parse_one(new_sql, read="postgres")
        placeholders = list(stmt.find_all(exp.Placeholder))
        assert len(placeholders) == 2
        # 每个别名的 customer_id 列都出现在过滤条件中
        filtered_aliases = {
            c.table for c in stmt.find_all(exp.Column) if c.name == "customer_id"
        }
        assert {"o1", "o2"} <= filtered_aliases
        # 同表共享同一占位符与值
        assert params == {"order_orders_customer_id": 101}

    def test_no_outer_where_injects_top_level_not_subquery(self, monkeypatch):
        """外层无 WHERE、子查询有 WHERE：条件注入顶层，不得落入子查询作用域"""
        inject = self._patch(monkeypatch)
        new_sql, _ = inject(
            "SELECT (SELECT count(*) FROM order.orders x WHERE x.status = 'paid') "
            "FROM order.orders o",
            user_context={"current_user_id": 101},
        )
        stmt = sqlglot.parse_one(new_sql, read="postgres")
        assert stmt.args.get("where") is not None, "顶层 SELECT 必须带上过滤条件"

    def test_outer_where_preserved_and_extended(self, monkeypatch):
        """外层已有 WHERE：原条件保留，过滤条件 AND 追加在顶层"""
        inject = self._patch(monkeypatch)
        new_sql, params = inject(
            "SELECT o.id FROM order.orders o WHERE o.status = 'paid'",
            user_context={"current_user_id": 101},
        )
        stmt = sqlglot.parse_one(new_sql, read="postgres")
        where_sql = stmt.args["where"].sql(dialect="postgres")
        assert "o.status" in where_sql
        assert "customer_id" in where_sql
        assert params == {"order_orders_customer_id": 101}
