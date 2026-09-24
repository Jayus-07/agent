"""STOP E：L2 摘要触发契约（trigger / hysteresis / output cap / off-critical-path）。

任务书 E2/E3/E8 验收：
- E8/P1：end_turn 不再在请求关闭路径同步等待摘要（后台任务化）；
- E2/P1：增量滞后门——水位线后增量 < CONTEXT_L2_SUMMARY_MIN_DELTA_MESSAGES
  不调 LLM（原实现稳态每轮一次摘要调用）；
- E3/P2：fallback 全量摘要路径与增量路径同一 provider 级 max_tokens 硬帽。

外部依赖边界：真实 PostgreSQL（会话/水位线/摘要落库）；LLM 用运行时
monkeypatch stub（沿用 test_summary_visibility 模式）。
"""

from __future__ import annotations

import uuid

import pytest

from backend.tests.memory.conftest import (
    cleanup_memory_prefix,
    require_memory_pg,
)

pytestmark = pytest.mark.asyncio

_PREFIX = "stope-sum-"
_USER = f"{_PREFIX}user"


@pytest.fixture(autouse=True)
async def _env():
    require_memory_pg()
    await cleanup_memory_prefix(_PREFIX)
    yield
    await cleanup_memory_prefix(_PREFIX)


class _StubLLM:
    """记录调用并返回固定摘要文本；raise_on_call 时充当「LLM 不许被调」哨兵。"""

    def __init__(self, content="存档摘要：用户讨论了预算与订单。",
                 raise_on_call=False):
        self.calls: list[dict] = []
        self.content = content
        self.raise_on_call = raise_on_call

    def invoke(self, *args, **kwargs):
        self.calls.append({"args": args, "kwargs": kwargs})
        if self.raise_on_call:
            raise RuntimeError("LLM 不应被调用（滞后门应拦截）")
        class _Resp:
            pass
        r = _Resp()
        r.content = self.content
        return r


def _stub_llm(monkeypatch, **kw) -> _StubLLM:
    stub = _StubLLM(**kw)
    import backend.infra.llm as llm_mod
    monkeypatch.setattr(llm_mod, "llm", stub, raising=False)
    return stub


async def _seed_messages(session_id: str, turns: int) -> None:
    from backend.memory.database import AsyncSessionLocal
    from backend.memory.repository.session_repo import SessionRepository
    async with AsyncSessionLocal() as db:
        repo = SessionRepository(db)
        await repo.get_or_create(session_id, _USER)
        for i in range(turns):
            await repo.save_message(session_id, "user", f"第{i}轮用户消息：预算相关内容{i}")
            await repo.save_message(session_id, "assistant", f"第{i}轮助手回复：已处理{i}")
        await db.commit()


async def _summary_state(session_id: str) -> dict:
    from backend.memory.database import AsyncSessionLocal
    from backend.memory.repository.session_repo import SessionRepository
    async with AsyncSessionLocal() as db:
        return dict(await SessionRepository(db).get_summary_state(session_id))


async def test_end_turn_schedules_summarize_without_awaiting(monkeypatch):
    """E8：end_turn 只调度后台摘要协程，不在本协程内等待其完成。"""
    from backend.memory import service as service_mod

    executed: list[str] = []

    class _FakeSRepo:
        async def get_or_create(self, *a, **kw):
            return None

        async def save_turn(self, *a, **kw):
            return (None, None)

        async def needs_summarization(self, *a, **kw):
            executed.append("needs_summarization")

        async def get_summary_state(self, *a, **kw):
            return {}

        async def summarizable_before_id(self, *a, **kw):
            return None

        async def load_messages_since(self, *a, **kw):
            return []

        async def update_summary(self, *a, **kw):
            executed.append("update_summary")

    class _FakeDBSession:
        async def rollback(self):
            return None

        async def commit(self):
            executed.append("commit")

    class _FakeCtx:
        async def __aenter__(self):
            return _FakeDBSession()

        async def __aexit__(self, *a):
            return False

    scheduled = []

    def _capture(coro):
        scheduled.append(coro)
        return None

    svc = service_mod.MemoryService.__new__(service_mod.MemoryService)
    svc._sessions = {}
    monkeypatch.setattr(service_mod, "AsyncSessionLocal", lambda: _FakeCtx())
    monkeypatch.setattr(service_mod, "SessionRepository", lambda db: _FakeSRepo())
    monkeypatch.setattr(service_mod.asyncio, "ensure_future", _capture)

    await svc.end_turn("no-block", "q", "a", user_id=_USER)

    # 摘要查询链（needs_summarization 等）尚未执行——仅 save_turn+commit 走完
    assert "commit" in executed
    assert "needs_summarization" not in executed, (
        "end_turn 不得在关闭路径执行摘要查询链（必须后台化）")
    assert len(scheduled) == 2, "应调度 摘要 + store 两个后台任务"
    for coro in scheduled:
        coro.close()


