"""Reranker Module - CrossEncoder + DashScope API Integration

P0 架构重构 (ENV_MODE 双模式):
1. ENV_MODE=cloud   → Cloud Reranker (DashScope qwen3-rerank)
2. ENV_MODE=local   → Local Reranker (BGE CrossEncoder)
3. RERANK_MODEL 配置动态化 (默认 qwen3-rerank)
4. Token Tracker 记录用量，Local 模式无法获取 usage 时 total_tokens=null

架构特性:
- 懒加载：本地模型仅在首次调用时加载
- 强制模式：根据 ENV_MODE 选择后端，不自动降级
- 统一接口：返回值始终为 list[tuple[Document, float]]
- 可观测性：完整日志追踪 backend_type、评分详情

配置环境变量:
- ENV_MODE: "cloud" (默认) | "local"
- RERANK_MODEL: "qwen3-rerank" (Cloud 模式)
- RERANKER_MODEL_PATH: "BAAI/bge-reranker-base" (Local 模式)
- DASHSCOPE_API_KEY: Cloud 模式必需
- RERANK_TIMEOUT: API 超时阈值 (秒)，默认 5
- RERANK_TOP_K: 返回文档数，默认 8
- RERANK_SCORE_THRESHOLD: 分数过滤阈值，默认 0.3
"""
from __future__ import annotations

import os
import math
import threading
import time
from typing import TYPE_CHECKING, Any

import requests

# dashscope SDK 可选保留（仅用于错误类型兼容）
try:
    import dashscope
    DASHSCOPE_AVAILABLE = True
except ImportError:
    DASHSCOPE_AVAILABLE = False

# sentence_transformers 延迟导入：顶层 import 连带 torch/transformers（~8s），
# 仅 ENV_MODE=local 真正加载本地 CrossEncoder 时才需要（LazyLocalModelLoader）。
if TYPE_CHECKING:
    from sentence_transformers import CrossEncoder

from langchain_core.documents.compressor import BaseDocumentCompressor
from backend.config import (
    ENV_MODE,
    RERANK_MODEL,
    RERANKER_MODEL_PATH,
    RERANK_SCORE_THRESHOLD,
    RERANK_TIMEOUT,
    RERANK_TOP_K,
    RERANKER_DEVICE,
    TOKEN_USAGE_LOG_PATH,
)
from backend.infra.token_tracker import create_tracker_for_rerank
from pathlib import Path
from backend.shared.logger import logger


# ═══════════════════════════════════════════════════════════
# Local Model Loader - 懒加载单例模式
# ═══════════════════════════════════════════════════════════

class LocalModelLoader:
    """本地 CrossEncoder 模型懒加载单例"""
    _instance: CrossEncoder | None = None
    _loaded_at: str = ""
    _lock = threading.Lock()

    @classmethod
    def get_instance(cls) -> CrossEncoder:
        """获取或创建 CrossEncoder 实例 (线程安全：双检锁，模型加载耗时长，
        并发首查若无锁会重复加载数百 MB 模型)"""
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    from sentence_transformers import CrossEncoder
                    cls._instance = CrossEncoder(RERANKER_MODEL_PATH, device=RERANKER_DEVICE)
                    cls._loaded_at = __import__('datetime').datetime.now().isoformat()
                    logger.info(f"本地 reranker 模型懒加载完成：{RERANKER_MODEL_PATH} (device={RERANKER_DEVICE}, at {cls._loaded_at})")
        return cls._instance

    @classmethod
    def is_loaded(cls) -> bool:
        """检查模型是否已加载"""
        return cls._instance is not None

    @classmethod
    def reset(cls) -> None:
        """重置实例 (用于测试)"""
        cls._instance = None
        cls._loaded_at = ""


# ═══════════════════════════════════════════════════════════
# DashScope Reranker - API Backend
# ═══════════════════════════════════════════════════════════

_DASHSCOPE_BASE_URL = "https://dashscope.aliyuncs.com/api/v1"
_DASHSCOPE_RERANK_PATH = "/services/rerank/text-rerank/text-rerank"

