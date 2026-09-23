"""STOP C（P1）加固测试 — 2026-09-23

覆盖：
  E. L5 并发：跨进程锁冲突跳过 / Redis 不可用降级放行 / ContextVar 重入守卫
  F. watermark CAS race：旧水位线不得覆盖新水位线
  C. tool call 原子组：assistant(tool_calls)+ToolMessage 成对保留/丢弃
  B+. Semantic Pin：当前用户消息（不一定是 messages[-1]）永不误删
  G. dependency-aware L3 + 分型压缩
"""
import pytest
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage

import backend.config as config
import backend.context_budget.auto_compact as ac_mod
from backend.context_budget.auto_compact import L5_ACTIVE, run_incremental_summary
from backend.context_budget.manager import ContextBudgetManager
from backend.context_budget.micro_compactor import (
    compact_previous_outputs,
    compute_dependency_ranks,
)
from backend.context_budget.pin import (
    PIN_CONFIRMATION,
    PinnedContext,
    build_atomic_groups,
    collect_pin_indices,
)
from backend.memory.token_budget import trim_messages_to_budget
from tests.context_budget.test_auto_compact import FakeStore


@pytest.fixture(autouse=True)
def _cfg(monkeypatch):
    monkeypatch.setattr(config, "CONTEXT_BUDGET_ENABLED", True)
    monkeypatch.setattr(config, "HISTORY_TOKEN_BUDGET", 2048)
    monkeypatch.setattr(config, "PREVIOUS_OUTPUTS_MAX_TOKENS", 1024)
    monkeypatch.setattr(config, "CONTEXT_OUTPUT_RESERVE_TOKENS", 0)
    monkeypatch.setattr(config, "CONTEXT_SAFETY_RESERVE_TOKENS", 0)


# ---------------------------------------------------------------------------
# E. L5 并发
# ---------------------------------------------------------------------------


class TestL5Concurrency:
    def _delta_rows(self):
        return [
            (31, "user", "帮我查订单 ORD20260922001 的退款进度"),
            (32, "assistant", "已记录退款申请"),
            (33, "user", "预计 2026-09-25 到账吗"),
            (34, "assistant", "是的"),
        ]

    def test_lock_conflict_skips_summary(self, monkeypatch):
        """锁被其他 worker 持有（False）→ 本轮放弃摘要，不调 LLM。"""
        monkeypatch.setattr(ac_mod, "_acquire_l5_lock", lambda sid: False)
        called = []

        def _no_llm(prompt):
            called.append(prompt)
            raise AssertionError("不应调用 LLM")

        monkeypatch.setattr(ac_mod, "_invoke_llm_with_timeout", _no_llm)
        store = FakeStore(through_id=30, boundary_id=40,
                          rows=self._delta_rows())
        assert run_incremental_summary("s-lock", store) is None
        assert called == []
        assert store.saved is None

    def test_lock_unavailable_degrades_through(self, monkeypatch):
        """Redis 不可用（None）→ 降级放行，摘要链路继续工作。"""
        monkeypatch.setattr(ac_mod, "_acquire_l5_lock", lambda sid: None)
        monkeypatch.setattr(ac_mod, "_invoke_llm_with_timeout",
                            lambda p: type("R", (), {
                                "content": "摘要内容，足够长以通过校验",
                                "response_metadata": {}})())
        store = FakeStore(through_id=30, boundary_id=40,
                          rows=self._delta_rows())
        outcome = run_incremental_summary("s-degrade", store)
        assert outcome is not None
        assert store.saved["through_id"] == 34

    def test_contextvar_guard_blocks_recursion(self, monkeypatch):
        """L5_ACTIVE 置位 → run_incremental_summary 直接跳过（防递归）。"""
        token = L5_ACTIVE.set(True)
        try:
            called = []
            monkeypatch.setattr(ac_mod, "_invoke_llm_with_timeout",
                                lambda p: called.append(p))
            store = FakeStore(through_id=30, boundary_id=40,
                              rows=self._delta_rows())
            assert run_incremental_summary("s-recur", store) is None
            assert called == []
        finally:
            L5_ACTIVE.reset(token)

    def test_thread_local_guard_removed(self):
        m = ContextBudgetManager()
        assert not hasattr(m, "_l5_thread_local")

    def test_summary_llm_runs_under_guard(self, monkeypatch):
        """摘要 LLM 调用发生时 L5_ACTIVE 必须已置位（executor 线程可见）。"""
        seen: dict = {}

        def _fake_invoke(prompt):
            seen["active"] = L5_ACTIVE.get()
            return type("R", (), {"content": "摘要", "response_metadata": {}})()

        monkeypatch.setattr(ac_mod, "_acquire_l5_lock", lambda sid: None)
        monkeypatch.setattr(ac_mod, "_invoke_llm_with_timeout", _fake_invoke)
        store = FakeStore(through_id=30, boundary_id=40,
                          rows=self._delta_rows())
        assert run_incremental_summary("s-guard", store) is not None
        assert seen["active"] is True


