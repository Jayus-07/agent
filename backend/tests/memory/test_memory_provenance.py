"""STOP B（Memory Production Closure）provenance 验收测试。

覆盖任务书 Case B1-B11：
  - 事实来源硬防线：assistant-only 事实不可落库（evidence 必须是用户原话子串）
  - origin 通道区分：自动提取=inferred、memory_store_tool=explicit、旧调用=legacy
  - confidence_score 激活：LLM 返回值 clamp[0,1]、非法回退、hedged 措辞封顶
  - source_message_id：save_turn 确定后透传，并发 turn 不串轮
  - migration 047：origin/source_message_id 列存在、存量 legacy
  - PII 防线不回退、store 异常不阻断

外部依赖边界：PostgreSQL 为真实库（skip 机制同 test_pgvector_l3）；
LLM 与 embedding 是外部服务，一律 mock（铁律：只 mock 外部边界）。
"""

from __future__ import annotations

import asyncio
import hashlib
import sys
import types
import uuid

import numpy as np
import psycopg2
import pytest
from sqlalchemy import text

from backend.config import (
    MEMORY_EXPLICIT_DEFAULT_CONFIDENCE,
    MEMORY_HEDGED_CONFIDENCE_CAP,
    MEMORY_INFERRED_DEFAULT_CONFIDENCE,
)
from backend.config.database import MEMORY_DB_CONFIG
from backend.memory.database import AsyncSessionLocal
from backend.memory.long_term import (
    MemoryFact,
    LongTermMemory,
    clamp_confidence,
)

_parse_facts = LongTermMemory._parse_facts
from backend.memory.models.memory import EMBEDDING_DIM
from backend.memory.service import MemoryService
from backend.memory.repository.memory_repo import MemoryRepository

pytestmark = pytest.mark.asyncio

_PREFIX = "prov-b-"
_USER = f"{_PREFIX}user"
_SESSION = f"{_PREFIX}session-{uuid.uuid4().hex[:8]}"


def _require_pg() -> None:
    try:
        with psycopg2.connect(**MEMORY_DB_CONFIG, connect_timeout=2) as conn:
            with conn.cursor() as cursor:
                cursor.execute(
                    "SELECT 1 FROM information_schema.columns "
                    "WHERE table_name='memory_records' AND column_name='origin'"
                )
                row = cursor.fetchone()
    except Exception as exc:
        pytest.skip(f"agent_memory PostgreSQL 不可达，跳过 STOP B 真实验收: {exc}")
    if row is None:
        pytest.skip("memory_records.origin 不存在（047 迁移未应用），跳过真实验收")


@pytest.fixture(autouse=True)
def _real_pg():
    _require_pg()


async def _cleanup() -> None:
    async with AsyncSessionLocal() as db:
        await db.execute(
            text("DELETE FROM memory_records WHERE user_id LIKE :p"), {"p": _PREFIX + "%"})
        await db.execute(
            text("DELETE FROM chat_sessions WHERE session_id LIKE :p"), {"p": _PREFIX + "%"})
        await db.commit()


class _FakeEmbedding:
    """确定性 embedding：同文本同向量、异文本近正交（md5 种子）。"""

    def embed_query(self, text_value: str) -> list[float]:
        seed = int(hashlib.md5(text_value.encode("utf-8")).hexdigest(), 16)
        rng = np.random.default_rng(seed % (2**32))
        v = rng.standard_normal(EMBEDDING_DIM).astype(np.float32)
        return (v / np.linalg.norm(v)).tolist()


