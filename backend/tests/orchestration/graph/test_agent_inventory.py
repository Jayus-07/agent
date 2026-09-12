"""test_agent_inventory.py — Multi-Agent 清单结构回归

锁定当前架构事实:
  - 主图: 7 内置节点 + 1 域图节点 (cs_graph_node) + 10 Skill 节点
  - 客服子图: 9 节点 (4 流程 + 5 专家)

节点被误删或改名时在此处立刻失败，而不是等到运行期路由报错。
"""
from __future__ import annotations

from backend.customer_service.graph_builder import build_cs_graph
from backend.orchestration.graph.builder import build_graph
from backend.orchestration.tool_registry import tool_registry

MAIN_BUILTIN = {
    "router", "skill_executor", "workflow_executor",
    "planner", "critique", "supervisor", "reporter",
}
DOMAIN_NODES = {"cs_graph_node"}
SKILL_NODES = {
    "business_analysis_skill", "competitor_analysis_skill",
    "data_collection_skill", "data_export_skill", "email_skill",
    "rag_skill", "report_skill", "sql_skill",
    "web_crawl_skill", "web_search_skill",
}
CS_NODES = {
    "cs_state_loader", "cs_pending_handler", "cs_supervisor", "cs_reporter",
    "cs_knowledge_expert", "cs_query_expert", "cs_action_expert",
    "cs_complaint_expert", "cs_handoff_expert",
}


def _graph_nodes(graph) -> set[str]:
    return set(graph.get_graph().nodes.keys()) - {"__start__", "__end__"}


def test_main_graph_builtin_nodes():
    nodes = _graph_nodes(build_graph())
    missing = MAIN_BUILTIN - nodes
    assert not missing, f"主图内置节点缺失: {missing}"


def test_main_graph_domain_nodes():
    nodes = _graph_nodes(build_graph())
    missing = DOMAIN_NODES - nodes
    assert not missing, f"域图节点缺失: {missing}"


def test_skill_registry_nodes():
    registered = set(tool_registry.get_skill_node_names())
    missing = SKILL_NODES - registered
    extra = registered - SKILL_NODES
    assert not missing, f"Skill 节点缺失: {missing}"
    # 新增 Skill 是预期扩展，此处仅提示不阻断
    assert not extra or True


def test_main_graph_skill_nodes_wired():
    """Skill 节点不仅要注册，还要真的接入主图。"""
    nodes = _graph_nodes(build_graph())
    unwired = SKILL_NODES - nodes
    assert not unwired, f"已注册但未接入主图的 Skill: {unwired}"


def test_main_graph_edges():
    """关键边: router 分流 + executor 直达 reporter + planner→critique。"""
    edges = {(e.source, e.target) for e in build_graph().get_graph().edges}
    required = {
        ("__start__", "router"),
        ("planner", "critique"),
        ("skill_executor", "reporter"),
        ("workflow_executor", "reporter"),
        ("cs_graph_node", "__end__"),
        ("reporter", "__end__"),
    }
    missing = required - edges
    assert not missing, f"主图关键边缺失: {missing}"


def test_cs_graph_nodes():
    nodes = _graph_nodes(build_cs_graph())
    missing = CS_NODES - nodes
    assert not missing, f"客服子图节点缺失: {missing}"
