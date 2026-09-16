"""models/task.py — 异步 Agent 任务领域模型。

纯数据定义，不依赖 Celery / LangGraph / DB 驱动（保持层级隔离：
Celery 与 LangGraph 都只消费这里的枚举与 dataclass）。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum


class TaskStatus(str, Enum):
    """任务生命周期状态机。

    PENDING → RUNNING → SUCCESS / FAILED / CANCELLED
                       ↘ WAITING_USER → (resume) → RUNNING
                       ↘ PAUSED      → (resume) → RUNNING
    FAILED --(重试)--> PENDING → RUNNING（从最近 checkpoint 续跑）
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


@dataclass
class TaskRecord:
    """agent_memory.tasks 的一行记录。"""

    id: str
    user_id: str
    tenant_id: str = "default"
    graph_name: str = "main"
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
    duration_ms: int | None = None
    queue: str = "agent"
    worker: str = ""
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
            duration_ms=int(row["duration_ms"]) if row.get("duration_ms") is not None else None,
            queue=row.get("queue") or "agent",
            worker=row.get("worker") or "",
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
            "retry_count": self.retry_count,
            "max_retries": self.max_retries,
            "duration_ms": self.duration_ms,
            "queue": self.queue,
            "worker": self.worker,
            "trace_id": self.trace_id,
            "biz_type": self.biz_type,
            "biz_id": self.biz_id,
            "graph_name": self.graph_name,
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
            "input": self.input,
            "parent_task_id": self.parent_task_id,
        }
