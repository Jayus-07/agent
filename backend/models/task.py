"""models/task.py — 异步 Agent 任务领域模型。

纯数据定义，不依赖 Celery / LangGraph / DB 驱动（保持层级隔离：
Celery 与 LangGraph 都只消费这里的枚举与 dataclass）。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum


class IllegalTaskTransition(Exception):
    """非法状态跳转（状态机拒绝；携带当前态与目标态便于定位调用方）。"""

    def __init__(self, task_id: str, current: "TaskStatus", target: "TaskStatus"):
        self.task_id = task_id
        self.current = current
        self.target = target
        super().__init__(
            f"非法状态跳转 task={task_id}: {current.value} → {target.value}")


class TaskLeaseLost(Exception):
    """执行租约已丢失（被接管/过期），fencing 拒绝后续写入。

    Worker 捕获后必须立即停止写 TaskState/checkpoint/event 并退出执行，
    不得落任何状态——任务的状态权威已归属新 execution 的 owner。
    """

    def __init__(self, task_id: str, execution_id: str = ""):
        self.task_id = task_id
        self.execution_id = execution_id
        super().__init__(f"执行租约已丢失 task={task_id} execution={execution_id[:8]}")


class TaskStatus(str, Enum):
    """任务生命周期状态机。

    PENDING → RUNNING → SUCCESS / FAILED / CANCELLED
                       ↘ WAITING_USER → (resume) → RUNNING
                       ↘ PAUSED      → (resume) → RUNNING
    FAILED --(显式重试/requeue)--> PENDING → RUNNING（从最近 checkpoint 续跑）

    合法跳转白名单见 _LEGAL_TRANSITIONS；终态 SUCCESS/CANCELLED 完全封闭，
    FAILED 仅允许自转换（错误信息刷新）与显式回 PENDING（重试）。
    """

    PENDING = "PENDING"
    RUNNING = "RUNNING"
    WAITING_USER = "WAITING_USER"
    PAUSED = "PAUSED"
    SUCCESS = "SUCCESS"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"

    @classmethod
    def terminal(cls) -> tuple["TaskStatus", ...]:
        """终态集合：终态任务不再被 Worker 拾取。"""
        return (cls.SUCCESS, cls.FAILED, cls.CANCELLED)

    def is_terminal(self) -> bool:
        return self in self.terminal()

    @classmethod
    def resumable(cls) -> tuple["TaskStatus", ...]:
        """可恢复（resume API 允许）的状态。"""
        return (cls.WAITING_USER, cls.PAUSED, cls.FAILED)

    @classmethod
    def _legal_transitions(cls) -> dict["TaskStatus", frozenset["TaskStatus"]]:
        """合法跳转白名单（Phase1 Task Runtime 验收口径）。

        规格必允许：PENDING→RUNNING、RUNNING→PAUSED、PAUSED→RUNNING、
        RUNNING→SUCCESS、RUNNING→FAILED、RUNNING/PAUSED→CANCELLED。
        规格必禁止：SUCCESS/FAILED/CANCELLED → RUNNING（终态不复活）；
        FAILED 恢复执行必须先显式回 PENDING（重试动作可审计）。
        自转换（同态重写进度/错误字段）与以下系统跳转一并登记：
        - PENDING→PAUSED：队列内暂停（Worker 租约只认 PENDING，天然不双跑）
        - PENDING→FAILED/CANCELLED：入队失败落终态 / 队列内取消（未开跑）
        - PAUSED/WAITING_USER→PENDING：resume 的回队标记（Worker 租约认领后进 RUNNING）
        - WAITING_USER→CANCELLED：等人态取消
        例外通道（不经本表、走原生 SQL，登记于 Phase1 报告）：
        - try_acquire_lease 的 stale-RUNNING 接管（RUNNING→RUNNING 所有权转移）
        - reap_zombie_running 的 RUNNING→FAILED/CANCELLED（条件 UPDATE 收尸）
        - claim_stale_for_recovery 的 RUNNING→PENDING（Phase2 Step1：租约过期
          自动恢复回队，原子认领幂等，recovery_count 计数）
        - revert_recovery_claim 的 PENDING→RUNNING（恢复重投失败回滚，租约
          字段维持过期值，下一次 sweep 重新处理）
        - fail_recovered_task 的 RUNNING→FAILED（自动恢复次数耗尽的终态收口）
        """
        return {
            cls.PENDING: frozenset({cls.PENDING, cls.RUNNING, cls.PAUSED,
                                    cls.FAILED, cls.CANCELLED}),
            cls.RUNNING: frozenset({cls.RUNNING, cls.PAUSED, cls.WAITING_USER,
                                    cls.SUCCESS, cls.FAILED, cls.CANCELLED}),
            cls.PAUSED: frozenset({cls.PAUSED, cls.RUNNING, cls.PENDING,
                                    cls.CANCELLED}),
            cls.WAITING_USER: frozenset({cls.WAITING_USER, cls.PENDING,
                                          cls.CANCELLED}),
            cls.FAILED: frozenset({cls.FAILED, cls.PENDING}),
            cls.SUCCESS: frozenset(),
            cls.CANCELLED: frozenset(),
        }

    def can_transition_to(self, target: "TaskStatus") -> bool:
        """target 是否为从当前态出发的合法跳转。"""
        return target in self._legal_transitions()[self]


@dataclass
class TaskRecord:
    """agent_memory.tasks 的一行记录。"""

    id: str
    user_id: str
    tenant_id: str = "default"
    graph_name: str = "main"
    conversation_id: str = ""        # 关联会话（客服窗口/聊天线程），可选
    status: TaskStatus = TaskStatus.PENDING
    input: dict = field(default_factory=dict)
    output: dict | None = None
    checkpoint_id: str = ""          # 最近一次 LangGraph checkpoint 的 thread_id
    thread_id: str = ""              # LangGraph checkpoint 线程标识（恢复定位）
    current_node: str = ""
    progress: str = ""
    error_message: str = ""
    error_type: str = ""
    traceback: str = ""
    retry_count: int = 0
    max_retries: int = 3
    recovery_count: int = 0         # 自动恢复次数（Phase2 Step1：租约过期重投计数）
    retry_exhausted: bool = False   # 重试耗尽（Phase2 Step2：FAILED 终态细分）
    duration_ms: int | None = None
    queue: str = "agent"
    worker: str = ""
    execution_id: str = ""           # 当前执行租约 id（Phase1 Step3：max executor=1）
    trace_id: str = ""
    biz_type: str = ""
    biz_id: str = ""
    parent_task_id: str = ""
    celery_task_id: str = ""
    created_at: datetime | None = None
    queued_at: datetime | None = None
    started_at: datetime | None = None
    finished_at: datetime | None = None
    updated_at: datetime | None = None

    @classmethod
    def from_row(cls, row: dict) -> "TaskRecord":
        """DB 行 → TaskRecord（status 字符串安全回落为 PENDING）。"""
        try:
            status = TaskStatus(row.get("status") or TaskStatus.PENDING.value)
        except ValueError:
            status = TaskStatus.PENDING
        return cls(
            id=str(row["id"]),
            user_id=row.get("user_id") or "",
            tenant_id=row.get("tenant_id") or "default",
            graph_name=row.get("graph_name") or "main",
            conversation_id=row.get("conversation_id") or "",
            status=status,
            input=row.get("input") or {},
            output=row.get("output"),
            checkpoint_id=row.get("checkpoint_id") or "",
            thread_id=row.get("thread_id") or "",
            current_node=row.get("current_node") or "",
            progress=row.get("progress") or "",
            error_message=row.get("error_message") or "",
            error_type=row.get("error_type") or "",
            traceback=row.get("traceback") or "",
            retry_count=int(row.get("retry_count") or 0),
            max_retries=int(row.get("max_retries") or 3),
            recovery_count=int(row.get("recovery_count") or 0),
            retry_exhausted=bool(row.get("retry_exhausted") or False),
            duration_ms=int(row["duration_ms"]) if row.get("duration_ms") is not None else None,
            queue=row.get("queue") or "agent",
            worker=row.get("worker") or "",
            execution_id=row.get("execution_id") or "",
            trace_id=row.get("trace_id") or "",
            biz_type=row.get("biz_type") or "",
            biz_id=row.get("biz_id") or "",
            parent_task_id=str(row["parent_task_id"]) if row.get("parent_task_id") else "",
            celery_task_id=row.get("celery_task_id") or "",
            created_at=row.get("created_at"),
            queued_at=row.get("queued_at"),
            started_at=row.get("started_at"),
            finished_at=row.get("finished_at"),
            updated_at=row.get("updated_at"),
        )

    @property
    def workflow(self) -> str:
        """Phase1 TaskState 口径别名：workflow = graph_name（单一事实源，不另存列）。"""
        return self.graph_name

    def to_public_dict(self) -> dict:
        """API 对外形态（GET /tasks/{id} 的主体）。"""
        return {
            "task_id": self.id,
            "status": self.status.value,
            "progress": self.progress,
            "current_node": self.current_node,
            "result": self.output,
            "error_message": self.error_message,
            "error_type": self.error_type,
            "error_code": self.error_type,   # Phase1 口径别名（同 error_type）
            "retry_count": self.retry_count,
            "max_retries": self.max_retries,
            "recovery_count": self.recovery_count,
            "retry_exhausted": self.retry_exhausted,
            "duration_ms": self.duration_ms,
            "queue": self.queue,
            "worker": self.worker,
            "trace_id": self.trace_id,
            "biz_type": self.biz_type,
            "biz_id": self.biz_id,
            "graph_name": self.graph_name,
            "workflow": self.graph_name,     # Phase1 口径别名（同 graph_name）
            "conversation_id": self.conversation_id,
            "tenant_id": self.tenant_id,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "queued_at": self.queued_at.isoformat() if self.queued_at else None,
            "started_at": self.started_at.isoformat() if self.started_at else None,
            "finished_at": self.finished_at.isoformat() if self.finished_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }

    def to_admin_dict(self) -> dict:
        """管理端形态：额外暴露 traceback 与内部定位字段（celery_task_id/thread_id）。

        调用方须已过管理员闸；traceback 可能含敏感内容，仅在 admin 通道输出。
        """
        return {
            **self.to_public_dict(),
            "traceback": self.traceback,
            "celery_task_id": self.celery_task_id,
            "thread_id": self.thread_id,
            "execution_id": self.execution_id,
            "input": self.input,
            "parent_task_id": self.parent_task_id,
        }
