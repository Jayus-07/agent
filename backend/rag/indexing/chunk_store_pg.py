"""PostgresChunkStore — chunk_store 的 PostgreSQL 连接层（迁移计划 Batch B）。

与 SQLite 版 `ChunkStore` 对外接口完全一致（insert_batch/delete_by_doc_id/
get_by_doc_id/count_by_doc_id），仅替换连接层与 SQL 方言。引擎开关见
PG 为唯一实现（2026-09-17 SQLite 轨删除）。
工厂分发见 `chunk_store.py::get_chunk_store`。

方言映射要点：
  - `INTEGER PRIMARY KEY AUTOINCREMENT` → `BIGSERIAL PRIMARY KEY`
  - `datetime('now')`(UTC) → 应用侧生成 UTC 文本（与 SQLite 语义一致，不依赖服务器时区）
  - executemany → psycopg2.extras.execute_batch
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
from backend.rag.indexing.chunk_store import ChunkStore
from backend.shared.logger import logger


def _now_utc() -> str:
    """等价 SQLite datetime('now')：UTC 本地文本（不依赖 PG 服务器时区）。"""
    return time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime())


class PostgresChunkStore(ChunkStore):
    """chunk_store 的 PostgreSQL 实现（ChunkStore 子类，isinstance 兼容）。"""

    def __init__(self, db_path: str = "data/chunk_store.db"):
        self._db_path = db_path  # 兼容保留，PG 模式下无意义
        self._lock = threading.Lock()
        self._table = os.getenv("RAG_STORES_PG_TABLE_PREFIX", "") + "chunk_store"
        self._init_db()

    # ---- 连接层 ----

    @contextmanager
    def _conn(self, row_factory=None) -> Iterator[Any]:
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
                    id                   BIGSERIAL PRIMARY KEY,
                    doc_id               TEXT NOT NULL,
                    chunk_index          INTEGER NOT NULL DEFAULT 0,
                    content              TEXT NOT NULL DEFAULT '',
                    char_count           INTEGER NOT NULL DEFAULT 0,
                    keywords             TEXT NOT NULL DEFAULT '',
                    llm_keywords         TEXT NOT NULL DEFAULT '',
                    llm_model            TEXT NOT NULL DEFAULT '',
                    section_title        TEXT NOT NULL DEFAULT '',
                    doc_type             TEXT NOT NULL DEFAULT '',
                    kb_id                TEXT NOT NULL DEFAULT '',
                    department           TEXT NOT NULL DEFAULT '',
                    simulated_questions  TEXT NOT NULL DEFAULT '[]',
                    created_at           TEXT NOT NULL DEFAULT ''
                )
            """)
            conn.cursor().execute(
                f"CREATE INDEX IF NOT EXISTS idx_{t}_doc_id ON {t}(doc_id)")

    # ---- 写入 ----

    def insert_batch(self, doc_id: str, chunks: list[dict]) -> int:
        if not chunks:
            return 0
        rows = [
            (doc_id, c.get("chunk_index", i), c.get("content", ""),
             len(c.get("content", "") or ""),
             c.get("keywords", ""),
             c.get("llm_keywords", ""),
             c.get("llm_model", ""),
             c.get("section_title", ""),
             c.get("doc_type", ""),
             c.get("kb_id", ""),
             c.get("department", ""),
             json.dumps(c.get("simulated_questions", []), ensure_ascii=False),
             _now_utc())
            for i, c in enumerate(chunks)
        ]
        with self._lock, self._conn() as conn:
            psycopg2.extras.execute_batch(
                conn.cursor(),
                f"""INSERT INTO {self._table}
                   (doc_id, chunk_index, content, char_count, keywords,
                    llm_keywords, llm_model, section_title, doc_type, kb_id,
                    department, simulated_questions, created_at)
                   VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
                rows,
            )
        logger.debug(f"[ChunkStore-PG] 写入 {len(rows)} chunks for doc={doc_id}")
        return len(rows)

    def delete_by_doc_id(self, doc_id: str) -> int:
        with self._lock, self._conn() as conn:
            cur = self._exec_scalar(
                conn, f"DELETE FROM {self._table} WHERE doc_id = %s", (doc_id,))
            return cur.rowcount

    # ---- 查询 ----

    def get_by_doc_id(self, doc_id: str) -> list[dict[str, Any]]:
        with self._conn() as conn:
            rows = self._exec(
                conn,
                f"""SELECT chunk_index, content, char_count, keywords, llm_keywords,
                           llm_model, section_title, doc_type, kb_id, department,
                           simulated_questions, created_at
                    FROM {self._table} WHERE doc_id = %s ORDER BY chunk_index""",
                (doc_id,),
            ).fetchall()
        out = []
        for r in rows:
            d = dict(r)
            try:
                d["simulated_questions"] = json.loads(d.get("simulated_questions") or "[]")
            except (ValueError, TypeError):
                d["simulated_questions"] = []
            out.append(d)
        return out

    def count_by_doc_id(self, doc_id: str) -> int:
        with self._conn() as conn:
            row = self._exec_scalar(
                conn,
                f"SELECT COUNT(*) FROM {self._table} WHERE doc_id = %s",
                (doc_id,),
            ).fetchone()
        return row[0] if row else 0

    def list_doc_ids(self) -> set[str]:
        """返回 chunk_store 中出现过的 doc_id 集合。

        一致性清扫只需要去重后的文档键，不应把 20 万级 chunk 正文全部
        拉回应用进程；`doc_id` 已有索引，查询成本远低于旧 SQLite 全表路径。
        """
        with self._conn() as conn:
            rows = self._exec_scalar(
                conn,
                f"SELECT DISTINCT doc_id FROM {self._table} WHERE doc_id <> %s",
                ("",),
            ).fetchall()
        return {row[0] for row in rows if row and row[0]}
