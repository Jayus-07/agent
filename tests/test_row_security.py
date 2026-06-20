"""RowSecurity 测试 — 行级安全注入"""
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
    """行级安全注入"""

    def test_injects_user_id_filter(self):
        sql = "SELECT * FROM tasks"
        result = inject_row_filter(sql, {"current_user_id": 101})
        assert "assignee_id" in result.lower()
        assert "101" in result

    def test_no_injection_for_public_table(self):
        sql = "SELECT * FROM public_info"
        result = inject_row_filter(sql, {"current_user_id": 101})
        assert "assignee_id" not in result.lower()
        assert "101" not in result  # 不应注入

    def test_injection_with_existing_where(self):
        sql = "SELECT * FROM tasks WHERE id > 5"
        result = inject_row_filter(sql, {"current_user_id": 101})
        assert "assignee_id" in result.lower()
        assert "101" in result
        assert "id > 5" in result.lower()

    def test_missing_param_skips_injection(self):
        """缺少上下文参数时跳过该表"""
        sql = "SELECT * FROM tasks"
        result = inject_row_filter(sql, {})  # 无 current_user_id
        assert "assignee_id" not in result.lower()

    def test_multi_table_injection(self):
        """两张表都有行级安全时同时注入"""
        sql = "SELECT * FROM tasks JOIN notes ON tasks.id = notes.id"
        result = inject_row_filter(sql, {"current_user_id": 202})
        assert "assignee_id" in result.lower()
        assert "author_id" in result.lower()
        assert "202" in result

    def test_original_sql_not_modified_for_no_match(self):
        sql = "SELECT * FROM public_info"
        result = inject_row_filter(sql, {"current_user_id": 99})
        # public_info 没有策略，原样返回
        assert result == sql

    def test_sql_with_join_and_aliases(self):
        """JOIN 表别名场景"""
        sql = "SELECT t.id FROM tasks t WHERE t.id = 1"
        result = inject_row_filter(sql, {"current_user_id": 303})
        # 注入到别名表
        assert "assignee_id" in result.lower()

    def test_preserves_select_structure(self):
        """注入后仍是合法 SELECT"""
        sql = "SELECT id, title FROM tasks"
        result = inject_row_filter(sql, {"current_user_id": 101})
        assert result.upper().startswith("SELECT")