def _install_fake_llm(monkeypatch, output_for_prompt) -> list[str]:
    """在 import 链上替换 LLM 与 prompt 渲染；返回调用过的 prompt 列表供断言。

    必须用 sys.modules stub 而不是 patch llm 实例：测试进程缺 provider
    凭据，真实 ``backend.infra.llm`` 模块在 import 期构建单例即失败。
    output_for_prompt(prompt) -> LLM 假输出文本。
    """
    calls: list[str] = []

    class _FakeLLM:
        def invoke(self, prompt, **kwargs):
            calls.append(prompt)
            return types.SimpleNamespace(content=output_for_prompt(prompt))

    fake_llm_module = types.ModuleType("backend.infra.llm")
    fake_llm_module.llm = _FakeLLM()
    monkeypatch.setitem(sys.modules, "backend.infra.llm", fake_llm_module)

    fake_prompts_module = types.ModuleType("backend.prompts.service")
    # text 直接回传 conversation 原文：FakeLLM 靠 <user_evidence> 标签回读证据
    fake_prompts_module.prompt_service = types.SimpleNamespace(
        render_sync=lambda key, **vars: types.SimpleNamespace(
            text=str(vars.get("conversation", ""))))
    monkeypatch.setitem(sys.modules, "backend.prompts.service", fake_prompts_module)

    monkeypatch.setattr(
        "backend.memory.long_term.get_embedding", lambda: _FakeEmbedding())
    return calls


def _user_evidence_from_prompt(prompt: str) -> str:
    """FakeLLM 按用户原话生成合规输出：从渲染 prompt 中回读 user_evidence。"""
    if "<user_evidence>" not in prompt:
        return "NONE"
    seg = prompt.split("<user_evidence>", 1)[1]
    return seg.split("</user_evidence>", 1)[0].strip()


# ============================================================
# Case B9：confidence clamp（纯单元）
# ============================================================

async def test_b9_confidence_clamp_all_shapes():
    default = MEMORY_INFERRED_DEFAULT_CONFIDENCE
    assert clamp_confidence(0.87) == 0.87
    assert clamp_confidence("0.87") == 0.87
    assert clamp_confidence(1.5) == 1.0          # 超上界 → 1.0
    assert clamp_confidence(-0.3) == 0.0         # 超下界 → 0.0
    assert clamp_confidence(None) == default     # 缺失 → 默认
    assert clamp_confidence("abc") == default    # 非数字 → 默认
    assert clamp_confidence(float("nan")) == default
    assert 0.0 <= clamp_confidence(10) <= 1.0


# ============================================================
# 解析协议：4 段 + evidence 硬防线（B1/B4 的 parser 层）
# ============================================================

async def test_parser_accepts_valid_candidate_with_user_evidence():
    facts, rejections = _parse_facts(
        "user_fact|主模型是DeepSeek|0.9|我现在主模型用的是 DeepSeek",
        user_evidence="我现在主模型用的是 DeepSeek。",
    )
    assert not rejections
    assert len(facts) == 1
    assert facts[0].fact_type == "user_fact"
    assert facts[0].confidence_score == 0.9
    assert facts[0].origin == "inferred"


async def test_parser_rejects_assistant_only_evidence():
    # evidence 是 assistant 的话（不在用户消息中）→ 拒绝
    facts, rejections = _parse_facts(
        "user_fact|你的项目使用appendfsync everysec|0.9|你的项目当前使用appendfsync everysec",
        user_evidence="Redis AOF 怎么配？",
    )
    assert not facts
    assert rejections == ["assistant_only"]


async def test_parser_fail_closed_on_legacy_two_segment_format():
    # 旧两段格式无证据片段 → 一律拒绝（宁缺毋滥）
    facts, rejections = _parse_facts(
        "user_fact|来源不明的事实\npreference|另一条无证据",
        user_evidence="用户说过的任意话",
    )
    assert not facts
    assert len(rejections) == 2
    assert all(r == "assistant_only" for r in rejections)


async def test_parser_hedged_evidence_caps_confidence():
    # 用户原话含不确定措辞 + LLM 声称 0.95 → 代码强制封顶（Case B4）
    facts, rejections = _parse_facts(
        f"decision|用户决定以后转Go|0.95|我可能之后看看Go",
        user_evidence="我可能之后看看Go",
    )
    assert not rejections
    assert len(facts) == 1
    assert facts[0].confidence_score <= MEMORY_HEDGED_CONFIDENCE_CAP


