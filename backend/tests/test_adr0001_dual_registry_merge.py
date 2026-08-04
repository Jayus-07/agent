"""
test_adr0001_dual_registry_merge.py — ADR-0001 验证

验证 tool_registry 的 CAPABILITY_MAP / CAPABILITY_SCHEMA
从 Skill 实例自动派生（不是硬编码），保证单一事实来源。
"""
import pytest

# 触发 Skill 包注册
from backend.skills import (  # noqa: F401
    sql, rag, report, email, data_export, web_search, web_crawl,
)
from backend.orchestration.tool_registry import tool_registry


class TestADRDualRegistryMerge:
    """ADR-0001：合并双注册表（静态字典 → 动态派生）"""

    def test_all_skills_have_required_metadata(self):
        """每个 Skill 必须声明 description 和 examples（__init_subclass__ 校验）"""
        from backend.skills.registry import _registry

        for cap, skill in _registry.items():
            assert skill.description, f"{skill.name} 缺少 description"
            assert skill.examples, f"{skill.name} 缺少 examples"
            assert skill.params_schema, f"{skill.name} 缺少 params_schema"

    def test_capability_map_derived_from_skills(self):
        """CAPABILITY_MAP 从 Skill.name 派生（f"{name}_skill"）"""
        caps = tool_registry.CAPABILITY_MAP
        assert "sql.query" in caps
        assert "rag.search" in caps
        assert "report.generate" in caps
        assert "email.send" in caps
        assert "data.export" in caps
        assert "web.search" in caps
        assert "web.crawl" in caps
        assert "data.collect" in caps  # 外部 Skill 惰性加载

        # 节点名约定
        assert caps["sql.query"] == "sql_skill"
        assert caps["rag.search"] == "rag_skill"
        assert caps["data.collect"] == "data_collection_skill"  # name 含下划线

    def test_capability_schema_derived_from_skills(self):
        """CAPABILITY_SCHEMA 从 Skill.description/params_schema/examples 派生"""
        schema = tool_registry.CAPABILITY_SCHEMA

        rag = schema["rag.search"]
        assert "知识库" in rag["description"]
        assert rag["params"] == {"question": "检索问题"}
        assert "question" in rag["示例"]

        sql = schema["sql.query"]
        assert "PostgreSQL" in sql["description"]
        assert "question" in sql["params"]

    def test_get_node_unchanged_api(self):
        """get_node() 接口保持向后兼容"""
        assert tool_registry.get_node("sql.query") == "sql_skill"
        assert tool_registry.get_node("rag.search") == "rag_skill"
        assert tool_registry.get_node("unknown.cap") is None  # 未知返回 None

    def test_get_schema_unchanged_api(self):
        """get_schema() 接口保持向后兼容"""
        s = tool_registry.get_schema("web.search")
        assert s["description"].startswith("搜索外部网页")
        assert tool_registry.get_schema("unknown.cap") is None

    def test_get_available_capabilities_returns_all(self):
        """get_available_capabilities() 返回全部 8 个"""
        caps = tool_registry.get_available_capabilities()
        assert len(caps) == 8
        # 包含 data.collect（外部 Skill，懒加载）
        assert "data.collect" in caps

    def test_get_capabilities_schema_text_for_planner(self):
        """Planner prompt 文本生成正常"""
        text = tool_registry.get_capabilities_schema_text()
        assert "### sql.query" in text
        assert "### rag.search" in text
        assert "描述:" in text
        assert "参数:" in text
        assert "示例:" in text

    def test_skill_node_registration_consistent(self):
        """每个 Skill 的节点名要同时出现在 _skill_nodes 和 CAPABILITY_MAP"""
        node_names = tool_registry.get_skill_node_names()
        cap_map = tool_registry.CAPABILITY_MAP

        # CAPABILITY_MAP 的所有节点名都应在 _skill_nodes 中
        for cap, node_name in cap_map.items():
            assert node_name in node_names, (
                f"capability {cap} 映射到 {node_name}，但 _skill_nodes 没有这个节点"
            )

    def test_cache_invalidation(self):
        """invalidate_cache() 清除 cached_property（动态加载场景）"""
        # 触发缓存
        m1 = tool_registry.CAPABILITY_MAP
        m2 = tool_registry.CAPABILITY_MAP
        assert m1 is m2  # cached_property 同一对象

        tool_registry.invalidate_cache()
        m3 = tool_registry.CAPABILITY_MAP
        assert m1 is not m3  # 失效后重新计算


class TestNewSkillWorkflow:
    """验证新增 Skill 的最小改动路径（ADR-0001 目标）"""

    def test_base_skill_rejects_missing_metadata(self):
        """缺 description 时，子类加载直接报错（编译期防御）"""
        from backend.skills.base import BaseSkill

        with pytest.raises(TypeError, match="description"):
            class BadSkill(BaseSkill):
                name = "bad"
                capabilities = ["bad.do"]
                # 故意缺 description
                @property
                def _tool_fn(self):
                    return None

    def test_base_skill_rejects_missing_examples(self):
        """缺 examples 时报错"""
        from backend.skills.base import BaseSkill

        with pytest.raises(TypeError, match="examples"):
            class BadSkill(BaseSkill):
                name = "bad"
                capabilities = ["bad.do"]
                description = "for test"
                @property
                def _tool_fn(self):
                    return None
