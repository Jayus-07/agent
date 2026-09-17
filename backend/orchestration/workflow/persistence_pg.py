"""PostgresWorkflowRunStore — workflow_runs 的 PostgreSQL 连接层（迁移计划 Batch C）。

与 SQLite 版 `WorkflowRunStore` 对外接口完全一致（save / list / get），
仅替换连接层与 SQL 方言。选型开关见 `backend/config/database.py::WORKFLOW_DB_BACKEND`
（env `WORKFLOW_DB_BACKEND=postgres` 启用；默认 sqlite，即回滚开关）。

库归属：agent_memory（Agent 自身运行状态，不暴露给 NL2SQL）。
schema 与 backend/sql/migrations/015_orchestration_pg.sql 保持一致。

方言映射：
  - `?`                    → `%s`
  - `INSERT OR REPLACE`    → `INSERT ... ON CONFLICT (id) DO UPDATE`
  - sqlite3 隐式事务       → psycopg2 显式 commit/rollback（per-op 连接，用完即关）
"""

from __future__ import annotations

import json as _json
import os
import threading
from contextlib import contextmanager
from typing import Any, Iterator

import psycopg2
import psycopg2.extras

from backend.config.database import WORKFLOW_DB_PG_CONFIG
from backend.orchestration.workflow.context import WorkflowContext
from backend.orchestration.workflow.persistence import (
    WorkflowRunStore,
    _safe_serialize,
)
from backend.shared.logger import logger

_TABLE = os.getenv("WORKFLOW_DB_PG_TABLE", "workflow_runs")

_SCHEMA_SQL = f"""
CREATE TABLE IF NOT EXISTS {_TABLE} (
    id            TEXT PRIMARY KEY,
    workflow_name TEXT NOT NULL,
    status        TEXT NOT NULL,
    started_at    TEXT NOT NULL,
    finished_at   TEXT,
    duration_ms   BIGINT,
    inputs_json   TEXT,
    outputs_json  TEXT,
    error         TEXT,
    trace_id      TEXT
);
CREATE INDEX IF NOT EXISTS idx_runs_workflow ON {_TABLE}(workflow_name, started_at DESC);
"""


class PostgresWorkflowRunStore(WorkflowRunStore):
    """workflow_runs 的 PostgreSQL 实现 — WorkflowRunStore 子类（isinstance 兼容）。

    db_path 参数保留但被忽略（签名兼容 SQLite 版调用方）。
    """

    def __init__(self, db_path: str = "data/workflow_runs.db"):
        self._db_path = db_path  # 兼容保留，PG 模式下无意义
        self._lock = threading.Lock()
        self._table = os.getenv("WORKFLOW_DB_PG_TABLE", _TABLE)
        self._init_db()

    # ---- 连接层 ----

    @contextmanager
    def _conn(self) -> Iterator[Any]:
        """每次操作独立连接：成功 commit、异常 rollback、退出必关。"""
        conn = psycopg2.connect(**WORKFLOW_DB_PG_CONFIG)
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def _exec(self, conn: Any, sql: str, params: tuple = ()) -> Any:
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cur.execute(sql, params)
        return cur

    def _init_db(self):
        with self._lock, self._conn() as conn:
            conn.cursor().execute(
                _SCHEMA_SQL.replace(_TABLE, self._table)
                if self._table != _TABLE
                else _SCHEMA_SQL
            )

    def save(self, ctx: WorkflowContext) -> None:
        """保存一次 workflow run 结果（软失败：不抛异常）"""
        try:
            with self._lock, self._conn() as conn:
                self._exec(
                    conn,
                    f"""INSERT INTO {self._table}
                       (id, workflow_name, status, started_at, finished_at,
                        duration_ms, inputs_json, outputs_json, error, trace_id)
                       VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                       ON CONFLICT (id) DO UPDATE SET
                            workflow_name = EXCLUDED.workflow_name,
                            status = EXCLUDED.status,
                            started_at = EXCLUDED.started_at,
                            finished_at = EXCLUDED.finished_at,
                            duration_ms = EXCLUDED.duration_ms,
                            inputs_json = EXCLUDED.inputs_json,
                            outputs_json = EXCLUDED.outputs_json,
                            error = EXCLUDED.error,
                            trace_id = EXCLUDED.trace_id""",
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
                            _safe_serialize(ctx.outputs), ensure_ascii=False, default=str
                        ),
                        ctx.error,
                        ctx.trace_id or "",
                    ),
                )
            logger.debug(f"[PostgresWorkflowRunStore] 保存 run {ctx.run_id}: {ctx.status}")
        except Exception as e:
            logger.warning(f"[PostgresWorkflowRunStore] 保存失败 ({ctx.run_id}): {e}")

    def list(
        self,
        workflow_name: str = "",
        page: int = 1,
        page_size: int = 20,
    ) -> list[dict[str, Any]]:
        """列出 workflow run 历史"""
        offset = (page - 1) * page_size
        with self._conn() as conn:
            if workflow_name:
                rows = self._exec(
                    conn,
                    f"""SELECT id AS run_id, workflow_name, status, started_at, finished_at,
                               duration_ms, error, trace_id
                        FROM {self._table} WHERE workflow_name = %s
                        ORDER BY started_at DESC LIMIT %s OFFSET %s""",
                    (workflow_name, page_size, offset),
                ).fetchall()
            else:
                rows = self._exec(
                    conn,
                    f"""SELECT id AS run_id, workflow_name, status, started_at, finished_at,
                               duration_ms, error, trace_id
                        FROM {self._table}
                        ORDER BY started_at DESC LIMIT %s OFFSET %s""",
                    (page_size, offset),
                ).fetchall()
        return [dict(r) for r in rows]

    def get(self, run_id: str) -> dict[str, Any] | None:
        """获取单次 run 详情（含 outputs）"""
        with self._conn() as conn:
            row = self._exec(
                conn,
                f"SELECT * FROM {self._table} WHERE id = %s",
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
