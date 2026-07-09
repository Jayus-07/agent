"""
multi_agent — 基于 LangGraph 的 Multi-Agent 工作流系统

架构: Planner → Critique → Supervisor ⇄ Skills (SQL/RAG/Report) → Reporter

目录结构:
  graph/       — LangGraph 图构建 + MultiAgentSystem 运行时
  planner/     — 任务规划 + 计划审查 + Prompt 模板
  supervisor/  — 调度 + 降级 + 告警
  skills/      — Skill 节点（RAG/SQL/Report），每种业务能力一个 Skill
  reporter/    — 结果汇总 → 委托 response/ 模块
  workers/     — 向后兼容 re-export（已迁移到 skills/）

特性:
  - DAG 任务拆解与依赖解析
  - 并行 Skill 执行
  - retry + timeout 错误恢复
  - Tool Registry (capability → skill 映射)
  - 零侵入接入已有子系统

用法:
    from multi_agent import MultiAgentSystem

    agent = MultiAgentSystem()
    answer = agent.ask("最近7天Amazon US的销售额，生成日报")
"""

from multi_agent.state import AgentState, StepResult
from multi_agent.tool_registry import ToolRegistry, tool_registry
from multi_agent.graph import MultiAgentSystem

__all__ = [
    "AgentState",
    "StepResult",
    "ToolRegistry",
    "tool_registry",
    "MultiAgentSystem",
]
