"""STOP C（Memory Production Closure）冲突裁决 + 事实版本管理验收。

覆盖硬条件：C8/C9/C10/C11/C12（裁决矩阵）、C13（top-1 blind spot 消失）、
C14（0.85~0.92 静默丢弃消失）、C15（unkeyed 高相似不 supersede）、
C20（PII 不经 key/value 泄漏）、C21（旧 API 兼容）、C22（legacy 不被改写）、
C6/C7（extraction 6 段协议 + normalize）、跨 key/user/tenant 隔离。

外部依赖边界：真实 PostgreSQL（权威库），LLM/embedding 一律 mock。
"""

from __future__ import annotations

import pytest
from sqlalchemy import text

from backend.config import MEMORY_EXPLICIT_DEFAULT_CONFIDENCE
from backend.memory.database import AsyncSessionLocal
from backend.memory.keying import (
    StoreOutcome,
    normalize_memory_key,
    normalize_memory_value,
    normalize_tenant_id,
    origin_priority,
)
from backend.memory.long_term import LongTermMemory, MemoryFact
from backend.memory.repository.memory_repo import MemoryRepository
from backend.tests.memory.conftest import (
    ScriptedEmbedding,
    cleanup_memory_prefix,
    require_memory_pg,
)

pytestmark = pytest.mark.asyncio

_PREFIX = "stopc-"
_USER = f"{_PREFIX}user"
_SESSION = f"{_PREFIX}session"


@pytest.fixture(autouse=True)
async def _env():
    require_memory_pg()
    await cleanup_memory_prefix(_PREFIX)
    yield
    await cleanup_memory_prefix(_PREFIX)


@pytest.fixture()
def emb():
    return ScriptedEmbedding()


def _fact(**kw) -> MemoryFact:
    kw.setdefault("fact_type", "preference")
    kw.setdefault("content", "事实内容")
    return MemoryFact(**kw)


async def _store(emb_obj, fact, expected=None) -> StoreResult:
    """每次写入绑定独立 session（与生产 store 链一致）。"""
    async with AsyncSessionLocal() as db:
        l3 = LongTermMemory(MemoryRepository(db))
        l3._embedding_model = emb_obj
        result = await l3.store_with_resolution(fact, _USER, _SESSION, "default")
        await db.commit()
    if expected is not None:
        assert result.outcome == expected, f"reason={result.reason}"
    return result


async def _rows(key: str | None = None, user: str = _USER,
                tenant: str = "default") -> list[tuple]:
    sql = ("SELECT memory_key, structured_value, origin, is_active, superseded_by, content "
           "FROM memory_records WHERE user_id = :u AND tenant_id = :t")
    params = {"u": user, "t": tenant}
    if key is not None:
        sql += " AND memory_key = :k"
        params["k"] = key
    async with AsyncSessionLocal() as db:
        return (await db.execute(text(sql), params)).all()


# ============================================================
# keying 单元（C7：normalize + validate）
# ============================================================

async def test_normalize_memory_key_contract():
    assert normalize_memory_key("  Project.Main_LLM ") == "project.main_llm"
    assert normalize_memory_key("project.main llm") == "project.main_llm"
    assert normalize_memory_key("") is None
    assert normalize_memory_key(None) is None
    assert normalize_memory_key("a" * 200) is None
    assert normalize_memory_key("user.email.a@b.com") is None   # @ 不在白名单
    assert normalize_memory_key("phone.138001380000") is None   # 纯数字段拒绝


async def test_normalize_memory_value_contract():
    assert normalize_memory_value("  DeepSeek ") == "deepseek"
    assert normalize_memory_value("Qwen3-8B") == "qwen3-8b"
    assert normalize_memory_value("Qwen3-14B") == "qwen3-14b"  # 型号不被误归一
    assert normalize_memory_value("") is None
    assert normalize_memory_value(None) is None
    assert normalize_memory_value("x" * 300) is None


