"""Embedding 模型全局单例 — 避免重复加载 BGE 模型（每次 ~400MB）。

用法:
    from backend.rag.embedding_singleton import get_embedding
    embedding = get_embedding()
"""
from __future__ import annotations

import threading
from langchain_huggingface import HuggingFaceEmbeddings

from backend.config import EMBEDDING_MODEL_PATH
from backend.shared.logger import logger

_embedding: HuggingFaceEmbeddings | None = None
_lock = threading.Lock()


def get_embedding() -> HuggingFaceEmbeddings:
    """获取共享的 embedding 模型实例（线程安全单例）。

    首次调用时加载 BGE 模型（~400MB），后续调用返回同一实例。
    避免 pipeline / long_term_memory / seed_importer 各自加载一份。
    """
    global _embedding
    if _embedding is None:
        with _lock:
            if _embedding is None:
                logger.info("[Embedding] 加载模型: %s", EMBEDDING_MODEL_PATH)
                _embedding = HuggingFaceEmbeddings(model_name=EMBEDDING_MODEL_PATH)
                logger.info("[Embedding] 模型加载完成（全局单例）")
    return _embedding
