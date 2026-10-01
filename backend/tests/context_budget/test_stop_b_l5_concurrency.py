"""STOP B 验收测试 — L5 异步语义与并发（2026-10-01）

对应验收项 CONTEXT_L5_CONCURRENCY_PASS：
  - 水位线一致性核对：核对不过 → 摘要只落库供下一轮，本轮确定性结果
  - 摘要成功 → 本轮 projection 重建（在线等待路径）
  - SessionMemory.summary_persisted 标记：增量 CAS 路径不再二次写摘要
  - 同轮/跨轮口径：等待超时后线程继续跑完（见 test_stop_a_hard_gate 的
    超时用例，此处不重复）
"""
import asyncio

import pytest
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage

import backend.config as config
from backend.context_budget.auto_compact import SummaryOutcome
from backend.context_budget.manager import ContextBudgetManager


@pytest.fixture(autouse=True)
def _cfg(monkeypatch):
    monkeypatch.setattr(config, "CONTEXT_BUDGET_ENABLED", True)
    monkeypatch.setattr(config, "CONTEXT_L5_ENABLED", True)
    monkeypatch.setattr(config, "CONTEXT_L5_TRIGGER_RATIO", 0.05)
    monkeypatch.setattr(config, "HISTORY_TOKEN_BUDGET", 2048)
    monkeypatch.setattr(config, "PREVIOUS_OUTPUTS_MAX_TOKENS", 1024)
    monkeypatch.setattr(config, "CONTEXT_OUTPUT_RESERVE_TOKENS", 0)
    monkeypatch.setattr(config, "CONTEXT_SAFETY_RESERVE_TOKENS", 0)
    from backend.context_budget import token_counter as tc
    monkeypatch.setattr(tc, "_get_model_entry", lambda m: None)
    monkeypatch.setattr(tc, "_get_provider", lambda m: "deepseek")
    tc._counter_cache.clear()


@pytest.fixture(autouse=True)
def _flights_and_session():
    from backend.context_budget import auto_compact as ac
    from backend.core.request_context import set_session_id
    ac._flights.clear()
    set_session_id("sess-stop-b-test")
    yield
    ac._flights.clear()
    set_session_id("multi-agent-default")


def _history(turns: int, msg_len: int = 120) -> list:
    msgs = []
    for i in range(turns):
        msgs.append(HumanMessage(content=f"用户第{i}轮：" + "问" * msg_len))
        msgs.append(AIMessage(content=f"助手第{i}轮：" + "答" * msg_len))
    return msgs


def _outcome(summary: str = "用户此前询问了三笔订单的退款进度，预算 500 元",
             through_id: int = 42, delta: int = 6) -> SummaryOutcome:
    return SummaryOutcome(
        summary=summary, through_id=through_id, token_count=24,
        delta_message_count=delta, protected_fact_count=0,
        patched_fact_count=0, boundary_id=through_id)


class TestSummaryCoversProjection:
    def test_structural_invariant_passes(self):
        ok = ContextBudgetManager._summary_covers_projection(
            _outcome(), [HumanMessage(content="x")], 1)
        assert ok is True

    def test_no_delta_rejected(self):
        ok = ContextBudgetManager._summary_covers_projection(
            _outcome(delta=0), [HumanMessage(content="x")], 1)
        assert ok is False

    def test_zero_watermark_rejected(self):
        ok = ContextBudgetManager._summary_covers_projection(
            _outcome(through_id=0), [HumanMessage(content="x")], 1)
        assert ok is False

    def test_no_boundary_rejected(self):
        ok = ContextBudgetManager._summary_covers_projection(
            _outcome(), [HumanMessage(content="x")], None)
        assert ok is False

    def test_db_message_ids_hard_check(self):
        """消息携带 db_message_id 时升级为逐条核对：头部 id > 水位线 → 拒绝。"""
        newer = HumanMessage(content="x")
        newer.additional_kwargs["db_message_id"] = 99
        ok = ContextBudgetManager._summary_covers_projection(
            _outcome(through_id=42), [newer], 1)
        assert ok is False
        newer.additional_kwargs["db_message_id"] = 42
        ok = ContextBudgetManager._summary_covers_projection(
            _outcome(through_id=42), [newer], 1)
        assert ok is True


