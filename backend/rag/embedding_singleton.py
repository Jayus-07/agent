"""Embedding 模型全局单例 — 双模式 (Cloud/Local) + Token Tracking.

架构设计 (P0 - 硬性约束):
1. ENV_MODE=cloud → Cloud Embedding (OpenAIEmbeddings via DashScope)
2. ENV_MODE=local → Local Embedding (HuggingFaceEmbeddings)
3. Cloud 模式必须校验 EMBEDDING_API_KEY，不存在时明确报错
4. 禁止 Cloud 配置错误时静默降级到 Local
5. Token Tracker 记录用量，Local 模式无法获取 usage 时 total_tokens=null

用法:
    from backend.rag.embedding_singleton import get_embedding
    embedding = get_embedding()
"""
from __future__ import annotations

import os
import threading
from pathlib import Path

from langchain_core.embeddings import Embeddings
from langchain_huggingface import HuggingFaceEmbeddings

from backend.config import (
    ENV_MODE,
    EMBEDDING_MODEL,
    EMBEDDING_API_BASE,
    EMBEDDING_API_KEY,
    EMBEDDING_MODEL_PATH,
    EVAL_DEVICE,
    TOKEN_USAGE_LOG_PATH,
)
from backend.infra.token_tracker import create_tracker_for_embedding
from backend.shared.logger import logger

# =====================================================
# Factory Pattern - Cloud vs Local
# =====================================================


def _get_cloud_embedding() -> Embeddings:
    """获取 Cloud Embedding (DashScope OpenAI 兼容)."""
    if not EMBEDDING_API_KEY:
        raise RuntimeError(
            "Cloud 模式需要 EMBEDDING_API_KEY，请在 .env 中设置.\n"
            "或设置 ENV_MODE=local 使用本地 BGE 模型."
        )
    
    try:
        from langchain_openai import ChatOpenAI, OpenAIEmbeddings
    except ImportError:
        raise ImportError(
            "Cloud 模式需要 langchain-openai: pip install langchain-openai"
        )
    
    embedding = OpenAIEmbeddings(
        model=EMBEDDING_MODEL,
        api_key=EMBEDDING_API_KEY,
        base_url=EMBEDDING_API_BASE,
        # DashScope 兼容模式只接受字符串数组；默认开启的 tiktoken 分词会把文本
        # 转成 token id 数组发给 API，触发 400 "contents is neither str nor
        # list of str"，导致启动时全量索引重建失败（chroma 0 embeddings）。
        # 关闭本地分词后直接发原文。
        check_embedding_ctx_length=False,
        # DashScope text-embedding-v3 单次请求最多 10 条文本，
        # 默认 chunk_size=1000 会把全部 chunk 一把发出，触发
        # 400 "batch size ... should not be larger than 10"。
        chunk_size=10,
    )
    
    logger.info(
        "[Embedding] Cloud 模式初始化完成 "
        f"(model={EMBEDDING_MODEL}, api_base={EMBEDDING_API_BASE})"
    )
    return embedding


def _get_local_embedding() -> Embeddings:
    """获取 Local Embedding (HuggingFace BGE)."""
    embedding = HuggingFaceEmbeddings(
        model_name=EMBEDDING_MODEL_PATH,
        model_kwargs={"device": EVAL_DEVICE},
    )
    
    logger.info(
        "[Embedding] Local 模式初始化完成 "
        f"(model={EMBEDDING_MODEL_PATH}, device={EVAL_DEVICE})"
    )
    return embedding


# =====================================================
# Singleton with Token Tracker
# =====================================================

_embedding: Embeddings | None = None
_lock = threading.Lock()
_tracker = None


def _init_tracker():
    """懒加载 TokenTracker."""
    global _tracker
    if _tracker is None:
        _tracker = create_tracker_for_embedding(
            log_path=str(Path(TOKEN_USAGE_LOG_PATH).expanduser().resolve()),
            model_name=EMBEDDING_MODEL if ENV_MODE == "cloud" else EMBEDDING_MODEL_PATH,
            backend=ENV_MODE,
        )
    return _tracker


def get_embedding() -> Embeddings:
    """获取共享的 embedding 模型实例（线程安全单例）。
    
    根据 ENV_MODE 自动选择 Cloud/Local 后端:
      - ENV_MODE=cloud   → OpenAIEmbeddings (DashScope)
      - ENV_MODE=local   → HuggingFaceEmbeddings (BGE)
    
    返回：
        Embeddings: LangChain Embeddings 接口
    
    抛出:
        RuntimeError: Cloud 模式下缺少 EMBEDDING_API_KEY
    """
    global _embedding
    
    if _embedding is None:
        with _lock:
            if _embedding is None:
                # 根据 ENV_MODE 选择后端
                if ENV_MODE == "cloud":
                    _embedding = _get_cloud_embedding()
                else:
                    _embedding = _get_local_embedding()
                
                # 包装为带 Token Tracker 的版本 (可选增强)
                _tracker = _init_tracker()
                # 注意：Embedding API 通常不返回 token usage，
                # Local 模式下 will be null
                
                logger.info(f"[Embedding] 全局单例创建完成 (mode={ENV_MODE})")
    
    return _embedding


def reset_embedding():
    """重置单例（仅用于测试）."""
    global _embedding, _tracker
    with _lock:
        _embedding = None
        _tracker = None
        logger.warning("[Embedding] 单例已重置（测试用）")
