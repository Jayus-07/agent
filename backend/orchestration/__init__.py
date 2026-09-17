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

# 注意：只 eager 导入轻量模块（state/tool_registry）。
# graph 严禁在此导入 —— graph → builder → critique → plan_utils →
# orchestration.capability_registry 会再次触发本包 __init__，若此处 eager 导 graph，
# 任何"先导 planner/tool_registry"的调用方都会撞上循环导入
# （2026-09-13 test_planner_critique 收集错误根因）。MultiAgentSystem
# 经 PEP 562 __getattr__ 懒加载，`from backend.orchestration import
# MultiAgentSystem` 用法保持不变。
from backend.orchestration.state import AgentState, StepResult
from backend.orchestration.capability_registry import ToolRegistry, tool_registry

__all__ = [
    "AgentState",
    "StepResult",
    "ToolRegistry",
    "tool_registry",
    "MultiAgentSystem",
]


def __getattr__(name: str):
    if name == "MultiAgentSystem":
        from backend.orchestration.graph import MultiAgentSystem

        return MultiAgentSystem
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