class DashScopeReranker(BaseDocumentCompressor):
    """阿里云 DashScope Reranker API 实现（直接 HTTP，无需 dashscope SDK）

    使用 requests 直接调用 DashScope 原生 REST API，避免 SDK 的全局状态污染。
    需要标准 API Key（sk-ws-）；Token Plan（sk-sp-）不支持 rerank 端点。
    
    P0: 模型名从 RERANK_MODEL 配置读取，不再硬编码
    """

    def __init__(self, api_key: str, timeout: int = 5):
        if not api_key:
            raise RuntimeError("DashScopeReranker 需要 DASHSCOPE_API_KEY")

        base_url = os.getenv("DASHSCOPE_API_BASE", _DASHSCOPE_BASE_URL).rstrip("/")
        endpoint = base_url + _DASHSCOPE_RERANK_PATH
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }

        self.__dict__['api_key'] = api_key
        self.__dict__['timeout'] = timeout
        self.__dict__['_endpoint'] = endpoint
        self.__dict__['_headers'] = headers
        self.__dict__['_last_total_tokens'] = 0

        logger.info(
            f"初始化 DashScope Reranker HTTP "
            f"(model={RERANK_MODEL}, endpoint={base_url}, timeout={timeout}s)"
        )

    def rank(self, query: str, documents: list[str], top_k: int = 8) -> list[tuple[int, float]]:
        """调用 DashScope Rerank REST API。

        Returns:
            list[tuple[int, float]]: (index, relevance_score) 列表，score 已在 0-1 区间
        """
        payload = {
            "model": RERANK_MODEL,  # P0: 动态模型名
            "input": {
                "query": query,
                "documents": documents,
            },
            "parameters": {
                "top_n": top_k,
                "return_documents": False,
            },
        }

        last_err: Exception | None = None
        for attempt in range(2):  # 一次网络抖动不该直接损失整轮排序质量，最多重试 1 次
            try:
                resp = requests.post(
                    self._endpoint,
                    json=payload,
                    headers=self._headers,
                    timeout=self.timeout,
                )

                if resp.status_code != 200:
                    raise Exception(
                        f"DashScope API error [status={resp.status_code}]: {resp.text[:500]}"
                    )

                data = resp.json()
                results = data.get("output", {}).get("results", [])
                scored = [(r["index"], r["relevance_score"]) for r in results]

                raw_tokens = data.get("usage", {}).get("total_tokens", 0)
                try:
                    self.__dict__['_last_total_tokens'] = int(raw_tokens)
                except (ValueError, TypeError):
                    self.__dict__['_last_total_tokens'] = 0
                logger.debug(
                    f"DashScope Rerank OK: query_len={len(query)}, "
                    f"doc_count={len(documents)}, top_k={top_k}, tokens={self._last_total_tokens}"
                )
                return scored
            except requests.exceptions.Timeout as e:
                last_err = e
            except requests.exceptions.ConnectionError as e:
                last_err = e
            except Exception as e:
                if "DashScope" in str(e):
                    raise
                last_err = Exception(f"DashScope rerank failed: {e}")

        if isinstance(last_err, requests.exceptions.Timeout):
            raise Exception(f"DashScope rerank 超时 ({self.timeout}s)")
        if isinstance(last_err, requests.exceptions.ConnectionError):
            raise Exception(f"DashScope 网络连接失败: {last_err}")
        raise last_err or Exception("DashScope rerank failed: unknown")

    def compress_documents(self, documents, query, **kwargs):
        """BaseDocumentCompressor 接口实现

        span 埋点在外层 RerankCompressor.compress_documents（同名 "rerank"），
        此处不再重复创建 —— 旧双层埋点导致 trace 树出现两条几乎等时的 rerank 行。
        """
        t0 = time.monotonic()

        if not documents:
            return []

        try:
            texts = [doc.page_content[:2000] for doc in documents]
            ranked_results = self.rank(query, texts, top_k=kwargs.get("top_k", RERANK_TOP_K))

            # 应用阈值过滤并限制数量
            threshold = kwargs.get("threshold", RERANK_SCORE_THRESHOLD)
            top_k = kwargs.get("top_k", RERANK_TOP_K)

            result = [
                (documents[idx], score)
                for idx, score in ranked_results
                if score > threshold
            ][:top_k]

            # 写入 rerank_score 到 metadata
            for doc, score in result:
                doc.metadata["rerank_score"] = round(float(score), 4)

            duration_ms = (time.monotonic() - t0) * 1000

            # 记录 token 用量到 SQLite（使用 rank() 从 API 响应中提取的实际值）
            self._record_tokens(len(texts), duration_ms, total_tokens=self._last_total_tokens)

            return [doc for doc, _ in result]

        except Exception as e:
            duration_ms = (time.monotonic() - t0) * 1000
            logger.error(f"DashScope API rerank 失败：{e}")
            self._record_tokens(len(documents), duration_ms, status="error")
            raise

    def _record_tokens(self, doc_count, duration_ms, total_tokens=0, status="success"):
        """写入 SQLite（LLMUsageStore）。软失败不影响主流程。"""
        try:
            from backend.observability.llm_usage_store import get_llm_usage_store
            from backend.observability.tracer import current_trace_context
            trace_id, session_id = current_trace_context()
            get_llm_usage_store().record({
                "component": "rerank",
                "model": RERANK_MODEL,
                "provider": "dashscope",
                "prompt_tokens": total_tokens,
                "completion_tokens": 0,
                "total_tokens": total_tokens,
                "cost_usd": 0.0,
                "duration_ms": duration_ms,
                "trace_id": trace_id or "",
                "session_id": session_id or "",
                "finish_reason": status,
            })
        except Exception:
            pass  # 软失败


