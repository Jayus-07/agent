"""KnowledgeDocImporter — 种子知识文档直写向量库。

绕过文件系统，将种子 knowledge_doc 的 content 分块后直接写入 ChromaDB，
同时保留完整的种子 metadata（category/tags/valid_from/channel 等）。

用法:
    from backend.seed.importers.knowledge_importer import KnowledgeDocImporter
    importer = KnowledgeDocImporter()
    importer.import_from_json("data/seed/mvp/knowledge_doc.json")
    importer.import_from_ctx(ctx)  # 直接从 GenerationContext 导入
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any

from langchain_text_splitters import RecursiveCharacterTextSplitter

# 项目根加到 sys.path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from backend.shared.logger import logger


class KnowledgeDocImporter:
    """将种子 knowledge_doc 导入向量库。

    流程:
    1. 加载 knowledge_doc JSON 或接收 dict 列表
    2. 按 500 字符分块（保留 metadata）
    3. 写入 chunk 级 ChromaDB (data/chroma/)
    4. 写入 doc 级 ChromaDB (data/doc_db/)

    保留的种子 metadata:
    - doc_id, title, category, category_name
    - tags, version, language, channel
    - valid_from, valid_to, is_active
    - author_id, source, last_updated_at
    """

    def __init__(
        self,
        chroma_path: str | None = None,
        doc_db_path: str | None = None,
        chunk_size: int = 500,
        chunk_overlap: int = 50,
    ):
        from backend.rag.vectorstore.knowledge_store import ChromaKnowledgeStore
        from backend.rag.embedding_singleton import get_embedding
        from backend.config import (
            CHROMA_PATH, DOC_DB_PATH,
        )

        self.chroma_path = chroma_path or CHROMA_PATH
        self.doc_db_path = doc_db_path or DOC_DB_PATH
        self.embedding = get_embedding()  # 全局单例
        self.splitter = RecursiveCharacterTextSplitter(
            chunk_size=chunk_size,
            chunk_overlap=chunk_overlap,
            separators=["\n\n", "\n", "。", ".", " ", ""],
        )

        # 连接到已有的 ChromaDB（如果存在），否则创建新的
        self._chunk_store = self._connect_or_create(
            self.chroma_path, "chunk"
        )
        self._doc_store = self._connect_or_create(
            self.doc_db_path, "doc"
        )

    def _connect_or_create(self, path: str, db_type: str):
        """连接到已有向量库，不存在则创建空库。"""
        from backend.rag.vectorstore.knowledge_store import ChromaKnowledgeStore

        if os.path.exists(path) and os.path.isdir(path):
            logger.info(f"连接已有 {db_type} 级向量库: {path}")
            return ChromaKnowledgeStore(
                persist_directory=path,
                embedding_function=self.embedding,
            )
        else:
            logger.info(f"创建新 {db_type} 级向量库: {path}")
            store = ChromaKnowledgeStore.__new__(ChromaKnowledgeStore)
            store.persist_directory = str(path)
            store.embedding_function = self.embedding
            # 创建一个空 Chroma
            from langchain_chroma import Chroma
            store._chroma = Chroma(
                persist_directory=str(path),
                embedding_function=self.embedding,
            )
            return store

    # ---- 导入入口 ----

    def import_from_json(self, json_path: str) -> dict:
        """从 JSON 文件导入知识文档。

        Returns:
            {"chunks": int, "docs": int, "errors": int}
        """
        path = Path(json_path)
        if not path.exists():
            raise FileNotFoundError(f"知识文档 JSON 不存在: {path}")

        with open(path, "r", encoding="utf-8") as f:
            docs = json.load(f)

        return self.import_docs(docs)

    def import_from_generation_context(self, ctx: "GenerationContext") -> dict:  # noqa: F821
        """从 GenerationContext 直接导入。

        Args:
            ctx: 已生成 knowledge_doc 的上下文
        """
        from backend.seed.exporters.python_dict import DictExporter
        exporter = DictExporter()
        data = exporter.export(ctx)
        docs = data.get("knowledge_doc", [])
        return self.import_docs(docs)

    def import_docs(self, docs: list[dict]) -> dict:
        """核心导入逻辑。

        将 dict 列表转为 Document 对象，分块后分别写入 chunk 级和 doc 级向量库。

        Args:
            docs: knowledge_doc dict 列表

        Returns:
            {"chunks": int, "docs": int, "errors": int}
        """
        from langchain_core.documents import Document

        chunk_docs: list[Document] = []
        doc_texts: list[str] = []
        doc_metas: list[dict] = []

        errors = 0

        for doc in docs:
            try:
                content = doc.get("content", "")
                doc_id = doc.get("doc_id", "")
                title = doc.get("title", "")

                if not content.strip():
                    continue

                # ---- Chunk 级: 分块 ----
                chunks = self.splitter.split_text(content)
                for i, chunk_text in enumerate(chunks):
                    chunk_id = f"{doc_id}:{i}"
                    chunk_meta = {
                        "chunk_id": chunk_id,
                        "doc_id": doc_id,
                        "chunk_index": i,
                        "title": title,
                        "category": doc.get("category", ""),
                        "category_name": doc.get("category_name", ""),
                        "tags": json.dumps(doc.get("tags", []), ensure_ascii=False),
                        "version": doc.get("version", ""),
                        "language": doc.get("language", "zh"),
                        "channel": doc.get("channel", "All"),
                        "source": doc.get("source", "SEED"),
                        "valid_from": doc.get("valid_from", ""),
                        "valid_to": doc.get("valid_to", ""),
                        "is_active": doc.get("is_active", True),
                        "author_id": doc.get("author_id", ""),
                        "kb_id": doc.get("category", "default"),  # RAG 兼容
                    }
                    chunk_docs.append(
                        Document(page_content=chunk_text, metadata=chunk_meta)
                    )

                # ---- Doc 级: 全文 + metadata ----
                doc_texts.append(content)
                doc_metas.append({
                    "doc_id": doc_id,
                    "title": title,
                    "category": doc.get("category", ""),
                    "category_name": doc.get("category_name", ""),
                    "tags": json.dumps(doc.get("tags", []), ensure_ascii=False),
                    "version": doc.get("version", ""),
                    "language": doc.get("language", "zh"),
                    "channel": doc.get("channel", "All"),
                    "source": doc.get("source", "SEED"),
                    "valid_from": doc.get("valid_from", ""),
                    "valid_to": doc.get("valid_to", ""),
                    "is_active": doc.get("is_active", True),
                    "author_id": doc.get("author_id", ""),
                    "kb_id": doc.get("category", "default"),
                    "person_names": "",  # RAG 兼容
                })

            except Exception as e:
                logger.error(f"导入文档 {doc.get('doc_id', '?')} 失败: {e}")
                errors += 1

        # 写入向量库
        chunk_count = 0
        doc_count = 0

        if chunk_docs:
            try:
                ids = self._chunk_store.add_documents(chunk_docs)
                chunk_count = len(ids)
                logger.info(f"Chunk 级写入: {chunk_count} 条")
            except Exception as e:
                logger.error(f"Chunk 级写入失败: {e}")
                errors += 1

        if doc_texts:
            try:
                ids = self._doc_store.add_texts(
                    texts=doc_texts, metadatas=doc_metas,
                )
                doc_count = len(ids)
                logger.info(f"Doc 级写入: {doc_count} 条")
            except Exception as e:
                logger.error(f"Doc 级写入失败: {e}")
                errors += 1

        result = {"chunks": chunk_count, "docs": doc_count, "errors": errors}
        logger.info(
            f"知识文档导入完成: {result['docs']} 篇文档, "
            f"{result['chunks']} 个 chunk, {result['errors']} 个错误"
        )
        return result

    # ---- 查询导入结果 ----

    def stats(self) -> dict:
        """返回导入后的向量库统计。"""
        try:
            chunk_data = self._chunk_store.get()
            doc_data = self._doc_store.get()
        except Exception:
            return {"chunks": 0, "docs": 0}

        return {
            "chunks": len(chunk_data.get("ids", [])),
            "docs": len(doc_data.get("ids", [])),
        }
