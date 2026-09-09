"""Reranker Module - CrossEncoder + DashScope API Integration

提供双后端重排序能力:
1. DashScope API(qwen3-rerank) - 默认使用，高质量中文重排序
2. Local CrossEncoder - 降级 fallback，保证高可用性

架构特性:
- 懒加载：本地模型仅在首次调用时加载
- 透明降级：API 异常自动切换到本地模型
- 统一接口：返回值始终为 list[tuple[Document, float]]
- 可观测性：完整日志追踪 backend_type、降级原因、评分详情

配置环境变量:
- RERANKER_BACKEND: "dashscope" (默认) | "local"
- DASHSCOPE_API_KEY: 阿里云 DashScope API Key（标准 sk-ws- 密钥；Token Plan sk-sp- 不支持 rerank）
- DASHSCOPE_API_BASE: 原生 API Base URL（默认 dashscope 公有云）
- RERANK_TIMEOUT: API 超时阈值 (秒)，默认 5
- RERANK_TOP_K: 返回文档数，默认 8
- RERANK_SCORE_THRESHOLD: 分数过滤阈值，默认 0.3
"""
import os
import math
from typing import Any

import requests

# dashscope SDK 可选保留（仅用于错误类型兼容）
try:
    import dashscope
    DASHSCOPE_AVAILABLE = True
except ImportError:
    DASHSCOPE_AVAILABLE = False

from sentence_transformers import CrossEncoder
from langchain_core.documents.compressor import BaseDocumentCompressor
from backend.config import (
    RERANKER_MODEL_PATH,
    RERANK_SCORE_THRESHOLD,
    RERANK_TIMEOUT,
    RERANK_TOP_K,
    RERANKER_DEVICE,
)
from backend.shared.logger import logger


# ═══════════════════════════════════════════════════════════
# Local Model Loader - 懒加载单例模式
# ═══════════════════════════════════════════════════════════

class LocalModelLoader:
    """本地 CrossEncoder 模型懒加载单例"""
    _instance: CrossEncoder | None = None
    _loaded_at: str = ""

    @classmethod
    def get_instance(cls) -> CrossEncoder:
        """获取或创建 CrossEncoder 实例 (线程安全)"""
        if cls._instance is None:
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

        logger.info(
            f"初始化 DashScope Reranker HTTP (model=qwen3-rerank, "
            f"endpoint={base_url}, timeout={timeout}s)"
        )

    def rank(self, query: str, documents: list[str], top_k: int = 8) -> list[tuple[int, float]]:
        """调用 DashScope Rerank REST API。

        Returns:
            list[tuple[int, float]]: (index, relevance_score) 列表，score 已在 0-1 区间
        """
        payload = {
            "model": "qwen3-rerank",
            "input": {
                "query": query,
                "documents": documents,
            },
            "parameters": {
                "top_n": top_k,
                "return_documents": False,
            },
        }

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

            total_tokens = data.get("usage", {}).get("total_tokens", "N/A")
            logger.debug(
                f"DashScope Rerank OK: query_len={len(query)}, "
                f"doc_count={len(documents)}, top_k={top_k}, tokens={total_tokens}"
            )
            return scored

        except requests.exceptions.Timeout:
            raise Exception(f"DashScope rerank 超时 ({self.timeout}s)")
        except requests.exceptions.ConnectionError as e:
            raise Exception(f"DashScope 网络连接失败: {e}")
        except Exception as e:
            if "DashScope" in str(e):
                raise
            raise Exception(f"DashScope rerank failed: {e}")

    def compress_documents(self, documents, query, **kwargs):
        """BaseDocumentCompressor 接口实现"""
        from backend.observability.tracer import trace_collector
        span = trace_collector.start_span("rerank", name="DashScope API")
        
        if not documents:
            trace_collector.end_span(span, metrics={"input_docs": 0, "output_docs": 0, "backend_type": "dashscope"})
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

            trace_collector.end_span(
                span,
                metrics={
                    "input_docs": len(documents),
                    "output_docs": len(result),
                    "backend_type": "dashscope",
                    "threshold": threshold
                }
            )

            return [doc for doc, _ in result]

        except Exception as e:
            trace_collector.end_span(
                span,
                metrics={
                    "input_docs": len(documents),
                    "output_docs": 0,
                    "backend_type": "dashscope",
                    "error_type": type(e).__name__,
                    "error_message": str(e)[:100]
                },
                status="error"
            )
            logger.error(f"DashScope API rerank 失败：{e}")
            raise


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
        """BaseDocumentCompressor 接口实现"""
        from backend.observability.tracer import trace_collector
        span = trace_collector.start_span("rerank", name="Local Model")

        if not documents:
            trace_collector.end_span(span, metrics={"input_docs": 0, "output_docs": 0, "backend_type": "local"})
            return []

        texts = [doc.page_content[:2000] for doc in documents]
        scored_indexed = self.rank(query, texts, top_k=kwargs.get("top_k", RERANK_TOP_K))

        if scored_indexed is None:
            # 超时/失败：透传原文档，标记为不可靠供下游 Gate 决策
            for doc in documents:
                doc.metadata["rerank_unreliable"] = True
            trace_collector.end_span(
                span,
                metrics={"input_docs": len(documents), "output_docs": len(documents),
                         "backend_type": "local", "fallback": "timeout_passthrough"})
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
            doc.metadata["rerank_score"] = round(score, 4)

        trace_collector.end_span(
            span,
            metrics={
                "input_docs": len(documents),
                "output_docs": len(result),
                "backend_type": "local",
                "threshold": threshold
            }
        )

        return [doc for doc, _ in result]


# ═══════════════════════════════════════════════════════════
# Factory Pattern - Backend Selector
# ═══════════════════════════════════════════════════════════

def get_reranker_backend() -> BaseDocumentCompressor:
    """
    获取 reranker 后端实例 (工厂函数)

    选择逻辑:
    1. 如果 RERANKER_BACKEND=dashscope 且 DASHSCOPE_API_KEY 存在 → 使用 DashScope API
    2. 否则 → 使用本地 CrossEncoder

    Returns:
        BaseDocumentCompressor: DashScopeReranker 或 LocalCrossEncoderBackend
    """
    backend_type = os.getenv("RERANKER_BACKEND", "dashscope")
    api_key = os.getenv("DASHSCOPE_API_KEY")

    if backend_type == "dashscope" and api_key:
        logger.info("使用 DashScope API 进行重排序（HTTP 直连）")
        return DashScopeReranker(api_key=api_key)
    else:
        reason = ""
        if not api_key:
            reason = "缺少 DASHSCOPE_API_KEY"
        elif backend_type != "dashscope":
            reason = f"RERANKER_BACKEND={backend_type}"
        
        logger.warning(f"{reason}, 降级到本地 CrossEncoder 模型")
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


