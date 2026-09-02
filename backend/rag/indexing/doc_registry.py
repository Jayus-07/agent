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

from backend.shared.logger import logger


# 文档状态枚举（完整生命周期，2026-08-11 加 pending_review）
DOC_STATUSES = ("uploading", "parsing", "embedding", "pending_review", "active", "failed", "deleted")

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
    quality_score REAL DEFAULT 0,
    quality_issues TEXT DEFAULT '',
    embedding_model TEXT DEFAULT '',
    minhash_sig  TEXT DEFAULT '',
    near_dup_id  TEXT DEFAULT '',
    status       TEXT DEFAULT 'active',
    last_indexed TEXT,
    created_at   TEXT DEFAULT (datetime('now')),
    updated_at   TEXT DEFAULT (datetime('now')),
    metadata_fingerprint TEXT DEFAULT '',
    doc_version  INTEGER DEFAULT 1,
    kb_version   TEXT DEFAULT 'v1',
    department   TEXT DEFAULT '',
    summary      TEXT DEFAULT '',
    keywords     TEXT DEFAULT '',
    time_refs    TEXT DEFAULT '',
    business_domain TEXT DEFAULT '',
    complexity   TEXT DEFAULT ''
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
        """建表（幂等）。开发阶段删除 .db 文件即可重建。"""
        os.makedirs(os.path.dirname(self._db_path) or ".", exist_ok=True)
        with self._conn() as conn:
            conn.executescript(SCHEMA_SQL)

    def _conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self._db_path)
        conn.row_factory = sqlite3.Row
        return conn

    # ---- 查询 ----

    def get_by_path(self, file_path: str) -> dict | None:
        """按文件路径查询。返回 None 表示未注册。

        P1 fix: 同时匹配 raw path 和 realpath（解析符号链接）。
        历史脏数据可能用 backend/data 前缀或 project-root/data 前缀，
        两者指向同一物理目录。统一用 OR 查询兼容。
        """
        import os as _os
        normalized = _os.path.realpath(file_path) if file_path else file_path
        with self._lock, self._conn() as conn:
            row = conn.execute(
                "SELECT * FROM doc_registry WHERE file_path IN (?, ?) ORDER BY updated_at DESC LIMIT 1",
                (file_path, normalized),
            ).fetchone()
        return dict(row) if row else None

    def get_by_doc_id(self, doc_id: str) -> dict | None:
        """按文档 ID 查询。

        F2 加固：历史重索引 bug 会让同一 doc_id 出现多行（不同 file_path、
        归属不一）。原实现 fetchone() 无排序，命中哪行取决于 rowid 顺序，
        曾导致 reindex 读取到损坏行后把文件复制到默认归属目录。
        现固定优先级：active 行优先，其次按 updated_at 最新。
        """
        with self._lock, self._conn() as conn:
            row = conn.execute(
                "SELECT * FROM doc_registry WHERE doc_id = ? "
                "ORDER BY CASE WHEN status = 'active' THEN 0 ELSE 1 END, "
                "updated_at DESC LIMIT 1",
                (doc_id,),
            ).fetchone()
        return dict(row) if row else None

    def count_active_by_doc_id(self, doc_id: str) -> int:
        """统计某 doc_id 的活跃行数（>1 说明存在重复数据，调用方应拒绝操作）。"""
        with self._lock, self._conn() as conn:
            return conn.execute(
                "SELECT COUNT(*) FROM doc_registry "
                "WHERE doc_id = ? AND status = 'active'",
                (doc_id,),
            ).fetchone()[0]

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

    def list_by_doc_type(self, doc_type: str, limit: int = 50) -> list[dict]:
        """按文档类型查询（供 MinHash 去重）。"""
        with self._lock, self._conn() as conn:
            rows = conn.execute(
                "SELECT doc_id, minhash_sig FROM doc_registry WHERE doc_type = ? AND status = 'active' LIMIT ?",
                (doc_type, limit),
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
        doc_type: str = "",
        kb_id: str = "",
        department: str = "",
        confidence_min: float = 0,
        llm_used: bool | None = None,
        quality_min: float = 0,
        sort_by: str = "updated_at",
        page: int = 1,
        page_size: int = 20,
    ) -> dict:
        """关键字搜索 + 元数据过滤 + 分页。

        Returns {"items": [...], "total": int, "page": int, "page_size": int}
        """
        conditions: list[str] = []
        params: list = []

        if keyword.strip():
            conditions.append("(file_name LIKE ? OR doc_type LIKE ?)")
            kw = f"%{keyword.strip()}%"
            params.extend([kw, kw])

        if type_filter.strip():
            conditions.append("file_name LIKE ?")
            params.append(f"%.{type_filter.strip()}")

        if status_filter.strip():
            conditions.append("status = ?")
            params.append(status_filter.strip())

        if kb_id.strip():
            conditions.append("kb_id = ?")
            params.append(kb_id.strip())

        if department.strip():
            conditions.append("department = ?")
            params.append(department.strip())

        if doc_type.strip():
            types = [t.strip() for t in doc_type.split(",") if t.strip()]
            if types:
                placeholders = ",".join(["?"] * len(types))
                conditions.append(f"doc_type IN ({placeholders})")
                params.extend(types)

        if confidence_min > 0:
            conditions.append("confidence >= ?")
            params.append(confidence_min)

        if llm_used is not None:
            conditions.append("llm_used = ?")
            params.append(1 if llm_used else 0)

        if quality_min > 0:
            conditions.append("quality_score >= ?")
            params.append(quality_min)

        sort_col = "updated_at" if sort_by not in ("confidence", "quality_score", "created_at", "updated_at") else sort_by

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
                f"SELECT * FROM doc_registry {where_clause} ORDER BY {sort_col} DESC LIMIT ? OFFSET ?",
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
        quality_score = meta.get("quality_score", 0)
        quality_issues = meta.get("quality_issues", "")
        embedding_model = meta.get("embedding_model", "")
        minhash_sig = meta.get("minhash_sig", "")
        near_dup_id = meta.get("near_dup_id", "")
        # Bug2 fix: 补 5 个文档级元数据字段
        summary = meta.get("summary", "")
        keywords = meta.get("keywords", "")
        time_refs = meta.get("time_refs", "")
        business_domain = meta.get("business_domain", "")
        complexity = meta.get("complexity", "")
        metadata_fingerprint = meta.get("metadata_fingerprint", "")
        doc_version = meta.get("doc_version", 1)
        kb_version = meta.get("kb_version", "v1")
        department = meta.get("department", "")

        with self._lock, self._conn() as conn:
            conn.execute(
                """INSERT OR REPLACE INTO doc_registry
                   (file_path, file_name, kb_id, doc_id, file_hash, file_size, file_mtime,
                    chunk_count, chunk_ids, doc_db_id, doc_type, confidence, llm_used,
                    quality_score, quality_issues, embedding_model, minhash_sig, near_dup_id,
                    summary, keywords, time_refs, business_domain, complexity,
                    metadata_fingerprint, doc_version, kb_version, department,
                    status, last_indexed, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'active', datetime('now'), datetime('now'))""",
                (
                    file_path, file_name, kb_id, doc_id, file_hash,
                    fsize, fmtime,
                    len(chunk_ids), json.dumps(chunk_ids), doc_db_id,
                    doc_type, confidence, llm_used,
                    quality_score, quality_issues, embedding_model,
                    minhash_sig, near_dup_id,
                    summary, keywords, time_refs, business_domain, complexity,
                    metadata_fingerprint, doc_version, kb_version, department,
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

    def count_by_kb_id(self, kb_id: str) -> int:
        """统计某个知识库的活跃文档数。"""
        with self._lock, self._conn() as conn:
            row = conn.execute(
                "SELECT COUNT(*) FROM doc_registry WHERE kb_id = ? AND status = 'active'",
                (kb_id,),
            ).fetchone()
        return row[0] if row else 0

    def mark_deleted(self, file_path: str):
        """标记文档为已删除（软删除，按 file_path）。"""
        with self._lock, self._conn() as conn:
            conn.execute(
                """UPDATE doc_registry
                   SET status = 'deleted', updated_at = datetime('now')
                   WHERE file_path = ?""",
                (file_path,),
            )

    def mark_deleted_by_doc_id(self, doc_id: str) -> int:
        """按 doc_id 软删所有行（修复绝对/相对路径导致的重复行问题）。
        返回受影响行数。"""
        with self._lock, self._conn() as conn:
            cur = conn.execute(
                """UPDATE doc_registry
                   SET status = 'deleted', updated_at = datetime('now')
                   WHERE doc_id = ? AND status = 'active'""",
                (doc_id,),
            )
            return cur.rowcount

    def update_status_by_doc_id(self, doc_id: str, new_status: str) -> int:
        """按 doc_id 更新状态（用于审核 dashboard，2026-08-11）。

        Returns: 实际更新的行数。0 表示 doc_id 不存在或状态不允许。
        """
        if new_status not in DOC_STATUSES:
            raise ValueError(f"无效状态: {new_status}，有效值: {DOC_STATUSES}")
        with self._lock, self._conn() as conn:
            cur = conn.execute(
                """UPDATE doc_registry
                   SET status = ?, updated_at = datetime('now')
                   WHERE doc_id = ? AND status IN ('pending_review', 'active')""",
                (new_status, doc_id),
            )
            return cur.rowcount

    def list_pending_review(self, page: int = 1, page_size: int = 20) -> dict:
        """列出待审核文档（status='pending_review'，按 confidence 升序）。"""
        offset = (page - 1) * page_size
        with self._lock, self._conn() as conn:
            total = conn.execute(
                "SELECT COUNT(*) FROM doc_registry WHERE status = 'pending_review'"
            ).fetchone()[0]
            rows = conn.execute(
                """SELECT * FROM doc_registry
                   WHERE status = 'pending_review'
                   ORDER BY confidence ASC, updated_at DESC
                   LIMIT ? OFFSET ?""",
                (page_size, offset),
            ).fetchall()
            columns = [d[0] for d in conn.execute("SELECT * FROM doc_registry LIMIT 1").description]
            items = [dict(zip(columns, row)) for row in rows]
        return {"items": items, "total": total, "page": page, "page_size": page_size}

    def clear(self):
        """清空注册表（用于全量重建兜底）。"""
        with self._lock, self._conn() as conn:
            conn.execute("DELETE FROM doc_registry")

    # ── 文档生命周期（2026-08-11 P2）──
    def ensure_expire_at_column(self) -> None:
        """兼容老数据库：若 expire_at 字段缺失则 ALTER TABLE 添加。"""
        with self._lock, self._conn() as conn:
            cols = [row[1] for row in conn.execute("PRAGMA table_info(doc_registry)").fetchall()]
            if "expire_at" not in cols:
                conn.execute("ALTER TABLE doc_registry ADD COLUMN expire_at TEXT")
                conn.commit()
                logger.info("[DocRegistry] 已添加 expire_at 字段")

    def bump_doc_version(self, doc_id: str, delta: int = 1) -> int:
        """公开方法：增加 doc_version（替代 reindex_file 直接访问私有 _lock/_conn）。

        P2-3:不再让 reindex_file 绕过封装直接写 SQL。
        - 成功:返回 bump 后的新版本号
        - doc_id 不存在或状态非 active:返回 -1
        """
        if not doc_id:
            return -1
        with self._lock, self._conn() as conn:
            # 当前值(原子读)
            cur_row = conn.execute(
                "SELECT doc_version FROM doc_registry WHERE doc_id = ? AND status = 'active'",
                (doc_id,),
            ).fetchone()
            if cur_row is None:
                return -1
            cur_version = cur_row["doc_version"]
            new_version = cur_version + delta
            conn.execute(
                """UPDATE doc_registry
                   SET doc_version = ?, updated_at = datetime('now')
                   WHERE doc_id = ? AND status = 'active'""",
                (new_version, doc_id),
            )
            conn.commit()
            return new_version

    def set_expire_at(self, doc_id: str, expire_at: str) -> int:
        """设置文档过期时间（ISO 8601 格式，2026-08-11）。"""
        with self._lock, self._conn() as conn:
            cur = conn.execute(
                """UPDATE doc_registry
                   SET expire_at = ?, updated_at = datetime('now')
                   WHERE doc_id = ? AND status = 'active'""",
                (expire_at, doc_id),
            )
            conn.commit()
            return cur.rowcount

    def list_expired(self, now: str | None = None) -> list[dict]:
        """列出已过期文档（expire_at <= now AND status='active'，2026-08-11）。"""
        from datetime import datetime
        now = now or datetime.now().isoformat()[:10]
        with self._lock, self._conn() as conn:
            rows = conn.execute(
                """SELECT * FROM doc_registry
                   WHERE status = 'active' AND expire_at IS NOT NULL AND expire_at <= ?""",
                (now,),
            ).fetchall()
            columns = [d[0] for d in conn.execute("SELECT * FROM doc_registry LIMIT 1").description]
            return [dict(zip(columns, row)) for row in rows]

    def archive_expired(self, now: str | None = None) -> int:
        """将过期文档标记为 deleted（软删除，2026-08-11）。"""
        expired = self.list_expired(now=now)
        if not expired:
            return 0
        doc_ids = list({d["doc_id"] for d in expired})
        with self._lock, self._conn() as conn:
            for doc_id in doc_ids:
                conn.execute(
                    """UPDATE doc_registry
                       SET status = 'deleted', updated_at = datetime('now')
                       WHERE doc_id = ? AND status = 'active'""",
                    (doc_id,),
                )
            conn.commit()
        return len(doc_ids)
