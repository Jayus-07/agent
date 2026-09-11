"""KnowledgeStore 抽象接口 — 向量知识库统一抽象。

设计目标:
- 隔离 ChromaDB 具体实现，业务层只依赖接口
- 支持后续无缝切换到 pgvector / Milvus / etc.
- 保持现有 filter 语法兼容（ChromaDB $and / $in 风格）

当前实现: ChromaKnowledgeStore
预留实现: PgVectorKnowledgeStore（后续 PR）
"""

from __future__ import annotations

import json
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any, ClassVar, Protocol


def normalize_where(f: dict | None) -> dict | None:
    """归一化 filter 使其符合 ChromaDB where 语法。

    Chroma 约束：where 顶层只能有一个键；多个条件必须包在单一算子内。
    形如 {"kb_id": ..., "doc_type": ...} 的 flat 多键 dict 会抛
    ValueError: Expected where to have exactly one operator（fix f6，
    /rag/ask 500）。

    规则:
      - None / 空 dict / 单键 → 原样返回
      - 多键（含 $or 与普通键混排）→ 每键拆为单条件，$and 包裹
    """
    if not f or len(f) <= 1:
        return f
    return {"$and": [{k: v} for k, v in f.items()]}


def _sanitize_metadata(meta: dict) -> dict:
    """清洗 metadata 使其兼容 ChromaDB。

    ChromaDB 约束：metadata 值只能是标量（str/int/float/bool），
    list 必须非空，dict 和 None 都不允许。这里把非标量值转 JSON 字符串，
    空 list 和 None 转空字符串，标量值原样保留。
    """
    cleaned: dict = {}
    for k, v in meta.items():
        if isinstance(v, dict):
            cleaned[k] = json.dumps(v, ensure_ascii=False)
        elif isinstance(v, list):
            cleaned[k] = json.dumps(v, ensure_ascii=False) if v else ""
        elif v is None:
            cleaned[k] = ""
        else:
            cleaned[k] = v
    return cleaned


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
    def add_documents(
        self, documents: list[Any], embeddings: list | None = None,
    ) -> list[str]:
        """增量添加 Document 到已有库（不覆盖现有数据）。

        embeddings: 可选预计算向量（与 documents 对齐），避免库内部重复嵌入。
        """
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

    @abstractmethod
    def update_metadata_where(
        self, where: dict, metadata_update: dict,
    ) -> int:
        """条件更新 metadata：匹配 where 的所有 chunk，更新指定 metadata 字段。

        用于版本快照：财务文档重索引时，旧版 chunk 标记 is_latest=False
        而非删除，保留历史版本向量供时间序列查询。

        Returns: 更新的记录数（部分实现可能返回 0 表示成功但无计数）。
        """
        ...


# ======================= Chroma 实现 =======================

