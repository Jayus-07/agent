"""P1-11 测试 — SQL 数据安全：脱敏修复 + 行级安全开关 + 服务端用户推导

覆盖：
  1. executor._mask_value 的 suffix_len=0 修复（原 bug：value[-0:] 返回整串泄露原文）
  2. schema_loader 行级安全总开关（SQL_ROW_SECURITY_ENABLED）
  3. /sql 路由的用户身份服务端推导（可信网关头，客户端字段废弃）
"""
from unittest.mock import Mock

import pytest

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
    def _make_request(self, headers=None):
        req = Mock()
        req.headers = headers or {}
        return req

    def test_untrusted_header_ignored(self, monkeypatch):
        """TRUST_USER_HEADER=false：即使客户端带 X-User-Id 也不采用"""
        monkeypatch.setattr(sql_route, "TRUST_USER_HEADER", False)
        req = self._make_request({"X-User-Id": "101"})
        assert sql_route._resolve_user_id(req) is None

    def test_trusted_header_parsed(self, monkeypatch):
        monkeypatch.setattr(sql_route, "TRUST_USER_HEADER", True)
        req = self._make_request({"X-User-Id": "101"})
        assert sql_route._resolve_user_id(req) == 101

    def test_trusted_header_missing(self, monkeypatch):
        monkeypatch.setattr(sql_route, "TRUST_USER_HEADER", True)
        req = self._make_request({})
        assert sql_route._resolve_user_id(req) is None

    def test_trusted_header_invalid_int(self, monkeypatch):
        monkeypatch.setattr(sql_route, "TRUST_USER_HEADER", True)
        req = self._make_request({"X-User-Id": "not-a-number"})
        assert sql_route._resolve_user_id(req) is None

    def test_custom_header_name(self, monkeypatch):
        monkeypatch.setattr(sql_route, "TRUST_USER_HEADER", True)
        monkeypatch.setattr(sql_route, "USER_ID_HEADER", "X-Auth-User")
        req = self._make_request({"X-Auth-User": "42", "X-User-Id": "999"})
        # 只认配置的头名
        assert sql_route._resolve_user_id(req) == 42


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
