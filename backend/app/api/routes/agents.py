"""Agent 层只读总览 API（B13 管理端 /agents 页数据源）

GET /agents  列出主图全部 Agent 节点（编排节点 / Skill 节点 / 域图节点）

只读原则：Agent 层事实源在代码（builder 的 add_node、各 Skill 包自注册、
域图自注册）。本接口只做汇总展示，不提供任何写路径，避免产生第二个事实源
（同 docs/2026-09-16-总交接与实施计划.md B13 的「不写假数据、只读起步」口径）。
"""
from fastapi import APIRouter

router = APIRouter(prefix="/agents", tags=["Agents"])

# 编排节点名单：与 builder.build_main_graph 的 add_node 调用一一对应。
# 标签运行时取 builder._NODE_LABELS（节点标签的单一事实源），缺失时回退节点名。
# 新增编排节点须同步维护此名单（builder 的 add_node 是结构事实源）。
_ORCHESTRATION_NODES = (
    "router",
    "tool_selector",
    "skill_executor",
    "workflow_executor",
    "planner",
    "critique",
    "supervisor",
    "reporter",
)


@router.get("")
async def list_agents():
    """列出主图全部 Agent 节点。

    返回 {count, summary, agents: [{name, label, kind, capabilities}]}，
    kind ∈ orchestration / skill / domain_graph。
    Skill 节点的 capabilities 来自 skills 注册表，供管理端对照能力归属。
    """
    # 惰性导入：builder 会连带触发域图/Skill 包自注册，避免拖慢应用导入期
    from backend.orchestration.domain_registry import domain_graph_registry
    from backend.orchestration.graph.builder import _NODE_LABELS
    from backend.orchestration.tool_registry import tool_registry
    from backend.skills import registry as skill_registry

    # node_name → capabilities（经 _node_name 约定反查 Skill 实例）
    skill_caps: dict[str, list[str]] = {}
    for skill_name, caps in skill_registry.list_skills().items():
        skill_caps[f"{skill_name}_skill"] = list(caps)

    agents: list[dict] = []
    for name in _ORCHESTRATION_NODES:
        agents.append({
            "name": name,
            "label": _NODE_LABELS.get(name, name),
            "kind": "orchestration",
            "capabilities": [],
        })
    for name in tool_registry.get_skill_nodes():
        agents.append({
            "name": name,
            "label": _NODE_LABELS.get(name, name),
            "kind": "skill",
            "capabilities": skill_caps.get(name, []),
        })
    for domain in domain_graph_registry.get_all().values():
        agents.append({
            "name": domain.node_name,
            "label": domain.label,
            "kind": "domain_graph",
            "route_mode": domain.name,
            "capabilities": [],
        })

    summary: dict[str, int] = {}
    for a in agents:
        summary[a["kind"]] = summary.get(a["kind"], 0) + 1
    return {"count": len(agents), "summary": summary, "agents": agents}


__all__ = ["router"]
