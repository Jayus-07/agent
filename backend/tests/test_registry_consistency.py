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


# ==================== 5. 规则表单一来源（治理 B） ====================

class TestRuleTableSingleSource:
    def test_complex_patterns_single_source(self):
        """multi_query 兜底复杂度检测必须引用 hybrid 的统一清单（同一对象），
        历史上两份清单各自维护曾漂移（操作性信号丢失 → 检索层回退）。"""
        from backend.rag.retrieval.hybrid import COMPLEX_PATTERNS
        from backend.rag.retrieval.multi_query import (
            COMPLEX_PATTERNS as MQ_PATTERNS,
        )

        assert MQ_PATTERNS is COMPLEX_PATTERNS
        assert len(COMPLEX_PATTERNS) == len(set(COMPLEX_PATTERNS)), "清单内含重复信号"


# ==================== 6. capabilities.yaml manifest（路由声明唯一事实源） ====================

class TestCapabilityManifest:
    """manifest ↔ skills/registry ↔ 路由派生量三方对账。

    背景：曾出现 competitor.analyze 已注册 skill、可被 rule_router 硬编码
    路由、却被 LLM Router 拒绝（ALL_CAPABILITIES 缺失）的三方漂移。manifest
    化后由本组测试锁死。
    """

    def test_types_derived_from_manifest(self):
        """ALL_CAPABILITIES / WORKFLOW_NAMES 必须等于 manifest 派生值，
        手写回去立即失败。"""
        from backend.orchestration.router import types
        from backend.orchestration.router.manifest import load_manifest

        m = load_manifest()
        assert types.ALL_CAPABILITIES == [c.name for c in m.routed_capabilities]
        assert types.ALL_DECLARED_CAPABILITIES == list(m.all_capability_names)
        assert types.WORKFLOW_NAMES == [w.name for w in m.workflows]

    def test_vector_router_examples_derived_from_manifest(self):
        """ROUTE_EXAMPLES / WORKFLOW_EXAMPLES 必须等于 manifest 派生值。"""
        from backend.orchestration.router.manifest import load_manifest
        from backend.orchestration.router import vector_router

        m = load_manifest()
        assert vector_router.ROUTE_EXAMPLES == {
            c.name: list(c.examples) for c in m.routed_capabilities
        }
        assert vector_router.WORKFLOW_EXAMPLES == {
            w.name: list(w.examples) for w in m.workflows
        }

    def test_manifest_routed_capability_has_skill(self):
        """manifest 中每条 routed capability 都能在 skills/registry 拿到
        Skill 实例，且 skill 字段与实例 name 一致（防打错名）。"""
        from backend.orchestration.router.manifest import load_manifest
        from backend.skills import registry as skill_reg

        for c in load_manifest().routed_capabilities:
            inst = skill_reg.get(c.name)
            assert inst is not None, f"manifest 声明了 {c.name} 但无 Skill 注册"
            assert inst.name == c.skill, (
                f"manifest 的 {c.name}.skill={c.skill!r} 与 Skill 实例 name={inst.name!r} 不一致"
            )

    def test_every_skill_capability_in_manifest(self):
        """反向对账：skills 注册表里每个 capability 都出现在 manifest
        （routed 或 unrouted）。新 Skill 加了 capability 忘了更新 manifest
        会导致它对路由不可见——正是 competitor.analyze 曾经的病。"""
        from backend.orchestration.router.manifest import load_manifest
        from backend.skills import registry as skill_reg

        declared = set(load_manifest().all_capability_names)
        for cap in skill_reg._registry:
            assert cap in declared, (
                f"capability {cap} 已注册 Skill 但不在 capabilities.yaml 中"
            )

    def test_routed_examples_keys_match_all_capabilities(self):
        """向量路由例子表与 ALL_CAPABILITIES 键集合一致（每条可路由能力
        都必须有 examples，否则向量层对它静默失明）。"""
        from backend.orchestration.router.types import ALL_CAPABILITIES
        from backend.orchestration.router.vector_router import ROUTE_EXAMPLES

        assert set(ROUTE_EXAMPLES) == set(ALL_CAPABILITIES)

    def test_manifest_load_is_fail_fast(self, tmp_path):
        """坏 manifest 必须炸而非静默：重复名 / examples 不足 / 缺 reason。"""
        import yaml

        from backend.orchestration.router.manifest import ManifestError, load_manifest

        def write(data, name):
            f = tmp_path / name
            f.write_text(yaml.safe_dump(data, allow_unicode=True), encoding="utf-8")
            return str(f)

        good_cap = {
            "name": "x.y", "skill": "s", "routed": True,
            "examples": ["例一", "例二"],
        }

        duplicate = {"version": 1, "capabilities": [good_cap, dict(good_cap)]}
        with pytest.raises(ManifestError, match="重复"):
            load_manifest(write(duplicate, "dup.yaml"))

        thin = {"version": 1, "capabilities": [dict(good_cap, examples=["只有一条"])]}
        with pytest.raises(ManifestError, match="examples"):
            load_manifest(write(thin, "thin.yaml"))

        unreasoned = {
            "version": 1,
            "capabilities": [dict(good_cap, routed=False, examples=[])],
        }
        with pytest.raises(ManifestError, match="reason"):
            load_manifest(write(unreasoned, "no_reason.yaml"))

    def test_missing_manifest_raises(self, tmp_path):
        from backend.orchestration.router.manifest import ManifestError, load_manifest

        with pytest.raises(ManifestError):
            load_manifest(str(tmp_path / "nope.yaml"))
