"""IncrementalIndexer — 增量知识索引器。

文档级增量同步: 扫描磁盘 → SHA256 diff → 分类处理(新增/修改/删除/跳过)。
保持现有 Retriever 和 RAG API 完全不变。

用法:
    indexer = IncrementalIndexer(docs_dir, vectordb, doc_db, embedding, registry)
    result = indexer.sync()
    # result.added, result.modified, result.deleted, result.skipped
"""

from __future__ import annotations

import asyncio
import concurrent.futures
import hashlib
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from langchain_community.document_loaders import TextLoader
from langchain_core.documents import Document

from utils.logger import logger


@dataclass
class SyncResult:
    """增量同步结果。"""
    added: int = 0
    modified: int = 0
    deleted: int = 0
    skipped: int = 0

    @property
    def total_changed(self) -> int:
        return self.added + self.modified + self.deleted

    def __repr__(self) -> str:
        return (f"SyncResult(added={self.added}, modified={self.modified}, "
                f"deleted={self.deleted}, skipped={self.skipped})")


def _run_async(coro):
    """安全运行异步协程。"""
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)
    else:
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            return pool.submit(asyncio.run, coro).result()


class IncrementalIndexer:
    """增量索引器。

    流程:
    1. _scan_disk() → {path: (sha256, size, mtime)}
    2. _compute_delta() → 对比 registry，分类为 ADDED/MODIFIED/DELETED/UNCHANGED
    3. _apply_delta() → 逐文件处理
       - ADDED: 加载 → 分块 → metadata → embed → 写入
       - MODIFIED: 删除旧向量 → 重新加载+写入 → 更新 registry
       - DELETED: 删除向量 → registry 标记 deleted
       - UNCHANGED: 跳过
    """

    SUPPORTED_EXTS = {".txt", ".md", ".pdf", ".docx"}

    def __init__(
        self,
        docs_dir: str,
        vectordb: Any,        # KnowledgeStore (chunk 级)
        doc_db: Any,          # KnowledgeStore (doc 级)
        embedding: Any,       # HuggingFaceEmbeddings
        registry: Any,        # DocumentRegistry
    ):
        self.docs_dir = Path(docs_dir)
        self.vectordb = vectordb
        self.doc_db = doc_db
        self.embedding = embedding
        self.registry = registry

    # ---- 主入口 ----

    def sync(self) -> SyncResult:
        """执行一次增量同步。

        首次运行（registry 为空）→ 所有文件视为 ADDED。
        后续运行 → 按 SHA256 diff。
        """
        disk_files = self._scan_disk()
        registry_rows = self.registry.list_all()

        # 只考虑 active 且路径在磁盘上的（排除已标记 deleted 的）
        active_registry = {
            p: r for p, r in registry_rows.items()
            if r.get("status") == "active"
        }

        delta = self._compute_delta(disk_files, active_registry)
        self._apply_delta(delta, disk_files, active_registry)

        result = SyncResult(
            added=len(delta.added),
            modified=len(delta.modified),
            deleted=len(delta.deleted),
            skipped=len(delta.unchanged),
        )
        logger.info(f"增量索引完成: {result}")
        return result

    # ---- 磁盘扫描 ----

    def _scan_disk(self) -> dict[str, tuple[str, int, float]]:
        """递归遍历 docs_dir，计算每个文件的 SHA256。

        Returns:
            {file_path: (sha256_hex, file_size, file_mtime)}
        """
        result: dict[str, tuple[str, int, float]] = {}

        for root, _dirs, files in os.walk(self.docs_dir):
            for fname in files:
                ext = os.path.splitext(fname)[1].lower()
                if ext not in self.SUPPORTED_EXTS:
                    continue

                file_path = os.path.join(root, fname)
                try:
                    stat = os.stat(file_path)
                    file_hash = self._sha256(file_path)
                    result[file_path] = (file_hash, stat.st_size, stat.st_mtime)
                except OSError as e:
                    logger.warning(f"无法读取文件 {file_path}: {e}")

        return result

    @staticmethod
    def _sha256(file_path: str) -> str:
        """计算文件的 SHA256 哈希。"""
        h = hashlib.sha256()
        with open(file_path, "rb") as f:
            for chunk in iter(lambda: f.read(65536), b""):
                h.update(chunk)
        return h.hexdigest()

    # ---- Diff 计算 ----

    def _compute_delta(
        self,
        disk: dict[str, tuple[str, int, float]],
        registry: dict[str, dict],
    ) -> Delta:
        """对比磁盘和注册表，分类文档。"""
        disk_paths = set(disk.keys())
        registry_paths = set(registry.keys())

        added = disk_paths - registry_paths
        deleted = registry_paths - disk_paths

        modified: set[str] = set()
        unchanged: set[str] = set()
        for p in disk_paths & registry_paths:
            if disk[p][0] != registry[p]["file_hash"]:
                modified.add(p)
            else:
                unchanged.add(p)

        return Delta(
            added=sorted(added),
            modified=sorted(modified),
            deleted=sorted(deleted),
            unchanged=sorted(unchanged),
        )

    # ---- Delta 应用 ----

    def _apply_delta(
        self,
        delta: Delta,
        disk_files: dict,
        registry: dict,
    ):
        """逐文件处理增量变更。"""
        # 删除
        for path in delta.deleted:
            row = registry.get(path, {})
            doc_id = row.get("doc_id", "")
            if doc_id:
                self._remove_document(doc_id)
            self.registry.mark_deleted(path)
            logger.info(f"[DELETED] {os.path.basename(path)}")

        # 修改: 先删后加
        for path in delta.modified:
            row = registry.get(path, {})
            doc_id = row.get("doc_id", "")
            if doc_id:
                self._remove_document(doc_id)
            self._index_file(path)
            logger.info(f"[MODIFIED] {os.path.basename(path)}")

        # 新增
        for path in delta.added:
            self._index_file(path)
            logger.info(f"[ADDED] {os.path.basename(path)}")

    # ---- 单文件索引 ----

    def _index_file(self, file_path: str):
        """索引单篇文档: 加载 → 分块 → metadata → embed → 写入 Chroma。"""
        kb_id = self._derive_kb_id(file_path)
        doc_id = hashlib.md5(os.path.basename(file_path).encode()).hexdigest()[:10]
        file_hash = self._sha256(file_path)
        ext = os.path.splitext(file_path)[1].lower()

        # 1. 加载文档
        if ext == ".pdf":
            try:
                from langchain_community.document_loaders import PyPDFLoader
                loader = PyPDFLoader(file_path)
                raw_docs = loader.load()
            except Exception as e:
                logger.error(f"PDF 加载失败 {file_path}: {e}")
                return
        elif ext == ".docx":
            try:
                from langchain_community.document_loaders import Docx2txtLoader
                loader = Docx2txtLoader(file_path)
                raw_docs = loader.load()
            except Exception as e:
                logger.error(f"DOCX 加载失败 {file_path}: {e}")
                return
        else:
            loader = TextLoader(file_path, encoding="utf-8")
            raw_docs = loader.load()

        if not raw_docs:
            logger.warning(f"文件为空，跳过: {file_path}")
            return

        # 注入 kb_id
        for d in raw_docs:
            d.metadata["kb_id"] = kb_id

        # 2. 分块
        from preprocessing.loader import split_documents
        chunks = split_documents(raw_docs, file_path)

        # 注入 doc_id 到每个 chunk
        for i, ch in enumerate(chunks):
            ch.metadata["doc_id"] = doc_id
            ch.metadata["chunk_index"] = i
            ch.metadata["source_file"] = os.path.basename(file_path)
            ch.metadata["file_path"] = file_path

        # 2.5 脏数据过滤
        from preprocessing.filter import ChunkFilter
        chunk_filter = ChunkFilter()
        filtered_chunks = []
        filtered_count = 0
        for chunk in chunks:
            ok, reason = chunk_filter.should_keep(chunk.page_content, chunk.metadata)
            if ok:
                # 应用 PII 脱敏到 chunk 内容
                if chunk.metadata.get("pii_masked"):
                    chunk.page_content = ChunkFilter.apply_pii_mask(chunk.page_content)
                filtered_chunks.append(chunk)
            else:
                filtered_count += 1
                logger.debug(f"[Filter] 拒绝 chunk: {reason} (doc={file_path})")
        if filtered_count > 0:
            logger.info(f"[Filter] {file_path}: 过滤 {filtered_count}/{len(chunks)} 个 chunk")
        chunks = filtered_chunks

        # 3. 写入 chunk 级向量库
        chunk_ids = []
        if chunks:
            try:
                chunk_ids = self.vectordb.add_documents(chunks)
            except Exception as e:
                logger.error(f"Chunk 写入失败: {e}")
                return

        # 4. 构建 doc 级全文 + metadata
        full_text = "\n\n".join(d.page_content for d in raw_docs)
        doc_meta = {
            "doc_id": doc_id,
            "source_file": os.path.basename(file_path),
            "file_path": file_path,
            "kb_id": kb_id,
            "doc_type": "general",
            "person_names": "",
        }

        # 异步构建元数据
        try:
            meta_result = _run_async(self._build_doc_metadata(full_text, doc_meta))
            doc_meta.update(meta_result)
        except Exception as e:
            logger.warning(f"元数据构建失败（使用默认值）: {e}")

        # 5. 写入 doc 级向量库
        doc_db_id = ""
        try:
            ids = self.doc_db.add_texts(
                texts=[full_text], metadatas=[doc_meta],
            )
            doc_db_id = ids[0] if ids else ""
        except Exception as e:
            logger.error(f"Doc 级写入失败: {e}")

        # 6. 注册到 registry
        self.registry.register(
            file_path=file_path,
            doc_id=doc_id,
            file_hash=file_hash,
            kb_id=kb_id,
            chunk_ids=chunk_ids,
            doc_db_id=doc_db_id,
        )

    async def _build_doc_metadata(self, full_text: str, base_meta: dict) -> dict:
        """异步构建文档级元数据。"""
        try:
            from preprocessing.metadata import (
                classify_doc_type, extract_time_refs,
                detect_business_domain,
            )
            from preprocessing.keyword import extract_doc_keywords
            from preprocessing.entity import extract_person_names
        except ImportError:
            return {}

        try:
            doc_type = classify_doc_type(full_text)
            time_refs = extract_time_refs(full_text)
            domain = detect_business_domain(full_text)
            keywords = extract_doc_keywords(full_text)
            person_names = extract_person_names(full_text)
        except Exception:
            return {"doc_type": "general"}

        return {
            "doc_type": doc_type,
            "business_domain": domain,
            "time_refs": time_refs,
            "doc_keywords": keywords,
            "person_names": ", ".join(person_names) if isinstance(person_names, list)
                           else str(person_names),
        }

    # ---- 公开重索引 ----

    def reindex_file(self, file_path: str) -> dict:
        """公开的单文件重索引 — 删除旧向量后重新加载/分块/Embedding/写入。

        复用 _remove_document() + _index_file()，不重复实现索引逻辑。

        Returns:
            {"doc_id": str, "chunk_count": int, "file_hash": str, "status": str}
        """
        row = self.registry.get_by_path(file_path)
        old_doc_id = row.get("doc_id", "") if row else ""

        # 1. 删除旧向量
        if old_doc_id:
            self._remove_document(old_doc_id)
            logger.info(f"[REINDEX] 已清理旧向量: {old_doc_id}")

        # 2. 重新索引
        self._index_file(file_path)

        # 3. 获取更新后的信息
        updated = self.registry.get_by_path(file_path) or {}
        return {
            "doc_id": updated.get("doc_id", ""),
            "chunk_count": updated.get("chunk_count", 0),
            "file_hash": updated.get("file_hash", ""),
            "status": updated.get("status", "active"),
        }

    # ---- 删除 ----

    def _remove_document(self, doc_id: str):
        """从两个向量库中删除文档的所有数据。"""
        if not doc_id:
            return
        try:
            self.vectordb.delete(where={"doc_id": doc_id})
        except Exception as e:
            logger.warning(f"删除 chunk 向量失败 (doc_id={doc_id}): {e}")
        try:
            self.doc_db.delete(where={"doc_id": doc_id})
        except Exception as e:
            logger.warning(f"删除 doc 向量失败 (doc_id={doc_id}): {e}")

    # ---- KB ID 推导 ----

    def _derive_kb_id(self, file_path: str) -> str:
        """从文件路径推导 kb_id（与 loader.py 逻辑一致）。

        第一级子目录名 = kb_id，根目录 = 'default'。
        """
        rel = os.path.relpath(file_path, self.docs_dir)
        parts = rel.replace("\\", "/").split("/")
        if len(parts) > 1:
            return parts[0]
        return "default"


@dataclass
class Delta:
    """增量 diff 结果。"""
    added: list[str] = field(default_factory=list)
    modified: list[str] = field(default_factory=list)
    deleted: list[str] = field(default_factory=list)
    unchanged: list[str] = field(default_factory=list)