# ═══════════════════════════════════════════════════════════
# Utility Functions
# ═══════════════════════════════════════════════════════════

def _sigmoid(x: float) -> float:
    """数值稳定的 sigmoid，把 CrossEncoder 输出的 logit 归一化到 0-1。

    BGE-reranker-base 的 predict() 输出范围通常是 [-10, +10](logit)，
    直接拿 logit 与 0.3 比较 → 大多数 chunk 的 score < 0.3，全被过滤。
    必须 sigmoid 归一化后再比较。
    """
    if x >= 0:
        return 1.0 / (1.0 + math.exp(-x))
    e = math.exp(x)
    return e / (1.0 + e)


class LocalCrossEncoderBackend(BaseDocumentCompressor):
    """本地 CrossEncoder 实现 (作为 DashScope API 的 fallback)"""

    def __init__(self):
        # 直接调用而非通过实例属性设置以避免 pydantic 约束
        self.__dict__['model'] = LocalModelLoader.get_instance()
        self.__dict__['_backend_name'] = "local"

    def rank(self, query: str, documents: list[str], top_k: int = 8) -> list[tuple[int, float]]:
        """
        使用本地 CrossEncoder 进行重排序

        Returns:
            list[tuple[int, float]]: (index, normalized_score) 列表
        """
        pairs = [(query, doc) for doc in documents]

        try:
            scores = self.model.predict(pairs)
        except Exception as e:
            logger.error(f"本地 reranker 推理失败: {e}")
            return None

        # 配对并排序
        indexed_scores = list(enumerate(scores))
        indexed_scores.sort(key=lambda x: x[1], reverse=True)

        # Sigmoid 归一化
        scored = [
            (idx, _sigmoid(float(score)))
            for idx, score in indexed_scores
        ]

        return scored[:top_k]

    def compress_documents(self, documents, query, **kwargs):
        """BaseDocumentCompressor 接口实现（span 埋点在外层 RerankCompressor，见 DashScope 同名说明）"""
        t0 = time.monotonic()

        if not documents:
            return []

        texts = [doc.page_content[:2000] for doc in documents]
        scored_indexed = self.rank(query, texts, top_k=kwargs.get("top_k", RERANK_TOP_K))

        if scored_indexed is None:
            # 超时/失败：透传原文档，标记为不可靠供下游 Gate 决策
            for doc in documents:
                doc.metadata["rerank_unreliable"] = True
            duration_ms = (time.monotonic() - t0) * 1000
            # 记录失败
            self._record_tokens(len(texts), duration_ms, status="error")
            return list(documents)

        # 创建索引映射
        doc_idx_map = {idx: documents[idx] for idx, _ in scored_indexed}

        # 过滤阈值
        threshold = kwargs.get("threshold", RERANK_SCORE_THRESHOLD)
        top_k = kwargs.get("top_k", RERANK_TOP_K)

        result = [
            (doc_idx_map[idx], score)
            for idx, score in scored_indexed
            if score > threshold
        ][:top_k]

        # 写入 rerank_score 到 metadata
        for doc, score in result:
            doc.metadata["rerank_score"] = round(float(score), 4)

        duration_ms = (time.monotonic() - t0) * 1000

        # 记录 token 用量
        self._record_tokens(len(texts), duration_ms)

        return [doc for doc, _ in result]

    def _record_tokens(self, doc_count, duration_ms, status="success"):
        """写入 SQLite（LLMUsageStore）。软失败不影响主流程。"""
        try:
            from backend.observability.llm_usage_store import get_llm_usage_store
            from backend.observability.tracer import current_trace_context
            trace_id, session_id = current_trace_context()
            # Local CrossEncoder 无 token usage API，用估算值
            # 平均每个文档 ~500 chars，按 3 chars/token 估算
            estimated_tokens = doc_count * 150  # 500 / 3 ≈ 167，保守取 150
            get_llm_usage_store().record({
                "component": "rerank",
                "model": RERANKER_MODEL_PATH,
                "provider": "local",
                "prompt_tokens": estimated_tokens,
                "completion_tokens": 0,
                "total_tokens": estimated_tokens,
                "cost_usd": 0.0,
                "duration_ms": duration_ms,
                "trace_id": trace_id or "",
                "session_id": session_id or "",
                "finish_reason": status,
            })
        except Exception:
            pass  # 软失败


