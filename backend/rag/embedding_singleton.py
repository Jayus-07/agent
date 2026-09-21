"""Embedding 模型全局单例 — 双模式 (Cloud/Local) + Token Tracking.

架构设计 (P0 - 硬性约束):
1. EMBEDDING_PROVIDER=cloud → Cloud Embedding (OpenAIEmbeddings，DashScope/SiliconFlow 等)
2. EMBEDDING_PROVIDER=local → Local Embedding (HuggingFaceEmbeddings)
3. Cloud 模式必须校验数据库供应商 API Key，不存在时明确报错
4. 禁止 Cloud 配置错误时静默降级到 Local
5. Token Tracker 记录用量，Local 模式无法获取 usage 时 total_tokens=null

EMBEDDING_PROVIDER 留空时跟随 ENV_MODE（向后兼容）。

用法:
    from backend.rag.embedding_singleton import get_embedding
    embedding = get_embedding()
"""
from __future__ import annotations

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
    EMBEDDING_MODEL_PATH,
    EMBEDDING_BATCH_SIZE,
    EMBEDDING_REQUEST_TIMEOUT,
    TOKEN_USAGE_LOG_PATH,
)
from backend.config import model_roles
from backend.infra.llm import credentials as credentials_mod
from backend.infra.llm import specialized as specialized_mod
from backend.infra.token_tracker import create_tracker_for_embedding
from backend.shared.logger import logger

# =====================================================
# Factory Pattern - Cloud vs Local
# =====================================================


def _configured_embedding_model() -> str:
    """读取向量化角色；无 DB 覆盖时保持历史配置常量。"""
    return model_roles.resolve_runtime_name("embedding", EMBEDDING_MODEL)


def _embedding_runtime_signature() -> tuple[Any, ...]:
    """返回影响 embedding 客户端的配置签名，供已构建的包装器热切换。"""
    binding = specialized_mod.resolve_binding("embedding")
    if binding is not None:
        return (
            "specialized",
            binding.provider_id,
            binding.adapter,
            binding.model_name,
            binding.base_url,
            repr(sorted(dict(binding.options).items())),
            credentials_mod.credentials_version(binding.provider_id),
        )
    return (
        "database-unconfigured",
        EMBEDDING_PROVIDER,
        _configured_embedding_model(),
    )


def _embedding_is_cloud() -> bool:
    """判断当前 embedding 是否应走远端协议。"""
    return (
        EMBEDDING_PROVIDER == "cloud"
        or specialized_mod.resolve_binding("embedding") is not None
    )


def _resolve_cloud_embedding_config() -> dict[str, Any]:
    """解析云端 embedding 出站配置；数据库专项绑定是唯一配置来源。

    都用 DB（2026-09-21 拍板）：无绑定时不再回退旧 env（EMBEDDING_API_KEY/BASE），
    返回空配置，由 `_get_cloud_embedding` 以明确报错拒绝，提示先在管理端绑定。
    """
    binding = specialized_mod.resolve_binding("embedding")
    if binding is None:
        return {
            "model": "",
            "api_key": "",
            "base_url": "",
            "dimensions": None,
            "provider": "",
        }

    credentials = credentials_mod.resolve_credentials(
        binding.provider_id,
        model_name=binding.model_name,
    )
    dimensions = binding.options.get("dimensions")
    return {
        "model": binding.model_name,
        "api_key": credentials.api_key or "",
        "base_url": binding.base_url,
        "dimensions": int(dimensions) if dimensions is not None else None,
        "provider": binding.provider_id,
    }


