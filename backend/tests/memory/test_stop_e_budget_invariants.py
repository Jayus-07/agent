"""STOP E：Context Budget 不变量验收（真实 PG + 真实装配链路）。

锁定任务书 E7 十条 invariant 中可在装配层验证的子集：
- E-I3：L3 memory 数据块参与 token budget（可被裁剪，无 System 豁免）；
- E-I4：L2 summary 数据块参与 token budget；
- E-I5：经 ContextBudget 预检后最终 payload <= configured input budget；
- E-I6：单条极长 memory 不得造成 provider context overflow；
- E-I9：memory 层预裁剪只执行 ContextBudget 派生的 history_budget
  （HISTORY_TOKEN_BUDGET 上限），不是第二 hard-budget authority。

外部依赖边界：真实 PostgreSQL；embedding 用 ScriptedEmbedding stub
（get_embedding 注入，沿用 STOP D 全链测试模式）；无 LLM 参与读路径。
"""

from __future__ import annotations

import uuid

import pytest

from backend.memory.database import AsyncSessionLocal
from backend.memory.long_term import LongTermMemory
from backend.memory.models.memory import MemoryRecord
from backend.memory.repository.memory_repo import MemoryRepository
from backend.memory.repository.session_repo import SessionRepository
from backend.tests.memory.conftest import (
    ScriptedEmbedding,
    cleanup_memory_prefix,
    require_memory_pg,
)

pytestmark = pytest.mark.asyncio

_PREFIX = "stope-bud-"
_USER = f"{_PREFIX}user"
_TENANT = "default"
_QUERY = "q"


@pytest.fixture(autouse=True)
async def _env():
    require_memory_pg()
    await cleanup_memory_prefix(_PREFIX)
    yield
    await cleanup_memory_prefix(_PREFIX)


def _record(content: str, embedding) -> MemoryRecord:
    return MemoryRecord(
        tenant_id=_TENANT, user_id=_USER, session_id=f"{_PREFIX}s",
        memory_type="preference", content=content,
        embedding=embedding, importance_score=0.7,
        confidence_score=0.8, origin="inferred",
        is_active=True,
    )


async def _insert_records(records: list[MemoryRecord]) -> None:
    async with AsyncSessionLocal() as db:
        repo = MemoryRepository(db)
        for r in records:
            await repo.insert(r)
        await db.commit()


async def _set_summary(session_id: str, text: str) -> None:
    async with AsyncSessionLocal() as db:
        repo = SessionRepository(db)
        await repo.get_or_create(session_id, _USER)
        await repo.update_summary(session_id, text)
        await db.commit()


def _is_system(msg) -> bool:
    return type(msg).__name__ == "SystemMessage"


def _history_budget_for(messages: list, query: str) -> int:
    """复算 memory 层预裁剪预算（与 service.start_session 同一派生式）。"""
    from backend.config import PREVIOUS_OUTPUTS_MAX_TOKENS
    from backend.context_budget import context_budget
    from backend.memory.token_budget import count_tokens
    system_tokens = sum(
        count_message_tokens_m(m) for m in messages if _is_system(m))
    return context_budget.history_budget(
        system_tokens=system_tokens,
        current_query_tokens=count_tokens(query) if query else 0,
        reserved_tokens=PREVIOUS_OUTPUTS_MAX_TOKENS,
    )


async def _start_session_with_stub(monkeypatch, emb: ScriptedEmbedding,
                                   query_vector, session_id: str, query: str):
    """service.start_session 全链（embedding 注入 stub，无外部调用）。"""

    class _StubEmb:
        def embed_query(self, text_value: str):
            return query_vector

    monkeypatch.setattr(
        "backend.memory.long_term.get_embedding", lambda: _StubEmb())
    from backend.memory.service import MemoryService
    svc = MemoryService()
    return await svc.start_session(session_id, user_id=_USER, query=query,
                                   tenant_id=_TENANT)


