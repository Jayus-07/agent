"""Embedding 模型全局单例 — 双模式 (Cloud/Local) + Token Tracking.

架构设计 (P0 - 硬性约束):
1. EMBEDDING_PROVIDER=cloud → Cloud Embedding (OpenAIEmbeddings，DashScope/SiliconFlow 等)
2. EMBEDDING_PROVIDER=local → Local Embedding (HuggingFaceEmbeddings)
3. Cloud 模式必须校验 EMBEDDING_API_KEY，不存在时明确报错
4. 禁止 Cloud 配置错误时静默降级到 Local
5. Token Tracker 记录用量，Local 模式无法获取 usage 时 total_tokens=null

EMBEDDING_PROVIDER 留空时跟随 ENV_MODE（向后兼容）。

用法:
    from backend.rag.embedding_singleton import get_embedding
    embedding = get_embedding()
"""
from __future__ import annotations

import os
import threading
import time
from pathlib import Path
from typing import Any, List

from langchain_core.embeddings import Embeddings
# langchain_huggingface 顶层 import 会连带 torch（~6s/进程），只在实际
# 加载本地模型时才引入 —— 见 _get_local_embedding 内的延迟导入。

from backend.config import (
    ENV_MODE,
    EMBEDDING_PROVIDER,
    EMBEDDING_MODEL,
    EMBEDDING_API_BASE,
    EMBEDDING_API_KEY,
    EMBEDDING_MODEL_PATH,
    EMBEDDING_BATCH_SIZE,
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
            "或设置 EMBEDDING_PROVIDER=local（或 ENV_MODE=local）使用本地 BGE 模型."
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
        # 单次请求携带的文本条数（EMBEDDING_BATCH_SIZE，默认 10）：
        # DashScope text-embedding-v3 上限 10；换 SiliconFlow / TEI 时可
        # 在 .env 调大到 32+ 提速索引。
        chunk_size=EMBEDDING_BATCH_SIZE,
    )
    
    logger.info(
        "[Embedding] Cloud 模式初始化完成 "
        f"(model={EMBEDDING_MODEL}, api_base={EMBEDDING_API_BASE})"
    )
    return embedding


def _get_local_embedding() -> Embeddings:
    """获取 Local Embedding (HuggingFace BGE)."""
    # 延迟解析设备：resolve_eval_device() 首次调用才 import torch，
    # 避免本模块在导入链上时所有进程陪跑 ~6s 的 torch 导入。
    from backend.config.llm import resolve_eval_device
    from langchain_huggingface import HuggingFaceEmbeddings
    device = resolve_eval_device()
    embedding = HuggingFaceEmbeddings(
        model_name=EMBEDDING_MODEL_PATH,
        model_kwargs={"device": device},
    )

    logger.info(
        "[Embedding] Local 模式初始化完成 "
        f"(model={EMBEDDING_MODEL_PATH}, device={device})"
    )
    return embedding


# =====================================================
# Token-tracking wrapper for Embedding calls
# =====================================================

