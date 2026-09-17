"""KnowledgeStore 实现选择工厂 — 引擎开关分发点。

迁移计划 2026-09-17「Chroma → pgvector」：VECTOR_BACKEND（env，默认 chroma）
  - "chroma"   → ChromaKnowledgeStore（现状，默认，合入后行为零变化）
  - "pgvector" → PgVectorKnowledgeStore（PG rag_vectors 表）

两类实现的构造/from_documents/from_texts 签名完全对齐，
调用方只需把 `ChromaKnowledgeStore` 替换为 `get_knowledge_store_class()`。
"""

from __future__ import annotations

from typing import Type

from backend.rag.vectorstore.knowledge_store import KnowledgeStore


def get_knowledge_store_class() -> Type[KnowledgeStore]:
    """按 VECTOR_BACKEND 返回 KnowledgeStore 实现类（每次调用读取，便于测试切换）。"""
    from backend.config.database import VECTOR_BACKEND

    if VECTOR_BACKEND == "pgvector":
        from backend.rag.vectorstore.pgvector_store import PgVectorKnowledgeStore
        return PgVectorKnowledgeStore
    from backend.rag.vectorstore.knowledge_store import ChromaKnowledgeStore
    return ChromaKnowledgeStore
