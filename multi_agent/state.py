"""
state.py — AgentState 与 StepResult 类型定义

统一图状态对象，所有节点通过读写此 state 协同工作。
"""
import operator
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
    # ⭐ 新增：结构化结果字段
    row_count: int | None                       # SQL 查询返回行数
    is_empty: bool | None                       # 是否为空结果（SQL 无数据 / RAG 无匹配）
    error_type: str | None                      # 错误分类: timeout / parse / auth / network / unknown


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
    # ⭐ 新增：可观测性 + 流程控制字段
    alerts: list[dict]                          # PlanAlert 列表（SSE 流展示）
    _supervisor_loop_count: int                 # Supervisor 调度轮次计数
    _plan_critiqued: bool                       # 是否经过了 Plan Critique
    _plan_changed: bool                         # Critique 是否修改了计划
    # 降级步骤集合：用 operator.or_ 作为 reducer（即 set union）
    # 节点必须返回**新** set（用 | 运算），禁止原地 .add() 修改 — 否则 reducer 看不到变化
    _degraded_steps: Annotated[set[str], operator.or_]
