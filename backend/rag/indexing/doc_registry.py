"""DocumentRegistry — SQLite 文档元数据注册表。

记录每篇已索引文档的路径、SHA256、状态等元数据。
增量索引器依赖此注册表判断文档的新增/修改/删除。
"""

from __future__ import annotations

import json
import os
import sqlite3
import threading
from pathlib import Path
from typing import Any


# 文档状态枚举（完整生命周期）
DOC_STATUSES = ("uploading", "parsing", "embedding", "active", "failed", "deleted")

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS doc_registry (
    file_path    TEXT PRIMARY KEY,
    file_name    TEXT NOT NULL,
    kb_id        TEXT NOT NULL,
    doc_id       TEXT NOT NULL,
    file_hash    TEXT NOT NULL,
    file_size    INTEGER NOT NULL,
    file_mtime   REAL NOT NULL,
    chunk_count  INTEGER DEFAULT 0,
    chunk_ids    TEXT DEFAULT '[]',
    doc_db_id    TEXT,
    doc_type     TEXT DEFAULT 'general',
    confidence   REAL DEFAULT 0,
    llm_used     INTEGER DEFAULT 0,
    status       TEXT DEFAULT 'active',
    last_indexed TEXT,
    created_at   TEXT DEFAULT (datetime('now')),
    updated_at   TEXT DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_registry_doc_id ON doc_registry(doc_id);
CREATE INDEX IF NOT EXISTS idx_registry_kb_id ON doc_registry(kb_id);
CREATE INDEX IF NOT EXISTS idx_registry_status ON doc_registry(status);
"""


class DocumentRegistry:
    """SQLite 文档注册表 — 线程安全。

    用法:
        registry = DocumentRegistry("data/doc_registry.db")
        registry.register("/path/to/doc.txt", "abc123", "sha256...", "hr", ["id1","id2"], "did1")
        row = registry.get_by_path("/path/to/doc.txt")
        registry.mark_deleted("/path/to/doc.txt")
    """

    def __init__(self, db_path: str = "data/doc_registry.db"):
        self._db_path = db_path
        self._lock = threading.Lock()
        self._init_db()

    def _init_db(self):
        """建表（幂等）+ 兼容旧表列迁移。"""
        os.makedirs(os.path.dirname(self._db_path) or ".", exist_ok=True)
        with self._conn() as conn:
            conn.executescript(SCHEMA_SQL)
            # 兼容旧表：追加 metadata 列
            for col, col_def in [("doc_type", "TEXT DEFAULT 'general'"),
                                  ("confidence", "REAL DEFAULT 0"),
                                  ("llm_used", "INTEGER DEFAULT 0")]:
                try:
                    conn.execute(f"SELECT {col} FROM doc_registry LIMIT 1")
                except sqlite3.OperationalError:
                    conn.execute(f"ALTER TABLE doc_registry ADD COLUMN {col} {col_def}")
                    conn.commit()

    def _conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self._db_path)
        conn.row_factory = sqlite3.Row
        return conn

    # ---- 查询 ----

    def get_by_path(self, file_path: str) -> dict | None:
        """按文件路径查询。返回 None 表示未注册。"""
        with self._lock, self._conn() as conn:
            row = conn.execute(
                "SELECT * FROM doc_registry WHERE file_path = ?", (file_path,)
            ).fetchone()
        return dict(row) if row else None

    def get_by_doc_id(self, doc_id: str) -> dict | None:
        """按文档 ID 查询。"""
        with self._lock, self._conn() as conn:
            row = conn.execute(
                "SELECT * FROM doc_registry WHERE doc_id = ?", (doc_id,)
            ).fetchone()
        return dict(row) if row else None

    def list_all(self) -> dict[str, dict]:
        """返回所有注册记录 {file_path: row}。"""
        with self._lock, self._conn() as conn:
            rows = conn.execute("SELECT * FROM doc_registry").fetchall()
        return {r["file_path"]: dict(r) for r in rows}

    def list_active(self) -> list[dict]:
        """返回所有 status='active' 的记录。"""
        with self._lock, self._conn() as conn:
            rows = conn.execute(
                "SELECT * FROM doc_registry WHERE status = 'active'"
            ).fetchall()
        return [dict(r) for r in rows]

    def list_by_kb(self, kb_id: str) -> list[dict]:
        """按知识库 ID 查询。"""
        with self._lock, self._conn() as conn:
            rows = conn.execute(
                "SELECT * FROM doc_registry WHERE kb_id = ? AND status = 'active'",
                (kb_id,),
            ).fetchall()
        return [dict(r) for r in rows]

    def search(
        self,
        keyword: str = "",
        type_filter: str = "",
        status_filter: str = "",
        page: int = 1,
        page_size: int = 20,
    ) -> dict:
        """关键字搜索 + 分页 + 类型/状态过滤。

        返回 {"items": [...], "total": int, "page": int, "page_size": int}
        """
        conditions: list[str] = []
        params: list = []

        if keyword.strip():
            conditions.append("file_name LIKE ?")
            params.append(f"%{keyword.strip()}%")

        if type_filter.strip():
            # 从 file_name 扩展名推断类型
            conditions.append("file_name LIKE ?")
            params.append(f"%.{type_filter.strip()}")

        if status_filter.strip():
            conditions.append("status = ?")
            params.append(status_filter.strip())

        where_clause = ""
        if conditions:
            where_clause = "WHERE " + " AND ".join(conditions)

        with self._lock, self._conn() as conn:
            # 总数
            count_row = conn.execute(
                f"SELECT COUNT(*) FROM doc_registry {where_clause}", params
            ).fetchone()
            total = count_row[0] if count_row else 0

            # 分页
            offset = max(0, (page - 1)) * page_size
            rows = conn.execute(
                f"SELECT * FROM doc_registry {where_clause} ORDER BY updated_at DESC LIMIT ? OFFSET ?",
                params + [page_size, offset],
            ).fetchall()

        return {
            "items": [dict(r) for r in rows],
            "total": total,
            "page": page,
            "page_size": page_size,
        }

    def count(self) -> int:
        """总记录数。"""
        with self._lock, self._conn() as conn:
            return conn.execute("SELECT COUNT(*) FROM doc_registry").fetchone()[0]

    def update_status(self, file_path: str, status: str):
        """更新文档状态（用于索引进度追踪）。"""
        if status not in DOC_STATUSES:
            raise ValueError(f"无效状态: {status}，有效值: {DOC_STATUSES}")
        with self._lock, self._conn() as conn:
            conn.execute(
                "UPDATE doc_registry SET status = ?, updated_at = datetime('now') WHERE file_path = ?",
                (status, file_path),
            )

    # ---- 写入 ----

    def register(
        self,
        file_path: str,
        doc_id: str,
        file_hash: str,
        kb_id: str,
        chunk_ids: list[str],
        doc_db_id: str,
        metadata: dict | None = None,
    ):
        """注册新文档。metadata 可选: {doc_type, confidence, llm_used}。"""
        file_name = os.path.basename(file_path)
        try:
            stat = os.stat(file_path)
            fsize, fmtime = stat.st_size, stat.st_mtime
        except OSError:
            fsize, fmtime = 0, 0.0

        meta = metadata or {}
        doc_type = meta.get("doc_type", "general")
        confidence = meta.get("confidence", 0)
        llm_used = 1 if meta.get("llm_used") else 0

        with self._lock, self._conn() as conn:
            conn.execute(
                """INSERT OR REPLACE INTO doc_registry
                   (file_path, file_name, kb_id, doc_id, file_hash, file_size, file_mtime,
                    chunk_count, chunk_ids, doc_db_id, doc_type, confidence, llm_used,
                    status, last_indexed, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'active', datetime('now'), datetime('now'))""",
                (
                    file_path, file_name, kb_id, doc_id, file_hash,
                    fsize, fmtime,
                    len(chunk_ids), json.dumps(chunk_ids), doc_db_id,
                    doc_type, confidence, llm_used,
                ),
            )

    def update_after_reindex(
        self, file_path: str, file_hash: str, chunk_ids: list[str], doc_db_id: str,
    ):
        """更新已修改文档的元数据。"""
        try:
            stat = os.stat(file_path)
            fsize, fmtime = stat.st_size, stat.st_mtime
        except OSError:
            fsize, fmtime = 0, 0.0
        with self._lock, self._conn() as conn:
            conn.execute(
                """UPDATE doc_registry
                   SET file_hash = ?, file_size = ?, file_mtime = ?,
                       chunk_count = ?, chunk_ids = ?, doc_db_id = ?,
                       status = 'active', last_indexed = datetime('now'),
                       updated_at = datetime('now')
                   WHERE file_path = ?""",
                (
                    file_hash, fsize, fmtime,
                    len(chunk_ids), json.dumps(chunk_ids), doc_db_id,
                    file_path,
                ),
            )

    def mark_deleted(self, file_path: str):
        """标记文档为已删除（软删除）。"""
        with self._lock, self._conn() as conn:
            conn.execute(
                """UPDATE doc_registry
                   SET status = 'deleted', updated_at = datetime('now')
                   WHERE file_path = ?""",
                (file_path,),
            )

    def clear(self):
        """清空注册表（用于全量重建兜底）。"""
        with self._lock, self._conn() as conn:
            conn.execute("DELETE FROM doc_registry")