async def test_normalize_tenant_id_fail_closed():
    assert normalize_tenant_id("") == "default"
    assert normalize_tenant_id(None) == "default"
    assert normalize_tenant_id("acme") == "acme"
    assert normalize_tenant_id("quarantine") == "default"  # 保留哨兵运行时永不产出


async def test_origin_priority_order():
    assert origin_priority("explicit") > origin_priority("inferred") > origin_priority("legacy")


# ============================================================
# keyed 裁决矩阵（C8-C12）
# ============================================================

async def test_same_key_same_value_no_second_active(emb):
    await _store(emb, _fact(memory_key="project.main_llm", structured_value="deepseek",
                            origin="inferred", confidence_score=0.8), StoreOutcome.INSERTED)
    await _store(emb, _fact(memory_key="project.main_llm", structured_value="deepseek",
                            origin="inferred", confidence_score=0.7), StoreOutcome.DUPLICATE)
    rows = [r for r in await _rows("project.main_llm") if r[3]]
    assert len(rows) == 1  # C8：不重复插入 active


async def test_same_key_same_value_reaffirm_upgrades_origin(emb):
    await _store(emb, _fact(memory_key="k.reaffirm", structured_value="v",
                            origin="inferred", confidence_score=0.6), StoreOutcome.INSERTED)
    result = await _store(emb, _fact(memory_key="k.reaffirm", structured_value="v",
                                     origin="explicit",
                                     confidence_score=MEMORY_EXPLICIT_DEFAULT_CONFIDENCE))
    assert result.outcome == StoreOutcome.REAFFIRMED
    rows = await _rows("k.reaffirm")
    assert len(rows) == 1 and rows[0][2] == "explicit"  # origin 只升不降


async def test_inferred_new_value_supersedes_inferred(emb):
    old = await _store(emb, _fact(memory_key="project.main_llm", structured_value="deepseek",
                                  origin="inferred", confidence_score=0.8,
                                  source_message_id=100), StoreOutcome.INSERTED)
    new = await _store(emb, _fact(memory_key="project.main_llm", structured_value="doubao",
                                  origin="inferred", confidence_score=0.85,
                                  source_message_id=101), StoreOutcome.SUPERSEDED)
    rows = await _rows("project.main_llm")
    active = [r for r in rows if r[3]]
    inactive = [r for r in rows if not r[3]]
    assert len(active) == 1 and active[0][1] == "doubao"   # C9
    assert len(inactive) == 1 and str(inactive[0][4]) == new.memory_id
    assert new.superseded_memory_id == old.memory_id


async def test_inferred_conflicting_with_explicit_is_blocked(emb):
    await _store(emb, _fact(memory_key="response.language", structured_value="zh",
                            origin="explicit"), StoreOutcome.INSERTED)
    result = await _store(emb, _fact(memory_key="response.language", structured_value="en",
                                     origin="inferred"))
    assert result.outcome == StoreOutcome.CONFLICT_BLOCKED_EXPLICIT  # C10
    rows = [r for r in await _rows("response.language") if r[3]]
    assert len(rows) == 1 and rows[0][1] == "zh"  # explicit 保持，无第二条 active


async def test_explicit_overrides_explicit_and_inferred(emb):
    # explicit → explicit（用户明确新决定优先，C12）
    await _store(emb, _fact(memory_key="response.language", structured_value="zh",
                            origin="explicit"), StoreOutcome.INSERTED)
    await _store(emb, _fact(memory_key="response.language", structured_value="en",
                            origin="explicit"), StoreOutcome.SUPERSEDED)
    rows = [r for r in await _rows("response.language") if r[3]]
    assert len(rows) == 1 and rows[0][1] == "en"

    # explicit → inferred（C11）
    await _store(emb, _fact(memory_key="response.detail", structured_value="brief",
                            origin="inferred"), StoreOutcome.INSERTED)
    await _store(emb, _fact(memory_key="response.detail", structured_value="detailed",
                            origin="explicit"), StoreOutcome.SUPERSEDED)
    rows = [r for r in await _rows("response.detail") if r[3]]
    assert len(rows) == 1 and rows[0][1] == "detailed"