async def test_delta_gate_skips_small_delta(monkeypatch):
    """E2：增量 < K 时不调 LLM、不写摘要（滞后门防每轮重复摘要）。"""
    from backend.memory.service import MemoryService

    session_id = f"{_PREFIX}small-{uuid.uuid4().hex[:8]}"
    # 55 条消息满足条数触发（≥50）；最近 4 轮保护后增量 = 50-8 ≈ 42 条？
    # 注意 summarizable_before_id 边界：第 4 轮末尾之前的都算增量——
    # 55 条消息 = 27.5 轮，边界取倒数第 4 个 user 消息 → 增量 ≈ 48 条 ≥ 10。
    # 所以先摘要一次推进水位线，再补 4 条消息（增量 4 < 10）验证跳过。
    await _seed_messages(session_id, turns=27)  # 54 条
    stub = _stub_llm(monkeypatch, content="首轮存档摘要")
    svc = MemoryService()
    await svc._summarize_if_needed(session_id)
    assert len(stub.calls) == 1, "首轮增量充足应摘要一次"
    state = await _summary_state(session_id)
    assert state["summary"].startswith("首轮存档摘要")
    through = state["through_id"]
    assert through, "水位线应已推进"

    # 补 2 轮（4 条）→ 增量 < 10 → 滞后门跳过（LLM 不许被调）
    await _seed_messages(session_id, turns=2)
    stub.raise_on_call = True
    await svc._summarize_if_needed(session_id)
    state2 = await _summary_state(session_id)
    assert state2["through_id"] == through, "水位线不得被滞后门绕过推进"
    assert state2["summary"].startswith("首轮存档摘要")


async def test_delta_gate_accumulates_then_summarizes(monkeypatch):
    """E2：增量攒批到 ≥ K 后恢复摘要（水位线推进，不丢上下文）。"""
    from backend.memory.service import MemoryService

    session_id = f"{_PREFIX}accum-{uuid.uuid4().hex[:8]}"
    await _seed_messages(session_id, turns=27)
    stub = _stub_llm(monkeypatch, content="第一批摘要")
    svc = MemoryService()
    await svc._summarize_if_needed(session_id)
    state = await _summary_state(session_id)
    assert state["summary"].startswith("第一批摘要")
    through = state["through_id"]

    # 补 6 轮（12 条 ≥ K=10）→ 摘要应执行且水位线越过新消息
    await _seed_messages(session_id, turns=6)
    stub2 = _stub_llm(monkeypatch, content="第二批摘要")
    await svc._summarize_if_needed(session_id)
    assert len(stub2.calls) == 1, "增量达到阈值应恰好摘要一次"
    state2 = await _summary_state(session_id)
    assert state2["summary"].startswith("第二批摘要")
    assert state2["through_id"] > through


async def test_below_trigger_count_never_summarizes(monkeypatch):
    """E2：条数未达 SESSION_MAX_MESSAGES 时整段跳过（触发口径不变）。"""
    from backend.memory.service import MemoryService

    session_id = f"{_PREFIX}low-{uuid.uuid4().hex[:8]}"
    await _seed_messages(session_id, turns=10)  # 20 条 < 50
    stub = _stub_llm(monkeypatch, raise_on_call=True)
    svc = MemoryService()
    await svc._summarize_if_needed(session_id)
    state = await _summary_state(session_id)
    assert not state["summary"]


async def test_fallback_summary_output_cap(monkeypatch):
    """E3：fallback 全量摘要必须带 provider 级 max_tokens 硬帽。"""
    from backend.config import CONTEXT_L5_SUMMARY_MAX_TOKENS
    from backend.memory.session import SessionMemory

    class _FakeRow:
        role = "user"
        content = "历史内容"

    class _FakeRepo:
        async def load_messages(self, session_id, limit=None):
            return [_FakeRow() for _ in range(3)]

    captured: dict = {}

    class _CapLLM:
        def invoke(self, *args, **kwargs):
            captured.update(kwargs)

            class _Resp:
                content = "受限摘要"

            return _Resp()

    import backend.infra.llm as llm_mod
    monkeypatch.setattr(llm_mod, "llm", _CapLLM(), raising=False)

    sm = SessionMemory("s-cap")
    sm._repo = _FakeRepo()
    result = await sm.summarize()
    assert result == "受限摘要"
    assert captured.get("max_tokens") == int(CONTEXT_L5_SUMMARY_MAX_TOKENS), (
        "fallback 摘要必须带与增量路径一致的 max_tokens 输出帽")