# ---------------------------------------------------------------------------
# F. watermark CAS race
# ---------------------------------------------------------------------------


class TestWatermarkCAS:
    def test_stale_watermark_cannot_overwrite_newer(self, monkeypatch):
        """规格 F：A(through=100) 后提交不得覆盖 B(through=105) 的摘要。"""
        monkeypatch.setattr(ac_mod, "_acquire_l5_lock", lambda sid: None)
        monkeypatch.setattr(ac_mod, "_invoke_llm_with_timeout",
                            lambda p: type("R", (), {
                                "content": "A 的旧摘要",
                                "response_metadata": {}})())
        # A 读到 through=30 开始摘要（慢）；期间 B 完成并把水位线推进到 105
        store = FakeStore(summary="B 的新摘要", through_id=30,
                          boundary_id=40, rows=[
                              (31, "user", "q1"), (32, "assistant", "a1")])
        real_get = store.get_summary_state

        def get_then_advance():
            state = real_get()  # A 读到 through=30
            assert state["through_id"] == 30
            store.through_id = 105  # B 先完成写入
            store.summary = "B 的新摘要"
            return state

        store.get_summary_state = get_then_advance
        outcome = run_incremental_summary("s-cas", store)
        assert outcome is None  # A 的旧结果被丢弃
        assert store.through_id == 105  # 水位线仍是 B 的
        assert store.summary == "B 的新摘要"  # 新摘要未被覆盖

    def test_store_cas_sql_uses_expected_watermark(self):
        """真 Store 的 SQL 必须带 CAS 谓词（防回退成无条件 UPDATE）。"""
        import inspect
        from backend.context_budget.auto_compact import SyncMemorySummaryStore
        src = inspect.getsource(
            SyncMemorySummaryStore.save_summary_state)
        assert "COALESCE(summary_through_message_id, 0) = %s" in src
        assert "summary_version = summary_version + 1" in src

    def test_migration_044_registered(self):
        import io
        from pathlib import Path
        init = Path(__file__).resolve().parents[3] / "scripts" / "init_db.py"
        text = init.read_text(encoding="utf-8")
        assert '"044_chat_sessions_summary_version.sql": "memory"' in text
        mig = (Path(__file__).resolve().parents[2]
               / "sql" / "migrations"
               / "044_chat_sessions_summary_version.sql")
        assert "summary_version" in mig.read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# C. tool 原子组 + B+. Semantic Pin
# ---------------------------------------------------------------------------


def _tool_pair(i: int, result: str = "结果"):
    return [
        AIMessage(content="", tool_calls=[
            {"name": "sql_query", "args": {"q": f"q{i}"}, "id": f"call_{i}"}]),
        ToolMessage(content=result, tool_call_id=f"call_{i}"),
    ]


