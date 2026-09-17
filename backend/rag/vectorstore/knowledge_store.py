"""KnowledgeStore 抽象接口 — 向量知识库统一抽象。

设计目标:
- 隔离具体向量库实现，业务层只依赖接口
- 支持后续无缝切换到 Milvus / etc.
- 保持现有 filter 语法兼容（$and / $in 风格，沿用原 ChromaDB where 语义）

当前实现: PgVectorKnowledgeStore（backend/rag/vectorstore/pgvector_store.py）
历史实现 ChromaKnowledgeStore 已随「Chroma → pgvector」迁移完成删除
（133e6d5 落地 + 数据对账=0 + 评测 PASS 后收口，回滚 = git revert）。
"""

from __future__ import annotations

import json
from abc import ABC, abstractmethod
from typing import Any


def normalize_where(f: dict | None) -> dict | None:
    """归一化 filter 使其符合统一 where 语法（沿用原 ChromaDB 语义）。

    约束：where 顶层只能有一个键；多个条件必须包在单一算子内。
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
    """清洗 metadata 为纯标量值（list/dict → JSON 字符串，None/空 list → 空串）。

    原为 ChromaDB 标量约束而生，pgvector 实现沿用同一规则，
    保证 JSONB 存储形态与历史 Chroma 数据一致。
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
    - PgVectorKnowledgeStore: PostgreSQL + pgvector（rag_vectors 表）
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