async def test_parser_invalid_confidence_falls_back():
    facts, _ = _parse_facts(
        f"preference|偏好中文|abc|以后回答都用中文",
        user_evidence="以后回答都用中文",
    )
    assert facts[0].confidence_score == MEMORY_INFERRED_DEFAULT_CONFIDENCE


# ============================================================
# Case B1：assistant-only fabricated fact write rate = 0（store 全链）
# ============================================================

async def test_b1_assistant_only_fact_never_written(monkeypatch):
    _install_fake_llm(
        monkeypatch,
        # 模拟 prompt 漂移后的最坏情况：模型仍试图写 assistant 事实，
        # 且用 assistant 原话做证据
        lambda prompt: (
            "user_fact|你的项目使用appendfsync everysec|0.92|"
            "你的项目当前使用appendfsync everysec"
        ),
    )
    svc = MemoryService()
    await svc.store(
        question="Redis AOF 怎么配？",
        answer="你的项目当前使用appendfsync everysec。",
        session_id=_SESSION,
        user_id=_USER,
        source_message_id=101,
    )
    async with AsyncSessionLocal() as db:
        rows = (await db.execute(
            text("SELECT content FROM memory_records WHERE user_id = :u"),
            {"u": _USER},
        )).all()
    assert rows == []  # assistant-only 事实未落库

    from backend.observability.metrics import memory_extraction_rejected_total
    assert memory_extraction_rejected_total.labels(
        reason="assistant_only")._value.get() >= 1
    await _cleanup()


# ============================================================
# Case B2：明确用户事实 → inferred + provenance 落库
# ============================================================

async def test_b2_user_fact_written_as_inferred_with_provenance(monkeypatch):
    _install_fake_llm(
        monkeypatch,
        lambda prompt: (
            f"user_fact|主模型是DeepSeek|0.9|{_user_evidence_from_prompt(prompt)}"
        ),
    )
    svc = MemoryService()
    await svc.store(
        question="我现在主模型用的是 DeepSeek。",
        answer="明白了。",
        session_id=_SESSION,
        user_id=_USER,
        source_message_id=202,
    )
    async with AsyncSessionLocal() as db:
        row = (await db.execute(
            text("SELECT origin, confidence_score, source_message_id, session_id "
                 "FROM memory_records WHERE user_id = :u"),
            {"u": _USER},
        )).first()
    assert row is not None
    origin, confidence, src_msg, session_id = row
    assert origin == "inferred"           # 自动通道 = inferred（写入通道语义）
    assert 0.0 <= float(confidence) <= 1.0
    assert src_msg == 202                 # 追溯到本轮 user message id
    assert session_id == _SESSION
    from backend.observability.metrics import memory_inferred_total
    assert memory_inferred_total._value.get() >= 1
    await _cleanup()


# ============================================================
# Case B3：memory_store_tool 显式通道 → origin=explicit、高置信
# ============================================================

async def test_b3_explicit_tool_channel_writes_explicit_origin(monkeypatch):
    monkeypatch.setattr(
        "backend.memory.long_term.get_embedding", lambda: _FakeEmbedding())

    # 不 mock run_tool：MemoryManager 后台 loop 在进程内已就绪，
    # 走真实 sync→async 桥（工具运行路径与生产一致）
    from backend.core.request_context import _current_session_id, _current_user_id
    token_sid = _current_session_id.set(_SESSION)
    token_uid = _current_user_id.set(_USER)
    try:
        from backend.tools.memory import memory_store_tool
        result = memory_store_tool.invoke(
            {"content": "用户偏好中文回答", "memory_type": "preference"})
    finally:
        _current_session_id.reset(token_sid)
        _current_user_id.reset(token_uid)
    assert "已记住" in result

    async with AsyncSessionLocal() as db:
        row = (await db.execute(
            text("SELECT origin, confidence_score, source_message_id "
                 "FROM memory_records WHERE user_id = :u AND memory_type='preference'"),
            {"u": _USER},
        )).first()
    assert row is not None
    origin, confidence, src_msg = row
    assert origin == "explicit"
    assert float(confidence) >= MEMORY_INFERRED_DEFAULT_CONFIDENCE
    assert float(confidence) == pytest.approx(MEMORY_EXPLICIT_DEFAULT_CONFIDENCE)
    assert src_msg is None  # tool 上下文无 message id，不造假
    from backend.observability.metrics import memory_explicit_total
    assert memory_explicit_total._value.get() >= 1
    await _cleanup()