async def test_long_l2_l3_memory_participates_and_honors_budget(monkeypatch):
    """E-I3/I4/I9：长 L2 + 长 L3 注入后，memory 层预裁剪把非 System
    内容压回 history_budget 内；memory 数据块无 System 豁免可被裁剪。"""
    emb = ScriptedEmbedding()
    q_vec = emb.unit(70)
    long_content = "用户偏好相关记忆内容，用于验证预算。" * 90  # ≈1800 字/条
    records = [_record(long_content, emb.blend(q_vec, 80 + i, 0.9))
               for i in range(5)]
    await _insert_records(records)

    session_id = f"{_PREFIX}long-{uuid.uuid4().hex[:8]}"
    await _set_summary(session_id, "长会话摘要内容，用于验证摘要参与预算。" * 200)

    l1 = await _start_session_with_stub(monkeypatch, emb, q_vec,
                                        session_id, _QUERY)

    budget = _history_budget_for(l1.messages, _QUERY)
    assert budget > 0, "本用例构造的输入应产生正预算（否则预裁剪关闭，断言无意义）"
    non_system = [m for m in l1.messages if not _is_system(m)]
    used = sum(count_message_tokens_m(m) for m in non_system)
    assert used <= budget, (
        f"memory 预裁剪后非 System token({used}) 超过 history_budget({budget})")

    # I3：注入的 <memory_context> 数据块在预算压力下必须可被裁剪——
    # 5 条 ×1600 字的记忆块远超 history_budget，整块必须被裁掉
    memory_data = [m for m in l1.messages
                   if getattr(m, "content", "").startswith("<memory_context>")]
    assert memory_data == [], "超预算 memory 数据块不得存活（无 System 豁免）"


async def test_extreme_long_memory_causes_no_provider_overflow(monkeypatch):
    """E-I6/I5/I1：单条极长 memory（>3 万字）→ 装配预裁剪丢弃数据块，
    proxy 预检后 payload 仍在 input budget 内、当前 query 永不丢弃。"""
    from langchain_core.messages import HumanMessage, SystemMessage

    from backend.context_budget import context_budget

    emb = ScriptedEmbedding()
    q_vec = emb.unit(70)
    extreme = "极长的记忆内容。" * 6000  # ≈4.2 万字，远超任何预算
    await _insert_records([_record(extreme, emb.blend(q_vec, 90, 0.95))])

    session_id = f"{_PREFIX}extreme-{uuid.uuid4().hex[:8]}"
    l1 = await _start_session_with_stub(monkeypatch, emb, q_vec,
                                        session_id, _QUERY)

    # 装配层已不含超长 memory 数据（memory 预裁剪丢弃）
    for m in l1.messages:
        content = getattr(m, "content", "")
        assert not content.startswith("<memory_context>"), (
            "极长 memory 数据块必须在装配预裁剪中被裁剪（E-I6 第一道防线）")

    # proxy 预检（最终 hard budget authority）兜底验证
    query_text = "这是一个相当长的当前用户问题。" * 60
    payload = [
        SystemMessage("业务系统提示词。" * 100),
        *l1.messages,
        HumanMessage(content=query_text),
    ]
    prepared = context_budget.prepare_llm_context(messages=payload)
    assert prepared.usage.used_tokens <= prepared.usage.input_budget, (
        f"最终 payload({prepared.usage.used_tokens}) 超过 input_budget"
        f"({prepared.usage.input_budget})")
    assert prepared.overflow is False
    assert prepared.messages[-1].content == query_text, "当前 query 永不被裁（E-I1）"


async def test_final_payload_within_budget_with_l2_l3_long_query(monkeypatch):
    """E-I5：L2 摘要 + 5 条 L3 记忆 + 长 query 并存 → proxy 预检后
    payload 不超 input budget 且结构完整（policy System + query 保留）。"""
    from langchain_core.messages import HumanMessage, SystemMessage

    from backend.context_budget import context_budget
    from backend.context_budget.role_safety import (
        is_historical_data_message,
        is_policy_system_message,
    )

    emb = ScriptedEmbedding()
    q_vec = emb.unit(70)
    records = [_record(f"记忆条目{i}：" + "相关偏好细节。" * 80,
                       emb.blend(q_vec, 100 + i, 0.9)) for i in range(5)]
    await _insert_records(records)

    session_id = f"{_PREFIX}combo-{uuid.uuid4().hex[:8]}"
    await _set_summary(session_id, "摘要内容。" * 500)
    l1 = await _start_session_with_stub(monkeypatch, emb, q_vec,
                                        session_id, _QUERY)

    query_text = "结合以上背景回答一个中等长度的问题。" * 30
    payload = [SystemMessage("系统策略。" * 50), *l1.messages,
               HumanMessage(content=query_text)]
    prepared = context_budget.prepare_llm_context(messages=payload)
    assert prepared.usage.used_tokens <= prepared.usage.input_budget
    assert prepared.overflow is False
    assert prepared.messages[-1].content == query_text
    # E-I10：STOP D role-safety 不退化——policy SystemMessage 是本模块
    # 固定文本；动态数据只允许出现在数据块消息中
    assert any(is_policy_system_message(m) for m in prepared.messages), (
        "memory/historical policy SystemMessage 应保留")
    for m in prepared.messages:
        if is_historical_data_message(m) or getattr(
                m, "content", "").startswith("<memory_context>"):
            assert not _is_system(m)


def count_message_tokens_m(msg):
    from backend.memory.token_budget import count_message_tokens
    return count_message_tokens(msg)