class _TrackedEmbedding(Embeddings):
    """拦截 embed_documents / embed_query，记录 token 用量到 SQLite。"""

    def __init__(self, inner: Embeddings, tracker):
        self._inner = inner
        self._tracker = tracker
        self._model_name = EMBEDDING_MODEL if EMBEDDING_PROVIDER == "cloud" else EMBEDDING_MODEL_PATH
        self._provider = "dashscope" if EMBEDDING_PROVIDER == "cloud" else "local"
        # 单次 embed_documents 调用的最优文本条数，供索引链路取批大小：
        # cloud 模式受 DashScope 单请求上限约束（外层攒 32 条会被
        # OpenAIEmbeddings 内部再拆 10+10+10+2，白多 3 次 RTT），直接取上限；
        # local 模式批推理走矩阵运算，维持配置批大小。
        if EMBEDDING_PROVIDER == "cloud":
            from backend.config.rag import EMBED_REQUEST_LIMIT
            self.embed_batch_size = max(1, EMBED_REQUEST_LIMIT)
        else:
            from backend.config.rag import EMBED_BATCH_SIZE
            self.embed_batch_size = max(1, EMBED_BATCH_SIZE)

    def embed_documents(self, texts: List[str]) -> List[List[float]]:
        t0 = time.monotonic()
        try:
            result = self._inner.embed_documents(texts)
            # DashScope Cloud 模式可能返回 usage；本地模式没有
            total_tokens = self._estimate_tokens(texts)
            self._record(total_tokens, len(texts), (time.monotonic() - t0) * 1000)
            return result
        except Exception:
            self._record(None, len(texts), (time.monotonic() - t0) * 1000, status="error")
            raise

    def embed_query(self, text: str) -> List[float]:
        t0 = time.monotonic()
        try:
            result = self._inner.embed_query(text)
            total_tokens = self._estimate_tokens([text])
            self._record(total_tokens, 1, (time.monotonic() - t0) * 1000)
            return result
        except Exception:
            self._record(None, 1, (time.monotonic() - t0) * 1000, status="error")
            raise

    @staticmethod
    def _estimate_tokens(texts: List[str]) -> int:
        """粗估 token 数：中文 ~1.5 chars/token，英文 ~4 chars/token。"""
        total_chars = sum(len(t) for t in texts)
        # 保守估计：平均 3 chars/token
        return max(1, total_chars // 3)

    def _record(self, total_tokens, doc_count, duration_ms, status="success"):
        """写入 SQLite（LLMUsageStore）。软失败不影响主流程。"""
        try:
            from backend.observability.llm_usage_store import get_llm_usage_store
            from backend.observability.tracer import current_trace_context
            trace_id, session_id = current_trace_context()
            get_llm_usage_store().record({
                "component": "embedding",
                "model": self._model_name,
                "provider": self._provider,
                "prompt_tokens": total_tokens or 0,
                "completion_tokens": 0,
                "total_tokens": total_tokens or 0,
                "cost_usd": 0.0,
                "duration_ms": duration_ms,
                "trace_id": trace_id or "",
                "session_id": session_id or "",
                "finish_reason": status,
            })
        except Exception:
            pass  # 软失败


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
            model_name=EMBEDDING_MODEL if EMBEDDING_PROVIDER == "cloud" else EMBEDDING_MODEL_PATH,
            backend=EMBEDDING_PROVIDER,
        )
    return _tracker


def get_embedding() -> Embeddings:
    """获取共享的 embedding 模型实例（线程安全单例）。

    根据 EMBEDDING_PROVIDER 自动选择 Cloud/Local 后端:
      - EMBEDDING_PROVIDER=cloud → OpenAIEmbeddings（DashScope/SiliconFlow 等）
      - EMBEDDING_PROVIDER=local → HuggingFaceEmbeddings (BGE)
    留空 EMBEDDING_PROVIDER 时跟随 ENV_MODE（向后兼容）。

    返回：
        Embeddings: LangChain Embeddings 接口

    抛出:
        RuntimeError: Cloud 模式下缺少 EMBEDDING_API_KEY
    """
    global _embedding

    if _embedding is None:
        with _lock:
            if _embedding is None:
                # 根据 EMBEDDING_PROVIDER 选择后端
                if EMBEDDING_PROVIDER == "cloud":
                    base_embedding = _get_cloud_embedding()
                else:
                    base_embedding = _get_local_embedding()

                # 包装为带 Token Tracker 的版本
                tracker = _init_tracker()
                _embedding = _TrackedEmbedding(base_embedding, tracker)

                logger.info(
                    f"[Embedding] 全局单例创建完成 "
                    f"(provider={EMBEDDING_PROVIDER}, env_mode={ENV_MODE}, tracking=True)"
                )
    
    return _embedding


def reset_embedding():
    """重置单例（仅用于测试）."""
    global _embedding, _tracker
    with _lock:
        _embedding = None
        _tracker = None
        logger.warning("[Embedding] 单例已重置（测试用）")