# ============================================================
# Case B5 + B7：通道区分与旧调用兼容（store_single 直调）
# ============================================================

async def test_b7_legacy_call_defaults_preserved(monkeypatch):
    monkeypatch.setattr(
        "backend.memory.long_term.get_embedding", lambda: _FakeEmbedding())
    fact = MemoryFact(fact_type="user_fact", content="legacy-default-fact")
    async with AsyncSessionLocal() as db:
        ok = await LongTermMemory(MemoryRepository(db)).store_single(
            fact, _USER, _SESSION)
        await db.commit()
    assert ok is True
    async with AsyncSessionLocal() as db:
        row = (await db.execute(
            text("SELECT origin, source_message_id FROM memory_records "
                 "WHERE user_id = :u"),
            {"u": _USER},
        )).first()
    assert row == ("legacy", None)  # 旧调用不崩溃、明确 legacy 语义
    await _cleanup()


async def test_b5_explicit_and_inferred_distinguished_in_same_table(monkeypatch):
    monkeypatch.setattr(
        "backend.memory.long_term.get_embedding", lambda: _FakeEmbedding())
    async with AsyncSessionLocal() as db:
        repo = MemoryRepository(db)
        l3 = LongTermMemory(repo)
        await l3.store_single(
            MemoryFact(fact_type="preference", content="偏好A-显式",
                       origin="explicit",
                       confidence_score=MEMORY_EXPLICIT_DEFAULT_CONFIDENCE),
            _USER, _SESSION)
        await l3.store_single(
            MemoryFact(fact_type="preference", content="偏好B-自动",
                       origin="inferred", confidence_score=0.7,
                       source_message_id=303),
            _USER, _SESSION)
        await db.commit()
    async with AsyncSessionLocal() as db:
        rows = (await db.execute(
            text("SELECT origin, confidence_score, source_message_id "
                 "FROM memory_records WHERE user_id = :u ORDER BY origin"),
            {"u": _USER},
        )).all()
    by_origin = {r[0]: (float(r[1]), r[2]) for r in rows}
    assert set(by_origin) == {"explicit", "inferred"}
    assert by_origin["explicit"][1] is None
    assert by_origin["inferred"][1] == 303
    assert by_origin["explicit"][0] > by_origin["inferred"][0]  # explicit 默认更高
    await _cleanup()


# ============================================================
# Case B6：并发 turn 不串 source_message_id
# ============================================================

