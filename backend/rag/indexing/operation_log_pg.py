"""PostgresDocumentOperationLogger — doc_operation_log 的 PostgreSQL 连接层。

与 SQLite 版 `DocumentOperationLogger` 对外接口完全一致（log/list/
get_last_ops_batch），仅替换连接层与 SQL 方言。引擎开关见
PG 为唯一实现（2026-09-17 SQLite 轨删除）。
工厂分发见 `backend/app/api/routes/_rag_shared.py::_get_op_logger`。

方言映射要点：
  - `INTEGER PRIMARY KEY AUTOINCREMENT` → `BIGSERIAL PRIMARY KEY`
    （get_last_ops_batch 的 MAX(id) GROUP BY 语义不变）
  - `datetime('now')` → 应用侧生成 UTC 文本
"""

from __future__ import annotations

import json
import os
import threading
import time
from contextlib import contextmanager
from typing import Any, Iterator

import psycopg2
import psycopg2.extras

from backend.config.database import RAG_STORES_PG_CONFIG
from backend.rag.indexing.operation_log import OPERATIONS, DocumentOperationLogger


def _now_utc() -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime())


class PostgresDocumentOperationLogger(DocumentOperationLogger):
    """doc_operation_log 的 PostgreSQL 实现（DocumentOperationLogger 子类）。"""

    def __init__(self, db_path: str = "data/doc_operation_log.db"):
        self._db_path = db_path  # 兼容保留，PG 模式下无意义
        self._lock = threading.Lock()
        self._table = os.getenv("RAG_STORES_PG_TABLE_PREFIX", "") + "doc_operation_log"
        self._init_db()

    # ---- 连接层 ----

    @contextmanager
    def _conn(self) -> Iterator[Any]:
        conn = psycopg2.connect(**RAG_STORES_PG_CONFIG)
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

    def _exec_scalar(self, conn: Any, sql: str, params: tuple = ()) -> Any:
        cur = conn.cursor()
        cur.execute(sql, params)
        return cur

    # ---- 建表（幂等，与 backend/sql/migrations/014_rag_stores_pg.sql 一致）----

    def _init_db(self) -> None:
        t = self._table
        with self._lock, self._conn() as conn:
            conn.cursor().execute(f"""
                CREATE TABLE IF NOT EXISTS {t} (
                    id           BIGSERIAL PRIMARY KEY,
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
                    created_at   TEXT NOT NULL DEFAULT ''
                )
            """)
            cur = conn.cursor()
            cur.execute(f"CREATE INDEX IF NOT EXISTS idx_{t}_created ON {t}(created_at DESC)")
            cur.execute(f"CREATE INDEX IF NOT EXISTS idx_{t}_doc ON {t}(doc_id)")
            cur.execute(f"CREATE INDEX IF NOT EXISTS idx_{t}_operation ON {t}(operation)")
            cur.execute(f"CREATE INDEX IF NOT EXISTS idx_{t}_batch ON {t}(batch_id)")

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
        if operation not in OPERATIONS:
            raise ValueError(f"无效操作: {operation}，有效值: {OPERATIONS}")
        detail_str = json.dumps(detail, ensure_ascii=False) if detail else None
        with self._lock, self._conn() as conn:
            self._exec(
                conn,
                f"""INSERT INTO {self._table}
                   (doc_id, doc_name, operation, user_id, source, trace_id,
                    batch_id, result, detail, duration_ms, created_at)
                   VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
                (doc_id, doc_name, operation, user_id, source, trace_id,
                 batch_id, result, detail_str, duration_ms, _now_utc()),
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
        conditions: list[str] = []
        params: list[Any] = []
        if operation.strip():
            conditions.append("operation = %s")
            params.append(operation.strip())
        if doc_id.strip():
            conditions.append("doc_id = %s")
            params.append(doc_id.strip())
        if batch_id.strip():
            conditions.append("batch_id = %s")
            params.append(batch_id.strip())

        where_clause = ("WHERE " + " AND ".join(conditions)) if conditions else ""

        with self._lock, self._conn() as conn:
            count_row = self._exec_scalar(
                conn,
                f"SELECT COUNT(*) FROM {self._table} {where_clause}",
                tuple(params),
            ).fetchone()
            total = count_row[0] if count_row else 0

            offset = max(0, (page - 1)) * page_size
            rows = self._exec(
                conn,
                f"""SELECT * FROM {self._table} {where_clause}
                    ORDER BY created_at DESC, id DESC
                    LIMIT %s OFFSET %s""",
                (*params, page_size, offset),
            ).fetchall()

        return {
            "items": [dict(r) for r in rows],
            "total": total,
            "page": page,
            "page_size": page_size,
        }

    def get_last_ops_batch(self, doc_ids: list[str]) -> tuple[dict[str, dict], dict[str, str]]:
        if not doc_ids:
            return {}, {}
        placeholders = ",".join(["%s"] * len(doc_ids))
        with self._lock, self._conn() as conn:
            rows = self._exec(
                conn,
                f"""SELECT doc_id, operation, created_at, trace_id, result
                    FROM {self._table}
                    WHERE id IN (SELECT MAX(id) FROM {self._table}
                                 WHERE doc_id IN ({placeholders}) GROUP BY doc_id)""",
                tuple(doc_ids),
            ).fetchall()
            last_ops = {r["doc_id"]: dict(r) for r in rows}
            trace_rows = self._exec(
                conn,
                f"""SELECT doc_id, trace_id FROM {self._table}
                    WHERE trace_id IS NOT NULL AND trace_id != '' AND doc_id IN ({placeholders})
                    AND id IN (SELECT MAX(id) FROM {self._table}
                               WHERE trace_id IS NOT NULL AND trace_id != ''
                                 AND doc_id IN ({placeholders}) GROUP BY doc_id)""",
                (*doc_ids, *doc_ids),
            ).fetchall()
            last_traces = {r["doc_id"]: r["trace_id"] for r in trace_rows}
        return last_ops, last_traces