class TestAtomicGroupsAndPins:
    def test_grouping_pairs(self):
        msgs = [SystemMessage(content="s"), HumanMessage(content="q1")] \
            + _tool_pair(1) + [HumanMessage(content="q2")]
        groups = build_atomic_groups(msgs)
        sizes = [len(g) for g in groups]
        assert sizes == [1, 1, 2, 1]  # sys | h1 | pair | h2

    def test_pair_never_split(self):
        """预算不够时旧 tool 对整对丢弃，绝不出现孤儿 ToolMessage。"""
        msgs = [SystemMessage(content="s")] + _tool_pair(1, "旧结果" * 200) \
            + [HumanMessage(content="中间问题")] + _tool_pair(2, "新结果" * 50) \
            + [HumanMessage(content="当前问题")]
        pins = collect_pin_indices(msgs)
        kept, dropped = trim_messages_to_budget(msgs, 250, pin_indices=pins)
        tool_ids = {m.tool_call_id for m in kept
                    if type(m).__name__ == "ToolMessage"}
        assistant_ids = {tc["id"] for m in kept
                         for tc in getattr(m, "tool_calls", [])}
        # 协议完整性：留下的 tool 结果必须有配对的 assistant tool_call
        assert tool_ids == assistant_ids
        assert dropped == 2  # 旧对整对丢弃
        assert kept[-1].content == "当前问题"

    def test_pair_kept_together_when_fits(self):
        msgs = [SystemMessage(content="s")] + _tool_pair(1, "短结果") \
            + [HumanMessage(content="q")]
        kept, dropped = trim_messages_to_budget(msgs, 10 ** 9)
        assert dropped == 0 and len(kept) == 4

    def test_current_query_not_last_message(self):
        """规格 B：最后一条不是 user（是 tool 结果）时，真正的当前问题
        （最后一条 HumanMessage）不得被误删。"""
        msgs = [SystemMessage(content="s"),
                HumanMessage(content="当前用户问题" + "详" * 300),
                AIMessage(content="处理中" + "述" * 300)] \
            + _tool_pair(2, "工具输出" + "果" * 300)
        pins = collect_pin_indices(msgs)
        kept, _ = trim_messages_to_budget(msgs, 120, pin_indices=pins)
        contents = [m.content for m in kept]
        assert any(str(c).startswith("当前用户问题") for c in contents)
        # 活跃 tool 对也被 pin 保留
        assert any(type(m).__name__ == "ToolMessage" for m in kept)

    def test_active_pair_pin_covers_only_latest(self):
        msgs = _tool_pair(1, "旧") + [HumanMessage(content="h")] \
            + _tool_pair(2, "新")
        pins = collect_pin_indices(msgs)
        # 旧对不 pin（下标 0/1），新对 pin（下标 3/4）
        assert 0 not in pins and 1 not in pins
        assert 3 in pins and 4 in pins

    def test_explicit_confirmation_pin(self):
        msgs = [SystemMessage(content="s"),
                HumanMessage(content="普通旧消息" + "旧" * 200),
                HumanMessage(content="确认执行：删除订单 ORD1"),
                HumanMessage(content="当前问题")]
        pins = PinnedContext().mark_index(2, PIN_CONFIRMATION).resolve(msgs)
        kept, _ = trim_messages_to_budget(msgs, 80, pin_indices=pins)
        assert any("确认执行" in str(m.content) for m in kept)
        assert kept[-1].content == "当前问题"

    def test_manager_uses_semantic_trim(self, monkeypatch):
        """prepare_llm_context 走语义 pin 裁剪：当前问题（最后一条 Human）
        即使不是 messages[-1] 也存活，未 pin 的旧消息先丢。"""
        m = ContextBudgetManager()
        msgs = [SystemMessage(content="s"),
                HumanMessage(content="较早问题" + "细" * 400),
                AIMessage(content="回" * 400),
                HumanMessage(content="追问" + "节" * 400)]
        monkeypatch.setattr("backend.config.llm.LLM_CONTEXT_LENGTH", 300)
        prepared = m.prepare_llm_context(messages=msgs)
        contents = [getattr(x, "content", "") for x in prepared.messages]
        assert any(str(c).startswith("追问") for c in contents)  # 语义 pin
        assert not any(str(c).startswith("较早问题") for c in contents)  # 旧的先丢


# ---------------------------------------------------------------------------
# G. dependency-aware L3 + 分型压缩
# ---------------------------------------------------------------------------