async def test_b6_concurrent_turns_keep_own_message_id(monkeypatch):
    # content 随用户原话变化：STOP C 后同 content 未 key 事实会被语义去重
    # 判 DUPLICATE，而 B6 的核心断言是「source_message_id 不串轮」
    def _llm(prompt):
        ev = _user_evidence_from_prompt(prompt)
        return f"preference|偏好：{ev}|0.9|{ev}"

    _install_fake_llm(monkeypatch, _llm)
    # save_turn 两次，拿到各自 user message id（模拟两个真实 turn）
    from backend.memory.repository.session_repo import SessionRepository
    svc = MemoryService()
    ids: list[int] = []
    async with AsyncSessionLocal() as db:
        repo = SessionRepository(db)
        await repo.get_or_create(_SESSION + "-c1", _USER)
        q1, _ = await repo.save_turn(_SESSION + "-c1", "第一轮：我喜欢中文", "好")
        await db.commit()
        ids.append(q1.id)
    async with AsyncSessionLocal() as db:
        repo = SessionRepository(db)
        await repo.get_or_create(_SESSION + "-c2", _USER)
        q2, _ = await repo.save_turn(_SESSION + "-c2", "第二轮：我喜欢详细答案", "好")
        await db.commit()
        ids.append(q2.id)

    # 两个后台 store 交错执行，各自携带保存时确定的 message id
    await asyncio.gather(
        svc.store("第一轮：我喜欢中文", "好", _SESSION + "-c1", _USER,
                  source_message_id=ids[0]),
        svc.store("第二轮：我喜欢详细答案", "好", _SESSION + "-c2", _USER,
                  source_message_id=ids[1]),
    )
    async with AsyncSessionLocal() as db:
        rows = (await db.execute(
            text("SELECT session_id, source_message_id FROM memory_records "
                 "WHERE user_id = :u"),
            {"u": _USER},
        )).all()
    assert len(rows) == 2
    mapping = {r[0]: r[1] for r in rows}
    assert mapping[_SESSION + "-c1"] == ids[0]  # 不允许都指向最新消息
    assert mapping[_SESSION + "-c2"] == ids[1]
    await _cleanup()


# ============================================================
# Case B8：migration 047 存量兼容（只读验证实库）
# ============================================================

async def test_b8_migration_legacy_backfill_and_column_presence():
    async with AsyncSessionLocal() as db:
        cols = (await db.execute(
            text("SELECT column_name FROM information_schema.columns "
                 "WHERE table_name='memory_records' AND column_name IN ('origin','source_message_id')"),
        )).all()
        assert {c[0] for c in cols} == {"origin", "source_message_id"}
        # STOP C 跟进：全表 legacy 断言在 048 后不再成立（新写入为
        # inferred/explicit、部分存量 backfill 至真实租户），迁移不变式的
        # 本质是 NOT NULL——origin/tenant 无 NULL 分支
        row = (await db.execute(
            text("SELECT count(*) FILTER (WHERE origin IS NULL) + "
                 "count(*) FILTER (WHERE tenant_id IS NULL) "
                 "FROM memory_records"),
        )).scalar()
        assert row == 0


# ============================================================
# Case B10：PII 防线保持
# ============================================================

async def test_b10_pii_sanitized_before_store(monkeypatch):
    _install_fake_llm(
        monkeypatch,
        lambda prompt: (
            f"preference|邮箱是alice@example.com且偏好中文|0.85|"
            f"{_user_evidence_from_prompt(prompt)}"
        ),
    )
    svc = MemoryService()
    await svc.store(
        question="我的邮箱是 alice@example.com，记住我喜欢中文。",
        answer="好的。",
        session_id=_SESSION,
        user_id=_USER,
        source_message_id=404,
    )
    async with AsyncSessionLocal() as db:
        content = (await db.execute(
            text("SELECT content FROM memory_records WHERE user_id = :u"),
            {"u": _USER},
        )).scalar()
    assert content is not None
    assert "alice@example.com" not in content  # PII 已脱敏
    assert "[邮箱]" in content
    await _cleanup()


# ============================================================
# Case B11：store 异常不阻断（非关键路径不退化）
# ============================================================

async def test_b11_store_failure_degrades_not_raises(monkeypatch):
    async def boom(question, answer):
        raise RuntimeError("simulated extraction timeout")

    svc = MemoryService()
    monkeypatch.setattr(LongTermMemory, "extract_facts", boom)
    # 不抛异常 = 后台写失败不反噬主链（end_turn 的 ensure_future 同理）
    await svc.store("问题", "回答", _SESSION, _USER, source_message_id=505)
    from backend.observability.metrics import memory_extraction_rejected_total
    assert memory_extraction_rejected_total.labels(reason="error")._value.get() >= 1
    await _cleanup()
