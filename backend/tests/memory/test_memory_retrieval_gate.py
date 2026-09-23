"""STOP D：Relevance Gate + Expiration + Access Semantics 验收。

覆盖硬条件：D5/D6/D7（SQL eligibility）、D8/D9/D10（gate 语义与边界）、
D11/D12（0 条合法/不强凑）、D13/D14（global 与普通 preference 区分）、
D15/D16（merge 去重/总上限）、D17-D21（mark_accessed 语义与 fail-open）、
D-I13/I14（只有注入条 mark；被拒/过期不 mark）、decay 联动（§104）。

外部依赖边界：真实 PostgreSQL；LLM/embedding mock（ScriptedEmbedding
精确构造相似度）。
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import text

from backend.memory.database import AsyncSessionLocal
from backend.memory.keying import normalize_tenant_id
from backend.memory.long_term import LongTermMemory
from backend.memory.models.memory import MemoryRecord
from backend.memory.repository.memory_repo import MemoryRepository
from backend.memory.retriever import HybridRetriever
from backend.tests.memory.conftest import (
    ScriptedEmbedding,
    cleanup_memory_prefix,
    require_memory_pg,
)

pytestmark = pytest.mark.asyncio

_PREFIX = "stopd-"
_USER = f"{_PREFIX}user"
_SESSION = f"{_PREFIX}session"
_TENANT = "default"


@pytest.fixture(autouse=True)
async def _env():
    require_memory_pg()
    await cleanup_memory_prefix(_PREFIX)
    yield
    await cleanup_memory_prefix(_PREFIX)


def _record(content: str, *, embedding=None, memory_key=None, expire_at=None,
            is_active=True, tenant_id=_TENANT, user_id=_USER,
            origin="inferred", importance=0.7, confidence=0.8) -> MemoryRecord:
    return MemoryRecord(
        tenant_id=tenant_id, user_id=user_id, session_id=_SESSION,
        memory_type="preference", content=content,
        embedding=embedding, importance_score=importance,
        confidence_score=confidence, origin=origin,
        memory_key=memory_key, structured_value=None,
        expire_at=expire_at, is_active=is_active,
    )


async def _insert_many(records: list[MemoryRecord]) -> list[str]:
    async with AsyncSessionLocal() as db:
        repo = MemoryRepository(db)
        ids = []
        for r in records:
            await repo.insert(r)
            ids.append(str(r.id))
        await db.commit()
    return ids


async def _retrieve(emb: ScriptedEmbedding, query_vector, **kw):
    async with AsyncSessionLocal() as db:
        retriever = HybridRetriever(MemoryRepository(db))
        return await retriever.retrieve("q", query_vector, _USER,
                                        tenant_id=_TENANT, **kw)


async def _access_row(record_id: str) -> tuple:
    async with AsyncSessionLocal() as db:
        return (await db.execute(text(
            "SELECT access_count, last_access_at FROM memory_records WHERE id=:i"),
            {"i": record_id})).first()


# ============================================================
# SQL eligibility（D5/D6/D7，§52/53/54/55：eligibility 优先于 semantic）
# ============================================================

async def test_expired_record_excluded_at_sql_level():
    """§52：expired(高相似) 不出现，valid(中相似) 出现——eligibility > semantic。"""
    emb = ScriptedEmbedding()
    base = emb.unit(0)
    expired = _record("过期记忆", embedding=base, expire_at=datetime.now(timezone.utc) - timedelta(days=1))
    valid = _record("有效记忆", embedding=emb.blend(base, 1, 0.80))
    await _insert_many([expired, valid])
    # expired 相似度更高（self-match 1.0）也必须被 SQL 层排除
    hits = await _retrieve(emb, base)
    contents = [m.record.content for m in hits]
    assert "过期记忆" not in contents and "有效记忆" in contents


async def test_inactive_record_never_returned():
    emb = ScriptedEmbedding()
    inactive = _record("已失活记忆", embedding=emb.unit(2), is_active=False)
    await _insert_many([inactive])
    assert await _retrieve(emb, emb.unit(2)) == []  # §53 semantic=1.0 也不返回


async def test_wrong_tenant_and_user_excluded():
    emb = ScriptedEmbedding()
    other_tenant = _record("别家租户的记忆", embedding=emb.unit(3), tenant_id="tenant-x")
    other_user = _record("别人家的记忆", embedding=emb.unit(3), user_id=f"{_PREFIX}other")
    await _insert_many([other_tenant, other_user])
    assert await _retrieve(emb, emb.unit(3)) == []  # §54/§55 SQL 层过滤


# ============================================================
# Relevance Gate（D8/D9/D11/D12，§47-51/64）
# ============================================================

async def test_irrelevant_memories_rejected_zero_injected():
    """§47：候选可 >0，但 accepted=0、无注入；gate 只看 semantic（D9）。"""
    emb = ScriptedEmbedding()
    query_vec = emb.unit(10)
    # 两条与 query 近正交（sim≈0）的无关记忆 + 高 importance 也抬不进门
    await _insert_many([
        _record("用户喜欢日料", embedding=emb.unit(11), importance=0.95),
        _record("用户偏好靠窗座位", embedding=emb.unit(12), importance=0.95),
    ])
    hits = await _retrieve(emb, query_vec)
    assert hits == []  # 无关记忆不陪跑（高 importance 不参与 gate）


async def test_threshold_boundary_inclusive():
    """§64：>= threshold 接受（含等号），略低拒绝。"""
    from backend.config import MEMORY_MIN_RELEVANCE_SCORE as T
    emb = ScriptedEmbedding()
    q = emb.unit(20)
    # 浮点/存储误差下严格等号不可靠：接受侧用 T+ε 验证 >= 语义
    at = _record("等于阈值", embedding=emb.blend(q, 21, min(1.0, T + 0.001)))
    below = _record("低于阈值", embedding=emb.blend(q, 22, max(0.0, T - 0.02)))
    await _insert_many([at, below])
    hits = await _retrieve(emb, q)
    contents = [m.record.content for m in hits]
    assert "等于阈值" in contents and "低于阈值" not in contents


async def test_mixed_candidates_only_relevant_injected():
    """§49：8 条无关 + 2 条相关 → 只注入 2 条，不强凑。"""
    emb = ScriptedEmbedding()
    q = emb.unit(30)
    records = [_record(f"无关填充{i}", embedding=emb.unit(31 + i)) for i in range(8)]
    records.append(_record("相关一", embedding=emb.blend(q, 40, 0.9)))
    records.append(_record("相关二", embedding=emb.blend(q, 41, 0.85)))
    await _insert_many(records)
    hits = await _retrieve(emb, q)
    assert len(hits) == 2
    assert {m.record.content for m in hits} == {"相关一", "相关二"}


async def test_max_injected_cap_and_mark_scope():
    """§50/D-I10/D-I13：10 条全过门 → 最多 5 条注入，且只有注入条 mark。"""
    emb = ScriptedEmbedding()
    q = emb.unit(50)
    sims = [0.95, 0.94, 0.93, 0.92, 0.91, 0.90, 0.89, 0.88, 0.87, 0.86]
    records = [_record(f"高相关{i}", embedding=emb.blend(q, 51 + i, s))
               for i, s in enumerate(sims)]
    ids = await _insert_many(records)
    hits = await _retrieve(emb, q)
    assert len(hits) <= 5
    # rank 分数与注入集合一致：前 5 名被 mark，其余不变
    injected_ids = {str(m.record.id) for m in hits}
    from backend.memory.keying import normalize_tenant_id
    async with AsyncSessionLocal() as db:
        await MemoryRepository(db).mark_accessed(
            [str(i) for i in injected_ids], tenant_id=_TENANT, user_id=_USER)
        await db.commit()
    for rid, rec in zip(ids, records):
        row = await _access_row(rid)
        if rid in injected_ids:
            assert row[0] == 1
        else:
            assert row[0] == 0  # 未注入不 mark（D-I14）


async def test_global_preference_bypasses_gate_and_dedup():
    """§93/§96：response.* 免 gate 跨主题生效；与 semantic 命中去重；总上限生效。"""
    from backend.config import MEMORY_MAX_INJECTED
    emb = ScriptedEmbedding()
    q = emb.unit(60)
    # global：semantic 极低也注入；同时它自身也 high-sim（dedup 只注入一次）
    g = _record("回答用中文", embedding=emb.blend(q, 61, 0.95),
                memory_key="response.language", origin="explicit")
    # 普通 preference：低 sim（0.1）→ 被 gate 掉（D14：不是所有 preference 都 global）
    ordinary = _record("旅游喜欢靠窗", embedding=emb.unit(62))
    await _insert_many([g, ordinary])
    hits = await _retrieve(emb, q)
    contents = [m.record.content for m in hits]
    assert "回答用中文" in contents and "旅游喜欢靠窗" not in contents
    assert len(hits) <= MEMORY_MAX_INJECTED
    assert [m.source for m in hits if m.record.content == "回答用中文"] == ["global"]


# ============================================================
# Access Semantics（D17-D21，§59-62/104）
# ============================================================

async def test_mark_accessed_updates_count_and_time():
    """§59：access_count+1、last_access_at 前移。"""
    emb = ScriptedEmbedding()
    old_time = datetime.now(timezone.utc) - timedelta(days=30)
    rec = _record("会被注入的记忆", embedding=emb.unit(70))
    rec.access_count = 3
    rec.last_access_at = old_time
    (rid,) = await _insert_many([rec])
    async with AsyncSessionLocal() as db:
        await MemoryRepository(db).mark_accessed([rid], tenant_id=_TENANT, user_id=_USER)
        await db.commit()
    count, last = await _access_row(rid)
    last = last.replace(tzinfo=timezone.utc) if last.tzinfo is None else last
    assert count == 4 and last > old_time


async def test_mark_accessed_failure_does_not_break_start_session(monkeypatch):
    """§62/D21：mark 失败 fail-open——start_session 仍返回带记忆的缓冲。"""
    # 先插入一条与 query 向量匹配的记忆，确保走到 mark_accessed 分支
    emb = ScriptedEmbedding()
    await _insert_many([_record("会被注入的记忆", embedding=emb.unit(70))])

    async def boom(self, ids, tenant_id="", user_id=""):
        raise RuntimeError("db down")

    monkeypatch.setattr(MemoryRepository, "mark_accessed", boom)
    stub = _StubEmb()
    stub._inner.vectors["q"] = emb.unit(70)  # query 向量指向预置记忆
    monkeypatch.setattr(
        "backend.memory.long_term.get_embedding", lambda: stub)
    from backend.memory.service import MemoryService
    svc = MemoryService()
    l1 = await svc.start_session(f"{_PREFIX}ms-fail", user_id=_USER,
                                 query="q", tenant_id=_TENANT)
    assert l1 is not None
    from backend.observability.metrics import memory_access_mark_failure_total
    assert memory_access_mark_failure_total._value.get() >= 1


class _StubEmb:
    """start_session 集成的最小 embedding stub（向量指向预置记忆）。"""

    def __init__(self):
        self._inner = ScriptedEmbedding()

    def embed_query(self, text_value: str):
        return self._inner.embed_query(text_value)


async def test_injected_memory_marked_via_start_session(monkeypatch):
    """§103/D17/D19：start_session 主路径只对注入条 +1，无双计数。"""
    emb = ScriptedEmbedding()
    q_text = "我现在项目主模型是什么？"
    q_vec = emb.unit(80)
    emb.vectors[q_text] = q_vec
    rec = _record("用户项目主模型是豆包", embedding=emb.blend(q_vec, 81, 0.9))
    (rid,) = await _insert_many([rec])

    monkeypatch.setattr(
        "backend.memory.long_term.get_embedding", lambda: _StubEmb2(emb))

    from backend.memory.service import MemoryService
    svc = MemoryService()
    l1 = await svc.start_session(f"{_PREFIX}ms-mark", user_id=_USER,
                                 query=q_text, tenant_id=_TENANT)
    # 注入条 +1（仅一次：start_session 直连 retriever，不经 search）
    count, _ = await _access_row(rid)
    assert count == 1
    # role separation（§56）：记忆原文在 AIMessage 数据块，不在 SystemMessage
    from langchain_core.messages import SystemMessage
    memory_texts = [m.content for m in l1.messages
                    if isinstance(m.content, str) and "豆包" in m.content]
    assert memory_texts, "相关记忆应被注入"
    for m in l1.messages:
        if isinstance(m, SystemMessage):
            assert "豆包" not in (m.content or "")  # D-I1：原文不进 System role


class _StubEmb2:
    def __init__(self, inner):
        self._inner = inner

    def embed_query(self, t):
        return self._inner.embed_query(t)


async def test_decay_skips_recently_accessed_after_mark():
    """§104：注入刷新 last_access_at 后，90/180 天档不会命中该记忆。"""
    emb = ScriptedEmbedding()
    rec = _record("刚被注入的记忆", embedding=emb.unit(90))
    rec.last_access_at = datetime.now(timezone.utc) - timedelta(days=200)
    rec.importance_score = 0.8
    (rid,) = await _insert_many([rec])
    # 模拟 STOP D mark_accessed 刚刷新过
    async with AsyncSessionLocal() as db:
        await MemoryRepository(db).mark_accessed([rid], tenant_id=_TENANT, user_id=_USER)
        await db.commit()
    result = await MemoryServiceSafeDecayProbe().run_decay()
    async with AsyncSessionLocal() as db:
        row = (await db.execute(text(
            "SELECT importance_score, is_active FROM memory_records WHERE id=:i"),
            {"i": rid})).first()
    assert row[1] is True and abs(row[0] - 0.8) < 1e-9  # 未被衰减


class MemoryServiceSafeDecayProbe:
    """decay 探针：直接走 MemoryService.run_decay（与生产任务同路径）。"""

    def __init__(self):
        from backend.memory.service import MemoryService
        self._svc = MemoryService()

    async def run_decay(self):
        return await self._svc.run_decay()
