from sentence_transformers import CrossEncoder
from langchain_core.documents.compressor import BaseDocumentCompressor
from config import RERANKER_MODEL_PATH, RERANK_SCORE_THRESHOLD, RERANK_TIMEOUT, RERANK_TOP_K
from utils.logger import logger
from utils.timeout import safe_call_with_timeout

# 本地加载交叉编码器模型（用于重排序）
reranker = CrossEncoder(RERANKER_MODEL_PATH)
logger.info(f"重排序模型加载完成: {RERANKER_MODEL_PATH}")


class RerankCompressor(BaseDocumentCompressor):
    """将全局重排序包装为 LangChain DocumentCompressor，在 MultiQuery 合并结果后统一执行一次"""

    top_k: int = RERANK_TOP_K

    def compress_documents(self, documents, query, **kwargs):
        if not documents:
            return []
        scored = rerank(query, list(documents), top_k=self.top_k)
        return [doc for doc, _ in scored]


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

    scored_docs = [
        (doc, score)
        for doc, score in scored_docs
        if score > RERANK_SCORE_THRESHOLD
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
