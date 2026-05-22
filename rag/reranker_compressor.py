"""
CrossEncoder Reranker → BaseDocumentCompressor 包装器
可被 ContextualCompressionRetriever 直接消费
"""
from typing import List, Optional, Sequence

from langchain_core.documents import BaseDocumentCompressor, Document
from pydantic import Field

from config import RERANK_SCORE_THRESHOLD, RERANK_TIMEOUT
from utils.logger import logger
from utils.timeout import safe_call_with_timeout


class CrossEncoderCompressor(BaseDocumentCompressor):
    """用 CrossEncoder 对文档进行精排压缩"""

    model: object = Field(description="CrossEncoder 模型实例")
    top_k: int = 8
    threshold: float = RERANK_SCORE_THRESHOLD

    class Config:
        arbitrary_types_allowed = True

    def compress_documents(
        self,
        documents: Sequence[Document],
        query: str,
        callbacks: Optional[list] = None,
    ) -> List[Document]:
        if not documents:
            return []

        pairs = [(query, doc.page_content) for doc in documents]

        scores = safe_call_with_timeout(
            self.model.predict,
            timeout=RERANK_TIMEOUT,
            default_value=None,
            error_message=f"重排序超时 ({RERANK_TIMEOUT}s)",
            sentences=pairs,
        )

        if scores is None:
            logger.warning("CrossEncoderCompressor: 重排序失败，返回原始文档")
            return list(documents)[: self.top_k]

        scored = sorted(zip(documents, scores), key=lambda x: x[1], reverse=True)

        result = []
        for doc, score in scored:
            if len(result) >= self.top_k:
                break
            if score > self.threshold:
                result.append(doc)

        logger.info(
            f"CrossEncoderCompressor: {len(documents)} → {len(result)} "
            f"(top_k={self.top_k}, threshold={self.threshold})"
        )
        return result
