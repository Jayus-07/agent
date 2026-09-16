"""orchestration/checkpoint/task_executor.py — 任务型图执行器（Worker 侧核心）。

职责（Celery 与 LangGraph 之间的唯一衔接点）：
1. 恢复/构建 LangGraph 执行状态
2. 流式执行图，每个节点边界：
   - 更新 tasks.current_node（进度查询）
   - 追加 agent_checkpoints 历史（节点输出快照）
   - 广播 node_start / node_finish 事件（SSE 数据源）
   - 检查取消/暂停控制标志（Redis）
3. 节点请求用户输入（state["needs_user_input"]）→ WAITING_USER，checkpoint 已由
   PostgresSaver 自动保存，恢复时从下一节点续跑（不重启整图）
4. 最终答案提取 + 任务输出落库

与 chat 路径（GraphRunner）的关系：GraphRunner 面向交互式会话（多轮记忆 +
token 流式 + 打字机兜底）；本执行器面向后台任务（一次性、无会话记忆、
事件广播替代 SSE 直推）。二者共享 make_initial_state /
_fallback_summary_from_results，不复制语义。节点解析直接消费
stream_mode="updates" 的 {node: update} 形态，对任意图拓扑通用。
"""
from __future__ import annotations

import time
from typing import Any, Generator

from backend.models.task import TaskRecord, TaskStatus
from backend.orchestration.graph.builder import _parse_event
from backend.orchestration.graph.events import make_initial_state
from backend.orchestration.graph.runner import _fallback_summary_from_results
from backend.security.input_guard import GuardAction, get_input_guard
from backend.shared.logger import logger

# 任务执行器专用图单例（与 chat 的 MultiAgentSystem 隔离：强制挂 checkpointer，
# 不受 MAIN_GRAPH_CHECKPOINTER_ENABLED 全局开关影响——任务恢复依赖持久化，必须开）
_task_graph: Any = None
_task_graph_lock = None


class TaskCancelled(Exception):
    """任务被用户取消（Worker 捕获后置 CANCELLED，不重试）。"""


class TaskPaused(Exception):
    """任务被用户暂停（Worker 捕获后置 PAUSED，不重试）。"""


def build_task_checkpointer() -> Any:
    """任务模式 checkpointer：PostgresSaver 必须（多 Worker 共享 + 重启恢复）。

    不走 build_main_checkpointer()（受全局开关控制且含 MemorySaver 降级——
    MemorySaver 下重启即丢 checkpoint，任务恢复语义不成立，只能硬失败）。
    """
    import psycopg
    from backend.config.database import MEMORY_DB_CONFIG

    c = MEMORY_DB_CONFIG
    dsn = (f"postgresql://{c['user']}:{c['password']}"
           f"@{c['host']}:{c['port']}/{c['dbname']}")
    conn = psycopg.Connection.connect(dsn, autocommit=True)
    from langgraph.checkpoint.postgres import PostgresSaver

    checkpointer = PostgresSaver(conn)
    checkpointer.setup()  # 幂等建表
    return checkpointer


def build_task_graph() -> Any:
    """构建（惰性单例）任务执行专用主图：builder.build_graph + 强制 checkpointer。"""
    global _task_graph
    if _task_graph is not None:
        return _task_graph
    import threading

    global _task_graph_lock
    if _task_graph_lock is None:
        _task_graph_lock = threading.Lock()
    with _task_graph_lock:
        if _task_graph is not None:
            return _task_graph
        from backend.orchestration.graph.builder import build_graph

        _task_graph = build_graph(checkpointer=build_task_checkpointer())
        logger.info("[TaskExecutor] task graph built with PostgresSaver checkpointer")
        return _task_graph


