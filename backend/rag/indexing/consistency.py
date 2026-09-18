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


def _metric_inc_issues(store: str, severity: str) -> None:
    """Prometheus 打点：对账检出问题数（软降级，metrics 不可用不影响主流程）。"""
    try:
        from backend.observability.metrics import rag_consistency_issues_total
        rag_consistency_issues_total.labels(store=store, severity=severity).inc()
    except Exception:
        pass


def _metric_inc_repair(store: str, ok: bool) -> None:
    """Prometheus 打点：修复动作结果（软降级同上）。"""
    try:
        from backend.observability.metrics import rag_consistency_repairs_total
        rag_consistency_repairs_total.labels(store=store, result="ok" if ok else "failed").inc()
    except Exception:
        pass

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

    # 索引进行中的状态：这些 doc 的向量属于"先写后删"/新上传的中间态，
    # 孤儿判定必须排除，否则 Sweeper 会把正在索引的文档向量误删
    _IN_PROGRESS_STATUSES = ("uploading", "parsing", "embedding")

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
                    _metric_inc_repair(issue.store, ok=True)
                elif issue.store == "chroma_doc":
                    self.doc_db.delete(where={"doc_id": issue.doc_id})
                    actions.append(f"删除 Chroma doc 孤儿: doc_id={issue.doc_id}")
                    _metric_inc_repair(issue.store, ok=True)
                elif issue.store == "chunk_store":
                    from backend.rag.indexing.chunk_store import get_chunk_store
                    get_chunk_store().delete_by_doc_id(issue.doc_id)
                    actions.append(f"删除 chunk_store 孤儿: doc_id={issue.doc_id}")
                    _metric_inc_repair(issue.store, ok=True)
                elif issue.store == "bm25":
                    if self.bm25_store is not None:
                        if "缺失" in issue.detail:
                            actions.append(self._repair_bm25_missing(issue.doc_id))
                        else:
                            self.bm25_store.remove_documents([issue.doc_id])
                            actions.append(f"清理 BM25 残留: doc_id={issue.doc_id}")
                        _metric_inc_repair(issue.store, ok=True)
            except Exception as e:
                actions.append(f"修复失败 ({issue.store} doc_id={issue.doc_id}): {e}")
                _metric_inc_repair(issue.store, ok=False)
        if actions and self.bm25_store is not None:
            try:
                self.pipeline.refresh_bm25_from_store()
            except Exception:
                pass
        return actions

    def _repair_bm25_missing(self, doc_id: str) -> str:
        """BM25 缺失修复：从 chunk_store 重建该文档的 BM25 条目。

        chunk_store 持有与向量库同源的 chunk 全文（上传路径先写 chunk_store
        再写 BM25，失败窗口内文本是完整的），以它为事实来源重建，避免整文档
        重索引（解析/嵌入成本）。file_path 取自 registry active 行。
        """
        try:
            row = next(
                (r for r in self.registry.list_all().values()
                 if r.get("doc_id") == doc_id and r.get("status") == "active"),
                None,
            )
            if row is None:
                return f"BM25 缺失修复跳过（registry 无 active 行）: doc_id={doc_id}"
            from backend.rag.indexing.chunk_store import get_chunk_store
            from langchain_core.documents import Document  # 局部导入:保持模块轻依赖
            rows = get_chunk_store().get_by_doc_id(doc_id)
            docs = [
                Document(page_content=r["content"],
                         metadata={"doc_id": doc_id,
                                   "source_file": row.get("file_path", ""),
                                   "chunk_index": r.get("chunk_index", 0),
                                   "doc_type": row.get("doc_type", "general")})
                for r in rows if (r.get("content") or "").strip()
            ]
            if not docs:
                return f"BM25 缺失修复跳过（chunk_store 无有效 chunk）: doc_id={doc_id}"
            file_path = row.get("file_path", "")
            self.bm25_store.replace_documents(
                docs, doc_id=doc_id, file_path=file_path)
            return f"BM25 缺失已从 chunk_store 重建 {len(docs)} chunks: doc_id={doc_id}"
        except Exception as e:
            return f"BM25 缺失修复失败（下轮重试）: doc_id={doc_id}: {e}"

    # ── 内部检查方法 ──

    def _active_docIds(self) -> set[str]:
        return {r["doc_id"] for r in self.registry.list_active() if r.get("doc_id")}

    def _in_progress_doc_ids(self) -> set[str]:
        """索引进行中的 doc_id（占位行状态）。查询失败软降级为空集。"""
        try:
            rows = self.registry.list_by_statuses(self._IN_PROGRESS_STATUSES)
        except Exception:
            return set()
        return {r["doc_id"] for r in rows if r.get("doc_id")}

    def _expected_doc_ids(self) -> set[str]:
        """孤儿判定口径：active ∪ 索引进行中。

        只用于"存储有、registry 没有"的孤儿检查；"registry 有、存储没有"
        的缺失检查仍按 active 口径（进行中记录本来就该还没写完）。
        """
        return self._active_docIds() | self._in_progress_doc_ids()

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
        expected_ids = self._expected_doc_ids()
        try:
            result = self.vectordb.get()
            chroma_doc_ids: set[str] = set()
            for meta in (result.get("metadatas") or []):
                did = (meta or {}).get("doc_id")
                if did:
                    chroma_doc_ids.add(did)
            for orphan_id in chroma_doc_ids - expected_ids:
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
        expected_ids = self._expected_doc_ids()
        try:
            result = self.doc_db.get()
            doc_db_ids: set[str] = set()
            for meta in (result.get("metadatas") or []):
                did = (meta or {}).get("doc_id")
                if did:
                    doc_db_ids.add(did)
            for orphan_id in doc_db_ids - expected_ids:
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
        """检查 PG chunk_store，避免重新引入已删除的 SQLite 轨。"""
        expected_ids = self._expected_doc_ids()
        try:
            from backend.rag.indexing.chunk_store import get_chunk_store
            cs = get_chunk_store()
            # chunk_store 已于 2026-09-17 收口到 PostgreSQL；通过接口取去重
            # doc_id，避免读取旧路径、避免把所有 chunk 正文加载到内存。
            cs_doc_ids = cs.list_doc_ids()
            for orphan_id in cs_doc_ids - expected_ids:
                report.issues.append(ConsistencyIssue(
                    severity="error",
                    store="chunk_store",
                    doc_id=orphan_id,
                    detail="PG chunk_store 有记录但 registry 无 active 记录",
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
        expected_ids = self._expected_doc_ids()
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

            for orphan_id in bm25_doc_ids - expected_ids:
                report.issues.append(ConsistencyIssue(
                    severity="error",
                    store="bm25",
                    doc_id=orphan_id,
                    detail=f"BM25 有 {bm25_counts.get(orphan_id, 0)} 个 chunk 但 registry 无 active 记录",
                ))

            try:
                result = self.vectordb.get()
                chroma_doc_ids: set[str] = set()
                chroma_counts: dict[str, int] = {}
                for meta in (result.get("metadatas") or []):
                    did = (meta or {}).get("doc_id", "")
                    if did:
                        chroma_doc_ids.add(did)
                        chroma_counts[did] = chroma_counts.get(did, 0) + 1

                # P0-1: BM25 缺失检测 —— active 文档在 Chroma 有向量但 BM25
                # 无条目（上传时 replace_documents 失败被吞 / 历史遗留）。
                # 按 active 口径而非 expected：进行中文档（先写后删/新上传
                # 占位行）尚未写 BM25 属正常中间态，不能误报。
                for did in active_ids & (chroma_doc_ids - bm25_doc_ids):
                    report.issues.append(ConsistencyIssue(
                        severity="error",
                        store="bm25",
                        doc_id=did,
                        detail="BM25 缺失: Chroma 有向量但 BM25 无条目（同步失败或历史遗留）",
                    ))

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


async def consistency_sweep_loop(
    first_delay_seconds: float | None = None,
    interval_seconds: float | None = None,
) -> None:
    """后台定期对账 + 修复 — 五路存储最终一致性清扫（Sweeper）。

    写路径的补偿回滚自身也可能失败（孤儿向量 / BM25 幽灵 chunk 残留），
    即时重试既阻塞用户请求又会因同样故障再次失败；改为事后对账清扫：
    定期跑 check() + repair()，修复失败留日志，下一轮重试。

    由 server 启动时 create_task。首次延迟避开启动期的全量增量索引；
    pipeline 未就绪（get_rag_pipeline 抛错）时本轮跳过、下轮再试。
    """
    import asyncio
    from backend.config.rag import (
        RAG_CONSISTENCY_SWEEP_FIRST_DELAY_MIN,
        RAG_CONSISTENCY_SWEEP_INTERVAL_HOURS,
    )
    first_delay = first_delay_seconds or RAG_CONSISTENCY_SWEEP_FIRST_DELAY_MIN * 60
    interval = interval_seconds or RAG_CONSISTENCY_SWEEP_INTERVAL_HOURS * 3600
    await asyncio.sleep(first_delay)
    while True:
        try:
            from backend.rag.pipeline import get_rag_pipeline
            checker = IndexConsistencyChecker(get_rag_pipeline())
            report = await asyncio.to_thread(checker.check)
            # 告警打点：error 级持续增长 = 数据不一致未收敛，接 Alertmanager
            for issue in report.issues:
                _metric_inc_issues(issue.store, issue.severity)
            if not report.consistent:
                actions = await asyncio.to_thread(checker.repair, report)
                logger.warning(
                    f"[ConsistencySweep] 检出不一致并执行修复 {len(actions)} 项: "
                    f"{actions[:10]}{'...' if len(actions) > 10 else ''}"
                )
        except Exception as e:
            logger.warning(f"[ConsistencySweep] 对账轮失败（下轮重试）: {e}")
        await asyncio.sleep(interval)