def _get_cloud_embedding() -> Embeddings:
    """获取 Cloud Embedding (DashScope OpenAI 兼容)."""
    runtime_config = _resolve_cloud_embedding_config()
    if not runtime_config["api_key"]:
        raise RuntimeError(
            "数据库未配置可用的 embedding 供应商 API Key，请先在管理端测试并保存"
            "向量模型；如需本地模型，请显式使用本地模式。"
        )
    
    try:
        from langchain_openai import ChatOpenAI, OpenAIEmbeddings
    except ImportError:
        raise ImportError(
            "Cloud 模式需要 langchain-openai: pip install langchain-openai"
        )
    
    embedding_kwargs: dict[str, Any] = {
        "model": runtime_config["model"],
        "api_key": runtime_config["api_key"],
        "base_url": runtime_config["base_url"],
        # DashScope 兼容模式只接受字符串数组；默认开启的 tiktoken 分词会把文本
        # 转成 token id 数组发给 API，触发 400 "contents is neither str nor
        # list of str"，导致启动时全量索引重建失败（chroma 0 embeddings）。
        # 关闭本地分词后直接发原文。
        "check_embedding_ctx_length": False,
        # 单次请求携带的文本条数（EMBEDDING_BATCH_SIZE，默认 10）：
        # DashScope text-embedding-v3 上限 10；换 SiliconFlow / TEI 时可
        # 在 .env 调大到 32+ 提速索引。
        "chunk_size": EMBEDDING_BATCH_SIZE,
        # 让 HTTP 请求在专家总超时前自行结束，不能依赖外层线程中断。
        "timeout": EMBEDDING_REQUEST_TIMEOUT,
    }
    if runtime_config["dimensions"] is not None:
        embedding_kwargs["dimensions"] = runtime_config["dimensions"]
    embedding = OpenAIEmbeddings(**embedding_kwargs)
    
    logger.info(
        "[Embedding] Cloud 模式初始化完成 "
        f"(model={runtime_config['model']}, api_base={runtime_config['base_url']})"
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
        self._refresh_lock = threading.RLock()
        self._config_signature = _embedding_runtime_signature()
        cloud_enabled = _embedding_is_cloud()
        self._model_name = _configured_embedding_model() if cloud_enabled else EMBEDDING_MODEL_PATH
        self._provider = "cloud" if cloud_enabled else "local"
        # 单次 embed_documents 调用的最优文本条数，供索引链路取批大小：
        # cloud 模式受 DashScope 单请求上限约束（外层攒 32 条会被
        # OpenAIEmbeddings 内部再拆 10+10+10+2，白多 3 次 RTT），直接取上限；
        # local 模式批推理走矩阵运算，维持配置批大小。
        if cloud_enabled:
            from backend.config.rag import EMBED_REQUEST_LIMIT
            self.embed_batch_size = max(1, EMBED_REQUEST_LIMIT)
        else:
            from backend.config.rag import EMBED_BATCH_SIZE
            self.embed_batch_size = max(1, EMBED_BATCH_SIZE)

    def _refresh_if_changed(self) -> None:
        """配置轮询后，在下一次调用前替换旧的出站 embedding 客户端。"""
        signature = _embedding_runtime_signature()
        if signature == self._config_signature:
            return
        with self._refresh_lock:
            if signature == self._config_signature:
                return
            cloud_enabled = _embedding_is_cloud()
            self._inner = _get_cloud_embedding() if cloud_enabled else _get_local_embedding()
            self._config_signature = signature
            self._model_name = _configured_embedding_model() if cloud_enabled else EMBEDDING_MODEL_PATH
            self._provider = "cloud" if cloud_enabled else "local"
            if cloud_enabled:
                from backend.config.rag import EMBED_REQUEST_LIMIT
                self.embed_batch_size = max(1, EMBED_REQUEST_LIMIT)
            else:
                from backend.config.rag import EMBED_BATCH_SIZE
                self.embed_batch_size = max(1, EMBED_BATCH_SIZE)
            logger.info(
                "[Embedding] 检测到配置变化，已热切换客户端 (model=%s)",
                self._model_name,
            )

    def embed_documents(self, texts: List[str]) -> List[List[float]]:
        t0 = time.monotonic()
        try:
            self._refresh_if_changed()
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
            self._refresh_if_changed()
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
            from backend.observability.llm_usage_store import current_usage_attribution
            attribution = current_usage_attribution()
            get_llm_usage_store().record({
                "component": "embedding",
                "model": self._model_name,
                "provider": self._provider,
                "prompt_tokens": total_tokens or 0,
                "completion_tokens": 0,
                "total_tokens": total_tokens or 0,
                "cost_usd": 0.0,
                "duration_ms": duration_ms,
                "trace_id": attribution["trace_id"],
                "session_id": attribution["session_id"],
                "request_id": attribution["request_id"],
                "user_id": attribution["user_id"],
                "tenant_id": attribution["tenant_id"],
                "run_id": attribution["run_id"],
                "step_id": attribution["step_id"],
                "role": attribution["role"] or "embedding",
                "stage": attribution["stage"] or "embedding",
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
        cloud_enabled = (
            EMBEDDING_PROVIDER == "cloud"
            or specialized_mod.resolve_binding("embedding") is not None
        )
        _tracker = create_tracker_for_embedding(
            log_path=str(Path(TOKEN_USAGE_LOG_PATH).expanduser().resolve()),
            model_name=_configured_embedding_model() if cloud_enabled else EMBEDDING_MODEL_PATH,
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
        RuntimeError: Cloud 模式下数据库未绑定 embedding 或供应商缺少 API Key
    """
    global _embedding

    if _embedding is None:
        with _lock:
            if _embedding is None:
                # 根据 EMBEDDING_PROVIDER 选择后端
                if (
                    EMBEDDING_PROVIDER == "cloud"
                    or specialized_mod.resolve_binding("embedding") is not None
                ):
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
