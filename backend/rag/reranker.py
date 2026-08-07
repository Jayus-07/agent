from sentence_transformers import CrossEncoder
from langchain_core.documents.compressor import BaseDocumentCompressor
from backend.config import RERANKER_MODEL_PATH, RERANK_SCORE_THRESHOLD, RERANK_TIMEOUT, RERANK_TOP_K
from backend.shared.logger import logger
from backend.infra.timeout import safe_call_with_timeout
import math


def _sigmoid(x: float) -> float:
    """数值稳定的 sigmoid，把 CrossEncoder 输出的 logit 归一化到 0-1。

    BGE-reranker-base 的 predict() 输出范围通常是 [-10, +10]（logit），
    直接拿 logit 与 0.3 比较 → 大多数 chunk 的 score < 0.3，全被过滤。
    必须 sigmoid 归一化后再比较。
    """
    if x >= 0:
        return 1.0 / (1.0 + math.exp(-x))
    e = math.exp(x)
    return e / (1.0 + e)


# 本地加载交叉编码器模型（用于重排序）
reranker = CrossEncoder(RERANKER_MODEL_PATH)
logger.info(f"重排序模型加载完成: {RERANKER_MODEL_PATH}")


class RerankCompressor(BaseDocumentCompressor):
    """将全局重排序包装为 LangChain DocumentCompressor，在 MultiQuery 合并结果后统一执行一次"""

    top_k: int = RERANK_TOP_K

    def compress_documents(self, documents, query, **kwargs):
        from backend.observability.tracer import trace_collector
        from backend.config import RERANK_SCORE_THRESHOLD
        span = trace_collector.start_span("rerank", name="Rerank")
        if not documents:
            trace_collector.end_span(span,
                                 metrics={"input_docs": 0, "output_docs": 0, "threshold": RERANK_SCORE_THRESHOLD})
            return []
        in_count = len(documents)
        scored = rerank(query, list(documents), top_k=self.top_k)
        result = [doc for doc, _ in scored]
        trace_collector.end_span(span,
                             metrics={"input_docs": in_count, "output_docs": len(result), "threshold": RERANK_SCORE_THRESHOLD})
        return result


def rerank(
        query,
        docs,
        top_k=3,
        debug=0
):
    """
    全局重排序函数

    参数:
        query: 用户查询字符串
        docs: 待重排的文档列表（每个元素应包含 page_content 和 metadata 属性）
        top_k: 最终返回的文档数量，默认为 3
        debug: 是否打印调试信息，默认为 False

    返回:
        重排后得分最高的 top_k 个文档，每个元素为 (doc, score) 元组
    """

    pairs = [
        (query, doc.page_content)
        for doc in docs
    ]

    scores = safe_call_with_timeout(
        reranker.predict,
        timeout=RERANK_TIMEOUT,
        default_value=None,
        error_message=f"重排序超时 ({RERANK_TIMEOUT}s)",
        sentences=pairs
    )

    if scores is None:
        logger.warning("⚠️ 重排序失败或超时，返回原始文档")
        return [(doc, 0.5) for doc in docs[:top_k]]

    scored_docs = sorted(
        zip(docs, scores),
        key=lambda x: x[1],
        reverse=True
    )

    # ⚠️ CrossEncoder.predict() 输出是 logit（未归一化，常见范围 -10~+10），
    # 必须 sigmoid 归一化到 0-1 再与 threshold 比较，否则绝大多数 chunk 被过滤。
    scored_docs = [
        (doc, _sigmoid(float(score)))
        for doc, score in scored_docs
        if _sigmoid(float(score)) > RERANK_SCORE_THRESHOLD
    ]

    # 将重排序分数写入 metadata，供来源展示使用
    for doc, score in scored_docs:
        doc.metadata["rerank_score"] = round(float(score), 4)

    if debug:
        logger.debug("全局重排序结果:")
        for i, (doc, score) in enumerate(scored_docs[:10]):
            logger.debug(
                f"[{i+1}] score={score:.4f} "
                f"source={doc.metadata.get('source_file')} "
                f"chunk_id={doc.metadata.get('chunk_id')} "
                f"content={doc.page_content[:50]}..."
            )

    logger.info(f"重排序完成: {len(docs)} -> {len(scored_docs[:top_k])} (threshold={RERANK_SCORE_THRESHOLD})")
    return scored_docs[:top_k]
