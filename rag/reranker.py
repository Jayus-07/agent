# =====================================================
# rag/reranker.py
# 全局精排模块
# =====================================================

from sentence_transformers import CrossEncoder
from config import RERANKER_MODEL_PATH, RERANK_SCORE_THRESHOLD, RERANK_TIMEOUT
from utils.logger import logger
from utils.timeout import safe_call_with_timeout

# 本地加载交叉编码器模型（用于重排序）
reranker = CrossEncoder(RERANKER_MODEL_PATH)
logger.info(f"重排序模型加载完成: {RERANKER_MODEL_PATH}")


def rerank(
        query,
        docs,
        top_k=3,
        debug=1
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

    # ====================================
    # 1. 构造（查询，文档内容）对
    # ====================================

    pairs = [
        (query, doc.page_content)
        for doc in docs
    ]

    # ====================================
    # 2. 使用交叉编码器预测相关性得分（带超时保护）
    # ====================================

    scores = safe_call_with_timeout(
        reranker.predict,
        timeout=RERANK_TIMEOUT,
        default_value=None,
        error_message=f"重排序超时 ({RERANK_TIMEOUT}s)",
        sentences=pairs
    )
    
    if scores is None:
        logger.warning("⚠️ 重排序失败或超时，返回原始文档")
        # 降级：直接返回前top_k个文档，赋予默认分数
        return [(doc, 0.5) for doc in docs[:top_k]]

    # ====================================
    # 3. 将文档和得分打包并按得分降序排序
    # ====================================

    scored_docs = sorted(
        zip(docs, scores),
        key=lambda x: x[1],
        reverse=True
    )

    # ====================================
    # 4. 根据得分阈值筛选（仅保留得分 > 0.3 的文档）
    # ====================================

    scored_docs = [
        (doc, score)
        for doc, score in scored_docs
        if score > RERANK_SCORE_THRESHOLD
    ]

    # ====================================
    # 调试信息输出（如果开启 debug 模式）
    # ====================================

    if debug:

        print("\n================ 全局重排序结果 ================\n")

        for i, (doc, score) in enumerate(scored_docs[:10]):

            print(f"[{i+1}] 重排序得分 = {score:.4f}")

            print(
                f"来源文件: "
                f"{doc.metadata.get('source_file')}"
            )

            print(
                f"文档块ID: "
                f"{doc.metadata.get('chunk_id')}"
            )

            print(doc.page_content[:100])  # 打印文档内容前100个字符

            print("-" * 60)

    # ====================================
    # 5. 返回前 top_k 个文档（确保不超过实际数量）
    # ====================================

    logger.info(f"重排序完成: {len(docs)} -> {len(scored_docs[:top_k])} (threshold={RERANK_SCORE_THRESHOLD})")
    return scored_docs[:top_k]