# ============================================================
# 隔离与 blind spot（C13 + §62-64）
# ============================================================

async def test_different_keys_never_conflict_even_if_semantically_close(emb):
    # 两个 key 的 content 用同一条向量（语义完全相同），也不得互相 supersede（§62）
    emb.vectors["embedding 模型是 bge"] = emb.unit(0)
    emb.vectors["主模型是 doubao"] = emb.unit(0)
    await _store(emb, _fact(content="embedding 模型是 bge",
                            memory_key="project.embedding_model", structured_value="bge",
                            origin="inferred"), StoreOutcome.INSERTED)
    await _store(emb, _fact(content="主模型是 doubao",
                            memory_key="project.main_llm", structured_value="doubao",
                            origin="inferred"), StoreOutcome.INSERTED)
    assert len([r for r in await _rows() if r[3]]) == 2


async def test_different_user_and_tenant_isolated(emb):
    async def _store_as(user, tenant, value) -> StoreResult:
        async with AsyncSessionLocal() as db:
            l3 = LongTermMemory(MemoryRepository(db))
            l3._embedding_model = emb
            result = await l3.store_with_resolution(
                _fact(memory_key="project.main_llm", structured_value=value,
                      origin="inferred"), user, _SESSION, tenant)
            await db.commit()
            return result

    # 不同 user 同 key 各自 active（§63）
    assert (await _store_as(f"{_PREFIX}A", "default", "doubao")).outcome == StoreOutcome.INSERTED
    assert (await _store_as(f"{_PREFIX}B", "default", "deepseek")).outcome == StoreOutcome.INSERTED
    # 不同 tenant 同 user 同 key 同值也隔离（§64：不合并、不互相 supersede）
    assert (await _store_as(f"{_PREFIX}A", "tenant-x", "doubao")).outcome == StoreOutcome.INSERTED


async def test_keyed_supersede_ignores_embedding_order(emb):
    """C13：existing 与新事实 embedding 完全正交（top-1 盲区场景），
    keyed 路径仍按 memory_key 精确 supersede。"""
    await _store(emb, _fact(content="旧版本内容 A", memory_key="job.target_role",
                            structured_value="backend", origin="inferred",
                            source_message_id=200), StoreOutcome.INSERTED)
    result = await _store(emb, _fact(content="完全不同表述的新版本 B",
                                     memory_key="job.target_role",
                                     structured_value="sre", origin="inferred",
                                     source_message_id=201))
    assert result.outcome == StoreOutcome.SUPERSEDED  # embedding 无关（C13）


async def test_older_inferred_event_cannot_supersede_newer_value(emb):
    """乱序完成时，较旧的用户来源事件不能覆盖较新版本。"""
    await _store(emb, _fact(memory_key="travel.pace", structured_value="relaxed",
                            origin="inferred", source_message_id=302),
                 StoreOutcome.INSERTED)
    result = await _store(emb, _fact(memory_key="travel.pace",
                                     structured_value="packed", origin="inferred",
                                     source_message_id=301))

    assert result.outcome == StoreOutcome.BLOCKED_STALE_EVENT
    rows = [r for r in await _rows("travel.pace") if r[3]]
    assert len(rows) == 1 and rows[0][1] == "relaxed"


# ============================================================
# unkeyed 兼容路径（C14/C15）
# ============================================================

async def test_unkeyed_mid_similarity_inserts_not_dropped(emb):
    """C14：0.85 ≤ sim < 0.92 不再静默丢弃 → INSERT。"""
    emb.vectors["用户喜欢 python"] = emb.unit(3)
    emb.vectors["用户喜欢 go"] = emb.blend(emb.unit(3), 4, 0.88)
    await _store(emb, _fact(content="用户喜欢 python"), StoreOutcome.INSERTED)
    await _store(emb, _fact(content="用户喜欢 go"), StoreOutcome.INSERTED)  # 旧行为 return False
    assert len([r for r in await _rows(None) if r[3]]) == 2


