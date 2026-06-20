"""
ToolRegistry 测试 — capability → worker 映射

覆盖:
  - get_worker(): capability 查找
  - get_schema(): 参数 schema 获取
  - get_available_capabilities(): 能力列表
  - get_capabilities_description(): Planner prompt 用描述
"""
import pytest

import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from multi_agent.tool_registry import ToolRegistry


class TestToolRegistry:
    """能力注册表"""

    def setup_method(self):
        self.registry = ToolRegistry()

    def test_get_worker_query_database(self):
        assert self.registry.get_worker("query_database") == "sql_worker"

    def test_get_worker_search_knowledge(self):
        assert self.registry.get_worker("search_knowledge") == "rag_worker"

    def test_get_worker_generate_report(self):
        assert self.registry.get_worker("generate_report") == "report_worker"

    def test_get_worker_unknown_returns_none(self):
        assert self.registry.get_worker("nonexistent_capability") is None

    def test_get_schema_returns_dict(self):
        schema = self.registry.get_schema("query_database")
        assert isinstance(schema, dict)
        assert "description" in schema
        assert "params" in schema

    def test_get_schema_unknown_returns_none(self):
        assert self.registry.get_schema("unknown") is None

    def test_get_available_capabilities_has_three(self):
        caps = self.registry.get_available_capabilities()
        assert len(caps) == 3
        assert "query_database" in caps
        assert "search_knowledge" in caps
        assert "generate_report" in caps

    def test_get_available_capabilities_returns_list(self):
        caps = self.registry.get_available_capabilities()
        assert isinstance(caps, list)

    def test_get_capabilities_description(self):
        desc = self.registry.get_capabilities_description()
        assert "query_database" in desc
        assert "search_knowledge" in desc
        assert "generate_report" in desc
        assert "\n" in desc  # 多行描述

    def test_capability_map_immutable_from_outside(self):
        """外部修改不影响注册表（复制测试）"""
        caps = self.registry.get_available_capabilities()
        caps.append("fake_capability")
        # 原始注册表不被污染
        assert "fake_capability" not in self.registry.get_available_capabilities()

    def test_all_schemas_have_required_fields(self):
        """所有注册的能力都有描述和参数"""
        for cap in self.registry.get_available_capabilities():
            schema = self.registry.get_schema(cap)
            assert schema is not None, f"{cap} 缺少 schema"
            assert "description" in schema, f"{cap} 缺少 description"
            assert "params" in schema, f"{cap} 缺少 params"

    def test_all_capabilities_have_valid_worker(self):
        """所有 capability 都能映射到 worker"""
        for cap in self.registry.get_available_capabilities():
            worker = self.registry.get_worker(cap)
            assert worker is not None, f"{cap} 的 worker 为 None"
            assert worker in ("sql_worker", "rag_worker", "report_worker")