class ChromaKnowledgeStore(KnowledgeStore):
    """ChromaDB 实现的向量知识库（当前生产实现）。

    封装 langchain_chroma.Chroma，对外只暴露 KnowledgeStore 接口。
    """

    def __init__(self, persist_directory: str, embedding_function: Any):
        """加载已持久化的 ChromaDB。
        
        P0: 检查 embedding metadata 一致性
        """
        from langchain_chroma import Chroma

        self.persist_directory = str(persist_directory)
        self.embedding_function = embedding_function
        
        # P0: 验证 embedding model 一致性
        self._validate_embedding_metadata()
        
        self._chroma: Chroma = Chroma(
            persist_directory=self.persist_directory,
            embedding_function=embedding_function,
        )
    
    def _validate_embedding_metadata(self):
        """P0: 验证已有 index 的 embedding metadata 是否一致。
        
        如果当前配置的 embedding model/backend 与已有 index 不一致，
        输出 WARNING 但不自动删除（禁止破坏性操作）。
        """
        import json
        import os
        
        meta_file = os.path.join(self.persist_directory, "_embedding_meta.json")
        if not os.path.exists(meta_file):
            return  # 无历史记录，跳过检查
        
        try:
            with open(meta_file, "r", encoding="utf-8") as f:
                stored_meta = json.load(f)
            
            # 获取当前配置
            from backend.config import ENV_MODE, EMBEDDING_MODEL
            current_meta = {
                "embedding_model": EMBEDDING_MODEL,
                "backend": ENV_MODE,
            }
            
            # 比较
            if stored_meta != current_meta:
                logger.warning(
                    f"[ChromaMetadata] Embedding model changed!\n"
                    f"  Existing: {stored_meta}\n"
                    f"  Current:  {current_meta}\n"
                    f"  Warning: Existing Chroma index may be incompatible.\n"
                    f"  Full index rebuild is required."
                )
        except Exception as e:
            logger.debug(f"[ChromaMetadata] Metadata validation failed: {e}")

    # ---- 工厂方法 ----

    @classmethod
    def from_documents(
        cls, documents: list[Any], embedding: Any, persist_directory: str,
        embedding_metadata: dict | None = None,
    ) -> "ChromaKnowledgeStore":
        """从 Document 列表创建 chunk 级向量库。
        
        Args:
            embedding_metadata: Optional metadata about the embedding model
                e.g., {"embedding_model": "text-embedding-v3", "backend": "cloud"}
        """
        from langchain_chroma import Chroma

        for doc in documents:
            if hasattr(doc, "metadata") and isinstance(doc.metadata, dict):
                doc.metadata = _sanitize_metadata(doc.metadata)
        instance = cls.__new__(cls)
        instance.persist_directory = str(persist_directory)
        instance.embedding_function = embedding
        
        # P0: 保存 embedding metadata 到持久化文件
        if embedding_metadata:
            import json
            import os
            meta_file = os.path.join(str(persist_directory), "_embedding_meta.json")
            with open(meta_file, "w", encoding="utf-8") as f:
                json.dump(embedding_metadata, f, ensure_ascii=False, indent=2)
        
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
        embedding_metadata: dict | None = None,
    ) -> "ChromaKnowledgeStore":
        """从文本列表创建 doc 级向量库。
        
        Args:
            embedding_metadata: Optional metadata about the embedding model
        """
        from langchain_chroma import Chroma

        instance = cls.__new__(cls)
        instance.persist_directory = str(persist_directory)
        instance.embedding_function = embedding
        
        # P0: 保存 embedding metadata
        if embedding_metadata:
            import json
            import os
            meta_file = os.path.join(str(persist_directory), "_embedding_meta.json")
            with open(meta_file, "w", encoding="utf-8") as f:
                json.dump(embedding_metadata, f, ensure_ascii=False, indent=2)
        
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
            query=query, k=k, filter=normalize_where(filter),
        )

    def similarity_search(
        self, query: str, k: int = 5, filter: dict | None = None,
    ) -> list[Any]:
        return self._chroma.similarity_search(
            query=query, k=k, filter=normalize_where(filter),
        )

    def get(self, where: dict | None = None) -> dict:
        if where is not None:
            return self._chroma.get(where=normalize_where(where))
        return self._chroma.get()

    # ---- 写入方法 ----

    def add_documents(
        self, documents: list[Any], embeddings: list | None = None,
    ) -> list[str]:
        """增量添加 Document 到已有 ChromaDB（清洗非标量 metadata）。

        Args:
            embeddings: 预计算向量（与 documents 对齐）。索引链路已做过预嵌入
                失败预检，传入可避免 langchain 内部对同一批文本再次全量嵌入。
        """
        for doc in documents:
            if hasattr(doc, "metadata") and isinstance(doc.metadata, dict):
                doc.metadata = _sanitize_metadata(doc.metadata)
        if embeddings is not None:
            return self._chroma.add_documents(documents, embeddings=embeddings)
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
            self._chroma._collection.delete(where=normalize_where(where))
            return 0  # Chroma 不返回精确计数
        return 0

    def update_metadata_where(
        self, where: dict, metadata_update: dict,
    ) -> int:
        """条件更新 metadata：匹配 where 的所有 chunk 更新 metadata 字段。

        ChromaDB update API：先 get 匹配 ID，再逐批 update metadata。
        用于版本快照：财务文档重索引时旧版 chunk 标记 is_latest=False。
        """
        normalized_where = normalize_where(where)
        cleaned_meta = _sanitize_metadata(metadata_update)
        try:
            result = self._chroma._collection.get(where=normalized_where)
            ids = result.get("ids", []) or []
            if not ids:
                return 0
            metadatas = [cleaned_meta] * len(ids)
            self._chroma._collection.update(
                ids=ids, metadatas=metadatas,
            )
            return len(ids)
        except Exception as e:
            from backend.shared.logger import logger
            logger.warning(f"[ChromaKnowledgeStore] update_metadata_where 失败: {e}")
            return 0
