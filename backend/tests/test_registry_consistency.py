"""注册表一致性自检（ADR-0001 收尾）

锁定四条单一事实来源链路，任何一环手写漂移即刻报错：
  1. capability 注册表从 Skill 实例派生（ADR-0001 核心）
  2. Skill.params_schema 与其 _tool_fn 的实际参数不脱节
  3. MCP server 的工具参数从 LangChain tool args_schema 派生（非手写）
  4. 标准 MCP 协议端点工具集 == manager.discover()（无第二份清单）
"""
import pytest


# ==================== 1. ADR-0001：capability 派生 ====================

class TestCapabilityDerivation:
    def test_capability_map_derived_from_skills(self):
        """CAPABILITY_MAP 与 skills/registry 实例一一对应，非硬编码。"""
        from backend.orchestration.tool_registry import tool_registry
        from backend.skills import registry as skill_reg

        expected = {cap: f"{inst.name}_skill" for cap, inst in skill_reg._registry.items()}
        assert tool_registry.CAPABILITY_MAP == expected

    def test_capability_schema_derives_descriptions(self):
        """schema 的 description 与 Skill 类声明同步（Planner prompt 来源）。"""
        from backend.orchestration.tool_registry import tool_registry
        from backend.skills.rag.skill import RAGSkill

        schema = tool_registry.get_schema("rag.search")
        assert schema["description"] == RAGSkill.description

    def test_every_capability_has_executable_skill(self):
        """每个 capability 都能经 get() 拿到 Skill 实例（防打错名运行时才炸）。"""
        from backend.orchestration.tool_registry import tool_registry
        from backend.skills import registry as skill_reg

        for cap in tool_registry.get_available_capabilities():
            assert skill_reg.get(cap) is not None, f"capability {cap} 无对应 Skill 实例"


# ==================== 2. Skill ↔ Tool 不脱节 ====================

class TestSkillToolAlignment:
    def test_rag_params_schema_matches_tool_args(self):
        """rag.search 的 params_schema 键必须存在于 tool 实际参数中。"""
        from backend.skills.rag.skill import RAGSkill
        from backend.tools.rag import search_knowledge_tool

        tool_args = set(search_knowledge_tool.args.keys())
        for param_name in RAGSkill.params_schema:
            assert param_name in tool_args, (
                f"RAGSkill.params_schema 声明了 {param_name}，"
                f"但 search_knowledge_tool 实际参数为 {tool_args}"
            )


# ==================== 3. MCP list_tools 派生（漂移防护） ====================

class TestMcpListToolsDerivation:
    def test_rag_search_knowledge_derived_from_tool(self):
        """search_knowledge 参数定义必须等于 tool args_schema 派生结果，
        手写回去会导致本测试失败（这就是防漂移断言）。"""
        from mcp_servers.servers.rag import RAGMCPServer
        from mcp_servers.schema_adapter import langchain_tool_to_mcp_meta
        from backend.tools.rag import search_knowledge_tool

        tools = {t["name"]: t for t in RAGMCPServer().list_tools()}
        expected = langchain_tool_to_mcp_meta(
            search_knowledge_tool, name="search_knowledge",
            description="从知识库检索 + LLM 生成回答",
        )
        assert tools["search_knowledge"] == expected

    def test_derived_params_match_tool_semantics(self):
        """派生结果保留必填/默认值语义（question 必填、kb_id 默认 default）。"""
        from mcp_servers.servers.rag import RAGMCPServer

        tools = {t["name"]: t for t in RAGMCPServer().list_tools()}
        params = tools["search_knowledge"]["parameters"]
        assert params["question"]["required"] is True
        assert params["kb_id"]["required"] is False
        assert params["kb_id"]["default"] == "default"

    def test_sql_query_derived_from_tool(self):
        from mcp_servers.servers.sql import SQLMCPServer
        from mcp_servers.schema_adapter import langchain_tool_to_mcp_meta
        from backend.tools.sql import sql_query_tool

        tools = {t["name"]: t for t in SQLMCPServer().list_tools()}
        expected = langchain_tool_to_mcp_meta(
            sql_query_tool, name="sql_query", description="自然语言转 SQL 并执行",
        )
        assert tools["sql_query"] == expected


# ==================== 4. 协议端点 == manager.discover() ====================

class TestProtocolEndpointParity:
    def test_build_mcp_tools_match_discover(self):
        """标准 MCP 端点注册的工具清单与 manager.discover() 完全一致。"""
        from mcp_servers.protocol_app import build_mcp
        from mcp_servers.manager import manager
        from mcp_servers.servers import register_all

        register_all()
        mcp = build_mcp(manager)

        import asyncio

        mcp_tools = {t.name for t in asyncio.run(mcp.list_tools())}
        discover_tools = {t["name"] for t in manager.discover()}
        assert mcp_tools == discover_tools