async def test_unkeyed_high_similarity_duplicate_never_supersedes(emb):
    """C15：sim ≥ 0.92 → DUPLICATE（保留旧行），绝不自动 supersede。"""
    emb.vectors["用户喜欢 python"] = emb.unit(5)
    emb.vectors["用户喜欢 go"] = emb.blend(emb.unit(5), 6, 0.95)
    await _store(emb, _fact(content="用户喜欢 python"), StoreOutcome.INSERTED)
    result = await _store(emb, _fact(content="用户喜欢 go"), StoreOutcome.DUPLICATE)
    rows = [r for r in await _rows(None) if r[3]]
    assert len(rows) == 1 and rows[0][5] == "用户喜欢 python"


# ============================================================
# PII / 兼容（C20/C21/C22）
# ============================================================

async def test_structured_value_cannot_bypass_pii(emb):
    """C20：structured_value 与 content 同受 PII 防线。"""
    result = await _store(emb, _fact(content="联系方式见邮箱", memory_key="user.contact",
                                     structured_value="alice@example.com", origin="explicit"),
                          StoreOutcome.INSERTED)
    async with AsyncSessionLocal() as db:
        row = (await db.execute(text(
            "SELECT structured_value FROM memory_records WHERE id = :i"),
            {"i": result.memory_id})).first()
    assert row is not None
    assert "alice@example.com" not in (row[0] or "")  # 已脱敏
    assert "[邮箱]" in row[0]


async def test_legacy_store_single_api_compatible(emb):
    """C21：旧 store_single 无 key/tenant 调用 → legacy/unkeyed 正常。"""
    async with AsyncSessionLocal() as db:
        l3 = LongTermMemory(MemoryRepository(db))
        l3._embedding_model = emb
        fact = MemoryFact(fact_type="user_fact", content="旧调用兼容事实")
        ok = await l3.store_single(fact, _USER, _SESSION)  # 不传 tenant
        await db.commit()
    assert ok is True
    rows = await _rows(None)
    assert rows[0][0] is None and rows[0][2] == "legacy"  # key=NULL, origin=legacy


async def test_legacy_incoming_cannot_override_inferred(emb):
    await _store(emb, _fact(memory_key="k.legacy", structured_value="v1",
                            origin="inferred"), StoreOutcome.INSERTED)
    result = await _store(emb, _fact(memory_key="k.legacy", structured_value="v2",
                                     origin="legacy"))
    assert result.outcome == StoreOutcome.CONFLICT_BLOCKED_EXPLICIT  # legacy 防御分支


# ============================================================
# extraction 6 段协议（C6/C7：不新增 LLM 调用）
# ============================================================

async def test_parser_extracts_key_value_pair():
    facts, rejections = LongTermMemory._parse_facts(
        "user_fact|主模型是DeepSeek|0.92|主模型用 DeepSeek|project.main_llm|deepseek",
        user_evidence="主模型用 DeepSeek",
    )
    assert not rejections and len(facts) == 1
    assert facts[0].memory_key == "project.main_llm"
    assert facts[0].structured_value == "deepseek"


async def test_parser_invalid_key_pairs_null():
    facts, _ = LongTermMemory._parse_facts(
        "user_fact|内容|0.9|内容原话|BAD KEY!|v",
        user_evidence="内容原话",
    )
    assert facts[0].memory_key is None and facts[0].structured_value is None
    # 有 key 无值 → 双双 NULL（成对约束）
    facts2, _ = LongTermMemory._parse_facts(
        "user_fact|内容2|0.9|内容2原话|project.main_llm",
        user_evidence="内容2原话",
    )
    assert facts2[0].memory_key is None
