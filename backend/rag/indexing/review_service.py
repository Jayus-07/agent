"""待复核文档的审核与索引清理服务。

审核动作必须在持有本地 RAGPipeline 的进程执行。管理端 app 在
``RAG_MODE=remote`` 时只负责鉴权和转发，避免远程代理被当成向量库使用。
"""
from __future__ import annotations

import os
from typing import Any, Literal

from backend.shared.logger import logger

ReviewAction = Literal["approve", "reject"]


def _get_chunk_store():
    from backend.rag.indexing.chunk_store import get_chunk_store

    return get_chunk_store()


def _invalidate_review_cache() -> None:
    """让批准/拒绝在当前 RAG 进程立即生效。"""
    from backend.rag.retrieval.hybrid import invalidate_pending_review_cache

    invalidate_pending_review_cache()


def _collect_residue(doc_id: str, pipeline: Any, chunk_store: Any) -> list[str]:
    residue: list[str] = []
    try:
        result = pipeline.vectordb.get(where={"doc_id": doc_id})
        if result and result.get("ids"):
            residue.append(f"pgvector chunk 残留 {len(result['ids'])} 条")
    except Exception as exc:  # noqa: BLE001 - 审核结果必须显式失败
        residue.append(f"pgvector chunk 验证失败: {exc}")
    try:
        result = pipeline.doc_db.get(where={"doc_id": doc_id})
        if result and result.get("ids"):
            residue.append(f"pgvector doc 残留 {len(result['ids'])} 条")
    except Exception as exc:  # noqa: BLE001 - 审核结果必须显式失败
        residue.append(f"pgvector doc 验证失败: {exc}")
    try:
        count = chunk_store.count_by_doc_id(doc_id)
        if count > 0:
            residue.append(f"chunk_store 残留 {count} 条")
    except Exception as exc:  # noqa: BLE001 - 审核结果必须显式失败
        residue.append(f"chunk_store 验证失败: {exc}")
    bm25_store = getattr(pipeline, "bm25_store", None)
    if bm25_store is not None:
        try:
            bm25_docs = bm25_store.load_docs()
            hits = [
                doc for doc in bm25_docs
                if (doc.metadata or {}).get("doc_id") == doc_id
            ]
            if hits:
                residue.append(f"BM25 残留 {len(hits)} chunks")
        except Exception as exc:  # noqa: BLE001 - 审核结果必须显式失败
            residue.append(f"BM25 验证失败: {exc}")
    return residue


def _failure(doc_id: str, warnings: list[str]) -> dict[str, Any]:
    logger.error("[Review] 文档拒绝清理未完成 doc_id=%s warnings=%s", doc_id, warnings)
    return {
        "ok": False,
        "doc_id": doc_id,
        "error": "文档清理未完成，仍保留待复核状态",
        "warnings": warnings,
    }