class TaskGraphExecutor:
    """执行一个任务型 Agent 图，节点边界写 checkpoint 并广播事件。"""

    def __init__(self, graph: Any | None = None, *,
                 poll_control_flags: bool = True):
        self._graph = graph if graph is not None else build_task_graph()
        self._poll_flags = poll_control_flags

    # ── 控制标志（测试可注入 stub 覆盖）────────────────────
    def _cancelled(self, task_id: str) -> bool:
        if not self._poll_flags:
            return False
        from backend.tasks.task_manager import is_cancel_requested

        return is_cancel_requested(task_id)

    def _paused(self, task_id: str) -> bool:
        if not self._poll_flags:
            return False
        from backend.tasks.task_manager import is_pause_requested

        return is_pause_requested(task_id)

    # ── 事件广播 ──────────────────────────────────────────
    @staticmethod
    def _publish(task_id: str, event: str, **payload) -> None:
        try:
            from backend.tasks.task_manager import publish_event

            publish_event(task_id, event, **payload)
        except Exception:
            logger.debug("[TaskExecutor] publish failed: %s/%s",
                         task_id, event, exc_info=True)

    # ── 主入口 ────────────────────────────────────────────
    def execute(self, record: TaskRecord) -> dict:
        """执行/恢复任务。返回任务输出 dict（Worker 落 tasks.output）。

        恢复判定：record.status ∈ {WAITING_USER, PAUSED, FAILED(重试)} 且
        thread_id 存在 → payload=None，LangGraph 从最近 checkpoint 续跑。
        """
        from backend.config import MAIN_GRAPH_RECURSION_LIMIT
        from backend.services import task_service

        task_id = record.id
        query = (record.input or {}).get("query", "")
        thread_id = record.thread_id or f"task-{task_id}"

        # checkpoint 线程：是否有节点级历史（区分"从未开跑"与"跑到一半"）
        has_history = task_service.list_checkpoints(task_id, record.user_id) != []
        # 恢复判定：有 checkpoint 历史 + 非 RUNNING 执行中。
        # 覆盖三种场景：WAITING_USER/PAUSED 恢复（resume API 已置回 PENDING）、
        # Celery 失败重试（FAILED）、Worker 宕机回队（acks_late 重投）。
        # 全新任务 thread_id 在库但无历史 → 走全新执行。
        resume = bool(record.thread_id) and has_history \
            and record.status != TaskStatus.RUNNING
        # 用户输入注入（resume API 落在 agent_checkpoints 的特殊行）
        pending_user_input = self._pop_user_input(task_id) if resume else ""

        config = {
            "recursion_limit": MAIN_GRAPH_RECURSION_LIMIT,
            "configurable": {"thread_id": thread_id},
        }

        if resume:
            if pending_user_input:
                # 用户输入合并进暂停时的状态（LangGraph update_state 产生新
                # checkpoint，下一跳节点立即可读 state["user_input"]）
                self._graph.update_state(
                    config, {"user_input": pending_user_input})
                self._publish(task_id, "user_input_injected",
                              node=record.current_node)
            payload: Any = None  # LangGraph resume 语义：None 输入 = 从 checkpoint 继续
            task_service.update_status(
                task_id, TaskStatus.RUNNING,
                progress=f"从 checkpoint 恢复（节点: {record.current_node}）")
        else:
            guard = get_input_guard().guard(query or "", session_id=task_id)
            if guard.action == GuardAction.BLOCK:
                message = guard.message or "输入被安全策略拦截。"
                task_service.update_status(
                    task_id, TaskStatus.FAILED,
                    error_message=message, progress="input_guard 拦截")
                self._publish(task_id, "failed", message=message)
                return {"answer": message, "step_results": {}, "blocked": True}
            payload = make_initial_state(
                query, task_id, kb_id=(record.input or {}).get("kb_id", "default"),
                messages=[], guard_result=guard.model_dump(mode="json"),
                user_id=record.user_id, department=(record.input or {}).get("department", ""),
            )
            task_service.update_status(
                task_id, TaskStatus.RUNNING, progress="开始执行")

        # ── 流式执行：节点边界 = checkpoint 边界 = 控制检查点 ──
        # stream_mode="updates" 事件形态：{node_name: update}（Send 并行分支
        # 单事件含多节点）。直接按键解析，不用主图 _parse_event 白名单——
        # 执行器必须对任意 graph_name 通用（stub 图/域图/未来新增图）。
        final_answer = ""
        step_results: dict = {}
        last_node = ""
        for event in self._stream(payload, config):
            if self._cancelled(task_id):
                raise TaskCancelled(task_id)
            if self._paused(task_id):
                raise TaskPaused(task_id)

            if not isinstance(event, dict):
                continue
            if "__interrupt__" in event:
                # LangGraph 原生 interrupt 语义 → 等待用户输入
                self._enter_waiting_user(
                    task_id, last_node or record.current_node or "graph",
                    "LangGraph interrupt：等待用户输入")
                return {"status": TaskStatus.WAITING_USER.value,
                        "interrupted": True}

            for node_name, node_output in event.items():
                if node_name.startswith("__"):
                    continue  # LangGraph 控制键（__end__ 等）

                last_node = node_name
                node_output = node_output or {}
                task_service.update_progress(task_id, node_name,
                                             progress=f"节点 {node_name} 完成")
                task_service.append_checkpoint(task_id, node_name, node_output)
                self._publish(task_id, "node_finish", node=node_name)

                if node_output.get("needs_user_input"):
                    # 节点主动请求用户输入：状态（含 needs_user_input）已被
                    # PostgresSaver 保存为最近 checkpoint，恢复时该节点不重跑
                    ask = node_output.get("needs_user_input")
                    self._enter_waiting_user(
                        task_id, node_name,
                        ask.get("message", "") if isinstance(ask, dict) else str(ask))
                    return {"status": TaskStatus.WAITING_USER.value,
                            "waiting_node": node_name}

                ans = node_output.get("final_answer", "")
                if ans:
                    final_answer = ans if isinstance(ans, str) else str(ans)
                if node_output.get("step_results"):
                    step_results.update(node_output.get("step_results", {}))

        # ── 正常完成 ──
        if not final_answer:
            final_answer = _fallback_summary_from_results(step_results)
        output = {"answer": final_answer, "step_results": step_results}
        task_service.update_status(
            task_id, TaskStatus.SUCCESS, progress="执行完成", output=output)
        self._publish(task_id, "completed", node="reporter")
        return output

    # ── 内部 ─────────────────────────────────────────────
    def _stream(self, payload: Any, config: dict) -> Generator[dict, None, None]:
        """graph.stream 包装：区分首次执行 / checkpoint 恢复。"""
        try:
            yield from self._graph.stream(payload, config=config,
                                          stream_mode="updates")
        except Exception:
            raise

    def _pop_user_input(self, task_id: str) -> str:
        """取回 resume API 注入的用户输入（读取即消费）。"""
        from backend.services import task_service

        return task_service.get_user_input(task_id)

    @staticmethod
    def _enter_waiting_user(task_id: str, node: str, message: str) -> None:
        from backend.services import task_service

        task_service.update_status(
            task_id, TaskStatus.WAITING_USER,
            progress=message or f"节点 {node} 等待用户输入")
        TaskGraphExecutor._publish(task_id, "waiting_user", node=node,
                                   message=message)
        logger.info("[TaskExecutor] task %s WAITING_USER (node=%s)", task_id, node)
