"""DocumentOperationLogger — 文档管理操作审计日志（SQLite）。

记录每次文档管理操作（upload / reindex / delete）：
  谁(user_id + source) + 什么时候 + 对哪个文档 + 什么操作 + 关联 trace_id + 结果。

与 trace_collector（内存，重启丢）的区别：
  本表持久化，重启后操作历史仍可查；trace_id 关联是 best-effort，
  近期操作能跳转 trace 详情，老操作/重启后 trace 可能已过期。

用法:
    logger = DocumentOperationLogger("data/doc_operation_log.db")
    logger.log(doc_id="abc", doc_name="x.md", operation="upload",
              source="127.0.0.1 | Mozilla/...", trace_id="t123", result="success",
              detail={"chunk_count": 5})
    page = logger.list(page=1, page_size=20, operation="upload")
"""

from __future__ import annotations

import json
import os
import sqlite3
import threading
from typing import Any


OPERATIONS = ("upload", "reindex", "delete")

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS doc_operation_log (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    doc_id       TEXT NOT NULL,
    doc_name     TEXT NOT NULL,
    operation    TEXT NOT NULL,
    user_id      TEXT DEFAULT 'anonymous',
    source       TEXT,
    trace_id     TEXT,
    batch_id     TEXT,
    result       TEXT DEFAULT 'success',
    detail       TEXT,
    duration_ms  INTEGER DEFAULT 0,
    created_at   TEXT DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_op_created ON doc_operation_log(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_op_doc ON doc_operation_log(doc_id);
CREATE INDEX IF NOT EXISTS idx_op_operation ON doc_operation_log(operation);
CREATE INDEX IF NOT EXISTS idx_op_batch ON doc_operation_log(batch_id);
"""


class DocumentOperationLogger:
    """文档操作审计日志 — 线程安全。"""

    def __init__(self, db_path: str = "data/doc_operation_log.db"):
        self._db_path = db_path
        self._lock = threading.Lock()
        self._init_db()

    def _init_db(self) -> None:
        os.makedirs(os.path.dirname(self._db_path) or ".", exist_ok=True)
        conn = self._conn()
        conn.executescript(SCHEMA_SQL)
        conn.commit()
        conn.close()

    def _conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self._db_path)
        conn.row_factory = sqlite3.Row
        return conn

    # ---- 写入 ----

    def log(
        self,
        doc_id: str,
        doc_name: str,
        operation: str,
        user_id: str = "anonymous",
        source: str = "",
        trace_id: str | None = None,
        batch_id: str | None = None,
        result: str = "success",
        detail: dict | None = None,
        duration_ms: int = 0,
    ) -> None:
        """记录一条操作日志。"""
        if operation not in OPERATIONS:
            raise ValueError(f"无效操作: {operation}，有效值: {OPERATIONS}")
        detail_str = json.dumps(detail, ensure_ascii=False) if detail else None
        with self._lock, self._conn() as conn:
            conn.execute(
                """INSERT INTO doc_operation_log
                   (doc_id, doc_name, operation, user_id, source, trace_id, batch_id, result, detail, duration_ms)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (doc_id, doc_name, operation, user_id, source, trace_id, batch_id, result, detail_str, duration_ms),
            )

    # ---- 查询 ----

    def list(
        self,
        page: int = 1,
        page_size: int = 20,
        operation: str = "",
        doc_id: str = "",
        batch_id: str = "",
    ) -> dict[str, Any]:
        """分页查询操作日志，支持按操作类型 / doc_id / batch_id 过滤。

        Returns: {"items": [...], "total": int, "page": int, "page_size": int}
        """
        conditions: list[str] = []
        params: list[Any] = []
        if operation.strip():
            conditions.append("operation = ?")
            params.append(operation.strip())
        if doc_id.strip():
            conditions.append("doc_id = ?")
            params.append(doc_id.strip())
        if batch_id.strip():
            conditions.append("batch_id = ?")
            params.append(batch_id.strip())

        where_clause = ("WHERE " + " AND ".join(conditions)) if conditions else ""

        with self._lock, self._conn() as conn:
            count_row = conn.execute(
                f"SELECT COUNT(*) FROM doc_operation_log {where_clause}", params
            ).fetchone()
            total = count_row[0] if count_row else 0

            offset = max(0, (page - 1)) * page_size
            rows = conn.execute(
                f"""SELECT * FROM doc_operation_log {where_clause}
                    ORDER BY created_at DESC, id DESC
                    LIMIT ? OFFSET ?""",
                params + [page_size, offset],
            ).fetchall()

        return {
            "items": [dict(r) for r in rows],
            "total": total,
            "page": page,
            "page_size": page_size,
        }

    def get_last_ops_batch(self, doc_ids: list[str]) -> tuple[dict[str, dict], dict[str, str]]:
        """批量查询每个文档的最新操作日志 + trace_id。

        Returns:
            (last_ops: {doc_id: {operation, created_at, trace_id, result}},
             last_traces: {doc_id: trace_id})
        """
        if not doc_ids:
            return {}, {}
        placeholders = ",".join(["?"] * len(doc_ids))
        with self._lock, self._conn() as conn:
            rows = conn.execute(
                f"SELECT doc_id, operation, created_at, trace_id, result FROM doc_operation_log "
                f"WHERE id IN (SELECT MAX(id) FROM doc_operation_log WHERE doc_id IN ({placeholders}) GROUP BY doc_id)",
                doc_ids,
            ).fetchall()
            last_ops = {r["doc_id"]: dict(r) for r in rows}
            trace_rows = conn.execute(
                f"SELECT doc_id, trace_id FROM doc_operation_log "
                f"WHERE trace_id IS NOT NULL AND trace_id != '' AND doc_id IN ({placeholders}) "
                f"AND id IN (SELECT MAX(id) FROM doc_operation_log "
                f"WHERE trace_id IS NOT NULL AND trace_id != '' AND doc_id IN ({placeholders}) GROUP BY doc_id)",
                [*doc_ids, *doc_ids],
            ).fetchall()
            last_traces = {r["doc_id"]: r["trace_id"] for r in trace_rows}
        return last_ops, last_traces
