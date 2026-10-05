"""PostgresDocumentRegistry — doc_registry 的 PostgreSQL 连接层（R1/C19）。

与 SQLite 版 `DocumentRegistry` 对外接口完全一致（同方法名、同参数、同返回结构），
仅替换连接层与 SQL 方言。PG 为唯一实现（2026-09-17 SQLite 轨删除）。

方言映射（SQLite → PostgreSQL）：
  - `?` 占位符            → `%s`
  - `INSERT OR REPLACE`   → `INSERT ... ON CONFLICT (file_path) DO UPDATE`
  - `datetime('now')`(UTC)→ `to_char(now() AT TIME ZONE 'UTC', 'YYYY-MM-DD HH24:MI:SS')`
    （列保持 TEXT，字符串比较排序语义与 SQLite 一致）
  - `LIKE`                → `ILIKE`（SQLite LIKE 对 ASCII 不区分大小写，PG 需 ILIKE 等价）
  - `PRAGMA table_info`   → `information_schema.columns`
  - sqlite3 隐式事务      → psycopg2 显式 commit/rollback（每次操作用完即关连接，
    避免 PG 连接泄漏打满 max_connections）
"""

from __future__ import annotations

import os
import threading
from contextlib import contextmanager
from typing import Any, Iterator

import psycopg2
import psycopg2.extras

from backend.config.database import DOC_REGISTRY_PG_CONFIG, DOC_REGISTRY_PG_TABLE
from backend.infra.db import engine_for
from backend.rag.indexing.doc_registry import (
    DOC_STATUSES,
    VERSION_GOVERNANCE_COLUMNS,
    DocumentRegistry,
)
from backend.shared.logger import logger

# UTC 时间文本，语义等价 SQLite 的 datetime('now')
_NOW_SQL = "to_char(now() AT TIME ZONE 'UTC', 'YYYY-MM-DD HH24:MI:SS')"

_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS {table} (
    file_path    TEXT PRIMARY KEY,
    file_name    TEXT NOT NULL,
    kb_id        TEXT NOT NULL,
    doc_id       TEXT NOT NULL,
    file_hash    TEXT NOT NULL,
    file_size    BIGINT NOT NULL DEFAULT 0,
    file_mtime   DOUBLE PRECISION NOT NULL DEFAULT 0,
    chunk_count  INTEGER DEFAULT 0,
    chunk_ids    TEXT DEFAULT '[]',
    doc_db_id    TEXT,
    doc_type     TEXT DEFAULT 'general',
    confidence   DOUBLE PRECISION DEFAULT 0,
    llm_used     INTEGER DEFAULT 0,
    quality_score DOUBLE PRECISION DEFAULT 0,
    quality_issues TEXT DEFAULT '',
    embedding_model TEXT DEFAULT '',
    minhash_sig  TEXT DEFAULT '',
    near_dup_id  TEXT DEFAULT '',
    status       TEXT DEFAULT 'active',
    last_indexed TEXT,
    created_at   TEXT DEFAULT ({now}),
    updated_at   TEXT DEFAULT ({now}),
    metadata_fingerprint TEXT DEFAULT '',
    doc_version  INTEGER DEFAULT 1,
    kb_version   TEXT DEFAULT 'v1',
    department   TEXT DEFAULT '',
    summary      TEXT DEFAULT '',
    keywords     TEXT DEFAULT '',
    time_refs    TEXT DEFAULT '',
    business_domain TEXT DEFAULT '',
    complexity   TEXT DEFAULT '',
    permission_scope TEXT DEFAULT 'general',
    fixture_set   TEXT DEFAULT '',
    version_id   TEXT DEFAULT '',
    effective_from TEXT,
    effective_to TEXT,
    supersedes_version_id TEXT DEFAULT '',
    source_priority INTEGER DEFAULT 0,
    quality_status TEXT DEFAULT 'unknown',
    last_processing_run_id TEXT DEFAULT '',
    pipeline_version TEXT DEFAULT '',
    metadata_route TEXT DEFAULT '',
    ocr_used BOOLEAN DEFAULT FALSE,
    ocr_model TEXT DEFAULT '',
    metadata_model TEXT DEFAULT '',
    model_count INTEGER DEFAULT 0,
    processing_status TEXT DEFAULT '',
    processing_finished_at TEXT,
    active_generation TEXT DEFAULT '',
    expire_at    TEXT
);
CREATE INDEX IF NOT EXISTS idx_{table}_doc_id ON {table}(doc_id);
CREATE INDEX IF NOT EXISTS idx_{table}_kb_id ON {table}(kb_id);
CREATE INDEX IF NOT EXISTS idx_{table}_status ON {table}(status);
"""

# register() 的 upsert 列集合收敛为 _build_register_row() 的行 dict
# （2026-10-01 B 阶段：register 与 register_published 共用行构造，防两套
# 列清单漂移；last_indexed/updated_at 不走参数位，由 _NOW_SQL 生成）


class PostgresDocumentRegistry(DocumentRegistry):
    """doc_registry 的 PostgreSQL 实现 — DocumentRegistry 子类（isinstance 兼容）。

    db_path 参数保留但被忽略（签名兼容 SQLite 版调用方）；实际连接参数来自
    `DOC_REGISTRY_PG_CONFIG`，表名来自 `DOC_REGISTRY_PG_TABLE`（可用 env 覆盖）。
    """

    def __init__(self, db_path: str = "data/doc_registry.db"):
        self._db_path = db_path  # 兼容保留，PG 模式下无意义
        self._lock = threading.Lock()
        self._table = os.getenv("DOC_REGISTRY_PG_TABLE", DOC_REGISTRY_PG_TABLE)
        self._init_db()

    # ---- 连接层 ----

    @contextmanager
    def _conn(self) -> Iterator[Any]:
        """每次操作独立连接：成功 commit、异常 rollback、退出必关（防连接泄漏）。"""
        conn = engine_for(DOC_REGISTRY_PG_CONFIG).raw_connection()
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def _exec(self, conn: Any, sql: str, params: tuple = ()) -> Any:
        """dict 行游标（对齐 sqlite3.Row → dict(row) 的返回形态）。"""
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cur.execute(sql, params)
        return cur

    def _exec_scalar(self, conn: Any, sql: str, params: tuple = ()) -> Any:
        """普通游标（用于 COUNT(*) 等 row[0] 取标量的场景）。"""
        cur = conn.cursor()
        cur.execute(sql, params)
        return cur

    def _init_db(self):
        """建表（幂等），与 backend/sql/migrations/010_doc_registry_pg.sql 保持一致。"""
        with self._lock, self._conn() as conn:
            conn.cursor().execute(_SCHEMA_SQL.format(table=self._table, now=_NOW_SQL))
            self._ensure_columns(conn)

    def _ensure_columns(self, conn) -> None:
        """存量表惰性加列（幂等），与 SQLite 版 _ensure_columns 对齐。

        R4 版本治理（§6 治理 11 字段）：六列惰性补齐，与 011 迁移脚本等价
        （应用侧建表即生效，脚本供容器 init / 手工 psql 两条路）。
        """
        existing = {
            r["column_name"]
            for r in self._exec(
                conn,
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_name = %s",
                (self._table,),
            ).fetchall()
        }
        if "permission_scope" not in existing:
            conn.cursor().execute(
                f"ALTER TABLE {self._table} "
                "ADD COLUMN permission_scope TEXT DEFAULT 'general'"
            )
            logger.info("[doc_registry_pg] 迁移：补列 permission_scope（默认 general）")
        if "fixture_set" not in existing:
            conn.cursor().execute(
                f"ALTER TABLE {self._table} ADD COLUMN fixture_set TEXT DEFAULT ''"
            )
            logger.info("[doc_registry_pg] 迁移：补列 fixture_set（评测范围，生产默认为空）")
        for col, coldef, desc in VERSION_GOVERNANCE_COLUMNS:
            if col not in existing:
                conn.cursor().execute(
                    f"ALTER TABLE {self._table} ADD COLUMN {col} {coldef}"
                )
                logger.info(f"[doc_registry_pg] 迁移：补列 {col}（{desc}）")
        lineage_columns = (
            ("last_processing_run_id", "TEXT DEFAULT ''"),
            ("pipeline_version", "TEXT DEFAULT ''"),
            ("metadata_route", "TEXT DEFAULT ''"),
            ("ocr_used", "BOOLEAN DEFAULT FALSE"),
            ("ocr_model", "TEXT DEFAULT ''"),
            ("metadata_model", "TEXT DEFAULT ''"),
            ("model_count", "INTEGER DEFAULT 0"),
            ("processing_status", "TEXT DEFAULT ''"),
            ("processing_finished_at", "TEXT"),
        )
        for col, coldef in lineage_columns:
            if col not in existing:
                conn.cursor().execute(
                    f"ALTER TABLE {self._table} ADD COLUMN {col} {coldef}"
                )
                logger.info(f"[doc_registry_pg] 迁移：补列 {col}（处理血缘）")
        if "active_generation" not in existing:
            conn.cursor().execute(
                f"ALTER TABLE {self._table} ADD COLUMN active_generation TEXT DEFAULT ''"
            )
            logger.info("[doc_registry_pg] 迁移：补列 active_generation（候选版本发布指针，B 阶段）")

    # ---- 查询 ----

    def get_by_path(self, file_path: str) -> dict | None:
        normalized = os.path.realpath(file_path) if file_path else file_path
        with self._lock, self._conn() as conn:
            row = self._exec(
                conn,
                f"SELECT * FROM {self._table} WHERE file_path IN (%s, %s) "
                "ORDER BY updated_at DESC, file_path LIMIT 1",
                (file_path, normalized),
            ).fetchone()
        return dict(row) if row else None

    def get_by_doc_id(self, doc_id: str) -> dict | None:
        with self._lock, self._conn() as conn:
            row = self._exec(
                conn,
                f"SELECT * FROM {self._table} WHERE doc_id = %s "
                "ORDER BY CASE WHEN status = 'active' THEN 0 ELSE 1 END, "
                "updated_at DESC, file_path LIMIT 1",
                (doc_id,),
            ).fetchone()
        return dict(row) if row else None

    def count_active_by_doc_id(self, doc_id: str) -> int:
        with self._lock, self._conn() as conn:
            row = self._exec_scalar(
                conn,
                f"SELECT COUNT(*) FROM {self._table} WHERE doc_id = %s AND status = 'active'",
                (doc_id,),
            ).fetchone()
        return row[0]

    def list_all(self) -> dict[str, dict]:
        with self._lock, self._conn() as conn:
            rows = self._exec(conn, f"SELECT * FROM {self._table}").fetchall()
        return {r["file_path"]: dict(r) for r in rows}

    def list_active(self) -> list[dict]:
        with self._lock, self._conn() as conn:
            rows = self._exec(
                conn, f"SELECT * FROM {self._table} WHERE status = 'active'"
            ).fetchall()
        return [dict(r) for r in rows]

    def list_by_statuses(self, statuses: tuple[str, ...] | list[str]) -> list[dict]:
        if not statuses:
            return []
        placeholders = ",".join(["%s"] * len(statuses))
        with self._lock, self._conn() as conn:
            rows = self._exec(
                conn,
                f"SELECT * FROM {self._table} WHERE status IN ({placeholders})",
                tuple(statuses),
            ).fetchall()
        return [dict(r) for r in rows]

    def register_in_progress(
        self,
        file_path: str,
        doc_id: str,
        file_hash: str,
        kb_id: str,
        department: str = "",
        doc_type: str = "general",
    ):
        normalized = os.path.realpath(file_path) if file_path else file_path
        try:
            stat = os.stat(file_path)
            fsize, fmtime = stat.st_size, stat.st_mtime
        except OSError:
            fsize, fmtime = 0, 0.0
        with self._lock, self._conn() as conn:
            cur = self._exec(
                conn,
                f"""UPDATE {self._table}
                   SET status = 'parsing', file_hash = %s, updated_at = { _NOW_SQL }
                   WHERE file_path IN (%s, %s)""",
                (file_hash, file_path, normalized),
            )
            if cur.rowcount == 0:
                self._exec(
                    conn,
                    f"""INSERT INTO {self._table}
                       (file_path, file_name, kb_id, doc_id, file_hash, file_size,
                        file_mtime, doc_type, department, status, updated_at)
                       VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, 'parsing', { _NOW_SQL })
                       ON CONFLICT (file_path) DO NOTHING""",
                    (
                        file_path, os.path.basename(file_path), kb_id, doc_id,
                        file_hash, fsize, fmtime, doc_type, department,
                    ),
                )

    def list_by_doc_type(self, doc_type: str, limit: int = 50) -> list[dict]:
        with self._lock, self._conn() as conn:
            rows = self._exec(
                conn,
                f"SELECT doc_id, minhash_sig FROM {self._table} "
                "WHERE doc_type = %s AND status = 'active' LIMIT %s",
                (doc_type, limit),
            ).fetchall()
        return [dict(r) for r in rows]

    def list_by_kb(self, kb_id: str) -> list[dict]:
        with self._lock, self._conn() as conn:
            rows = self._exec(
                conn,
                f"SELECT * FROM {self._table} WHERE kb_id = %s AND status = 'active'",
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
        kb_scope: list[str] | None = None,
        visible_scopes: list[str] | None = None,
    ) -> dict:
        """kb_scope/visible_scopes（2026-10-01 权限收口）：授权范围下推到 SQL，
        行集与 COUNT(total) 同条件——列表页不允许「总数全库、行集截断」的
        口径分裂。None = 调用方未启用授权（仅内部直调），空列表 = 授权后
        无可见范围（正确返回 0 行）。"""
        conditions: list[str] = []
        params: list = []

        if kb_scope is not None:
            conditions.append("kb_id = ANY(%s)")
            params.append([str(k) for k in kb_scope])
        if visible_scopes is not None:
            # 存量行 permission_scope 为空串 → 视作 general（与建表默认一致）
            conditions.append(
                "COALESCE(NULLIF(permission_scope, ''), 'general') = ANY(%s)"
            )
            params.append([str(s) for s in visible_scopes])

        if keyword.strip():
            conditions.append("(file_name ILIKE %s OR doc_type ILIKE %s)")
            kw = f"%{keyword.strip()}%"
            params.extend([kw, kw])

        if type_filter.strip():
            conditions.append("file_name ILIKE %s")
            params.append(f"%.{type_filter.strip()}")

        if status_filter.strip():
            conditions.append("status = %s")
            params.append(status_filter.strip())

        if kb_id.strip():
            conditions.append("kb_id = %s")
            params.append(kb_id.strip())

        if department.strip():
            conditions.append("department = %s")
            params.append(department.strip())

        if doc_type.strip():
            types = [t.strip() for t in doc_type.split(",") if t.strip()]
            if types:
                placeholders = ",".join(["%s"] * len(types))
                conditions.append(f"doc_type IN ({placeholders})")
                params.extend(types)

        if confidence_min > 0:
            conditions.append("confidence >= %s")
            params.append(confidence_min)

        if llm_used is not None:
            conditions.append("llm_used = %s")
            params.append(1 if llm_used else 0)

        if quality_min > 0:
            conditions.append("quality_score >= %s")
            params.append(quality_min)

        sort_col = "updated_at" if sort_by not in ("confidence", "quality_score", "created_at", "updated_at") else sort_by

        where_clause = ""
        if conditions:
            where_clause = "WHERE " + " AND ".join(conditions)

        with self._lock, self._conn() as conn:
            count_row = self._exec_scalar(
                conn, f"SELECT COUNT(*) FROM {self._table} {where_clause}", tuple(params)
            ).fetchone()
            total = count_row[0] if count_row else 0

            offset = max(0, (page - 1)) * page_size
            rows = self._exec(
                conn,
                f"SELECT * FROM {self._table} {where_clause} "
                f"ORDER BY {sort_col} DESC, file_path LIMIT %s OFFSET %s",
                tuple(params) + (page_size, offset),
            ).fetchall()

        return {
            "items": [dict(r) for r in rows],
            "total": total,
            "page": page,
            "page_size": page_size,
        }

    def count(self) -> int:
        with self._lock, self._conn() as conn:
            row = self._exec_scalar(conn, f"SELECT COUNT(*) FROM {self._table}").fetchone()
        return row[0]

    def update_status(self, file_path: str, status: str):
        if status not in DOC_STATUSES:
            raise ValueError(f"无效状态: {status}，有效值: {DOC_STATUSES}")
        with self._lock, self._conn() as conn:
            self._exec(
                conn,
                f"UPDATE {self._table} SET status = %s, updated_at = { _NOW_SQL } "
                "WHERE file_path = %s",
                (status, file_path),
            )

    def update_fields(self, file_path: str, fields: dict):
        """白名单字段部分更新（全量重建后回填快照字段用，防注入只放行元数据列）。"""
        allowed = {
            "minhash_sig", "near_dup_id", "doc_type", "summary", "keywords",
            "time_refs", "business_domain", "complexity", "quality_score",
            "quality_issues", "confidence", "permission_scope",
            "fixture_set",
            # §6 治理 11 字段（R4）：版本治理元数据可回填
            "version_id", "effective_from", "effective_to",
            "supersedes_version_id", "source_priority", "quality_status",
            "last_processing_run_id", "pipeline_version", "metadata_route",
            "ocr_used", "ocr_model", "metadata_model", "model_count", "processing_status",
            "processing_finished_at",
        }
        sets = {k: v for k, v in (fields or {}).items() if k in allowed}
        if not sets:
            return
        clause = ", ".join(f"{k} = %s" for k in sets)
        with self._lock, self._conn() as conn:
            self._exec(
                conn,
                f"UPDATE {self._table} SET {clause}, updated_at = { _NOW_SQL } "
                "WHERE file_path = %s",
                [*sets.values(), file_path],
            )

    # ---- 写入 ----

    def _build_register_row(
        self,
        file_path: str,
        doc_id: str,
        file_hash: str,
        kb_id: str,
        chunk_ids: list[str],
        doc_db_id: str,
        metadata: dict | None = None,
    ) -> dict[str, Any]:
        """register/register_published 共用的行构造（列→值，含 status 判定）。"""
        file_name = os.path.basename(file_path)
        try:
            stat = os.stat(file_path)
            fsize, fmtime = stat.st_size, stat.st_mtime
        except OSError:
            fsize, fmtime = 0, 0.0

        meta = metadata or {}
        # 候选发布：注册时正式文件可能尚未就位（发布协议先 CAS 后 replace），
        # 调用方经 metadata 透传暂存文件实测的 size/mtime（发布即真实值）
        fsize = int(meta.get("_file_size") or fsize or 0)
        fmtime = float(meta.get("_file_mtime") or fmtime or 0.0)
        doc_type = meta.get("doc_type", "general")
        confidence = meta.get("confidence", 0)
        llm_used = 1 if meta.get("llm_used") else 0
        quality_score = meta.get("quality_score", 0)
        quality_issues = meta.get("quality_issues", "")
        embedding_model = meta.get("embedding_model", "")
        minhash_sig = meta.get("minhash_sig", "")
        near_dup_id = meta.get("near_dup_id", "")
        summary = meta.get("summary", "")
        keywords = meta.get("keywords", "")
        time_refs = meta.get("time_refs", "")
        business_domain = meta.get("business_domain", "")
        complexity = meta.get("complexity", "")
        metadata_fingerprint = meta.get("metadata_fingerprint", "")
        doc_version = meta.get("doc_version", 1)
        kb_version = meta.get("kb_version", "v1")
        department = meta.get("department", "")
        permission_scope = meta.get("permission_scope", "general")
        fixture_set = meta.get("fixture_set", "")
        # §6 治理 11 字段（R4 版本治理），与 SQLite 版 register 语义一致
        version_id = meta.get("version_id", "")
        effective_from = meta.get("effective_from") or None
        effective_to = meta.get("effective_to") or None
        supersedes_version_id = meta.get("supersedes_version_id", "")
        source_priority = meta.get("source_priority", 0)
        quality_status = meta.get("quality_status", "unknown")
        last_processing_run_id = meta.get("last_processing_run_id", "")
        pipeline_version = meta.get("pipeline_version", "")
        metadata_route = meta.get("metadata_route", "")
        ocr_used = bool(meta.get("ocr_used", False))
        ocr_model = meta.get("ocr_model", "")
        metadata_model = meta.get("metadata_model", "")
        model_count = int(meta.get("model_count") or 0)
        processing_status = meta.get("processing_status", "")
        processing_finished_at = meta.get("processing_finished_at") or None

        status = "pending_review" if near_dup_id else "active"
        return {
            "file_path": file_path, "file_name": file_name, "kb_id": kb_id,
            "doc_id": doc_id, "file_hash": file_hash, "file_size": fsize,
            "file_mtime": fmtime, "chunk_count": len(chunk_ids),
            "chunk_ids": psycopg2.extras.Json(chunk_ids), "doc_db_id": doc_db_id,
            "doc_type": doc_type, "confidence": confidence, "llm_used": llm_used,
            "quality_score": quality_score, "quality_issues": quality_issues,
            "embedding_model": embedding_model, "minhash_sig": minhash_sig,
            "near_dup_id": near_dup_id, "summary": summary, "keywords": keywords,
            "time_refs": time_refs, "business_domain": business_domain,
            "complexity": complexity, "metadata_fingerprint": metadata_fingerprint,
            "doc_version": doc_version, "kb_version": kb_version,
            "department": department, "permission_scope": permission_scope,
            "fixture_set": fixture_set, "version_id": version_id,
            "effective_from": effective_from, "effective_to": effective_to,
            "supersedes_version_id": supersedes_version_id,
            "source_priority": source_priority, "quality_status": quality_status,
            "last_processing_run_id": last_processing_run_id,
            "pipeline_version": pipeline_version, "metadata_route": metadata_route,
            "ocr_used": ocr_used, "ocr_model": ocr_model,
            "metadata_model": metadata_model, "model_count": model_count,
            "processing_status": processing_status,
            "processing_finished_at": processing_finished_at,
            "status": status,
        }

    @staticmethod
    def _upsert_sql(table: str, row: dict[str, Any], guard: str = "") -> tuple[str, tuple]:
        """行 dict → 条件 upsert SQL。guard 非空时在 DO UPDATE 后追加 WHERE 守卫；
        守卫的占位参数由调用方追加到 params 末尾（register_published）。"""
        cols = list(row.keys())
        all_cols = ", ".join(cols) + ", last_indexed, updated_at"
        placeholders = ", ".join(["%s"] * len(cols)) + f", {_NOW_SQL}, {_NOW_SQL}"
        updates = ", ".join(f"{c} = EXCLUDED.{c}" for c in cols if c != "file_path")
        updates += ", last_indexed = EXCLUDED.last_indexed, updated_at = EXCLUDED.updated_at"
        sql = (
            f"INSERT INTO {table} ({all_cols}) VALUES ({placeholders}) "
            f"ON CONFLICT (file_path) DO UPDATE SET {updates}"
        )
        if guard:
            sql += f" WHERE {guard}"
        return sql, tuple(row.values())

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
        row = self._build_register_row(
            file_path, doc_id, file_hash, kb_id, chunk_ids, doc_db_id, metadata)
        # 旧 register 不碰 active_generation（保留既有值）：直接 register 的
        # 路径（重索引/入库脚本）语义是「原地覆盖当前版本」，发布指针不变
        with self._lock, self._conn() as conn:
            self._exec(conn, self._upsert_sql(self._table, row)[0], tuple(row.values()))

    def register_published(
        self,
        file_path: str,
        doc_id: str,
        file_hash: str,
        kb_id: str,
        chunk_ids: list[str],
        doc_db_id: str,
        metadata: dict | None = None,
        *,
        active_generation: str = "",
        expected_base_generation: str = "",
    ) -> int:
        """候选版本的条件发布（B 阶段提交点）。

        与 register() 同一套行构造，额外：
          - 写入 active_generation = 本次候选代次；
          - ON CONFLICT DO UPDATE ... WHERE 基准守卫：仅当当前 active_generation
            等于认领时观测的 expected_base_generation 才允许覆盖——两个并发
            候选同时发布同一逻辑文档时，后到者 rowcount=0（输掉 CAS，必须
            自行清理候选并报 superseded），先到者成为唯一发布者。
        首次上传（无行）走 INSERT 分支，守卫不生效（无被覆盖对象）。
        返回 1 = 发布成功；0 = CAS 失败（已有更新的发布者）。
        """
        row = self._build_register_row(
            file_path, doc_id, file_hash, kb_id, chunk_ids, doc_db_id, metadata)
        row["active_generation"] = active_generation
        guard = f"{self._table}.active_generation IS NOT DISTINCT FROM %s"
        sql, params = self._upsert_sql(self._table, row, guard=guard)
        params = params + (expected_base_generation,)
        with self._lock, self._conn() as conn:
            cur = self._exec(conn, sql, params)
            return cur.rowcount

    def update_after_reindex(
        self, file_path: str, file_hash: str, chunk_ids: list[str], doc_db_id: str,
    ):
        try:
            stat = os.stat(file_path)
            fsize, fmtime = stat.st_size, stat.st_mtime
        except OSError:
            fsize, fmtime = 0, 0.0
        with self._lock, self._conn() as conn:
            self._exec(
                conn,
                f"""UPDATE {self._table}
                   SET file_hash = %s, file_size = %s, file_mtime = %s,
                       chunk_count = %s, chunk_ids = %s, doc_db_id = %s,
                       status = 'active', last_indexed = { _NOW_SQL },
                       updated_at = { _NOW_SQL }
                   WHERE file_path = %s""",
                (
                    file_hash, fsize, fmtime,
                    len(chunk_ids), psycopg2.extras.Json(chunk_ids), doc_db_id,
                    file_path,
                ),
            )

    def count_by_kb_id(self, kb_id: str) -> int:
        with self._lock, self._conn() as conn:
            row = self._exec_scalar(
                conn,
                f"SELECT COUNT(*) FROM {self._table} WHERE kb_id = %s AND status = 'active'",
                (kb_id,),
            ).fetchone()
        return row[0]

    def mark_deleted(self, file_path: str):
        with self._lock, self._conn() as conn:
            self._exec(
                conn,
                f"UPDATE {self._table} SET status = 'deleted', active_generation = '', "
                f"updated_at = { _NOW_SQL } "
                "WHERE file_path = %s",
                (file_path,),
            )

    def mark_deleted_by_doc_id(self, doc_id: str) -> int:
        with self._lock, self._conn() as conn:
            cur = self._exec(
                conn,
                f"UPDATE {self._table} SET status = 'deleted', active_generation = '', "
                f"updated_at = { _NOW_SQL } "
                # pending_review / failed 都必须进入终态，否则删除后审批
                # 或失败存量仍会留下幽灵行；active 保持原有删除语义。
                "WHERE doc_id = %s AND status IN ('pending_review', 'failed', 'active')",
                (doc_id,),
            )
            return cur.rowcount

    def update_status_by_doc_id(self, doc_id: str, new_status: str) -> int:
        if new_status not in DOC_STATUSES:
            raise ValueError(f"无效状态: {new_status}，有效值: {DOC_STATUSES}")
        with self._lock, self._conn() as conn:
            cur = self._exec(
                conn,
                f"UPDATE {self._table} SET status = %s, "
                f"active_generation = CASE WHEN %s = 'deleted' THEN '' "
                f"ELSE active_generation END, updated_at = { _NOW_SQL } "
                "WHERE doc_id = %s AND status IN ('pending_review', 'active')",
                (new_status, new_status, doc_id),
            )
            return cur.rowcount

    def transition_status_by_doc_id(
        self, doc_id: str, new_status: str, allowed_from: tuple[str, ...],
    ) -> int:
        """生命周期状态机专用 CAS 迁移（2026-10-03 lifecycle.py）：
        仅当当前 status ∈ allowed_from 时写入，行数 0 = 源状态已变化。
        与 update_status_by_doc_id 的固定白名单不同，源状态由调用方
        （状态机裁决结果）显式给定。"""
        if new_status not in DOC_STATUSES:
            raise ValueError(f"无效状态: {new_status}，有效值: {DOC_STATUSES}")
        if not allowed_from:
            raise ValueError("allowed_from 不能为空")
        placeholders = ", ".join(["%s"] * len(allowed_from))
        with self._lock, self._conn() as conn:
            cur = self._exec(
                conn,
                f"UPDATE {self._table} SET status = %s, updated_at = { _NOW_SQL } "
                f"WHERE doc_id = %s AND status IN ({placeholders})",
                (new_status, doc_id, *allowed_from),
            )
            return cur.rowcount

    def list_pending_review(
        self, page: int = 1, page_size: int = 20,
        kb_scope: list[str] | None = None,
        department: str = "",
    ) -> dict:
        """待审核列表。kb_scope/department（2026-10-01 权限收口）：
        管理面按「可管理范围」过滤——非 admin 编辑只审本部门、可见库的文档；
        total 同条件统计。"""
        conditions: list[str] = ["status = 'pending_review'"]
        params: list = []
        if kb_scope is not None:
            conditions.append("kb_id = ANY(%s)")
            params.append([str(k) for k in kb_scope])
        if department:
            conditions.append("department = %s")
            params.append(department)
        where_clause = " WHERE " + " AND ".join(conditions)
        offset = (page - 1) * page_size
        with self._lock, self._conn() as conn:
            total = self._exec_scalar(
                conn,
                f"SELECT COUNT(*) FROM {self._table}{where_clause}",
                tuple(params),
            ).fetchone()[0]
            rows = self._exec(
                conn,
                f"""SELECT * FROM {self._table}{where_clause}
                   ORDER BY confidence ASC, updated_at DESC
                   LIMIT %s OFFSET %s""",
                tuple(params) + (page_size, offset),
            ).fetchall()
        return {"items": [dict(r) for r in rows], "total": total, "page": page, "page_size": page_size}

    def distinct_permission_scopes(self) -> list[str]:
        """registry 中实际存在的 permission_scope 值（空串归一为 general）。

        供授权层把文档级裁决下推为 SQL ANY 过滤（visible_scopes），
        避免拉全表到应用侧过滤。"""
        with self._lock, self._conn() as conn:
            # 必须用普通游标：r[0] 是位置索引取列，_exec 的 RealDictCursor
            # 返回按列名索引的字典行，r[0] 会 KeyError: 0（2026-10-01 修复）
            rows = self._exec_scalar(
                conn,
                f"""SELECT DISTINCT COALESCE(NULLIF(permission_scope, ''), 'general')
                   FROM {self._table} WHERE status = 'active'""",
            ).fetchall()
        return [r[0] for r in rows]

    def clear(self):
        with self._lock, self._conn() as conn:
            self._exec_scalar(conn, f"DELETE FROM {self._table}")

    # ── 文档生命周期 ──

    def ensure_expire_at_column(self) -> None:
        """兼容检查：expire_at 列缺失则补（PG 建表已内置，正常为 no-op）。"""
        with self._lock, self._conn() as conn:
            cur = self._exec_scalar(
                conn,
                """SELECT COUNT(*) FROM information_schema.columns
                   WHERE table_schema = current_schema()
                     AND table_name = %s AND column_name = 'expire_at'""",
                (self._table,),
            )
            if cur.fetchone()[0] == 0:
                conn.cursor().execute(f"ALTER TABLE {self._table} ADD COLUMN expire_at TEXT")
                logger.info("[DocRegistry-PG] 已添加 expire_at 字段")

    def bump_doc_version(self, doc_id: str, delta: int = 1) -> int:
        if not doc_id:
            return -1
        with self._lock, self._conn() as conn:
            cur_row = self._exec_scalar(
                conn,
                f"SELECT doc_version FROM {self._table} WHERE doc_id = %s AND status = 'active'",
                (doc_id,),
            ).fetchone()
            if cur_row is None:
                return -1
            new_version = cur_row[0] + delta
            self._exec(
                conn,
                f"UPDATE {self._table} SET doc_version = %s, updated_at = { _NOW_SQL } "
                "WHERE doc_id = %s AND status = 'active'",
                (new_version, doc_id),
            )
            return new_version

    def set_expire_at(self, doc_id: str, expire_at: str) -> int:
        with self._lock, self._conn() as conn:
            cur = self._exec(
                conn,
                f"UPDATE {self._table} SET expire_at = %s, updated_at = { _NOW_SQL } "
                "WHERE doc_id = %s AND status = 'active'",
                (expire_at, doc_id),
            )
            return cur.rowcount

    def list_expired(self, now: str | None = None) -> list[dict]:
        from datetime import datetime
        now = now or datetime.now().isoformat()[:10]
        with self._lock, self._conn() as conn:
            rows = self._exec(
                conn,
                f"""SELECT * FROM {self._table}
                   WHERE status = 'active' AND expire_at IS NOT NULL AND expire_at <= %s""",
                (now,),
            ).fetchall()
        return [dict(r) for r in rows]

    def archive_expired(self, now: str | None = None) -> int:
        expired = self.list_expired(now=now)
        if not expired:
            return 0
        doc_ids = list({d["doc_id"] for d in expired})
        with self._lock, self._conn() as conn:
            for doc_id in doc_ids:
                self._exec(
                    conn,
                    f"UPDATE {self._table} SET status = 'deleted', active_generation = '', "
                    f"updated_at = { _NOW_SQL } "
                    "WHERE doc_id = %s AND status = 'active'",
                    (doc_id,),
                )
        return len(doc_ids)
