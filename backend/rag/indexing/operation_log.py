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
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    doc_id      TEXT NOT NULL,
    doc_name    TEXT NOT NULL,
    operation   TEXT NOT NULL,
    user_id     TEXT DEFAULT 'anonymous',
    source      TEXT,
    trace_id    TEXT,
    result      TEXT DEFAULT 'success',
    detail      TEXT,
    created_at  TEXT DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_op_created ON doc_operation_log(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_op_doc ON doc_operation_log(doc_id);
CREATE INDEX IF NOT EXISTS idx_op_operation ON doc_operation_log(operation);
"""


class DocumentOperationLogger:
    """文档操作审计日志 — 线程安全。"""

    def __init__(self, db_path: str = "data/doc_operation_log.db"):
        self._db_path = db_path
        self._lock = threading.Lock()
        self._init_db()

    def _init_db(self) -> None:
        os.makedirs(os.path.dirname(self._db_path) or ".", exist_ok=True)
        with self._conn() as conn:
            conn.executescript(SCHEMA_SQL)

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
        result: str = "success",
        detail: dict | None = None,
    ) -> None:
        """记录一条操作日志。"""
        if operation not in OPERATIONS:
            raise ValueError(f"无效操作: {operation}，有效值: {OPERATIONS}")
        detail_str = json.dumps(detail, ensure_ascii=False) if detail else None
        with self._lock, self._conn() as conn:
            conn.execute(
                """INSERT INTO doc_operation_log
                   (doc_id, doc_name, operation, user_id, source, trace_id, result, detail)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (doc_id, doc_name, operation, user_id, source, trace_id, result, detail_str),
            )

    # ---- 查询 ----

    def list(
        self,
        page: int = 1,
        page_size: int = 20,
        operation: str = "",
        doc_id: str = "",
    ) -> dict[str, Any]:
        """分页查询操作日志，支持按操作类型 / doc_id 过滤。

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
