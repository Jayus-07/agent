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
from backend.config.database import CHUNK_STORE_PATH

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS chunk_store (
    id                   INTEGER PRIMARY KEY AUTOINCREMENT,
    doc_id               TEXT NOT NULL,
    chunk_index          INTEGER NOT NULL DEFAULT 0,
    content              TEXT NOT NULL DEFAULT '',
    char_count           INTEGER NOT NULL DEFAULT 0,
    keywords             TEXT NOT NULL DEFAULT '',  -- 规则提取 chunk 关键词
    llm_keywords         TEXT NOT NULL DEFAULT '',  -- Qwen LLM 提取 chunk 关键词（高价值文档）
    llm_model            TEXT NOT NULL DEFAULT '',  -- 提取使用的 LLM 模型名
    section_title        TEXT NOT NULL DEFAULT '',  -- chunk 所属章节标题
    doc_type             TEXT NOT NULL DEFAULT '',  -- 文档类型（继承自文档级元数据）
    kb_id                TEXT NOT NULL DEFAULT '',  -- 知识库 ID（继承自文档级元数据）
    department           TEXT NOT NULL DEFAULT '',  -- 部门（继承自文档级元数据）
    simulated_questions  TEXT NOT NULL DEFAULT '[]',  -- P1: Document Expansion 用 LLM 生成的模拟问题（JSON 数组字符串）
    created_at           TEXT DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_cs_doc_id ON chunk_store(doc_id);
"""

# 兼容旧库的增量迁移
# v1 → v2：新增 doc_type, kb_id, department 列
MIGRATION_SQL = """
ALTER TABLE chunk_store ADD COLUMN doc_type TEXT NOT NULL DEFAULT '';
ALTER TABLE chunk_store ADD COLUMN kb_id TEXT NOT NULL DEFAULT '';
ALTER TABLE chunk_store ADD COLUMN department TEXT NOT NULL DEFAULT '';
"""
# v2 → v3：新增 simulated_questions 列（Document Expansion）
MIGRATION_SQL_V3 = """
ALTER TABLE chunk_store ADD COLUMN simulated_questions TEXT NOT NULL DEFAULT '[]';
"""


class ChunkStore:
    """Chunk 文本 SQLite 存储 — 线程安全，按 doc_id 批量读写。"""

    def __init__(self, db_path: str = CHUNK_STORE_PATH):
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
        # 兼容旧库增量迁移（忽略列已存在的错误）
        try:
            conn.executescript(MIGRATION_SQL)
        except sqlite3.OperationalError:
            pass  # 列已存在，跳过
        try:
            conn.executescript(MIGRATION_SQL_V3)
        except sqlite3.OperationalError:
            pass  # 列已存在，跳过
        conn.commit()
        conn.close()

    # ── 写入 ──

    def insert_batch(self, doc_id: str, chunks: list[dict]) -> int:
        """批量写入 chunk 文本。chunks: [{chunk_index, content, keywords?, llm_keywords?, llm_model?, section_title?, doc_type?, kb_id?, department?, simulated_questions?}]。"""
        if not chunks:
            return 0
        import json as _json
        with self._lock:
            conn = self._conn()
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
                 _json.dumps(c.get("simulated_questions", []), ensure_ascii=False))
                for i, c in enumerate(chunks)
            ]
            conn.executemany(
                "INSERT INTO chunk_store (doc_id, chunk_index, content, char_count, keywords, llm_keywords, llm_model, section_title, doc_type, kb_id, department, simulated_questions) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
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
            "SELECT chunk_index, content, char_count, keywords, llm_keywords, llm_model, section_title, doc_type, kb_id, department, simulated_questions, created_at FROM chunk_store WHERE doc_id = ? ORDER BY chunk_index",
            (doc_id,),
        ).fetchall()
        conn.close()
        import json as _json
        out = []
        for r in rows:
            d = dict(r)
            # 反序列化 JSON 字符串（向后兼容旧库默认 "[]"）
            try:
                d["simulated_questions"] = _json.loads(d.get("simulated_questions") or "[]")
            except (ValueError, TypeError):
                d["simulated_questions"] = []
            out.append(d)
        return out

    def count_by_doc_id(self, doc_id: str) -> int:
        conn = self._conn()
        row = conn.execute(
            "SELECT COUNT(*) as cnt FROM chunk_store WHERE doc_id = ?", (doc_id,)
        ).fetchone()
        conn.close()
        return row["cnt"] if row else 0


# 模块级单例
_store: ChunkStore | None = None


def get_chunk_store(db_path: str = CHUNK_STORE_PATH) -> ChunkStore:
    global _store
    if _store is None:
        _store = ChunkStore(db_path)
    return _store
