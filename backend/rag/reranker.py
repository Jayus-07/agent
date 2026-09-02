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
- DASHSCOPE_API_KEY: 阿里云 DashScope API Key
- RERANK_TIMEOUT: API 超时阈值 (秒)，默认 5
- RERANK_TOP_K: 返回文档数，默认 8
- RERANK_SCORE_THRESHOLD: 分数过滤阈值，默认 0.3
"""
import os
import math
from typing import Any

# 尝试导入 dashscope SDK，如果未安装则手动降级
try:
    import dashscope
    from http import HTTPStatus
    DASHSCOPE_AVAILABLE = True
except ImportError:
    DASHSCOPE_AVAILABLE = False
    # 如果使用 API 但未安装 SDK，会抛出清晰的错误

from sentence_transformers import CrossEncoder
from langchain_core.documents.compressor import BaseDocumentCompressor
from backend.config import (
    RERANKER_MODEL_PATH,
    RERANK_SCORE_THRESHOLD,
    RERANK_TIMEOUT,
    RERANK_TOP_K,
)
from backend.shared.logger import logger
from backend.infra.timeout import safe_call_with_timeout


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
            cls._instance = CrossEncoder(RERANKER_MODEL_PATH)
            cls._loaded_at = __import__('datetime').datetime.now().isoformat()
            logger.info(f"本地 reranker 模型懒加载完成：{RERANKER_MODEL_PATH} (at {cls._loaded_at})")
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

class DashScopeReranker(BaseDocumentCompressor):
    """阿里云 DashScope Reranker API 实现 (使用官方 SDK)"""

    def __init__(self, api_key: str, timeout: int = 5):
        """
        Args:
            api_key: 阿里云 DashScope API Key
            timeout: API 请求超时 (秒)
        """
        if not DASHSCOPE_AVAILABLE:
            raise RuntimeError(
                "DashScope API requires the 'dashscope' package. "
                "Please install it with: pip install dashscope"
            )
        
        # 直接设置属性以避免 pydantic 约束
        self.__dict__['api_key'] = api_key
        self.__dict__['timeout'] = timeout
        
        # 配置 dashscope SDK
        dashscope.api_key = api_key
        dashscope.base_http_api_url = 'https://dashscope.aliyuncs.com/api/v1'
        
        logger.info(f"初始化 DashScope Reranker SDK (model=qwen3-rerank, timeout={self.timeout}s)")

    def rank(self, query: str, documents: list[str], top_k: int = 8) -> list[tuple[int, float]]:
        """
        调用 DashScope API 进行重排序 (使用官方 SDK)

        Args:
            query: 用户查询
            documents: 文档内容列表
            top_k: 返回前 K 个结果

        Returns:
            list[tuple[int, float]]: (index, relevance_score) 列表，score 已在 0-1 区间

        Raises:
            Exception: 调用失败时抛出异常，由上层处理降级
        """
        try:
            # 使用官方 DashScope TextReRank API
            resp = dashscope.TextReRank.call(
                model="qwen3-rerank",
                query=query,
                documents=documents,
                top_n=top_k,
                return_documents=False  # 不需要返回文档内容，节省带宽
            )

            if resp.status_code == HTTPStatus.OK:
                # 解析响应：extract (index, score) from results
                results = resp.output.results if hasattr(resp, 'output') and hasattr(resp.output, 'results') else []
                scored = [
                    (r.index, r.relevance_score)
                    for r in results
                ]

                # 记录 Token 使用
                usage = resp.usage if hasattr(resp, 'usage') else {}
                total_tokens = usage.get('total_tokens', 'N/A') if isinstance(usage, dict) else 'N/A'
                
                logger.debug(
                    f"DashScope TextReRank OK: query_len={len(query)}, doc_count={len(documents)}, "
                    f"top_k={top_k}, tokens={total_tokens}"
                )

                return scored
            else:
                # API 返回错误
                error_msg = f"DashScope API error [status={resp.status_code}]: {resp.message}"
                raise Exception(error_msg)

        except dashscope.errors.InputError as e:
            raise Exception(f"Invalid input to DashScope API: {e}")
        except dashscope.errors.APIException as e:
            raise Exception(f"DashScope API exception: {e}")
        except dashscope.errors.NetworkError as e:
            raise Exception(f"DashScope network error: {e}")
        except Exception as e:
            raise Exception(f"DashScope rerank failed: {e}")

    def compress_documents(self, documents, query, **kwargs):
        """BaseDocumentCompressor 接口实现"""
        from backend.observability.tracer import trace_collector
        span = trace_collector.start_span("rerank", name="DashScope API")
        
        if not documents:
            trace_collector.end_span(span, metrics={"input_docs": 0, "output_docs": 0, "backend_type": "dashscope"})
            return []

        try:
            texts = [doc.page_content for doc in documents]
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

        scores = safe_call_with_timeout(
            self.model.predict,
            timeout=RERANK_TIMEOUT,
            default_value=None,
            error_message=f"本地 reranker 超时 ({RERANK_TIMEOUT}s)",
            inputs=pairs  # 使用新的参数名 inputs 代替 sentences
        )

        if scores is None:
            # Fallback: 返回所有文档索引，分数为 0.5
            return [(i, 0.5) for i in range(min(len(documents), top_k))]

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

        texts = [doc.page_content for doc in documents]
        scored_indexed = self.rank(query, texts, top_k=kwargs.get("top_k", RERANK_TOP_K))

        # 创建索引映射
        doc_idx_map = {idx: doc for idx, _ in scored_indexed}

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
        logger.info("使用 DashScope API 进行重排序")
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
        """懒加载后端实例"""
        if self.backend is None:
            self.__dict__['backend'] = get_reranker_backend()
            if isinstance(self.backend, DashScopeReranker):
                self.__dict__['_backend_type'] = "dashscope"
            else:
                self.__dict__['_backend_type'] = "local"

    def compress_documents(self, documents, query, **kwargs):
        from backend.observability.tracer import trace_collector
        
        self._ensure_backend()
        
        span = trace_collector.start_span("rerank", name=self._backend_type.capitalize())
        
        if not documents:
            trace_collector.end_span(span,
                                     metrics={"input_docs": 0, "output_docs": 0, 
                                             "threshold": self.threshold, "backend_type": self._backend_type})
            return []
        
        in_count = len(documents)
        
        try:
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
                                           "output_docs": 0, 
                                           "threshold": RERANK_SCORE_THRESHOLD,
                                           "backend_type": self._backend_type,
                                           "error": str(e)[:100]},
                                   status="error")
            logger.error(f"RerankCompressorscompress_documents失败：{e}")
            # 降级：返回空列表或原始文档
            return []


def rerank(
        query,
        docs,
        top_k=3,
        debug=0
):
    """
    全局重排序函数（向后兼容）

    参数:
        query: 用户查询字符串
        docs: 待重排的文档列表 (每个元素应包含 page_content 和 metadata 属性)
        top_k: 最终返回的文档数量，默认为 3
        debug: 是否打印调试信息，默认为 False

    返回:
        重排后得分最高的 top_k 个文档，每个元素为 (doc, score) 元组
    """
    if not docs:
        return []

    # 使用工厂函数获取后端实例
    backend = get_reranker_backend()
    primary_backend_type = type(backend).__name__
    
    # 转换为文本列表
    texts = [doc.page_content for doc in docs]
    
    # 尝试使用首选后端，如果失败则降级
    result_docs = None
    used_fallback = False
    error_reason = None
    
    try:
        ranked_results = backend.rank(query, texts, top_k=top_k)
        
        # 应用阈值过滤并构建结果
        threshold = RERANK_SCORE_THRESHOLD
        result_docs = [
            (docs[idx], score)
            for idx, score in ranked_results
            if score > threshold
        ][:top_k]

    except Exception as e:
        error_reason = str(e)[:100]
        logger.warning(f"Primary backend ({primary_backend_type}) failed: {error_reason}")
        
        # 尝试降级到本地模型
        if isinstance(backend, DashScopeReranker):
            logger.info("Falling back to local CrossEncoder model...")
            fallback_backend = LocalCrossEncoderBackend()
            try:
                ranked_results = fallback_backend.rank(query, texts, top_k=top_k)
                threshold = RERANK_SCORE_THRESHOLD
                result_docs = [
                    (docs[idx], score)
                    for idx, score in ranked_results
                    if score > threshold
                ][:top_k]
                used_fallback = True
            except Exception as fallback_error:
                logger.error(f"Fallback to local model also failed: {fallback_error}")
                raise
        else:
            raise

    # 将重排序分数写入 metadata，供来源展示使用
    for doc, score in result_docs:
        doc.metadata["rerank_score"] = round(float(score), 4)

    if debug:
        logger.debug("Global rerank results:")
        for i, (doc, score) in enumerate(result_docs[:10]):
            logger.debug(
                f"[{i+1}] score={score:.4f} "
                f"source={doc.metadata.get('source_file')} "
                f"chunk_id={doc.metadata.get('chunk_id')} "
                f"content={doc.page_content[:50]}..."
            )

    backend_name = "DashScope API" if isinstance(backend, DashScopeReranker) else "Local Model"
    status = " (with fallback)" if used_fallback else ""
    logger.info(f"Re-rank complete ({backend_name}{status}): {len(docs)} -> {len(result_docs)} (threshold={RERANK_SCORE_THRESHOLD})")
    return result_docs