class TestSameTurnAdoption:
    async def test_success_rebuilds_projection_this_turn(
            self, monkeypatch):
        """在线等待成功 → 本轮即重建 projection（摘要内容进入消息层）。"""
        monkeypatch.setattr(
            __import__("backend.context_budget.auto_compact", fromlist=["x"]),
            "run_incremental_summary", lambda *a, **k: _outcome())

        msgs = [SystemMessage(content="系统提示")] + _history(8) \
            + [HumanMessage(content="当前问题")]
        prepared = await ContextBudgetManager().prepare_llm_context_async(
            messages=msgs)
        joined = "\n".join(str(m.content) for m in prepared.messages)
        assert "退款进度" in joined, "摘要未在本轮 projection 生效"
        assert len(prepared.messages) < len(msgs), "重建必须压缩历史"
        assert prepared.messages[-1].content == "当前问题"

    async def test_inconsistent_outcome_not_adopted_this_turn(
            self, monkeypatch):
        """水位线核对不过（无增量）→ 本轮不用摘要重建，只落库供下一轮。"""
        monkeypatch.setattr(
            __import__("backend.context_budget.auto_compact", fromlist=["x"]),
            "run_incremental_summary", lambda *a, **k: _outcome(delta=0))

        msgs = [SystemMessage(content="系统提示")] + _history(8) \
            + [HumanMessage(content="当前问题")]
        prepared = await ContextBudgetManager().prepare_llm_context_async(
            messages=msgs)
        joined = "\n".join(str(m.content) for m in prepared.messages)
        assert "退款进度" not in joined, "核对不过的摘要不得进入本轮 projection"
        assert prepared.messages[-1].content == "当前问题"

    async def test_sync_entry_in_event_loop_does_not_wait(
            self, monkeypatch):
        """同步入口命中事件循环：不等待、不另起后台任务（裸 create_task
        已移除）——返回确定性结果且不产生在途摘要。"""
        from backend.context_budget import auto_compact as ac

        def _must_not_run(*a, **k):
            raise AssertionError("同步入口在事件循环里不得启动摘要")

        monkeypatch.setattr(ac, "run_incremental_summary", _must_not_run)
        msgs = [SystemMessage(content="系统提示")] + _history(8) \
            + [HumanMessage(content="当前问题")]
        prepared = ContextBudgetManager().prepare_llm_context(messages=msgs)
        assert prepared.messages[-1].content == "当前问题"
        assert ac._flights == {}


class TestSessionMemoryPersistedFlag:
    def _make_session_memory(self):
        from backend.memory.repository.session_repo import SessionRepository
        from backend.memory.session import SessionMemory
        sm = SessionMemory("s-flag")
        sm._repo = SessionRepository.__new__(SessionRepository)
        return sm

    def test_incremental_path_marks_persisted(self, monkeypatch):
        """增量摘要成功 = CAS 已落库 → summary_persisted=True（调用方不再写）。"""
        sm = self._make_session_memory()

        def _ok(*a, **k):
            return _outcome()

        monkeypatch.setattr(
            __import__("backend.context_budget.auto_compact", fromlist=["x"]),
            "run_incremental_summary", _ok)
        assert asyncio.run(sm.summarize()) is not None
        assert sm.summary_persisted is True

    def test_fallback_path_not_marked(self, monkeypatch):
        """增量路径失败回退全量 → 未持久化（调用方需自行 update_summary）。"""
        sm = self._make_session_memory()

        async def _fake_load(*args, **kwargs):
            return []

        monkeypatch.setattr(
            __import__("backend.context_budget.auto_compact", fromlist=["x"]),
            "run_incremental_summary",
            lambda *a, **k: (_ for _ in ()).throw(RuntimeError("db down")))
        monkeypatch.setattr(sm._repo, "load_messages", _fake_load)

        class _FakeLLM:
            def invoke(self, *a, **k):
                return AIMessage(content="全量摘要内容")

        # session.py 经包命名空间取 llm（from backend.infra.llm import llm）
        monkeypatch.setattr("backend.infra.llm.llm", _FakeLLM())
        assert asyncio.run(sm.summarize()) is not None
        assert sm.summary_persisted is False
