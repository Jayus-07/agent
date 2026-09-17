"""workflow/persistence.py — workflow_runs 持久化（接口层，PG 实现）

设计：
- workflow_runs(id, workflow_name, status, started_at, finished_at, duration_ms,
                inputs_json, outputs_json, error, trace_id)
- 持久化对 Executor 无侵入（Executor.run() 后自动落库）
- 2026-09-17 SQLite 轨已删除，唯一实现为 PostgresWorkflowRunStore
  （persistence_pg.py）；本模块保留接口与共享序列化工具。
"""
from __future__ import annotations

import json as _json
from typing import Any


class WorkflowRunStore:
    """workflow_runs 存储接口（唯一实现：PostgresWorkflowRunStore）。"""

    def __new__(cls, *args, **kwargs):
        # 2026-09-17 SQLite 轨删除：无条件返回 PG 实现（db_path 等参数兼容保留，表名由 env 决定）。
        from backend.orchestration.workflow.persistence_pg import PostgresWorkflowRunStore
        return super().__new__(PostgresWorkflowRunStore)

    pass


def _safe_serialize(obj: Any) -> Any:
    """把不可序列化的对象降级为 str"""
    try:
        _json.dumps(obj, ensure_ascii=False)
        return obj
    except (TypeError, ValueError):
        if isinstance(obj, dict):
            return {k: str(v) for k, v in obj.items()}
        if isinstance(obj, (list, tuple)):
            return [str(v) for v in obj]
        return str(obj)


# 模块级单例
_store: WorkflowRunStore | None = None


def get_workflow_run_store() -> WorkflowRunStore:
    """存储工厂（2026-09-17 SQLite 轨删除，直连 PG 实现）。"""
    global _store
    if _store is None:
        from backend.orchestration.workflow.persistence_pg import (
            PostgresWorkflowRunStore,
        )

        _store = PostgresWorkflowRunStore()
    return _store
