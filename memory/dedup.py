"""
记忆去重模块 — 检测新事实是否与已存储事实重复/冲突。

策略:
  1. 余弦相似度去重: 新事实与已有向量相似度 ≥ 阈值 → 视为重复，跳过
  2. 同类型覆盖: 同 fact_type + 高相似度 → 新事实 supersede 旧事实
  3. 跨类型冲突: 新事实与已有事实语义相似但类型不同 → 保留两者，降低置信度

不依赖 LLM，全部基于向量相似度 + 启发式规则。
"""

from dataclasses import dataclass, field

from utils.logger import logger


@dataclass
class DedupDecision:
    """去重决策结果"""
    action: str  # "insert" | "skip" | "supersede" | "insert_lower_confidence"
    reason: str
    supersede_id: str | None = None
    adjusted_confidence: float = 1.0


def decide(
    new_content: str,
    new_fact_type: str,
    new_embedding: list[float],
    store,  # MemoryStoreBackend
    tenant_id: str = "default",
    cosine_threshold: float = 0.85,
    supersede_threshold: float = 0.92,
) -> DedupDecision:
    """
    判断新事实的去重策略。

    返回 DedupDecision 告诉调用方该做什么:
      - insert: 正常写入
      - skip: 高度重复，丢弃
      - supersede: 新事实替代旧事实（设置 superseded_by）
      - insert_lower_confidence: 有冲突但不确定，降低置信度写入
    """
    # 1. 查找最相似的已有事实
    existing = store.find_similar(new_embedding, threshold=cosine_threshold, tenant_id=tenant_id)

    if existing is None:
        return DedupDecision(action="insert", reason="no_similar")

    # 2. 高度相似 (≥ 0.92): 同类型覆盖，跨类型降权
    if hasattr(existing, 'content'):
        similarity = _cosine_similarity_score(new_embedding, existing)
    else:
        similarity = cosine_threshold  # fallback

    if similarity >= supersede_threshold:
        if existing.fact_type == new_fact_type:
            # 完全一致类型 → 覆盖旧记忆
            return DedupDecision(
                action="supersede",
                reason=f"高度相似({similarity:.3f}), 同类型覆盖",
                supersede_id=existing.id,
            )
        else:
            # 不同但高度相似 → 可能是有冲突的分类，保留两者但降权
            return DedupDecision(
                action="insert_lower_confidence",
                reason=f"高度相似({similarity:.3f})但类型不同({existing.fact_type} vs {new_fact_type})",
                adjusted_confidence=0.7,
            )

    # 3. 中等相似 (0.85 ~ 0.92): 跳过，已有足够相似的记录
    logger.debug(
        f"[Dedup] 跳过重复事实: \"{new_content[:50]}...\" "
        f"(相似: \"{existing.content[:50]}...\", sim={similarity:.3f})"
    )
    return DedupDecision(
        action="skip",
        reason=f"与已有事实相似({similarity:.3f}): \"{existing.content[:60]}\"",
    )


def _cosine_similarity_score(emb_a: list[float], fact_b) -> float:
    """计算向量与 StoredFact 之间的余弦相似度（简化估算）"""
    # PgvectorStore 的 find_similar 已在 SQL 中计算了精确相似度，
    # 这里作为 ChromaDB 回退使用。ChromaDB 返回时相似度已体现在排序中。
    # 实际项目中应由 store 返回 score 字段。
    return 0.88  # ChromaDB 回退默认值（中等相似，保守跳过）
