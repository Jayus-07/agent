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

    # ── 执行时授权解析（SQL 生产收口 STOP D P0，2026-09-23）────
    def _build_request_context(self, record: TaskRecord) -> dict:
        """任务持久化 actor → 当前权威授权 → checkpoint_safe dict。

        身份固定：user_id/tenant_id 只来自 tasks 表记录（创建时网关验签
        身份落列），不来自 query/tool args/resume body。
        授权动态：每次执行/恢复都重新解析 auth.users 当前
        role/status/tenant——撤权、禁用、移出租户后即使任务排队数小时
        也立即失效，不信任创建时快照，也不信任 checkpoint 里的旧权限。
        """
        from backend.core.request_context import RequestContext
        from backend.security.task_authorization import resolve_task_authorization

        auth_ctx = resolve_task_authorization(record.user_id, record.tenant_id)
        principal = auth_ctx.principal
        req_ctx = RequestContext(
            session_id=record.id,
            user_id=principal.user_id,
            tenant_id=principal.tenant_id,
            kb_id=(record.input or {}).get("kb_id", "default"),
            department=principal.department,
            roles=principal.roles,
            # data_scope 由授权层按当前 roles 折算（单一来源），随状态透传
            data_scope=auth_ctx.data_scope or "",
            subject_type=principal.subject_type,
        )
        # 任务图强制 PostgresSaver：只能放可序列化 dict 形态（同 runner 主图
        # checkpointer 感知分支），节点入口经 get_context_from_state 还原
        return req_ctx.checkpoint_safe()

    # ── 主入口 ────────────────────────────────────────────
    def execute(self, record: TaskRecord, *,
                execution_id: str = "", heartbeat: Any = None) -> dict:
        """执行/恢复任务。返回任务输出 dict（Worker 落 tasks.output）。

        恢复判定：record.status ∈ {WAITING_USER, PAUSED, FAILED(重试)} 且
        thread_id 存在 → payload=None，LangGraph 从最近 checkpoint 续跑。

        execution_id/heartbeat（Phase2 fencing 接口位）：本提交只对齐
        调用方签名（agent_tasks 已按 kwargs 调用），fencing 校验逻辑
        由 Phase2 在此接口位上继续实现，本提交不含。
        """
        from backend.config import MAIN_GRAPH_RECURSION_LIMIT
        from backend.services import task_service

        task_id = record.id
        query = (record.input or {}).get("query", "")
        thread_id = record.thread_id or f"task-{task_id}"

        # 终态短路（Phase1 Step2 纵深防御）：SUCCESS/FAILED/CANCELLED 不可执行，
        # 状态机会拒绝 SUCCESS→RUNNING 写入，此处更早拦截（重试路径在 impl
        # 已先显式回 PENDING，正常链路不会到达这里）。
        if record.status.is_terminal():
            logger.info("[TaskExecutor] %s terminal (%s), skip execution",
                        task_id, record.status.value)
            return {"answer": "", "step_results": {},
                    "skipped_terminal": record.status.value}

        # checkpoint 线程：是否有节点级历史（区分"从未开跑"与"跑到一半"）
        has_history = task_service.list_checkpoints(task_id, record.user_id) != []
        # 恢复判定（Phase1 Step2）：只看 thread_id + checkpoint 历史是否存在，
        # 不看 record.status——Worker 硬杀后状态停在 RUNNING（无 _fail 落库），
        # acks_late 重投/租约接管时若按 status!=RUNNING 判定会走全新执行分支，
        # 已完成节点整图重跑。有历史 = 从最近 checkpoint 续跑（覆盖三种场景：
        # WAITING_USER/PAUSED 恢复、Celery 失败重试（impl 已回 PENDING）、
        # Worker 宕机接管）。全新任务 thread_id 在库但无历史 → 走全新执行。
        resume = bool(record.thread_id) and has_history
        # 用户输入注入（resume API 落在 agent_checkpoints 的特殊行）
        pending_user_input = self._pop_user_input(task_id) if resume else ""

        config = {
            "recursion_limit": MAIN_GRAPH_RECURSION_LIMIT,
            "configurable": {"thread_id": thread_id},
        }

        # ── 执行时授权解析（SQL 生产收口 STOP D P0）：resume 与全新执行
        # 都重新解析。失败 fail-closed 终态，绝不以无授权上下文进图
        # （SQLSkill 等授权消费方依赖 state["request_context"]，缺省 =
        # 授权未启用旧行为）。
        from backend.security.task_authorization import TaskAuthorizationDenied

        try:
            request_ctx = self._build_request_context(record)
        except TaskAuthorizationDenied as exc:
            logger.warning("[TaskExecutor] %s 授权解析失败: %s", task_id, exc)
            task_service.update_status(
                task_id, TaskStatus.FAILED,
                error_message=f"授权解析失败: {exc}",
                progress="执行时授权校验未通过（fail-closed）")
            self._publish(task_id, "failed", message=str(exc))
            return {"answer": "任务执行身份授权校验未通过，任务终止。",
                    "step_results": {}, "blocked": True}

        if resume:
            # 授权刷新覆盖 checkpoint 旧权限：身份归属固定（actor 不变），
            # 授权权限动态（以本次解析为准）。与 user_input 注入同一
            # update_state 通道（产生新 checkpoint，下一跳节点立即可读）。
            state_patch: dict = {"request_context": request_ctx}
            if pending_user_input:
                state_patch["user_input"] = pending_user_input
            self._graph.update_state(config, state_patch)
            if pending_user_input:
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
                user_id=record.user_id,
                # department 用执行时解析的组织属性权威值（auth.users.dept），
                # 不取创建时可伪造的 input.department
                department=request_ctx.get("department", ""),
            )
            payload["request_context"] = request_ctx
            task_service.update_status(
                task_id, TaskStatus.RUNNING, progress="开始执行")

        # ── 流式执行：节点边界 = checkpoint 边界 = 控制检查点 ──
        # stream_mode="updates" 事件形态：{node_name: update}（Send 并行分支
        # 单事件含多节点）。直接按键解析，不用主图 _parse_event 白名单——
        # 执行器必须对任意 graph_name 通用（stub 图/域图/未来新增图）。
        # 取消/暂停检查点在「当前节点边界落库之后、下一节点调度之前」：
        # 语义 = 不强杀原子节点，已完成节点的 checkpoint/进度痕迹完整保留
        # （Phase1 Step4：pause 后 checkpoint 必须指向最后成功节点）。
        final_answer = ""
        step_results: dict = {}
        last_node = ""
        for event in self._stream(payload, config):
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
                task_service.update_progress(
                    task_id, node_name,
                    progress=f"节点 {node_name} 完成",
                    checkpoint_id=self._latest_checkpoint_id(config))
                task_service.append_checkpoint(task_id, node_name, node_output)
                self._publish(task_id, "node_finish", node=node_name,
                              progress=f"节点 {node_name} 完成",
                              status=TaskStatus.RUNNING.value)

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

            # 控制检查：当前节点已完成落库，下一节点尚未开始
            if self._cancelled(task_id):
                raise TaskCancelled(task_id)
            if self._paused(task_id):
                raise TaskPaused(task_id)

        # ── 正常完成 ──
        if not final_answer:
            final_answer = _fallback_summary_from_results(step_results)
        output = {"answer": final_answer, "step_results": step_results}
        task_service.update_status(
            task_id, TaskStatus.SUCCESS, progress="执行完成", output=output)
        self._publish(task_id, "completed", node="reporter")
        return output

    # ── 内部 ─────────────────────────────────────────────
    def _latest_checkpoint_id(self, config: dict) -> str | None:
        """读最近一次 LangGraph checkpoint 的真实 id（Phase1 Step2：TaskState
        checkpoint_id 不再恒写 thread_id 占位，恢复定位可对账）。读失败不
        阻断执行——update_progress 会回落为 thread_id 旧口径。"""
        try:
            snap = self._graph.get_state(config)
        except Exception:
            logger.debug("[TaskExecutor] get_state failed", exc_info=True)
            return None
        if snap is None or not getattr(snap, "config", None):
            return None
        return snap.config.get("configurable", {}).get("checkpoint_id")

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
