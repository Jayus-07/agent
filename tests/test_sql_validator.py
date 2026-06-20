"""SQLValidator 测试 — sqlglot AST 安全校验 Layers 1-5"""
import pytest

import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# 注册测试表到 schema_loader
from sql_agent.schema_loader import schema_loader
schema_loader.register_table("users", {
    "id": "用户ID", "name": "姓名", "email": "邮箱",
    "phone": "手机号", "dept_id": "部门ID",
}, "用户表")
schema_loader.register_table("departments", {
    "id": "部门ID", "name": "部门名称",
}, "部门表")
schema_loader.register_table("projects", {
    "id": "项目ID", "name": "项目名称", "budget": "预算",
}, "项目表")

# 设置敏感列：phone 和 email 不可查询
schema_loader.sensitive_columns = {"users.phone", "users.email"}

from sql_agent.sql_validator import SQLValidator, ValidationError


class TestSQLValidatorLayer1:
    """Layer 1: SQL 类型校验 — 只允许 SELECT"""

    def setup_method(self):
        self.v = SQLValidator()

    def test_simple_select(self):
        sql, tables, _ = self.v.validate("SELECT * FROM users")
        assert "SELECT" in sql.upper()

    def test_rejects_insert(self):
        with pytest.raises(ValidationError, match="只允许 SELECT"):
            self.v.validate("INSERT INTO users VALUES (1, 'test')")

    def test_rejects_update(self):
        with pytest.raises(ValidationError, match="只允许 SELECT"):
            self.v.validate("UPDATE users SET name = 'x'")

    def test_rejects_delete(self):
        with pytest.raises(ValidationError, match="只允许 SELECT"):
            self.v.validate("DELETE FROM users")

    def test_rejects_drop(self):
        with pytest.raises(ValidationError, match="只允许 SELECT"):
            self.v.validate("DROP TABLE users")

    def test_rejects_multiple_statements(self):
        with pytest.raises(ValidationError, match="禁止多条语句"):
            self.v.validate("SELECT 1; DROP TABLE users;")

    def test_rejects_empty_sql(self):
        with pytest.raises(ValidationError):
            self.v.validate("")

    def test_select_with_where(self):
        sql, tables, _ = self.v.validate("SELECT name FROM users WHERE id = 1")
        assert "SELECT" in sql.upper()

    def test_select_with_join(self):
        sql, tables, _ = self.v.validate(
            "SELECT u.name, d.name FROM users u JOIN departments d ON u.dept_id = d.id"
        )
        assert "SELECT" in sql.upper()


class TestSQLValidatorLayer2:
    """Layer 2: 表名白名单"""

    def setup_method(self):
        self.v = SQLValidator()

    def test_allowed_table(self):
        sql, tables, _ = self.v.validate("SELECT * FROM users")
        assert "users" in tables

    def test_rejects_unknown_table(self):
        with pytest.raises(ValidationError, match="禁止访问表"):
            self.v.validate("SELECT * FROM secret_table")

    def test_extracts_table_names(self):
        _, tables, _ = self.v.validate(
            "SELECT * FROM users JOIN departments ON users.dept_id = departments.id"
        )
        assert "users" in tables
        assert "departments" in tables


class TestSQLValidatorLayer3:
    """Layer 3: 敏感列拒绝"""

    def setup_method(self):
        self.v = SQLValidator()

    def test_rejects_sensitive_column_phone(self):
        # sqlglot 30.x: 需用表限定列名才能在 AST 中匹配
        with pytest.raises(ValidationError, match="禁止查询敏感列"):
            self.v.validate("SELECT users.phone FROM users")

    def test_rejects_sensitive_column_email(self):
        with pytest.raises(ValidationError, match="禁止查询敏感列"):
            self.v.validate("SELECT users.email FROM users")

    def test_allows_non_sensitive_column(self):
        sql, _, _ = self.v.validate("SELECT name FROM users")
        assert "name" in sql

    def test_allows_star_select(self):
        """SELECT * 不直接触发列级检查"""
        sql, _, _ = self.v.validate("SELECT * FROM users LIMIT 10")
        # * 不生成 Column 节点，不会触发敏感列检查
        assert "SELECT" in sql.upper()


class TestSQLValidatorLayer5:
    """Layer 5: LIMIT 强制添加"""

    def setup_method(self):
        self.v = SQLValidator()

    def test_adds_default_limit(self):
        sql, _, _ = self.v.validate("SELECT * FROM users")
        assert "LIMIT" in sql.upper()

    def test_preserves_lower_limit(self):
        sql, _, _ = self.v.validate("SELECT * FROM users LIMIT 5")
        assert "LIMIT 5" in sql.upper()


class TestSQLValidatorIntegration:
    """集成测试：多层校验同时生效"""

    def setup_method(self):
        self.v = SQLValidator()

    def test_valid_complex_query(self):
        sql, tables, ast = self.v.validate(
            "SELECT u.name, d.name FROM users u "
            "JOIN departments d ON u.dept_id = d.id "
            "WHERE d.name = '技术部'"
        )
        assert "users" in tables
        assert "departments" in tables
        assert "LIMIT" in sql.upper()

    def test_returned_ast_is_select(self):
        from sqlglot import expressions as exp
        _, _, ast = self.v.validate("SELECT 1")
        assert isinstance(ast, exp.Select)
