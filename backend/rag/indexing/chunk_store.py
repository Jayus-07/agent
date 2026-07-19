"""Chunk 文本持久化存储 — 用于 Trace 详情页展示完整 Chunk 内容。

设计：SQLite 单表 + 线程安全 + 模块级单例。
ChromaDB 不适合做「按 doc_id 查所有 chunk 文本」这种 OLTP 查询，
因此独立建表存储，仅供 trace 页查询，不参与检索链路。
"""
from __future__ import annotations

import os
import sqlite3
import threading
from typing import Any

from backend.shared.logger import logger

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS chunk_store (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    doc_id       TEXT NOT NULL,           -- 关联 doc_id
    chunk_index  INTEGER NOT NULL DEFAULT 0,
    content      TEXT NOT NULL DEFAULT '',
    token_count  INTEGER NOT NULL DEFAULT 0,
    keywords     TEXT NOT NULL DEFAULT '', -- 逗号分隔 chunk 级关键词
    created_at   TEXT DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_cs_doc_id ON chunk_store(doc_id);
"""


class ChunkStore:
    """Chunk 文本 SQLite 存储 — 线程安全，按 doc_id 批量读写。"""

    def __init__(self, db_path: str = "data/chunk_store.db"):
        self._db_path = db_path
        self._lock = threading.Lock()
        self._init_db()

    def _conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self._db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self) -> None:
        os.makedirs(os.path.dirname(self._db_path) or ".", exist_ok=True)
        conn = self._conn()
        conn.executescript(SCHEMA_SQL)
        conn.commit()
        conn.close()

    # ── 写入 ──

    def insert_batch(self, doc_id: str, chunks: list[dict]) -> int:
        """批量写入 chunk 文本。chunks: [{chunk_index, content, keywords?}]。返回写入条数。"""
        if not chunks:
            return 0
        with self._lock:
            conn = self._conn()
            rows = [
                (doc_id, c.get("chunk_index", i), c.get("content", ""),
                 len((c.get("content", "") or "").encode()),
                 c.get("keywords", ""))
                for i, c in enumerate(chunks)
            ]
            conn.executemany(
                "INSERT INTO chunk_store (doc_id, chunk_index, content, token_count, keywords) VALUES (?, ?, ?, ?, ?)",
                rows,
            )
            conn.commit()
            count = len(rows)
            conn.close()
        logger.debug(f"[ChunkStore] 写入 {count} chunks for doc={doc_id}")
        return count

    def delete_by_doc_id(self, doc_id: str) -> int:
        """删除某文档的所有 chunk 记录。返回删除条数。"""
        with self._lock:
            conn = self._conn()
            cur = conn.execute("DELETE FROM chunk_store WHERE doc_id = ?", (doc_id,))
            conn.commit()
            deleted = cur.rowcount
            conn.close()
        return deleted

    # ── 查询 ──

    def get_by_doc_id(self, doc_id: str) -> list[dict[str, Any]]:
        """按 doc_id 查询所有 chunk，按 chunk_index 排序。"""
        conn = self._conn()
        rows = conn.execute(
            "SELECT chunk_index, content, token_count, keywords, created_at FROM chunk_store WHERE doc_id = ? ORDER BY chunk_index",
            (doc_id,),
        ).fetchall()
        conn.close()
        return [dict(r) for r in rows]

    def count_by_doc_id(self, doc_id: str) -> int:
        conn = self._conn()
        row = conn.execute(
            "SELECT COUNT(*) as cnt FROM chunk_store WHERE doc_id = ?", (doc_id,)
        ).fetchone()
        conn.close()
        return row["cnt"] if row else 0


# 模块级单例
_store: ChunkStore | None = None


def get_chunk_store(db_path: str = "data/chunk_store.db") -> ChunkStore:
    global _store
    if _store is None:
        _store = ChunkStore(db_path)
    return _store
