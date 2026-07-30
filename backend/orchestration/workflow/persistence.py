"""workflow/persistence.py — workflow_runs 持久化

设计：
- SQLite 单表 + 线程安全
- workflow_runs(id, workflow_name, status, started_at, finished_at, duration_ms,
                inputs_json, outputs_json, error, trace_id)
- 持久化对 Executor 无侵入（Executor.run() 后自动落库）

注：Phase 1 只做"跑完后落库"，不做运行中状态实时更新。
后续 Phase 5 可加 started_at 时立即插入一行。
"""
from __future__ import annotations

import json as _json
import os
import sqlite3
import threading
from datetime import datetime
from typing import Any

from backend.orchestration.workflow.context import WorkflowContext
from backend.shared.logger import logger


SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS workflow_runs (
    id           TEXT PRIMARY KEY,
    workflow_name TEXT NOT NULL,
    status       TEXT NOT NULL,
    started_at   TEXT NOT NULL,
    finished_at  TEXT,
    duration_ms  INTEGER,
    inputs_json  TEXT,
    outputs_json TEXT,
    error        TEXT,
    trace_id     TEXT
);
CREATE INDEX IF NOT EXISTS idx_runs_workflow ON workflow_runs(workflow_name, started_at DESC);
"""


class WorkflowRunStore:
    """workflow_runs SQLite 存储"""

    def __init__(self, db_path: str = "data/workflow_runs.db"):
        self._db_path = db_path
        self._lock = threading.Lock()
        self._init_db()

    def _conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self._db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self):
        os.makedirs(os.path.dirname(self._db_path) or ".", exist_ok=True)
        with self._lock, self._conn() as conn:
            conn.executescript(SCHEMA_SQL)
            conn.commit()

    def save(self, ctx: WorkflowContext) -> None:
        """保存一次 workflow run 结果"""
        try:
            with self._lock, self._conn() as conn:
                conn.execute(
                    """INSERT OR REPLACE INTO workflow_runs
                       (id, workflow_name, status, started_at, finished_at,
                        duration_ms, inputs_json, outputs_json, error, trace_id)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        ctx.run_id,
                        ctx.workflow_name,
                        ctx.status,
                        ctx.started_at.isoformat(),
                        ctx.finished_at.isoformat() if ctx.finished_at else None,
                        ctx.duration_ms,
                        _json.dumps(ctx.inputs, ensure_ascii=False, default=str),
                        # outputs 可能含不可序列化对象，做安全降级
                        _json.dumps(
                            _safe_serialize(ctx.outputs),
                            ensure_ascii=False,
                            default=str,
                        ),
                        ctx.error,
                        ctx.trace_id or "",
                    ),
                )
                conn.commit()
            logger.debug(f"[WorkflowRunStore] 保存 run {ctx.run_id}: {ctx.status}")
        except Exception as e:
            logger.warning(f"[WorkflowRunStore] 保存失败 ({ctx.run_id}): {e}")

    def list(
        self,
        workflow_name: str = "",
        page: int = 1,
        page_size: int = 20,
    ) -> list[dict[str, Any]]:
        """列出 workflow run 历史"""
        offset = (page - 1) * page_size
        with self._lock, self._conn() as conn:
            if workflow_name:
                rows = conn.execute(
                    """SELECT id AS run_id, workflow_name, status, started_at, finished_at,
                              duration_ms, error, trace_id
                       FROM workflow_runs WHERE workflow_name = ?
                       ORDER BY started_at DESC LIMIT ? OFFSET ?""",
                    (workflow_name, page_size, offset),
                ).fetchall()
            else:
                rows = conn.execute(
                    """SELECT id AS run_id, workflow_name, status, started_at, finished_at,
                              duration_ms, error, trace_id
                       FROM workflow_runs
                       ORDER BY started_at DESC LIMIT ? OFFSET ?""",
                    (page_size, offset),
                ).fetchall()
        return [dict(r) for r in rows]

    def get(self, run_id: str) -> dict[str, Any] | None:
        """获取单次 run 详情（含 outputs）"""
        with self._lock, self._conn() as conn:
            row = conn.execute(
                """SELECT * FROM workflow_runs WHERE id = ?""",
                (run_id,),
            ).fetchone()
        if row is None:
            return None
        data = dict(row)
        # 反序列化 JSON
        try:
            data["inputs"] = _json.loads(data.get("inputs_json") or "{}")
        except Exception:
            data["inputs"] = {}
        try:
            data["outputs"] = _json.loads(data.get("outputs_json") or "{}")
        except Exception:
            data["outputs"] = {}
        return data


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
    global _store
    if _store is None:
        _store = WorkflowRunStore()
    return _store