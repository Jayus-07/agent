"""Agent 层只读总览 API（B13 管理端 /agents 页数据源）

GET /agents  列出主图全部 Agent 节点（编排节点 / Skill 节点 / 域图节点）

只读原则：Agent 层事实源在代码（builder 的 add_node、各 Skill 包自注册、
域图自注册）。本接口只做汇总展示，不提供任何写路径，避免产生第二个事实源
（同 docs/2026-09-16-总交接与实施计划.md B13 的「不写假数据、只读起步」口径）。
"""
from pathlib import Path

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
    from backend.orchestration.capability_registry import tool_registry
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


@router.get("/tools")
async def list_tools_inventory():
    """Tool 层运行时清单（2026-09-17 新增，给 Tool 表接真实运行时消费方）。

    背景：``backend/tools/tool_registry`` 此前只有测试期消费方
    （test_layer_consistency / tool_quality_check），运行时零消费——34 个
    @tool 注册后无人读取。本接口把它变成管理端可观测的运行时事实源：

    - loaded: 运行进程已加载并注册的 Tool（name/description/来源文件）
    - duplicates: 同名多次定义检测（P0 防护，正常恒空）
    - not_loaded: AST 扫描到 @tool 声明、但运行进程未加载（漏加载/死代码）
    - phantom: 注册表里有、但仓库里扫不到声明（幽灵注册）

    只读原则同 GET /agents：事实源在代码（@tool 声明 + 各模块自注册），
    本接口只做汇总展示，不提供写路径。
    """
    import backend.skills  # noqa: F401  # 触发 Skill 包自注册 → 连带加载 Skill 依赖的 Tool 模块
    import backend.tools  # noqa: F401  # tools 包 __init__ 集中导入大部分 Tool 模块

    from backend.tools.tool_registry import (  # 延迟导入：避免拖慢应用导入期
        scan_repo_declared_tools,
        tool_registry,
    )

    tools = []
    for name, fn in tool_registry.available_tools.items():
        tools.append({
            "name": name,
            "description": getattr(fn, "description", "") or "",
            "source_file": (tool_registry._tool_sources.get(name) or ["unknown"])[-1],
        })
    tools.sort(key=lambda t: t["name"])

    declared = scan_repo_declared_tools(_repo_root())
    loaded_names = set(tool_registry.tool_names)
    declared_names = set(declared.keys())

    summary = {
        "loaded": len(tools),
        "duplicates": len(tool_registry.check_duplicates()),
        "not_loaded": len(declared_names - loaded_names),
        "phantom": len(loaded_names - declared_names),
    }
    return {
        "count": len(tools),
        "summary": summary,
        "tools": tools,
        "duplicates": tool_registry.check_duplicates(),
        "not_loaded": sorted(declared_names - loaded_names),
        "phantom": sorted(loaded_names - declared_names),
    }


def _repo_root() -> str:
    """仓库根目录（backend/ 的上一级），供 AST 扫描定位 backend/tools。"""
    import backend

    return str(Path(backend.__file__).resolve().parent.parent)


__all__ = ["router"]