class TestDependencyAwareL3:
    def _results(self):
        return {
            "1": {"capability": "sql.query", "status": "success"},
            "2": {"capability": "rag.search", "status": "success"},
            "3": {"capability": "web.fetch", "status": "success"},
            "4": {"capability": "report.generate", "status": "success"},
        }

    def test_ranks_prefer_final_path_and_decision_caps(self):
        edges = {"2": ["1"], "3": ["1"], "4": ["2", "3"]}
        ranks = compute_dependency_ranks(edges, "4", self._results())
        # 2（rag 知识证据 +0.5）与 3（普通工具）都直达终局步骤 4，
        # rag 能力加分使 2 排在 3 前；1 不是当前步直接前驱，不在注入集合
        assert set(ranks) == {"2", "3"}
        assert ranks["2"] > ranks["3"]

    def test_compact_keeps_high_priority_full(self):
        """G：sql 结果（高优先）即便更旧也要完整保留，helper 先降级。"""
        sql_big = "SELECT 结果" * 100        # ~420 token，单独放得下
        helper_big = "辅助输出" * 300         # ~920 token，sql 入选后放不下
        po = {"3": helper_big, "1": sql_big}
        meta = {
            "1": {"step_id": "1", "tool": "sql.query", "status": "success"},
            "3": {"step_id": "3", "tool": "web.fetch", "status": "success"},
        }
        ranks = {"1": 4.5, "3": 1.0}
        result = compact_previous_outputs(po, meta=meta, priorities=ranks)
        assert result["1"] == sql_big          # 高优先完整保留
        assert isinstance(result["3"], dict)   # helper 降级
        assert result["3"]["compacted"] is True

    def test_no_priorities_falls_back_latest_first(self):
        po = {"1": "旧" * 1000, "2": "新" * 1000}
        meta = {
            "1": {"step_id": "1", "tool": "web.fetch", "status": "success"},
            "2": {"step_id": "2", "tool": "web.fetch", "status": "success"},
        }
        result = compact_previous_outputs(po, meta=meta)
        assert result["2"] == po["2"]
        assert isinstance(result["1"], dict)

    def test_sql_typed_degrade_keeps_key_fields(self):
        po = {"1": {"query": "SELECT GMV FROM orders",
                    "columns": ["gmv"], "row_count": 42,
                    "rows": [{"gmv": 1}], "big_payload": "x" * 5000}}
        meta = {"1": {"step_id": "1", "tool": "sql.query",
                      "status": "success"}}
        result = compact_previous_outputs(po, meta=meta)
        entry = result["1"]
        assert entry["compacted"] is True
        assert entry["query"] == "SELECT GMV FROM orders"
        assert entry["row_count"] == 42
        assert "big_payload" not in entry  # 白名单外字段不保留
        assert "preview" not in entry      # 分型结构，不再是裸 preview

    def test_rag_typed_degrade(self):
        po = {"2": {"doc_ids": ["d1", "d2"], "rerank_score": 0.91,
                    "evidence": "证据片段" * 100, "text": "y" * 5000}}
        meta = {"2": {"step_id": "2", "tool": "rag.search",
                      "status": "success"}}
        entry = compact_previous_outputs(po, meta=meta)["2"]
        assert entry["compacted"] is True
        assert entry["doc_ids"] == ["d1", "d2"]
        assert entry["rerank_score"] == 0.91

    def test_l1_preview_never_reexpanded_even_if_typed_cap(self):
        """L1 preview 条目无论能力类型都只收缩，不重载全文。"""
        preview = {"context_compacted": True, "type": "tool_result_preview",
                   "preview": "SQL 预览" * 200, "original_tokens": 9999}
        po = {"1": preview, "2": "其他" * 400}
        meta = {
            "1": {"step_id": "1", "tool": "sql.query", "status": "success"},
            "2": {"step_id": "2", "tool": "web.fetch", "status": "success"},
        }
        entry = compact_previous_outputs(po, meta=meta)["1"]
        assert entry["compacted"] is True
        assert entry.get("type") == "tool_result_preview"  # 结构保持
        assert "query" not in entry  # 没有重新拼回结构化全文
