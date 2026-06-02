"""
multi_agent — 基于 LangGraph 的 Multi-Agent 工作流系统

架构: Planner → Supervisor → Workers (SQL/RAG/Report) → Reporter

特性:
  - DAG 任务拆解与依赖解析
  - 并行 Worker 执行
  - retry + timeout 错误恢复
  - Tool Registry (capability → worker 映射)
  - 零侵入接入已有子系统

用法:
    from multi_agent import MultiAgentSystem

    agent = MultiAgentSystem()
    answer = agent.ask("分析技术部预算，找类似项目经验，生成报告")
    print(answer)
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