# ═══════════════════════════════════════════════════════════
# Factory Pattern - Backend Selector (P0: ENV_MODE Control)
# ═══════════════════════════════════════════════════════════

# Singleton tracker for token tracking
_reranker_tracker = None


def _get_tracker() -> "TokenTracker":
    """懒加载 TokenTracker."""
    global _reranker_tracker
    if _reranker_tracker is None:
        _reranker_tracker = create_tracker_for_rerank(
            log_path=str(Path(TOKEN_USAGE_LOG_PATH).expanduser().resolve()),
            model_name=RERANK_MODEL if ENV_MODE == "cloud" else RERANKER_MODEL_PATH,
            backend=ENV_MODE,
        )
    return _reranker_tracker


def get_reranker_backend() -> BaseDocumentCompressor:
    """
    获取 reranker 后端实例 (工厂函数，P0 架构重构)
    
    P0 选择逻辑:
      - ENV_MODE=cloud   → DashScopeReranker (强制，API key 错误时抛异常)
      - ENV_MODE=local   → LocalCrossEncoderBackend
    
    关键约束:
      - 不根据 API key 存在与否自动降级
      - Cloud 模式下 DASHSCOPE_API_KEY 缺失时明确报错
      - Token Tracker 记录用量
    """
    if ENV_MODE == "cloud":
        # Cloud 模式：强制使用 DashScope，缺少 API Key 时明确报错
        api_key = os.getenv("DASHSCOPE_API_KEY")
        if not api_key:
            raise RuntimeError(
                "Cloud 模式需要 DASHSCOPE_API_KEY，请在 .env 中设置.\n"
                "或设置 ENV_MODE=local 使用本地 BGE Reranker."
            )
        logger.info(f"[Reranker] Cloud 模式初始化完成 (model={RERANK_MODEL})")
        return DashScopeReranker(api_key=api_key, timeout=RERANK_TIMEOUT)
    else:
        # Local 模式：使用 CrossEncoder
        logger.info(f"[Reranker] Local 模式初始化完成 (model={RERANKER_MODEL_PATH})")
        return LocalCrossEncoderBackend()


# ═══════════════════════════════════════════════════════════
# Legacy Interface - Backward Compatibility
# ═══════════════════════════════════════════════════════════