def review_pending_doc(
    doc_id: str,
    action: ReviewAction,
    *,
    registry: Any,
    pipeline: Any,
) -> dict[str, Any]:
    """批准或拒绝一个待复核文档。

    批准只变更 registry 状态；拒绝必须先完成所有索引清理并通过回读校验，
    最后才删除源文件和切换 registry 状态。失败时不返回假成功。
    """
    if action not in ("approve", "reject"):
        raise ValueError(f"无效审核动作: {action}")

    doc = registry.get_by_doc_id(doc_id)
    if not doc:
        return {"ok": False, "doc_id": doc_id, "error": "文档不存在"}
    if doc.get("status") != "pending_review":
        return {
            "ok": False,
            "doc_id": doc_id,
            "error": f"文档状态为 {doc.get('status')}，不是 pending_review",
        }

    if action == "approve":
        updated = registry.update_status_by_doc_id(doc_id, "active")
        if updated == 0:
            return {"ok": False, "doc_id": doc_id, "error": "状态更新失败（可能并发）"}
        _invalidate_review_cache()
        return {
            "ok": True,
            "doc_id": doc_id,
            "new_status": "active",
            "warnings": None,
        }

    warnings: list[str] = []
    chunk_store = _get_chunk_store()
    for store_name, store in (
        ("向量库(chunks)", getattr(pipeline, "vectordb", None)),
        ("向量库(doc)", getattr(pipeline, "doc_db", None)),
    ):
        try:
            if store is None:
                raise RuntimeError("存储实例未初始化")
            store.delete(where={"doc_id": doc_id})
        except Exception as exc:  # noqa: BLE001 - 汇总后统一失败
            warnings.append(f"{store_name}清理失败: {exc}")
    try:
        chunk_store.delete_by_doc_id(doc_id)
    except Exception as exc:  # noqa: BLE001 - 汇总后统一失败
        warnings.append(f"chunk_store 清理失败: {exc}")
    try:
        pipeline.remove_documents_from_bm25([doc_id], file_paths=[doc.get("file_path", "")])
    except Exception as exc:  # noqa: BLE001 - 汇总后统一失败
        warnings.append(f"BM25 清理失败: {exc}")

    warnings.extend(_collect_residue(doc_id, pipeline, chunk_store))
    if warnings:
        return _failure(doc_id, warnings)

    file_path = doc.get("file_path", "")
    if file_path:
        try:
            os.remove(file_path)
        except FileNotFoundError:
            pass
        except OSError as exc:
            return _failure(doc_id, [f"原文件删除失败: {exc}"])

    updated = registry.update_status_by_doc_id(doc_id, "deleted")
    if updated == 0:
        return _failure(doc_id, ["状态更新失败（可能并发）"])
    _invalidate_review_cache()
    return {
        "ok": True,
        "doc_id": doc_id,
        "new_status": "deleted",
        "warnings": None,
    }


def delete_document_cascade(
    doc_id: str,
    *,
    registry: Any,
    pipeline: Any,
) -> dict[str, Any]:
    """删除文档（任意状态）：软删 registry + 完整级联清理 + 回读校验。

    与 reject（仅 pending_review、失败即整单失败）不同：delete 的用户预期
    是「删掉」，registry 软删先行，级联/文件失败降级为 degraded 警告，
    不回滚——与本地实现的历史语义一致（2026-10-01 remote 收口补齐）。
    """
    doc = registry.get_by_doc_id(doc_id)
    if not doc:
        return {"ok": False, "doc_id": doc_id, "error": "文档不存在"}

    file_path = doc.get("file_path", "")
    deleted_rows = registry.mark_deleted_by_doc_id(doc_id)
    warnings: list[str] = []
    if deleted_rows == 0:
        warnings.append("registry 中无活跃记录（可能已被删除）")

    chunk_store = _get_chunk_store()
    for store_name, store in (
        ("向量库(chunks)", getattr(pipeline, "vectordb", None)),
        ("向量库(doc)", getattr(pipeline, "doc_db", None)),
    ):
        try:
            if store is None:
                raise RuntimeError("存储实例未初始化")
            store.delete(where={"doc_id": doc_id})
        except Exception as exc:  # noqa: BLE001 - 汇总后统一降级
            warnings.append(f"{store_name}清理失败: {exc}")
    try:
        chunk_store.delete_by_doc_id(doc_id)
    except Exception as exc:  # noqa: BLE001 - 汇总后统一降级
        warnings.append(f"chunk_store 清理失败: {exc}")
    try:
        pipeline.remove_documents_from_bm25([doc_id], file_paths=[file_path])
    except Exception as exc:  # noqa: BLE001 - 汇总后统一降级
        warnings.append(f"BM25 清理失败: {exc}")

    warnings.extend(_collect_residue(doc_id, pipeline, chunk_store))

    if file_path:
        try:
            os.remove(file_path)
        except FileNotFoundError:
            pass
        except OSError as exc:  # noqa: BLE001 - 文件失败降级不回滚软删
            warnings.append(f"原文件删除失败: {exc}")

    if warnings:
        logger.warning("[Review] 删除存在残留 doc_id=%s warnings=%s", doc_id, warnings)
    _invalidate_review_cache()
    return {
        "ok": True,
        "doc_id": doc_id,
        "deleted_rows": deleted_rows,
        "degraded": bool(warnings),
        "warnings": warnings or None,
    }
