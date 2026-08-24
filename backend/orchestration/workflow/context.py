"""workflow/context.py — WorkflowContext

Workflow 运行时的状态对象。
- 每次 Workflow.run() 创建新实例
- Step 通过 ctx.outputs[step_name] 读上游输出
- 通过 ctx.outputs[step_name] = value 写自己的输出
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any


@dataclass
class WorkflowContext:
    """Workflow 单次运行的完整上下文

    字段：
    - workflow_name: workflow 名（@workflow 装饰器声明）
    - run_id: 本次运行唯一 ID（12 字符 hex）
    - inputs: 初始输入（trigger 调用时传入）
    - outputs: 各 Step 写入的结果（key = step 方法名）
    - trace_id: 关联现有 tracer（Phase 1 commit 4 时填充）
    - started_at / finished_at: 运行起止时间
    - status: 运行状态
    - error: 失败信息
    - skip_steps: 跳过的 Step 集合（on_error="skip"）
    - run_if_skips: run_if 条件跳过集合，不参与 partial 判定
    """
    workflow_name: str
    run_id: str
    inputs: dict[str, Any] = field(default_factory=dict)
    outputs: dict[str, Any] = field(default_factory=dict)
    trace_id: str | None = None
    started_at: datetime = field(default_factory=datetime.now)
    finished_at: datetime | None = None
    status: str = "running"  # running / success / failed / partial
    error: str | None = None
    skip_steps: set[str] = field(default_factory=set)
    run_if_skips: set[str] = field(default_factory=set)

    @property
    def duration_ms(self) -> int | None:
        """运行耗时（毫秒）"""
        if self.finished_at is None:
            return None
        delta = self.finished_at - self.started_at
        return int(delta.total_seconds() * 1000)

    def mark_success(self) -> None:
        self.status = "success"
        self.finished_at = datetime.now()

    def mark_failed(self, error: str) -> None:
        self.status = "failed"
        self.error = error
        self.finished_at = datetime.now()

    def mark_partial(self) -> None:
        """部分成功（部分 Step 失败但 on_error=skip）"""
        self.status = "partial"
        self.finished_at = datetime.now()

    def summary(self) -> dict[str, Any]:
        """导出为 dict（持久化用）"""
        return {
            "workflow_name": self.workflow_name,
            "run_id": self.run_id,
            "status": self.status,
            "started_at": self.started_at.isoformat(),
            "finished_at": self.finished_at.isoformat() if self.finished_at else None,
            "duration_ms": self.duration_ms,
            "error": self.error,
            "skip_steps": list(self.skip_steps),
            "outputs_keys": list(self.outputs.keys()),
        }