"""
state.py — AgentState 与 StepResult 类型定义

统一图状态对象，所有节点通过读写此 state 协同工作。
"""

from typing import TypedDict, Literal, Any, Annotated
from langgraph.graph.message import add_messages


def _merge_step_results(left: dict, right: dict) -> dict:
    """Reducer: 合并并行 Worker 返回的 step_results。

    LangGraph 并行执行多个 Worker 时，每个 Worker 返回
    {"step_results": {"step_id": result}}，reducer 负责合并。
    right 中的 key 覆盖 left 中的同 key（允许状态更新覆盖旧值）。
    """
    if not left:
        return dict(right)
    if not right:
        return dict(left)
    merged = dict(left)
    merged.update(right)
    return merged


class StepResult(TypedDict, total=False):
    """单个步骤的执行结果"""
    step_id: str
    capability: str                             # Planner 分配给该步骤的 capability
    description: str                            # 步骤描述
    status: Literal["pending", "running", "success", "failed", "skipped"]
    output: Any                                 # Worker 执行后的返回值
    error: str | None                           # 失败时的错误信息
    retries: int                                # 已重试次数
    started_at: float                           # 开始时间 (time.time())
    finished_at: float                          # 完成时间


class AgentState(TypedDict):
    """Multi-Agent 工作流全局状态"""
    question: str                               # 用户原始问题
    kb_id: str                                  # 知识库ID（policy/tech/finance/hr/default）
    plan: dict                                  # Planner 产出的 DAG:
                                                # {"nodes": {"1": {...}, "2": {...}},
                                                #  "edges": {"3": ["1","2"]}}
    step_results: Annotated[dict[str, StepResult], _merge_step_results]
    current_step_id: str | None                 # 当前正在执行的 step（Worker 用）
    messages: Annotated[list, add_messages]     # ReAct 对话历史
    final_answer: str                           # Reporter 产物
