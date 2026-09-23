"""MemoryConflictResolver — keyed/unkeyed 写入裁决的唯一集中实现（STOP C）。

裁决顺序（§23）：tenant → user → active → memory_key → normalized
structured_value → origin priority → confidence。embedding 相似度只在
unkeyed 路径用于 duplicate 判定，**永不决定事实版本关系**。

keyed 矩阵（§92 / §27-30）：
  same value            → REAFFIRMED（incoming 通道优先级更高时升格
                          origin/confidence，只升不降）/ DUPLICATE
  different value
    incoming explicit   → SUPERSEDED（可覆盖任何旧通道，含旧 explicit）
    incoming inferred   → existing explicit → CONFLICT_BLOCKED_EXPLICIT
                          其他 → SUPERSEDED
    incoming legacy     → 仅 existing legacy 可被覆盖（防御分支）

unkeyed（§43-46）：sim ≥ SUPERSEDE 阈值 → DUPLICATE；否则一律 INSERT
（0.85~0.92 不再静默丢弃，也不再有 supersede 资格）。

原子性（§34-35）：keyed supersede 的 deactivate → insert → 回填
superseded_by 在调用方传入的同一 AsyncSession 事务内完成，任何一步抛出
即整体 rollback（旧行恢复 active，无中间态泄漏）。行锁由
find_active_by_key(for_update=True) 保证；首建竞态由 partial unique
index uq_memory_active_key 兜底，调用方捕获 IntegrityError 后重试一次。
"""
from sqlalchemy.exc import IntegrityError

from backend.config import (
    L3_DEDUP_COSINE_THRESHOLD,
    L3_SUPERSEDE_THRESHOLD,
    MEMORY_ORIGIN_EXPLICIT,
    MEMORY_ORIGIN_INFERRED,
)
from backend.memory.keying import StoreOutcome, StoreResult, origin_priority


def _build_record(fact, user_id: str, session_id: str, tenant_id: str, embedding):
    from backend.memory.models.memory import MemoryRecord

    return MemoryRecord(
        tenant_id=tenant_id,
        user_id=user_id,
        session_id=session_id,
        memory_type=fact.fact_type,
        content=fact.content,
        embedding=embedding,
        importance_score=fact.importance_score,
        confidence_score=fact.confidence_score,
        origin=fact.origin,
        source_message_id=fact.source_message_id,
        memory_key=fact.memory_key,
        structured_value=fact.structured_value,
    )


async def resolve_keyed(repo, fact, user_id: str, session_id: str,
                        tenant_id: str, embedding) -> StoreResult:
    """keyed 事实写入：定位 active 版本 → 按 origin 矩阵裁决 → 原子落库。

    在 repo 所属的当前事务内执行（不自行 commit）——supersede 三步与
    调用方同事务，rollback 即整体回滚（§34）。
    """
    existing = await repo.find_active_by_key(
        tenant_id, user_id, fact.memory_key, for_update=True)

    if existing is None:
        record = _build_record(fact, user_id, session_id, tenant_id, embedding)
        # 首建竞态由 partial unique index 兜底：并发双插时 loser 撞
        # IntegrityError，事务 abort，调用方 rollback 后有界重试一次（§40）
        await repo.insert(record)
        return StoreResult(StoreOutcome.INSERTED, memory_id=str(record.id))

    same_value = (existing.structured_value or "") == (fact.structured_value or "")
    if same_value:
        return await _resolve_reaffirm(repo, existing, fact)
    return await _resolve_conflict(repo, existing, fact, user_id, session_id, tenant_id, embedding)


async def _resolve_reaffirm(repo, existing, fact) -> StoreResult:
    """同 key 同值：不新增 active 行；origin/confidence 只升不降（§25）。"""
    upgraded = origin_priority(fact.origin) > origin_priority(existing.origin)
    confidence_up = fact.confidence_score > existing.confidence_score
    if upgraded or confidence_up:
        fields = {}
        if upgraded:
            fields["origin"] = fact.origin
        if confidence_up:
            fields["confidence_score"] = fact.confidence_score
        await repo.update_fields(str(existing.id), **fields)
        fact.origin = fields.get("origin", existing.origin)
        fact.confidence_score = max(existing.confidence_score, fact.confidence_score)
        return StoreResult(
            StoreOutcome.REAFFIRMED,
            memory_id=str(existing.id),
            reason=",".join(fields) or "noop",
        )
    fact.origin = existing.origin
    fact.confidence_score = existing.confidence_score
    return StoreResult(StoreOutcome.DUPLICATE, memory_id=str(existing.id))


async def _resolve_conflict(repo, existing, fact, user_id: str, session_id: str,
                            tenant_id: str, embedding) -> StoreResult:
    """同 key 不同值：按 origin 矩阵裁决 supersede 或阻断。"""
    incoming = fact.origin
    if incoming == MEMORY_ORIGIN_EXPLICIT:
        pass  # explicit 新值可覆盖任何旧通道（§27/§28：用户明确新决定优先）
    elif incoming == MEMORY_ORIGIN_INFERRED:
        if existing.origin == MEMORY_ORIGIN_EXPLICIT:
            # I4：inferred 不覆盖 conflicting explicit，也不插入第二条 active
            return StoreResult(
                StoreOutcome.CONFLICT_BLOCKED_EXPLICIT,
                memory_id=str(existing.id),
                reason="inferred vs active explicit",
            )
    else:  # legacy incoming（旧调用兼容路径）仅可覆盖 legacy
        if existing.origin != "legacy":
            return StoreResult(
                StoreOutcome.CONFLICT_BLOCKED_EXPLICIT,
                memory_id=str(existing.id),
                reason="legacy vs active non-legacy",
            )

    # 原子 supersede（同事务）：deactivate → insert → 回填 superseded_by
    old_id = existing.id
    await repo.deactivate(str(old_id))
    record = _build_record(fact, user_id, session_id, tenant_id, embedding)
    await repo.insert(record)
    await repo.supersede(str(old_id), str(record.id))
    return StoreResult(
        StoreOutcome.SUPERSEDED,
        memory_id=str(record.id),
        superseded_memory_id=str(old_id),
    )


async def resolve_unkeyed(repo, fact, user_id: str, session_id: str,
                          tenant_id: str, embedding) -> StoreResult:
    """unkeyed 兼容路径：语义相似只判 duplicate，永不 supersede（§43-45）。

    sim ≥ L3_SUPERSEDE_THRESHOLD → DUPLICATE（高相似同义表达）；
    L3_DEDUP_COSINE_THRESHOLD ≤ sim < SUPERSEDE → INSERT（旧版静默丢弃带，
    现在「中等相似 = 独立事实」）；< DEDUP → INSERT。
    """
    candidates = await repo.find_similar_candidates(
        embedding, user_id, tenant_id,
        threshold=L3_DEDUP_COSINE_THRESHOLD, top_n=5)
    if candidates:
        best_record, best_sim = candidates[0]
        if best_sim >= L3_SUPERSEDE_THRESHOLD:
            return StoreResult(
                StoreOutcome.DUPLICATE,
                memory_id=str(best_record.id),
                reason=f"semantic sim={best_sim:.3f}",
            )
    record = _build_record(fact, user_id, session_id, tenant_id, embedding)
    await repo.insert(record)
    return StoreResult(StoreOutcome.INSERTED, memory_id=str(record.id))


__all__ = [
    "resolve_keyed",
    "resolve_unkeyed",
    "IntegrityError",
]
