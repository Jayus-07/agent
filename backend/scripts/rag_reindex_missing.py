"""按登记路径批量重建缺失索引，避免同 doc_id 历史路径串线。"""

from __future__ import annotations

import argparse
import json
from typing import Any


def select_targets(
    rows: list[dict[str, Any]], *, since: str, status: str = "failed",
    missing_doc_ids: set[str] | None = None,
) -> list[dict[str, Any]]:
    """只选择指定状态且缺正式向量的真实源文件，返回副本并按路径排序。"""
    from backend.rag.indexing.upload_path_guard import test_artifact_path_reason

    return sorted(
        [
            dict(row)
            for row in rows
            if row.get("status") == status
            and (missing_doc_ids is None or row.get("doc_id") in missing_doc_ids)
            and str(row.get("file_path", "")).replace("\\", "/").startswith(
                "/app/data/docs/"
            )
            and str(row.get("updated_at") or "") >= since
            and test_artifact_path_reason(row.get("file_path")) is None
        ],
        key=lambda row: str(row.get("file_path", "")),
    )


class _PathPinnedRegistry:
    """为一次重建固定登记路径，其余 registry 行为继续委托。"""

    def __init__(self, base: Any, row: dict[str, Any]):
        self._base = base
        self._row = dict(row)

    def get_by_doc_id(self, doc_id: str) -> dict[str, Any] | None:
        if doc_id == self._row.get("doc_id"):
            return dict(self._row)
        return self._base.get_by_doc_id(doc_id)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._base, name)


def run_batch(
    *, since: str, limit: int = 0, status: str = "failed",
    only_missing_vectors: bool = False,
) -> dict[str, Any]:
    import asyncio

    from psycopg2.extras import RealDictCursor

    from backend.config.database import (
        DOC_REGISTRY_PG_CONFIG, DOC_REGISTRY_PG_TABLE, VECTOR_PG_TABLE_PREFIX,
    )
    from backend.infra.db import engine_for
    from backend.rag.indexing.doc_registry_pg import PostgresDocumentRegistry
    from backend.rag.indexing.reindex_service import run_reindex

    conn = engine_for(DOC_REGISTRY_PG_CONFIG).raw_connection()
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cursor:
            cursor.execute(
                f"SELECT * FROM {DOC_REGISTRY_PG_TABLE} "
                "WHERE status=%s AND file_path LIKE '/app/data/docs/%%' "
                "AND updated_at >= %s ORDER BY file_path",
                (status, since),
            )
            rows = [dict(row) for row in cursor.fetchall()]
            missing_doc_ids = None
            if only_missing_vectors:
                from backend.rag.indexing.reconcile import default_collection

                cursor.execute(
                    f"SELECT DISTINCT doc_id FROM {VECTOR_PG_TABLE_PREFIX}rag_vectors "
                    "WHERE collection=%s",
                    (default_collection(),),
                )
                indexed = {str(row["doc_id"]) for row in cursor.fetchall()}
                missing_doc_ids = {
                    str(row["doc_id"]) for row in rows
                    if str(row.get("doc_id")) not in indexed
                }
    finally:
        conn.rollback()
        conn.close()
    targets = select_targets(
        rows, since=since, status=status, missing_doc_ids=missing_doc_ids,
    )
    if limit > 0:
        targets = targets[:limit]
    registry = PostgresDocumentRegistry()
    from backend.infra.llm.registry_store import refresh_registry

    asyncio.run(refresh_registry())
    pipeline = None
    results: list[dict[str, Any]] = []
    for row in targets:
        try:
            if pipeline is None:
                from backend.rag.pipeline import get_rag_pipeline

                pipeline = get_rag_pipeline()
            result = run_reindex(
                row["doc_id"], registry=_PathPinnedRegistry(registry, row),
                pipeline=pipeline, source="reconcile",
                executor="rag_index_reconcile",
            )
            results.append({"file_path": row["file_path"], "ok": True,
                            "doc_id": row["doc_id"],
                            "chunk_count": result.get("chunk_count", 0)})
        except Exception as exc:  # 单文档失败留痕并继续，其余文档可恢复
            results.append({"file_path": row["file_path"], "ok": False,
                            "doc_id": row["doc_id"],
                            "error": f"{type(exc).__name__}: {exc}"})
    return {
        "selected_row_count": len(targets),
        "success_count": sum(item["ok"] for item in results),
        "failure_count": sum(not item["ok"] for item in results),
        "results": results,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--since", required=True, help="本批降级时间，UTC 文本")
    parser.add_argument("--limit", type=int, default=0, help="最多重建行数，0=全部")
    parser.add_argument("--status", choices=("failed", "active"), default="failed")
    parser.add_argument("--only-missing-vectors", action="store_true")
    parser.add_argument("--execute", action="store_true", help="确认执行重建")
    args = parser.parse_args(argv)
    if not args.execute:
        parser.error("必须显式指定 --execute")
    result = run_batch(
        since=args.since, limit=args.limit, status=args.status,
        only_missing_vectors=args.only_missing_vectors,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
    return 0 if result["failure_count"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
