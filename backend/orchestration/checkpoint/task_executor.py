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

import threading
import time
from typing import Any, Generator

from backend.models.task import TaskLeaseLost, TaskRecord, TaskStatus
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

    连接载体用 psycopg_pool 连接池（langgraph _internal.get_connection 原生
    支持 ConnectionPool 类型）：checkout 时探活、死连接自动换新。修复缺陷
    （F3 演练 P3 实锤，2026-09-25）：此前的单条裸 psycopg 连接在 PG 重启后
    死亡且永不重建，worker 内所有后续任务一律 "the connection is closed"
    失败直至进程重启。
    """
    from backend.config.database import MEMORY_DB_CONFIG

    c = MEMORY_DB_CONFIG
    from psycopg_pool import ConnectionPool

    pool = ConnectionPool(
        kwargs={
            "host": c["host"], "port": c["port"], "dbname": c["dbname"],
            "user": c["user"], "password": c["password"],
            "autocommit": True,
        },
        min_size=1, max_size=4, open=True,
        check=ConnectionPool.check_connection,  # 借出前探活，坏连接自动重建
    )
    from langgraph.checkpoint.postgres import PostgresSaver

    checkpointer = PostgresSaver(pool)
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
        # 执行期 fencing 上下文（execute 时绑定；直调/eager 测试可为空）
        self._execution_id = ""
        self._heartbeat: Any = None
        self._task_id = ""

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

    # ── 执行期 fencing（Phase2 Step1）─────────────────────
    def _guard_lease(self) -> None:
        """租约守卫：执行器绑定 execution_id 后，丢失即抛 TaskLeaseLost。

        检查顺序：心跳线程的 lost 标志（免 DB 往返）→ DB 权威校验。
        未绑定 execution_id（legacy 调用方/eager 测试直调）不设防。
        """
        if not self._execution_id:
            return
        if self._heartbeat is not None and self._heartbeat.lost:
            raise TaskLeaseLost(self._task_id, self._execution_id)
        from backend.services import task_service

        if not task_service.check_lease_active(self._task_id,
                                               self._execution_id):
            raise TaskLeaseLost(self._task_id, self._execution_id)

    # ── 事件广播 ──────────────────────────────────────────
    @staticmethod
    def _publish(task_id: str, event: str, **payload) -> None:
        try:
            from backend.tasks.task_manager import publish_event

            publish_event(task_id, event, **payload)
        except Exception:
            logger.debug("[TaskExecutor] publish failed: %s/%s",
                         task_id, event, exc_info=True)

    def _publish_fenced(self, task_id: str, event: str, **payload) -> None:
        """fencing 事件广播：租约丢失的旧 Worker 不得再发 runtime event。"""
        if self._execution_id:
            try:
                self._guard_lease()
            except TaskLeaseLost:
                logger.warning(
                    "[TaskExecutor] %s 租约丢失，丢弃事件 %s", task_id, event)
                return
        self._publish(task_id, event, **payload)

    # ── 执行时授权解析（STOP D P0，2026-09-23）────────────
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
        """执行/恢复任务（薄壳）：绑定租约上下文 + 任务级 trace 生命周期。

        Phase2 Step1：execution_id（租约认领返回值）传入后，本执行器的全部
        TaskState/checkpoint/event 写走 fencing——租约被接管的旧 Worker 在
        下一次写点被 TaskLeaseLost 拒绝并退出（heartbeat 用于节点边界零成本
        预检，None 时退化为逐次 DB 校验）。

        Phase2-F：execution_id 非空（真实 worker 执行）时绑定任务级 trace
        （session_id=thread_id 携带 task 关联；tags 记录 task_id/execution_id/
        queue），任何出口（SUCCESS/WAITING_USER/授权拒绝/租约丢失/异常）都在
        finally 收口——trace 观测 best-effort，失败绝不影响任务执行。
        """
        task_id = record.id
        self._task_id = task_id
        self._execution_id = execution_id
        self._heartbeat = heartbeat

        trace_record = self._start_task_trace(record) if execution_id else None
        try:
            result = self._execute_inner(record, execution_id=execution_id,
                                         heartbeat=heartbeat)
        except BaseException as exc:  # noqa: BLE001 — 只读不吞，转给 finally 收口
            self._finish_task_trace(trace_record, record, result=None, exc=exc)
            raise
        self._finish_task_trace(trace_record, record, result=result, exc=None)
        return result

    def _start_task_trace(self, record: TaskRecord):
        """任务级 trace 绑定（Phase2-F，best-effort）：None = 观测不可用。

        关联模型（§三十八：可关联优先于单一 trace 形状）：
        - session_id = thread_id（含 task_id，跨 retry/recovery/resume 不变）
        - tags["task_id"]/tags["execution_id"] = 本 execution 的归属
        - tasks.trace_id 回填 = 任务行 ↔ 最新执行 trace 双向可查
        - 每次 execution 一个 trace（旧 execution 在自身 finally 收口，
          不制造跨 takeover 的 dangling span）
        """
        try:
            import time as _time

            from backend.observability.tracer import (
                WorkflowKind,
                trace_collector,
            )

            self._trace_t0 = _time.monotonic()
            query = (record.input or {}).get("query", "")
            trace = trace_collector.start(
                str(query)[:200],
                session_id=record.thread_id or f"task-{record.id}",
                workflow_name=record.graph_name,
                workflow_kind=WorkflowKind.LG_WORKFLOW.value)
            trace.tags.update({
                "task_id": record.id,
                "execution_id": self._execution_id or "",
                "queue": record.queue or "",
            })
            trace_collector.start_span(
                "root", parent_id=None, name="异步任务执行", type="workflow",
                input={
                    "task_id": record.id,
                    "execution_id": self._execution_id,
                    "thread_id": record.thread_id or f"task-{record.id}",
                    "retry_count": record.retry_count,
                    "recovery_count": record.recovery_count,
                })
            from backend.services import task_service

            task_service.set_trace_id(record.id, trace.id)
            return trace
        except Exception:  # noqa: BLE001 — 观测失败不影响任务（§三十四）
            logger.debug("[TaskExecutor] 任务 trace 绑定失败（best-effort）",
                         exc_info=True)
            return None

    def _finish_task_trace(self, trace_record, record: TaskRecord, *,
                           result: dict | None,
                           exc: BaseException | None) -> None:
        if trace_record is None:
            return
        try:
            import time as _time

            from backend.observability.tracer import trace_collector

            blocked = bool(result and result.get("blocked"))
            if exc is not None or blocked:
                # 失败出口：root span 显式 error，顶层状态聚合不再误标 success
                trace_collector.end_open_span("root", status="error")
            if exc is not None:
                answer = f"执行异常: {exc}"[:200]
            elif result and result.get("status") == TaskStatus.WAITING_USER.value:
                answer = "等待用户输入（interrupt）"
            elif result:
                answer = str(result.get("answer", ""))[:200]
            else:
                answer = ""
            trace_collector.finish(
                trace_record, answer,
                total_ms=int((_time.monotonic()
                              - getattr(self, "_trace_t0", _time.monotonic()))
                             * 1000),
                model="")
        except Exception:  # noqa: BLE001 — 收口失败只记日志
            logger.debug("[TaskExecutor] 任务 trace 收口失败（best-effort）",
                         exc_info=True)

    def _execute_inner(self, record: TaskRecord, *, execution_id: str = "",
                       heartbeat: Any = None) -> dict:
        """执行主体（原 execute 逻辑；trace/租约上下文已由薄壳绑定）。"""
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

        # ── 执行时授权解析（STOP D P0）：resume 与全新执行都重新解析 ──
        # 失败 fail-closed 终态，绝不以无授权上下文进图（SQLSkill 等授权
        # 消费方依赖 state["request_context"]，缺省 = 授权未启用旧行为）。
        from backend.security.task_authorization import TaskAuthorizationDenied

        try:
            request_ctx = self._build_request_context(record)
        except TaskAuthorizationDenied as exc:
            logger.warning("[TaskExecutor] %s 授权解析失败: %s", task_id, exc)
            # Phase2-F：授权拒绝观测（低基数 label，best-effort）
            try:
                from backend.observability.metrics import (
                    task_authorization_denied_total,
                )

                task_authorization_denied_total.labels(
                    workflow=record.graph_name).inc()
            except Exception:  # noqa: BLE001 — 观测失败不影响 fail-closed 语义
                pass
            task_service.update_status(
                task_id, TaskStatus.FAILED,
                error_message=f"授权解析失败: {exc}",
                progress="执行时授权校验未通过（fail-closed）",
                execution_id=execution_id or None)
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
                self._publish_fenced(task_id, "user_input_injected",
                                     node=record.current_node)
            payload: Any = None  # LangGraph resume 语义：None 输入 = 从 checkpoint 继续
            task_service.update_status(
                task_id, TaskStatus.RUNNING,
                progress=f"从 checkpoint 恢复（节点: {record.current_node}）",
                execution_id=execution_id or None)
        else:
            guard = get_input_guard().guard(query or "", session_id=task_id)
            if guard.action == GuardAction.BLOCK:
                message = guard.message or "输入被安全策略拦截。"
                task_service.update_status(
                    task_id, TaskStatus.FAILED,
                    error_message=message, progress="input_guard 拦截",
                    execution_id=execution_id or None)
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
                task_id, TaskStatus.RUNNING, progress="开始执行",
                execution_id=execution_id or None)

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
                    "LangGraph interrupt：等待用户输入",
                    execution_id=execution_id or None)
                return {"status": TaskStatus.WAITING_USER.value,
                        "interrupted": True}

            for node_name, node_output in event.items():
                if node_name.startswith("__"):
                    continue  # LangGraph 控制键（__end__ 等）

                last_node = node_name
                node_output = node_output or {}
                # fencing 写：租约被接管的旧 Worker 在此被拒（返回 False → 抛出）
                if not task_service.update_progress(
                        task_id, node_name,
                        progress=f"节点 {node_name} 完成",
                        checkpoint_id=self._latest_checkpoint_id(config),
                        execution_id=execution_id or None):
                    raise TaskLeaseLost(task_id, execution_id)
                if not task_service.append_checkpoint(
                        task_id, node_name, node_output,
                        execution_id=execution_id or None):
                    raise TaskLeaseLost(task_id, execution_id)
                self._publish_fenced(task_id, "node_finish", node=node_name,
                                     progress=f"节点 {node_name} 完成",
                                     status=TaskStatus.RUNNING.value)

                if node_output.get("needs_user_input"):
                    # 节点主动请求用户输入：状态（含 needs_user_input）已被
                    # PostgresSaver 保存为最近 checkpoint，恢复时该节点不重跑
                    ask = node_output.get("needs_user_input")
                    self._enter_waiting_user(
                        task_id, node_name,
                        ask.get("message", "") if isinstance(ask, dict) else str(ask),
                        execution_id=execution_id or None)
                    return {"status": TaskStatus.WAITING_USER.value,
                            "waiting_node": node_name}

                ans = node_output.get("final_answer", "")
                if ans:
                    final_answer = ans if isinstance(ans, str) else str(ans)
                if node_output.get("step_results"):
                    step_results.update(node_output.get("step_results", {}))

            # 控制检查：当前节点已完成落库，下一节点尚未开始
            # （Phase2 Step1：租约丢失也在此退出，不开始下一节点）
            self._guard_lease()
            if self._cancelled(task_id):
                raise TaskCancelled(task_id)
            if self._paused(task_id):
                raise TaskPaused(task_id)

        # ── 正常完成 ──
        if not final_answer:
            final_answer = _fallback_summary_from_results(step_results)
        output = {"answer": final_answer, "step_results": step_results}
        task_service.update_status(
            task_id, TaskStatus.SUCCESS, error_message="", progress="执行完成",
            output=output, execution_id=execution_id or None)
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
    def _enter_waiting_user(task_id: str, node: str, message: str,
                            *, execution_id: str | None = None) -> None:
        """RUNNING → WAITING_USER；execution_id 传入时为 fencing 写。"""
        from backend.models.task import TaskLeaseLost
        from backend.services import task_service

        try:
            task_service.update_status(
                task_id, TaskStatus.WAITING_USER,
                progress=message or f"节点 {node} 等待用户输入",
                execution_id=execution_id)
        except TaskLeaseLost:
            logger.warning("[TaskExecutor] %s 租约丢失，WAITING_USER 不落库"
                           "（新 owner 负责）", task_id)
            return
        TaskGraphExecutor._publish(task_id, "waiting_user", node=node,
                                   message=message)
        logger.info("[TaskExecutor] task %s WAITING_USER (node=%s)", task_id, node)
