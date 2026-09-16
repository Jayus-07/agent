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
    retry_count: int = 0
    celery_task_id: str = ""
    created_at: datetime | None = None
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
            retry_count=int(row.get("retry_count") or 0),
            celery_task_id=row.get("celery_task_id") or "",
            created_at=row.get("created_at"),
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
            "retry_count": self.retry_count,
            "graph_name": self.graph_name,
            "tenant_id": self.tenant_id,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }
