"""生产索引对账：纯快照判断与 PostgreSQL 只读采样。"""

from __future__ import annotations

from typing import Any


_DEFERRED_STATUSES = frozenset(
    {"uploading", "parsing", "embedding", "pending_review"}
)


def build_missing_index_repairs(
    registry: list[dict[str, Any]],
    vector_doc_ids: set[str],
    inflight_doc_ids: set[str],
) -> list[dict[str, Any]]:
    """返回待修复的路径原始行；同 ID 任一记录在途则全部延后。"""
    protected = inflight_doc_ids | {
        row["doc_id"] for row in registry
        if row["status"] in _DEFERRED_STATUSES
    }
    return sorted(
        [dict(row) for row in registry if row["status"] == "active"
         and row["doc_id"] not in vector_doc_ids | protected],
        key=lambda row: row["file_path"],
    )


def repair_missing_indexes(backup_path: Any, collection: str | None = None) -> dict:
    """锁内重验并修正虚假 active；完整行先落盘，原因与变更同事务落审计。"""
    import json
    import os
    from datetime import datetime, timezone
    from pathlib import Path

    from psycopg2 import sql
    from psycopg2.extras import Json, RealDictCursor

    from backend.config.database import (
        DOC_REGISTRY_PG_CONFIG, DOC_REGISTRY_PG_TABLE,
        RAG_STORES_PG_CONFIG, VECTOR_PG_CONFIG, VECTOR_PG_TABLE_PREFIX,
    )
    from backend.infra.db import engine_for

    endpoints = {
        (cfg["host"], cfg["port"], cfg["dbname"])
        for cfg in (DOC_REGISTRY_PG_CONFIG, RAG_STORES_PG_CONFIG, VECTOR_PG_CONFIG)
    }
    if len(endpoints) != 1:
        raise ValueError("修复要求登记、向量与 chunk 存储同库")
    collection = collection or default_collection()
    # 数据源不可用时拒绝修复，不将不可观测误判成索引缺失。
    fetch_bm25_snapshot()
    conn = engine_for(DOC_REGISTRY_PG_CONFIG).raw_connection()
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cursor:
            cursor.execute("SET LOCAL lock_timeout = '5s'")
            cursor.execute("SET LOCAL statement_timeout = '30s'")
            cursor.execute(sql.SQL("LOCK TABLE {} IN SHARE ROW EXCLUSIVE MODE").format(
                sql.Identifier(DOC_REGISTRY_PG_TABLE)))
            cursor.execute("LOCK TABLE rag_index_runs IN SHARE MODE")
            cursor.execute(sql.SQL("LOCK TABLE {} IN SHARE MODE").format(
                sql.Identifier(VECTOR_PG_TABLE_PREFIX + "rag_vectors")))
            cursor.execute(sql.SQL("SELECT * FROM {}").format(
                sql.Identifier(DOC_REGISTRY_PG_TABLE)))
            registry = [dict(row) for row in cursor.fetchall()]
            cursor.execute(sql.SQL(
                "SELECT DISTINCT doc_id FROM {} WHERE collection=%s"
            ).format(sql.Identifier(VECTOR_PG_TABLE_PREFIX + "rag_vectors")),
                (collection,))
            vectors = {row["doc_id"] for row in cursor.fetchall()}
            cursor.execute("SELECT DISTINCT doc_id FROM rag_index_runs "
                           "WHERE status IN ('claimed','indexing','publishing')")
            inflight = {row["doc_id"] for row in cursor.fetchall()}
            repairs = build_missing_index_repairs(registry, vectors, inflight)
            for doc_id in sorted({row["doc_id"] for row in repairs}):
                cursor.execute("SELECT pg_try_advisory_xact_lock(hashtext(%s)) AS acquired",
                               (f"rag_reindex:{doc_id}",))
                if not cursor.fetchone()["acquired"]:
                    raise RuntimeError(f"文档正在重索引，整批修复回滚: {doc_id}")
            cursor.execute("SELECT to_char(now() AT TIME ZONE 'UTC',"
                           "'YYYY-MM-DD HH24:MI:SS') AS stamp")
            repair_updated_at = cursor.fetchone()["stamp"]
            detail = {
                "operation": "repair_missing_indexes",
                "reason": "index_missing_reconcile",
                "collection": collection,
                "created_at": datetime.now(timezone.utc).isoformat(),
                "before_rows": repairs,
                "matched_row_count": len(repairs),
                "matched_doc_count": len({row["doc_id"] for row in repairs}),
                "repair_updated_at": repair_updated_at,
            }
            target = Path(backup_path)
            target.parent.mkdir(parents=True, exist_ok=True)
            # 不覆盖旧备份；写失败则尚未开始任何业务变更。
            with target.open("x", encoding="utf-8") as output:
                json.dump(detail, output, ensure_ascii=False, indent=2, default=str)
                output.write("\n")
                output.flush()
                os.fsync(output.fileno())
            changed = 0
            for row in repairs:
                cursor.execute(sql.SQL(
                    "UPDATE {} SET status='failed',last_indexed=NULL,"
                    "updated_at=%s "
                    "WHERE file_path=%s AND doc_id=%s AND status='active'"
                ).format(sql.Identifier(DOC_REGISTRY_PG_TABLE)),
                    (repair_updated_at, row["file_path"], row["doc_id"]))
                if cursor.rowcount != 1:
                    raise RuntimeError("修复匹配数变化，整批回滚")
                changed += cursor.rowcount
            cursor.execute(
                "INSERT INTO ai.rag_reconcile_reports "
                "(consistent,issue_count,bm25_status,detail) "
                "VALUES (false,%s,'available',%s) RETURNING id",
                (len(repairs), Json(detail, dumps=lambda value: json.dumps(
                    value, ensure_ascii=False, default=str))),
            )
            report_id = cursor.fetchone()["id"]
        conn.commit()
        return {"changed_row_count": changed, "report_id": report_id,
                "backup_path": str(target), "reason": detail["reason"]}
    except BaseException:
        conn.rollback()
        raise
    finally:
        conn.close()


