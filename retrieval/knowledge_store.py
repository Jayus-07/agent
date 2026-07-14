"""KnowledgeStore 抽象接口 — 向量知识库统一抽象。

设计目标:
- 隔离 ChromaDB 具体实现，业务层只依赖接口
- 支持后续无缝切换到 pgvector / Milvus / etc.
- 保持现有 filter 语法兼容（ChromaDB $and / $in 风格）

当前实现: ChromaKnowledgeStore
预留实现: PgVectorKnowledgeStore（后续 PR）
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any, ClassVar, Protocol


# ======================= 抽象接口 =======================

class KnowledgeStore(ABC):
    """向量知识库统一抽象。

    职责:
    - 存储文档/片段的向量表示
    - 支持语义检索（带/不带分数、metadata 过滤）
    - 支持批量读取（用于全量索引构建）

    子类:
    - ChromaKnowledgeStore: 本地文件存储（当前）
    - PgVectorKnowledgeStore: PostgreSQL + pgvector（预留）
    """

    persist_directory: str
    embedding_function: Any

    # ---- 工厂方法 ----

    @classmethod
    @abstractmethod
    def from_documents(
        cls, documents: list[Any], embedding: Any, persist_directory: str,
    ) -> "KnowledgeStore":
        """从 Document 列表创建新库（chunk 级）。"""
        ...

    @classmethod
    @abstractmethod
    def from_texts(
        cls, texts: list[str], embedding: Any,
        metadatas: list[dict] | None, persist_directory: str,
    ) -> "KnowledgeStore":
        """从文本 + metadata 列表创建新库（doc 级）。"""
        ...

    # ---- 查询方法 ----

    @abstractmethod
    def similarity_search_with_score(
        self, query: str, k: int = 5, filter: dict | None = None,
    ) -> list[tuple[Any, float]]:
        """语义检索，返回 (Document, score) 列表。"""
        ...

    @abstractmethod
    def similarity_search(
        self, query: str, k: int = 5, filter: dict | None = None,
    ) -> list[Any]:
        """语义检索，返回 Document 列表（不含分数）。"""
        ...

    @abstractmethod
    def get(self, where: dict | None = None) -> dict:
        """原始数据访问。Returns: {ids, metadatas, documents}"""
        ...

    # ---- 写入方法 ----

    @abstractmethod
    def add_documents(self, documents: list[Any]) -> list[str]:
        """增量添加 Document 到已有库（不覆盖现有数据）。"""
        ...

    @abstractmethod
    def add_texts(
        self, texts: list[str], metadatas: list[dict] | None = None,
    ) -> list[str]:
        """增量添加文本 + metadata 到已有库（不覆盖现有数据）。"""
        ...

    # ---- 删除方法 ----

    @abstractmethod
    def delete(
        self, ids: list[str] | None = None, where: dict | None = None,
    ) -> int:
        """删除向量。ids 精确删，where 条件删（如 {"doc_id": "abc"}）。返回删除数量。"""
        ...


# ======================= Chroma 实现 =======================

class ChromaKnowledgeStore(KnowledgeStore):
    """ChromaDB 实现的向量知识库（当前生产实现）。

    封装 langchain_chroma.Chroma，对外只暴露 KnowledgeStore 接口。
    """

    def __init__(self, persist_directory: str, embedding_function: Any):
        """加载已持久化的 ChromaDB。

        Args:
            persist_directory: 持久化目录路径
            embedding_function: HuggingFaceEmbeddings 实例
        """
        from langchain_chroma import Chroma

        self.persist_directory = str(persist_directory)
        self.embedding_function = embedding_function
        self._chroma: Chroma = Chroma(
            persist_directory=self.persist_directory,
            embedding_function=embedding_function,
        )

    # ---- 工厂方法 ----

    @classmethod
    def from_documents(
        cls, documents: list[Any], embedding: Any, persist_directory: str,
    ) -> "ChromaKnowledgeStore":
        """从 Document 列表创建 chunk 级向量库。"""
        from langchain_chroma import Chroma

        instance = cls.__new__(cls)
        instance.persist_directory = str(persist_directory)
        instance.embedding_function = embedding
        instance._chroma = Chroma.from_documents(
            documents=documents,
            embedding=embedding,
            persist_directory=str(persist_directory),
        )
        return instance

    @classmethod
    def from_texts(
        cls, texts: list[str], embedding: Any,
        metadatas: list[dict] | None, persist_directory: str,
    ) -> "ChromaKnowledgeStore":
        """从文本列表创建 doc 级向量库。"""
        from langchain_chroma import Chroma

        instance = cls.__new__(cls)
        instance.persist_directory = str(persist_directory)
        instance.embedding_function = embedding
        instance._chroma = Chroma.from_texts(
            texts=texts,
            embedding=embedding,
            metadatas=metadatas,
            persist_directory=str(persist_directory),
        )
        return instance

    # ---- 查询方法 ----

    def similarity_search_with_score(
        self, query: str, k: int = 5, filter: dict | None = None,
    ) -> list[tuple[Any, float]]:
        return self._chroma.similarity_search_with_score(
            query=query, k=k, filter=filter,
        )

    def similarity_search(
        self, query: str, k: int = 5, filter: dict | None = None,
    ) -> list[Any]:
        return self._chroma.similarity_search(
            query=query, k=k, filter=filter,
        )

    def get(self, where: dict | None = None) -> dict:
        if where is not None:
            return self._chroma.get(where=where)
        return self._chroma.get()

    # ---- 写入方法 ----

    def add_documents(self, documents: list[Any]) -> list[str]:
        """增量添加 Document 到已有 ChromaDB。"""
        return self._chroma.add_documents(documents)

    def add_texts(
        self, texts: list[str], metadatas: list[dict] | None = None,
    ) -> list[str]:
        """增量添加文本到已有 ChromaDB。"""
        return self._chroma.add_texts(texts=texts, metadatas=metadatas)

    # ---- 删除方法 ----

    def delete(
        self, ids: list[str] | None = None, where: dict | None = None,
    ) -> int:
        """删除 ChromaDB 中的向量。

        Args:
            ids: 精确 ID 列表
            where: 条件删除，如 {"doc_id": "abc"}（匹配所有 chunk）
        """
        if ids is not None:
            self._chroma._collection.delete(ids=ids)
            return len(ids)
        if where is not None:
            self._chroma._collection.delete(where=where)
            return 0  # Chroma 不返回精确计数
        return 0


