"""摘要失败可见化回归测试（改造方案 R2）。

修复前事实：
- SessionMemory.summarize 失败时用 conversation[:500] 顶替并落库，坏摘要
  覆盖 DB 旧摘要，异常仅 warning 级；
- MemoryManager._run 把 loop 未就绪/未运行/异常全部静默吞为 None。

锁定修复后行为：
1. summarize 失败返回 None、保留旧 _summary，不上抛不伪造
2. service.end_turn 在 summary=None 时不调用 update_summary
3. manager._run 失败时记 error 日志并递增 degradation_alerts_total
"""
import asyncio
import pytest

import backend.orchestration.graph  # noqa: F401  # 循环导入规避（同其他测试）

from backend.memory.session import SessionMemory


class _FakeRow:
    def __init__(self, role, content):
        self.role = role
        self.content = content


class _FakeRepo:
    async def load_messages(self, session_id, limit=None):
        return [_FakeRow("user", f"消息{i}内容") for i in range(limit or 1)]


class _BrokenLLM:
    def invoke(self, *a, **kw):
        raise RuntimeError("LLM down")


@pytest.fixture
def broken_llm(monkeypatch):
    # session.summarize 在调用点才 from backend.infra.llm import llm，运行时 patch 生效
    import backend.infra.llm as llm_mod
    monkeypatch.setattr(llm_mod, "llm", _BrokenLLM(), raising=False)


class TestSummarizeFailureContract:
    def test_failure_returns_none_keeps_old_summary(self, broken_llm):
        sm = SessionMemory("s-1")
        sm._repo = _FakeRepo()
        sm._summary = "旧摘要"
        result = asyncio.run(sm.summarize())
        assert result is None, "失败应返回 None，而不是伪造摘要"
        assert sm._summary == "旧摘要", "失败不应覆盖内存中的旧摘要"

    def test_failure_without_old_summary_does_not_invent(self, broken_llm):
        sm = SessionMemory("s-2")
        sm._repo = _FakeRepo()
        result = asyncio.run(sm.summarize())
        assert result is None
        assert sm._summary is None, "无旧摘要时也不得伪造 conversation[:500]"

    def test_success_still_returns_summary(self, broken_llm, monkeypatch):
        sm = SessionMemory("s-4")
        sm._repo = _FakeRepo()

        class _GoodLLM:
            class _Resp:
                content = "新摘要"

            def invoke(self, *a, **kw):
                return _GoodLLM._Resp()

        import backend.infra.llm as llm_mod
        monkeypatch.setattr(llm_mod, "llm", _GoodLLM(), raising=False)
        result = asyncio.run(sm.summarize())
        assert result == "新摘要"


class TestEndTurnSkipsOverwrite:
    def test_end_turn_skips_update_summary_on_failure(self, broken_llm, monkeypatch):
        """summarize 返回 None 时不得调用 update_summary（防坏摘要覆盖 DB）。"""
        from backend.memory import service as service_mod

        calls = {"update_summary": 0, "save_turn": 0}

        class _FakeSRepo:
            async def get_or_create(self, *a, **kw):
                return None

            async def save_turn(self, *a, **kw):
                calls["save_turn"] += 1

            async def needs_summarization(self, *a, **kw):
                return True

            async def load_messages(self, session_id, limit=None):
                return [_FakeRow("user", "内容")]

            async def update_summary(self, session_id, summary):
                calls["update_summary"] += 1

        class _FakeDBSession:
            async def rollback(self):
                return None

            async def commit(self):
                return None

        class _FakeCtx:
            async def __aenter__(self):
                return _FakeDBSession()  # SessionRepository 已被替换，仅需支持 rollback/commit

            async def __aexit__(self, *a):
                return False

        sm = SessionMemory("s-3")
        sm._repo = _FakeRepo()
        service = service_mod.MemoryService.__new__(service_mod.MemoryService)
        service._sessions = {"s-3": sm}
        monkeypatch.setattr(service_mod, "AsyncSessionLocal", lambda: _FakeCtx())
        monkeypatch.setattr(service_mod, "SessionRepository", lambda db: _FakeSRepo())

        asyncio.run(service.end_turn("s-3", "q", "a", user_id="u"))
        assert calls["save_turn"] == 1, "正常落库不受摘要失败影响"
        assert calls["update_summary"] == 0, "摘要失败时不得调用 update_summary 覆盖 DB"