class RerankCompressor(BaseDocumentCompressor):
    """将全局重排序包装为 LangChain DocumentCompressor，在 MultiQuery 合并结果后统一执行一次"""

    # Pydantic v2 要求字段必须在类级别声明为 type 注解，且必须有默认值
    top_k: int = RERANK_TOP_K
    threshold: float = RERANK_SCORE_THRESHOLD

    def __init__(self):
        # Pydantic v2 初始化流程：先调用 super().__init__() 设置所有 field，
        # 然后再用 __dict__ 添加非 field 属性（如 backend）
        super().__init__()
        # 此时 self.top_k 应通过 Pydantic 机制可用
        self.__dict__['backend'] = None
        self.__dict__['_backend_type'] = "unknown"

    def _ensure_backend(self):
        """懒加载后端实例（线程安全）"""
        if self.backend is None:
            import threading
            if not hasattr(self, '_ensure_lock'):
                self.__dict__['_ensure_lock'] = threading.Lock()
            with self._ensure_lock:
                if self.backend is None:
                    self.__dict__['backend'] = get_reranker_backend()
                    if isinstance(self.backend, DashScopeReranker):
                        self.__dict__['_backend_type'] = "dashscope"
                    else:
                        self.__dict__['_backend_type'] = "local"

    def compress_documents(self, documents, query, **kwargs):
        from backend.observability.tracer import trace_collector

        # 后端懒加载提前到 span 之前，确保 span name 正确
        try:
            self._ensure_backend()
        except Exception:
            pass  # 下方 try 块会再次尝试并捕获完整异常

        span = trace_collector.start_span("rerank", name=self._backend_type.capitalize())

        if not documents:
            trace_collector.end_span(span,
                                     metrics={"input_docs": 0, "output_docs": 0,
                                             "threshold": self.threshold, "backend_type": self._backend_type})
            return []

        # 小文档集合跳过 rerank API 调用（≤2 篇排序无意义）
        if len(documents) <= 2:
            for doc in documents:
                doc.metadata.setdefault("rerank_score", 1.0)
            trace_collector.end_span(span,
                                     metrics={"input_docs": len(documents), "output_docs": len(documents),
                                             "backend_type": self._backend_type, "skipped": "small_n"})
            return list(documents)

        in_count = len(documents)

        try:
            self._ensure_backend()

            # 委托给后端实现 - 使用实例的 threshold 属性而非从 config 导入
            result_docs = self.backend.compress_documents(
                list(documents),
                query,
                top_k=self.top_k,
                threshold=self.threshold
            )

            # ── 驱逐明细（治理 A）：记录哪些文档被 rerank 淘汰及原因 ──
            # 2026-09-13 golden RC-013/080 排查事故：中间阶段静默驱逐文档，
            # 无任何"谁被淘汰、为什么"的留痕。签名 = (source, 内容前40字)，
            # 兼容 backend 返回新对象的情况。
            def _sig(d):
                m = d.metadata or {}
                name = m.get("source") or m.get("source_file") or m.get("doc_id") or "?"
                return (name, (d.page_content or "")[:40])

            try:
                survived = {_sig(d) for d in result_docs}
                evicted = [
                    {"doc": _sig(d)[0], "content_head": _sig(d)[1][:30]}
                    for d in documents if _sig(d) not in survived
                ]
                if evicted:
                    trace_collector.add_event(span, "rerank_eviction", "info",
                        f"Rerank 淘汰 {len(evicted)}/{in_count} 篇 "
                        f"(低于阈值 {RERANK_SCORE_THRESHOLD} 或超出 top_k={self.top_k})",
                        data={"evicted": evicted[:10], "evicted_count": len(evicted)})
                    logger.info(
                        f"[Rerank] 淘汰 {len(evicted)}/{in_count} 篇: "
                        f"{[e['doc'] for e in evicted[:5]]}{'...' if len(evicted) > 5 else ''}"
                    )
            except Exception:
                pass  # 契约留痕失败不影响主流程

            trace_collector.end_span(span,
                                 metrics={"input_docs": in_count,
                                         "output_docs": len(result_docs),
                                         "threshold": RERANK_SCORE_THRESHOLD,
                                         "backend_type": self._backend_type})
            return result_docs

        except Exception as e:
            trace_collector.end_span(span,
                                   metrics={"input_docs": in_count,
                                           "output_docs": min(in_count, self.top_k),
                                           "threshold": RERANK_SCORE_THRESHOLD,
                                           "backend_type": self._backend_type,
                                           "fallback": "passthrough",
                                           "error": str(e)[:100]},
                                   status="error")
            logger.error(f"RerankCompressor 重排失败，降级透传原文档：{e}")
            # 降级契约：重排是增强组件，失败不得减少召回数量 —— 透传原文档，
            # 由下游 Evidence Gate 基于其他信号判定，而非静默清空触发误拒答
            return list(documents)[: self.top_k]