def default_collection() -> str:
    """沿用运行时以索引路径末段命名正式集合的约定。"""
    from backend.config.database import CHROMA_PATH
    from backend.rag.vectorstore.pgvector_store import _collection_name_from_path

    return _collection_name_from_path(CHROMA_PATH)


def reconcile_snapshot(
    registry: list[dict[str, Any]],
    vector_doc_ids: set[str],
    chunk_doc_ids: set[str],
    inflight_doc_ids: set[str],
    bm25_doc_ids: set[str] | None,
) -> dict[str, Any]:
    """在途数据不裁决；BM25 不可达时不得声称三方一致。"""
    protected = inflight_doc_ids | {
        row["doc_id"] for row in registry
        if row["status"] in _DEFERRED_STATUSES
    }
    active = {
        row["doc_id"] for row in registry if row["status"] == "active"
    } - protected
    vectors = vector_doc_ids - protected
    chunks = chunk_doc_ids - protected
    indexed = {
        row["doc_id"] for row in registry
        if row["status"] == "active" and row.get("last_indexed")
    } - protected
    issues = {
        "active_missing_vectors": sorted(active - vectors),
        "orphan_vectors": sorted(vectors - active),
        "chunk_vector_mismatch": sorted(chunks ^ vectors),
        "indexed_missing_vectors": sorted(indexed - vectors),
    }
    bm25 = bm25_doc_ids - protected if bm25_doc_ids is not None else None
    missing = sorted(active - bm25) if bm25 is not None else None
    orphans = sorted(bm25 - active) if bm25 is not None else None
    return {
        "issues": issues,
        "issue_count": sum(len(values) for values in issues.values()),
        "active_doc_count": len(active),
        "active_registry_row_count": sum(
            row["status"] == "active" for row in registry
        ),
        "registry_row_count": len(registry),
        "vector_doc_count": len(vectors),
        "chunk_doc_count": len(chunks),
        "active_coverage": len(active & vectors) / len(active) if active else None,
        "deferred_doc_ids": sorted(protected),
        "bm25_status": "available" if bm25 is not None else "unavailable",
        "snapshot_scope": "PG repeatable-read; BM25 separately sampled",
        "bm25_missing_doc_ids": missing,
        "bm25_orphan_doc_ids": orphans,
        "consistent": not any(issues.values()) and bm25 is not None
        and not missing and not orphans,
    }


