"""
state.py — AgentState 与 StepResult 类型定义

统一图状态对象，所有节点通过读写此 state 协同工作。
"""
import operator
from typing import Annotated, Any, Literal, TypedDict

from langgraph.graph.message import add_messages

from backend.customer_service.context import CSContext
from backend.orchestration.request_context import RequestContext


# 步骤状态的进展优先级：终态 > running > pending。
# 并行 Send 分支返回的是"全量 step_results 快照"（BaseSkill.execute 在
# dispatch 时刻的 state 上更新自己的步骤），其他步骤在快照里还是 running
# 旧视图。朴素 dict.update 会让**后返回的分支把先返回分支的真实成果覆盖
# 成过期快照**（实测 2026-09-15：sql success 被 rag 分支的 running 快照
# 覆盖，reporter 全判失败）。合并必须按状态进展择优。
_STATUS_PRIORITY = {"pending": 0, "running": 1, "skipped": 2, "failed": 3, "success": 4}


def _merge_step_results(left: dict, right: dict) -> dict:
    """Reducer: 合并并行 Worker 返回的 step_results。

    按步骤逐个择优：状态更"进展"的版本胜出（success > failed/skipped >
    running > pending）；同优先级时取 right（最新写入）。
    降级重试场景（failed → 重新派发 → success）最终态仍能正确胜出。
    """
    if not left:
        return dict(right)
    if not right:
        return dict(left)
    merged = dict(left)
    for sid, sr in right.items():
        cur = merged.get(sid)
        if cur is None:
            merged[sid] = sr
            continue
        cur_pri = _STATUS_PRIORITY.get(cur.get("status"), 0)
        new_pri = _STATUS_PRIORITY.get(sr.get("status"), 0)
        if new_pri >= cur_pri:
            merged[sid] = sr
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
    route_decision: dict                         # Router 决策（execution_mode + candidates + workflow_name）
    route_mode: str                              # Router 决策的模式字符串（direct/plan/workflow）
    resolved_params: dict | None                 # tool_selector（FC）解析出的执行参数；None=未解析，direct_executor 回退 question 透传
    executor_error: str | None                   # V2 executor 错误信息
    executor_mode: str | None                    # V2 executor 模式（direct/workflow）
    executor_workflow: str | None                # V2 executor 实际执行的 workflow 名
    workflow_result: dict | None                 # V2 workflow executor 结果快照
    guard_result: dict                           # Input Guard 判定快照（风险标注，供 Tool Guard 预留）
    # ⭐ 新增：可观测性 + 流程控制字段
    alerts: list[dict]                          # PlanAlert 列表（SSE 流展示）
    _supervisor_loop_count: int                 # Supervisor 调度轮次计数
    _plan_critiqued: bool                       # 是否经过了 Plan Critique
    _plan_changed: bool                         # Critique 是否修改了计划
    # 降级步骤集合：用 operator.or_ 作为 reducer（即 set union）
    # 节点必须返回**新** set（用 | 运算），禁止原地 .add() 修改 — 否则 reducer 看不到变化
    _degraded_steps: Annotated[set[str], operator.or_]
    # 请求级执行上下文（trace/session/user/流式 sink）— 随状态显式流动，
    # Send 派发时透传（scheduler.route_after_supervisor），节点入口经
    # trace_middleware 统一重新绑定（跨线程 ContextVar 不可继承）
    request_context: RequestContext
    # 请求身份平铺（P3 CS 断链修复）：request_context 是权威对象，但 CS 域
    # （cs_prefilter / cs_graph_node / experts/*）按惯例直接读 state 平铺键，
    # 缺这俩键会导致客服域恒为 anonymous（审计/授权/会话归属全部失真）。
    # 每轮由 make_initial_state 写入当前请求值，无跨轮残留问题。
    user_id: str
    department: str
    # 入口域提示平铺（2026-09-18 客服窗口锁域）：CSDrawer 每条消息带
    # domain_hint=customer_service，router_node 据此强制走 CS 预过滤
    # （跳过域检测门/灰度/其他域图 prefilter）；空串 = 全局入口，行为不变。
    # 每轮由 make_initial_state 写入当前请求值，无跨轮残留问题。
    domain_hint: str
    # 会话 ID 平铺（与 user_id/department 同一批断链修复，session_id 被漏）：
    # cs_prefilter / travel_prefilter / experts/* 按惯例读 state["session_id"]，
    # 缺此键导致客服域恒回退 "default" —— 转人工工单 conversation_id 全部
    # 挤在 "default" 一个桶里，坐席无法按真实会话认领（2026-09-17 实测）。
    # 每轮由 make_initial_state 写入当前请求值，无跨轮残留问题。
    session_id: str


class OrchestratorState(AgentState):
    """主图（编排层）状态 — 整张主 StateGraph 共用

    扩展 AgentState，供主图所有节点读写。其中 cs_context 等客服专有字段
    是主图挂载的客服扩展：route_mode="customer_service" 时由 cs_graph_node
    转换为客服子图输入，cs_context 承载认证用户、会话、转接状态、确认状态机等。
    客服子图内部状态为独立的 CSGraphState（backend/customer_service/graph_state.py）。
    """

    cs_context: CSContext
    cs_action_result: dict                # 业务操作执行结果
    cs_audit_entries: list[dict]          # 审计日志条目
