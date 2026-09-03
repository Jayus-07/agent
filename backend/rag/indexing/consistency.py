"""索引一致性检查器 — 审计 5 个存储之间的数据一致性。

检查项:
  1. Registry ↔ 磁盘文件（active 记录指向缺失文件）
  2. Chroma chunk 孤儿（doc_id 在 Chroma 但不在 registry-active）
  3. Registry → Chroma 缺失（active doc_id 在 Chroma 中无向量）
  4. doc_db 孤儿 / 缺失
  5. chunk_store 孤儿（doc_id 在 chunk_store 但不在 registry-active）
  6. BM25 残留条目 + chunk 计数与 Chroma 不一致
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from backend.shared.logger import logger

if TYPE_CHECKING:
    from backend.rag.pipeline import RAGPipeline


@dataclass
class ConsistencyIssue:
    severity: str  # "error" | "warning"
    store: str  # "registry" | "chroma_chunk" | "chroma_doc" | "chunk_store" | "bm25"
    doc_id: str
    detail: str


@dataclass
class ConsistencyReport:
    issues: list[ConsistencyIssue] = field(default_factory=list)

    @property
    def consistent(self) -> bool:
        return not any(i.severity == "error" for i in self.issues)

    @property
    def error_count(self) -> int:
        return sum(1 for i in self.issues if i.severity == "error")

    @property
    def warning_count(self) -> int:
        return sum(1 for i in self.issues if i.severity == "warning")

    def summary(self) -> str:
        status = "CONSISTENT" if self.consistent else "INCONSISTENT"
        return (
            f"[{status}] {len(self.issues)} issues "
            f"({self.error_count} errors, {self.warning_count} warnings)"
        )

    def to_dict(self) -> dict:
        return {
            "consistent": self.consistent,
            "error_count": self.error_count,
            "warning_count": self.warning_count,
            "summary": self.summary(),
            "issues": [
                {"severity": i.severity, "store": i.store, "doc_id": i.doc_id, "detail": i.detail}
                for i in self.issues
            ],
        }


class IndexConsistencyChecker:
    """只读审计 5 个存储之间的一致性。

    用法:
        checker = IndexConsistencyChecker(pipeline)
        report = checker.check()
        if not report.consistent:
            actions = checker.repair(report)
    """

    def __init__(self, pipeline: RAGPipeline):
        self.pipeline = pipeline
        from backend.rag.indexing.doc_registry import DocumentRegistry
        from backend.config import DOC_REGISTRY_PATH
        self.registry = DocumentRegistry(DOC_REGISTRY_PATH)
        self.vectordb = pipeline.vectordb
        self.doc_db = pipeline.doc_db
        self.bm25_store = pipeline.bm25_store

    def check(self) -> ConsistencyReport:
        report = ConsistencyReport()
        self._check_registry_disk(report)
        self._check_chroma_chunk_orphans(report)
        self._check_registry_chroma_missing(report)
        self._check_doc_db(report)
        self._check_chunk_store(report)
        self._check_bm25(report)
        logger.info(f"[Consistency] {report.summary()}")
        return report

    def repair(self, report: ConsistencyReport) -> list[str]:
        """保守修复：删除孤儿、清理残留。返回操作描述列表。"""
        actions: list[str] = []
        for issue in report.issues:
            if issue.severity != "error":
                continue
            try:
                if issue.store == "chroma_chunk":
                    self.vectordb.delete(where={"doc_id": issue.doc_id})
                    actions.append(f"删除 Chroma chunk 孤儿: doc_id={issue.doc_id}")
                elif issue.store == "chroma_doc":
                    self.doc_db.delete(where={"doc_id": issue.doc_id})
                    actions.append(f"删除 Chroma doc 孤儿: doc_id={issue.doc_id}")
                elif issue.store == "chunk_store":
                    from backend.rag.indexing.chunk_store import get_chunk_store
                    get_chunk_store().delete_by_doc_id(issue.doc_id)
                    actions.append(f"删除 chunk_store 孤儿: doc_id={issue.doc_id}")
                elif issue.store == "bm25":
                    if self.bm25_store is not None:
                        self.bm25_store.remove_documents([issue.doc_id])
                        actions.append(f"清理 BM25 残留: doc_id={issue.doc_id}")
            except Exception as e:
                actions.append(f"修复失败 ({issue.store} doc_id={issue.doc_id}): {e}")
        if actions and self.bm25_store is not None:
            try:
                self.pipeline.refresh_bm25_from_store()
            except Exception:
                pass
        return actions

    # ── 内部检查方法 ──

    def _active_docIds(self) -> set[str]:
        return {r["doc_id"] for r in self.registry.list_active() if r.get("doc_id")}

    def _check_registry_disk(self, report: ConsistencyReport) -> None:
        for row in self.registry.list_active():
            fp = row.get("file_path", "")
            doc_id = row.get("doc_id", "")
            if fp and not os.path.isfile(fp):
                report.issues.append(ConsistencyIssue(
                    severity="error",
                    store="registry",
                    doc_id=doc_id,
                    detail=f"active 记录指向缺失文件: {fp}",
                ))

    def _check_chroma_chunk_orphans(self, report: ConsistencyReport) -> None:
        active_ids = self._active_docIds()
        try:
            result = self.vectordb.get()
            chroma_doc_ids: set[str] = set()
            for meta in (result.get("metadatas") or []):
                did = (meta or {}).get("doc_id")
                if did:
                    chroma_doc_ids.add(did)
            for orphan_id in chroma_doc_ids - active_ids:
                report.issues.append(ConsistencyIssue(
                    severity="error",
                    store="chroma_chunk",
                    doc_id=orphan_id,
                    detail="Chroma chunk 向量存在但 registry 无 active 记录",
                ))
        except Exception as e:
            report.issues.append(ConsistencyIssue(
                severity="warning",
                store="chroma_chunk",
                doc_id="",
                detail=f"Chroma chunk 读取失败: {e}",
            ))

    def _check_registry_chroma_missing(self, report: ConsistencyReport) -> None:
        active_rows = self.registry.list_active()
        try:
            result = self.vectordb.get()
            chroma_doc_ids: set[str] = set()
            for meta in (result.get("metadatas") or []):
                did = (meta or {}).get("doc_id")
                if did:
                    chroma_doc_ids.add(did)
            for row in active_rows:
                doc_id = row.get("doc_id", "")
                if doc_id and doc_id not in chroma_doc_ids:
                    report.issues.append(ConsistencyIssue(
                        severity="error",
                        store="chroma_chunk",
                        doc_id=doc_id,
                        detail=f"registry active 但 Chroma 无向量: {row.get('file_path', '')}",
                    ))
        except Exception as e:
            report.issues.append(ConsistencyIssue(
                severity="warning",
                store="chroma_chunk",
                doc_id="",
                detail=f"Chroma chunk 读取失败（跳过缺失检查）: {e}",
            ))

    def _check_doc_db(self, report: ConsistencyReport) -> None:
        active_ids = self._active_docIds()
        try:
            result = self.doc_db.get()
            doc_db_ids: set[str] = set()
            for meta in (result.get("metadatas") or []):
                did = (meta or {}).get("doc_id")
                if did:
                    doc_db_ids.add(did)
            for orphan_id in doc_db_ids - active_ids:
                report.issues.append(ConsistencyIssue(
                    severity="error",
                    store="chroma_doc",
                    doc_id=orphan_id,
                    detail="doc_db 向量存在但 registry 无 active 记录",
                ))
            for missing_id in active_ids - doc_db_ids:
                report.issues.append(ConsistencyIssue(
                    severity="warning",
                    store="chroma_doc",
                    doc_id=missing_id,
                    detail="registry active 但 doc_db 无向量",
                ))
        except Exception as e:
            report.issues.append(ConsistencyIssue(
                severity="warning",
                store="chroma_doc",
                doc_id="",
                detail=f"doc_db 读取失败: {e}",
            ))

    def _check_chunk_store(self, report: ConsistencyReport) -> None:
        active_ids = self._active_docIds()
        try:
            from backend.rag.indexing.chunk_store import get_chunk_store
            cs = get_chunk_store()
            import sqlite3
            from backend.config import CHUNK_STORE_PATH
            conn = sqlite3.connect(f"file:{CHUNK_STORE_PATH}?mode=ro", uri=True)
            try:
                rows = conn.execute("SELECT DISTINCT doc_id FROM chunk_store").fetchall()
            finally:
                conn.close()
            cs_doc_ids = {r[0] for r in rows if r[0]}
            for orphan_id in cs_doc_ids - active_ids:
                report.issues.append(ConsistencyIssue(
                    severity="error",
                    store="chunk_store",
                    doc_id=orphan_id,
                    detail="chunk_store 有记录但 registry 无 active 记录",
                ))
        except Exception as e:
            report.issues.append(ConsistencyIssue(
                severity="warning",
                store="chunk_store",
                doc_id="",
                detail=f"chunk_store 读取失败: {e}",
            ))

    def _check_bm25(self, report: ConsistencyReport) -> None:
        if self.bm25_store is None:
            return
        active_ids = self._active_docIds()
        try:
            bm25_docs = self.bm25_store.load_docs()
            if not bm25_docs:
                return

            bm25_doc_ids: set[str] = set()
            bm25_counts: dict[str, int] = {}
            for d in bm25_docs:
                meta = d.metadata or {}
                did = meta.get("doc_id", "")
                if did:
                    bm25_doc_ids.add(did)
                    bm25_counts[did] = bm25_counts.get(did, 0) + 1

            for orphan_id in bm25_doc_ids - active_ids:
                report.issues.append(ConsistencyIssue(
                    severity="error",
                    store="bm25",
                    doc_id=orphan_id,
                    detail=f"BM25 有 {bm25_counts.get(orphan_id, 0)} 个 chunk 但 registry 无 active 记录",
                ))

            try:
                result = self.vectordb.get()
                chroma_counts: dict[str, int] = {}
                for meta in (result.get("metadatas") or []):
                    did = (meta or {}).get("doc_id", "")
                    if did:
                        chroma_counts[did] = chroma_counts.get(did, 0) + 1
                for did in bm25_doc_ids & set(chroma_counts.keys()):
                    if bm25_counts.get(did, 0) != chroma_counts.get(did, 0):
                        report.issues.append(ConsistencyIssue(
                            severity="warning",
                            store="bm25",
                            doc_id=did,
                            detail=(
                                f"BM25 chunk 数 ({bm25_counts[did]}) "
                                f"!= Chroma chunk 数 ({chroma_counts[did]})"
                            ),
                        ))
            except Exception:
                pass
        except Exception as e:
            report.issues.append(ConsistencyIssue(
                severity="warning",
                store="bm25",
                doc_id="",
                detail=f"BM25 读取失败: {e}",
            ))
