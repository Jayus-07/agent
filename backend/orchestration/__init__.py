"""
orchestration — 基于 LangGraph 的 Multi-Agent 工作流系统

架构: Planner → Critique → Supervisor ⇄ Skills (SQL/RAG/Report) → Reporter

目录结构:
  graph/       — LangGraph 图构建 + MultiAgentSystem 运行时
  planner/     — 任务规划 + 计划审查
  supervisor/  — 调度 + 降级 + 告警
  skills/      — Skill 节点（RAG/SQL/Report），每种业务能力一个 Skill
  reporter/    — 最终回答生成 + Context Filter

特性:
  - DAG 任务拆解与依赖解析
  - 并行 Skill 执行
  - retry + timeout 错误恢复
  - Tool Registry (capability → skill 映射)
  - 零侵入接入已有子系统

用法:
    from backend.orchestration import MultiAgentSystem

    agent = MultiAgentSystem()
    answer = agent.ask("最近7天Amazon US的销售额，生成日报")
"""

from backend.orchestration.state import AgentState, StepResult
from backend.orchestration.tool_registry import ToolRegistry, tool_registry

__all__ = [
    "AgentState",
    "StepResult",
    "ToolRegistry",
    "tool_registry",
    "MultiAgentSystem",
]


def __getattr__(name: str):
    # 延迟导入 MultiAgentSystem：graph/builder 依赖 agents.planner，而
    # planner → tool_registry 会先触发本包初始化，eager 导入形成循环
    # （planner → orchestration → graph/builder → critique → 半初始化的 planner）。
    # PEP 562 包级 __getattr__ 保持 `from backend.orchestration import
    # MultiAgentSystem` 用法不变。
    if name == "MultiAgentSystem":
        from backend.orchestration.graph import MultiAgentSystem
        return MultiAgentSystem
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
