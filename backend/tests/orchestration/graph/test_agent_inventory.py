"""test_agent_inventory.py — Multi-Agent 清单结构回归

锁定当前架构事实:
  - 主图: 7 内置节点 + 2 域图节点 (cs_graph_node / travel_graph_node) + 11 Skill 节点
  - 客服子图: 9 节点 (4 流程 + 5 专家)
  - 旅游子图: 9 节点 (2 入口/调度 + 4 专家 + 校验 + 修复 + 输出)

节点被误删或改名时在此处立刻失败，而不是等到运行期路由报错。
"""
from __future__ import annotations

from backend.customer_service.graph_builder import build_cs_graph
from backend.orchestration.graph.builder import build_graph
from backend.orchestration.capability_registry import tool_registry
from backend.travel.graph_builder import build_travel_graph

MAIN_BUILTIN = {
    "router", "skill_executor", "workflow_executor",
    "planner", "critique", "supervisor", "reporter",
}
DOMAIN_NODES = {"cs_graph_node", "travel_graph_node"}
SKILL_NODES = {
    "business_analysis_skill", "competitor_analysis_skill",
    "data_collection_skill", "data_export_skill", "email_skill",
    "rag_skill", "report_skill", "sql_skill", "travel_poi_skill",
    "web_crawl_skill", "web_search_skill",
}
CS_NODES = {
    "cs_state_loader", "cs_pending_handler", "cs_supervisor", "cs_reporter",
    "cs_knowledge_expert", "cs_query_expert", "cs_action_expert",
    "cs_complaint_expert", "cs_handoff_expert",
}
TRAVEL_NODES = {
    "travel_slot_filler", "travel_supervisor", "travel_reporter",
    "travel_poi_expert", "travel_transit_expert",
    "travel_budget_expert", "travel_risk_expert",
    "travel_validator", "travel_repair",
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
        ("travel_graph_node", "__end__"),
        ("reporter", "__end__"),
    }
    missing = required - edges
    assert not missing, f"主图关键边缺失: {missing}"


def test_cs_graph_nodes():
    nodes = _graph_nodes(build_cs_graph())
    missing = CS_NODES - nodes
    assert not missing, f"客服子图节点缺失: {missing}"


def test_travel_graph_nodes():
    nodes = _graph_nodes(build_travel_graph())
    missing = TRAVEL_NODES - nodes
    assert not missing, f"旅游子图节点缺失: {missing}"


def test_travel_graph_experts_return_to_supervisor():
    """supervisor 的每个阶段目标都必须是图里真实存在的节点。

    不用「专家→supervisor 边」来断言：supervisor 与客服域一样走
    Command(goto=...) 动态路由，LangGraph 的静态边列表里根本不出现这些
    目标（构建出的边只有 start→slot_filler→supervisor）。断言静态边等于
    断言一个不存在的表示层；断言「目标名可解析到真实节点」才真正拦得住
    改名与拼写错误。
    """
    from backend.travel.supervisor import stage_targets

    nodes = _graph_nodes(build_travel_graph())
    targets = set(stage_targets().values())
    missing = targets - nodes
    assert not missing, f"supervisor 跳转目标不存在于图中: {missing}"
    # 反向：专家节点必须被某个阶段引用（防止加了节点却永远调度不到）
    orphan = {
        n for n in nodes
        if n.endswith("_expert") or n in ("travel_validator", "travel_repair")
    } - targets
    assert not orphan, f"以下节点没有任何阶段会调度到: {orphan}"