def load_pg_snapshot(collection: str | None = None) -> tuple[
    list[dict[str, Any]], set[str], set[str], set[str]
]:
    """同一只读事务采样；只检查正式 chunk collection，排除 doc_db。"""
    from psycopg2 import sql
    from psycopg2.extras import RealDictCursor

    from backend.config.database import (
        DOC_REGISTRY_PG_CONFIG, DOC_REGISTRY_PG_TABLE,
        RAG_STORES_PG_CONFIG, RAG_STORES_PG_TABLE_PREFIX,
        VECTOR_PG_CONFIG, VECTOR_PG_TABLE_PREFIX,
    )
    from backend.infra.db import engine_for

    collection = collection or default_collection()
    configs = (DOC_REGISTRY_PG_CONFIG, RAG_STORES_PG_CONFIG, VECTOR_PG_CONFIG)
    endpoints = {(cfg["host"], cfg["port"], cfg["dbname"]) for cfg in configs}
    if len(endpoints) != 1:
        raise ValueError("索引三方对账要求同库快照，当前存储配置指向不同库")
    conn = engine_for(DOC_REGISTRY_PG_CONFIG).raw_connection()
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cursor:
            cursor.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
            cursor.execute(sql.SQL(
                "SELECT doc_id,status,last_indexed FROM {}"
            ).format(sql.Identifier(DOC_REGISTRY_PG_TABLE)))
            registry = [dict(row) for row in cursor.fetchall()]
            cursor.execute(sql.SQL(
                "SELECT DISTINCT doc_id FROM {} WHERE collection=%s "
                "AND doc_id IS NOT NULL AND doc_id <> ''"
            ).format(sql.Identifier(VECTOR_PG_TABLE_PREFIX + "rag_vectors")),
                (collection,))
            vectors = {row["doc_id"] for row in cursor.fetchall()}
            cursor.execute(sql.SQL(
                "SELECT DISTINCT doc_id FROM {} WHERE doc_id <> ''"
            ).format(sql.Identifier(RAG_STORES_PG_TABLE_PREFIX + "chunk_store")))
            chunks = {row["doc_id"] for row in cursor.fetchall()}
            cursor.execute(
                "SELECT DISTINCT doc_id FROM rag_index_runs "
                "WHERE status IN ('claimed','indexing','publishing') "
                "AND doc_id <> ''"
            )
            inflight = {row["doc_id"] for row in cursor.fetchall()}
        return registry, vectors, chunks, inflight
    finally:
        conn.rollback()
        conn.close()


def fetch_bm25_snapshot() -> set[str]:
    """只读调用持有 BM25 的服务，禁止维护 worker 初始化本地模型。"""
    import json
    import urllib.request

    from backend.config.messaging import AI_INTERNAL_TOKEN
    from backend.config.rag import RAG_SERVICE_URL

    request = urllib.request.Request(
        RAG_SERVICE_URL.rstrip("/") + "/admin/index/reconcile-snapshot",
        headers={"X-Internal-Token": AI_INTERNAL_TOKEN},
        method="GET",
    )
    with urllib.request.urlopen(request, timeout=30.0) as response:
        payload = json.loads(response.read().decode("utf-8"))
    ids = payload.get("doc_ids")
    if not isinstance(ids, list) or not all(isinstance(item, str) for item in ids):
        raise ValueError("BM25 对账响应缺少有效 doc_ids")
    return set(ids)


def run_reconcile(collection: str | None = None) -> dict[str, Any]:
    """采样失败明确退出；BM25 不可达记录不可验收状态。"""
    from urllib.error import HTTPError, URLError

    from backend.shared.logger import logger

    collection = collection or default_collection()
    registry, vectors, chunks, inflight = load_pg_snapshot(collection)
    try:
        bm25 = fetch_bm25_snapshot()
    except (HTTPError, URLError, TimeoutError, ValueError) as exc:
        logger.warning("[RAGReconcile] BM25 快照不可用: %s", type(exc).__name__)
        bm25 = None
    result = reconcile_snapshot(registry, vectors, chunks, inflight, bm25)
    result["collection"] = collection
    return result
