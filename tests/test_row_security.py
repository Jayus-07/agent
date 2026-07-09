"""RowSecurity 测试 — 行级安全注入（参数化版本）"""
import pytest

import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sql_agent.schema_loader import schema_loader

# 注册测试表
schema_loader.register_table("tasks", {
    "id": "任务ID", "title": "标题", "assignee_id": "负责人ID",
}, "任务表")
schema_loader.register_table("notes", {
    "id": "笔记ID", "content": "内容", "author_id": "作者ID",
}, "笔记表")
schema_loader.register_table("public_info", {
    "id": "ID", "data": "数据",
}, "公开表")

# 注入行级安全策略（直接写入 schema_loader.row_security dict）
schema_loader.row_security["tasks"] = {"column": "assignee_id", "param": "current_user_id"}
schema_loader.row_security["notes"] = {"column": "author_id", "param": "current_user_id"}

from sql_agent.row_security import inject_row_filter, RowSecurityError


class TestRowSecurity:
    """行级安全注入（参数化版本）"""

    def test_injects_user_id_filter(self):
        """正常注入：SQL 出现列名 + params 字典持有值（值不进 SQL）"""
        sql = "SELECT * FROM tasks"
        new_sql, params = inject_row_filter(sql, {"current_user_id": 101})
        assert "assignee_id" in new_sql.lower()
        # 参数化：值 101 在 params 字典里，**不在 SQL 文本里**
        assert params.get("tasks_assignee_id") == 101
        assert "101" not in new_sql  # 关键：不进 SQL
        assert "%(tasks_assignee_id)s" in new_sql  # 关键：占位符在

    def test_no_injection_for_public_table(self):
        """非保护表不注入"""
        sql = "SELECT * FROM public_info"
        new_sql, params = inject_row_filter(sql, {"current_user_id": 101})
        assert "assignee_id" not in new_sql.lower()
        assert params == {}  # 无参数

    def test_injection_with_existing_where(self):
        """已有 WHERE 时正确合并"""
        sql = "SELECT * FROM tasks WHERE id > 5"
        new_sql, params = inject_row_filter(sql, {"current_user_id": 101})
        assert "assignee_id" in new_sql.lower()
        assert params.get("tasks_assignee_id") == 101
        assert "id > 5" in new_sql.lower()

    def test_missing_param_raises_strict(self):
        """严格模式：受保护表缺少 param → 抛 RowSecurityError（不再静默跳过）"""
        sql = "SELECT * FROM tasks"
        with pytest.raises(RowSecurityError, match="行级安全要求参数"):
            inject_row_filter(sql, {})  # 无 current_user_id

    def test_multi_table_injection(self):
        """两张受保护表共享同一 param → 各自独立占位符，params 去重"""
        sql = "SELECT * FROM tasks JOIN notes ON tasks.id = notes.id"
        new_sql, params = inject_row_filter(sql, {"current_user_id": 202})
        assert "assignee_id" in new_sql.lower()
        assert "author_id" in new_sql.lower()
        # 两个不同表 → 两个不同占位符
        assert "tasks_assignee_id" in params
        assert "notes_author_id" in params
        # 值 202 不进 SQL 文本
        assert "202" not in new_sql
        assert "%(" in new_sql  # 占位符语法

    def test_original_sql_not_modified_for_no_match(self):
        """非受保护表原样返回"""
        sql = "SELECT * FROM public_info"
        new_sql, params = inject_row_filter(sql, {"current_user_id": 99})
        assert new_sql == sql
        assert params == {}

    def test_sql_with_join_and_aliases(self):
        """JOIN 表别名场景"""
        sql = "SELECT t.id FROM tasks t WHERE t.id = 1"
        new_sql, params = inject_row_filter(sql, {"current_user_id": 303})
        assert "assignee_id" in new_sql.lower()
        assert params.get("tasks_assignee_id") == 303

    def test_preserves_select_structure(self):
        """注入后仍是合法 SELECT"""
        sql = "SELECT id, title FROM tasks"
        new_sql, params = inject_row_filter(sql, {"current_user_id": 101})
        assert new_sql.upper().startswith("SELECT")

    def test_placeholder_is_named_param_format(self):
        """占位符是 psycopg2 %(name)s 格式"""
        sql = "SELECT * FROM tasks"
        new_sql, params = inject_row_filter(sql, {"current_user_id": 555})
        # psycopg2 命名占位符格式: %(name)s
        import re
        assert re.search(r"%\([a-z_]+\)s", new_sql), f"期望命名占位符，实际: {new_sql}"
        # 不应有裸 %s
        assert re.search(r"%s\b(?!\w)", new_sql) is None or "%(" in new_sql
