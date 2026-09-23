"""L5 AutoCompact 测试（2026-09-22 Phase 3）

覆盖：
- ProtectedFacts 确定性抽取（订单号/SKU/金额/百分比/日期/URL/数量）
- validate_and_patch：摘要遗漏事实**追加**进 [关键实体]，零额外 LLM 调用
- fold_rebuild：System/最近 N 轮保留；旧摘要与 L4 投影被新摘要整体替换；
  原始列表不被修改
- run_incremental_summary：只取水位线之后 delta / 成功推进水位线 /
  失败安全回退（None，不落库）
- prepare_llm_context L5 触发链路：L4 之后仍达阈值才触发；低于阈值不触发；
  开关关闭不触发；触发后重建 active projection
- 红线：L5 全程不触碰原始消息列表（调用方持有对象不变）
"""
import pytest

import backend.config as config
import backend.context_budget.auto_compact as ac_mod
from backend.context_budget.auto_compact import (
    L2_SUMMARY_MARKER,
    ProtectedFact,
    SyncMemorySummaryStore,
    SummaryOutcome,
    extract_protected_facts,
    fold_rebuild,
    run_incremental_summary,
    validate_and_patch,
)
from backend.context_budget.manager import ContextBudgetManager
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage


@pytest.fixture(autouse=True)
def _budget_cfg(monkeypatch):
    monkeypatch.setattr(config, "CONTEXT_BUDGET_ENABLED", True)
    monkeypatch.setattr(config, "CONTEXT_OUTPUT_RESERVE_TOKENS", 768)
    monkeypatch.setattr(config, "CONTEXT_SAFETY_RESERVE_TOKENS", 256)
    monkeypatch.setattr(config, "CONTEXT_L5_ENABLED", True)
    monkeypatch.setattr(config, "CONTEXT_L5_MIN_DELTA_MESSAGES", 2)
    monkeypatch.setattr(config, "CONTEXT_L5_MAX_DELTA_MESSAGES", 200)
    monkeypatch.setattr(config, "CONTEXT_L4_KEEP_RECENT_TURNS", 4)


# ── ProtectedFacts ──────────────────────────────────────────────

class TestExtractProtectedFacts:
    def test_ecommerce_entities(self):
        rows = [
            (1, "我的订单号是 ORD20260922001，SKU 是 SKU-ABC-123，预算 ¥5000"),
            (2, "折扣 15%，2026-09-22 之前发货，见 https://example.com/x"),
            (3, "库存还有 35件，请联系我"),
        ]
        facts = extract_protected_facts(rows)
        values = {f.value for f in facts}
        assert "ORD20260922001" in values
        assert "ABC-123" in values          # SKU 捕获组去前缀
        assert "¥5000" in values
        assert any(f.value.endswith("%") for f in facts)
        assert "2026-09-22" in values
        assert "https://example.com/x" in values
        assert any(f.value == "35件" for f in facts)
        assert all(f.source_message_id in (1, 2, 3) for f in facts)

    def test_dedup_and_cap(self):
        rows = [(i, "订单号 ORD12345678 金额 ¥100") for i in range(100)]
        facts = extract_protected_facts(rows)
        assert len(facts) <= int(config.CONTEXT_L5_MAX_PROTECTED_FACTS)
        assert len([f for f in facts if f.value == "ORD12345678"]) == 1

    def test_empty(self):
        assert extract_protected_facts([]) == []


class TestValidateAndPatch:
    def test_missing_facts_appended(self):
        facts = [
            ProtectedFact(type="SKU", value="ABC-123"),
            ProtectedFact(type="订单号", value="ORD20260922001"),
        ]
        summary = "[用户目标]\n咨询退款\n\n[关键实体]\n- SKU: ABC-123"
        patched, missing = validate_and_patch(summary, facts)
        assert missing == [facts[1]]
        assert "ORD20260922001" in patched
        assert patched.count("[关键实体]") == 2  # 追加新小节，不改原文

    def test_all_present(self):
        facts = [ProtectedFact(type="SKU", value="ABC-123")]
        summary = "SKU ABC-123 已退款"
        patched, missing = validate_and_patch(summary, facts)
        assert missing == []
        assert patched == summary


# ── fold_rebuild ────────────────────────────────────────────────

def _history(turns: int) -> list:
    msgs: list = []
    for i in range(turns):
        msgs.append(HumanMessage(content=f"用户问题{i} 订单号 ORD{i:05d}"))
        msgs.append(AIMessage(content=f"助手回答{i}"))
    return msgs


class TestFoldRebuild:
    def test_rebuild_keeps_recent_and_replaces_old(self):
        msgs = [SystemMessage(content="系统指令")] + _history(8)
        original = list(msgs)
        rebuilt, replaced, boundary = fold_rebuild(msgs, "新摘要内容")
        assert replaced > 0
        assert boundary is not None
        # System 指令保留
        assert rebuilt[0].content == "系统指令"
        # 角色安全（P0-2）：新摘要 = 固定 policy SystemMessage + 数据 AIMessage，
        # 摘要内容只出现在 AIMessage 的 <historical_context> 标签内
        from backend.context_budget.role_safety import (
            HISTORICAL_CONTEXT_POLICY_TEXT,
            HISTORICAL_TAG_CLOSE,
            HISTORICAL_TAG_OPEN,
        )
        assert rebuilt[1].content == HISTORICAL_CONTEXT_POLICY_TEXT
        assert type(rebuilt[2]).__name__ == "AIMessage"
        assert rebuilt[2].content.startswith(HISTORICAL_TAG_OPEN)
        assert "新摘要内容" in rebuilt[2].content
        assert rebuilt[2].content.rstrip().endswith(HISTORICAL_TAG_CLOSE)
        # 动态摘要内容不得出现在任何 SystemMessage 里
        assert not any("新摘要内容" in m.content
                       for m in rebuilt if isinstance(m, SystemMessage))
        # 最近 4 轮保留（4 条 Human + 4 条 AI）
        tail = rebuilt[3:]
        assert sum(1 for m in tail if isinstance(m, HumanMessage)) == 4
        assert sum(isinstance(m, HumanMessage) and "用户问题7" in m.content
                   for m in tail) == 1
        # 旧消息被替换
        assert all("用户问题0" not in getattr(m, "content", "")
                   for m in rebuilt)
        # 红线：原列表不被修改
        assert msgs == original

    def test_replaces_old_l2_summary_and_l4_projection(self):
        old_summary = SystemMessage(
            content=f"{L2_SUMMARY_MARKER}，可结合它理解用户当前问题：\n旧摘要")
        fold_proj = SystemMessage(
            content="[Earlier conversation folded]\n3 earlier messages...")
        msgs = [SystemMessage(content="系统指令"), old_summary, fold_proj] \
            + _history(6)
        rebuilt, replaced, _ = fold_rebuild(msgs, "新摘要")
        assert replaced >= 2
        assert not any(
            L2_SUMMARY_MARKER in m.content and "旧摘要" in m.content
            for m in rebuilt if isinstance(m, SystemMessage))
        assert any(isinstance(m, SystemMessage) and m.content == "系统指令"
                   for m in rebuilt)

    def test_not_enough_turns(self):
        msgs = _history(3)  # 3 轮 < 4
        rebuilt, replaced, boundary = fold_rebuild(msgs, "新摘要")
        assert rebuilt == msgs and replaced == 0 and boundary is None


# ── run_incremental_summary ─────────────────────────────────────

class FakeStore:
    """水位线 Store 假实现（记录调用，模拟 DB 行为）。

    CAS 语义（2026-09-23 STOP C）：expected_through 与当前 through_id 不符
    时写入失败返回 False（模拟并发下已有更新摘要落库）。
    """

    def __init__(self, summary=None, through_id=None, boundary_id=100,
                 rows=None):
        self.summary = summary
        self.through_id = through_id
        self.boundary_id = boundary_id
        self.rows = rows or []
        self.load_calls: list[tuple] = []
        self.saved: dict | None = None

    def get_summary_state(self):
        return {"summary": self.summary, "through_id": self.through_id,
                "token_count": None, "version": 1 if self.through_id else 0}

    def summarizable_before_id(self, keep_recent_turns):
        return self.boundary_id

    def load_delta_messages(self, after_id, before_id, limit):
        self.load_calls.append((after_id, before_id, limit))
        return self.rows

    def save_summary_state(self, summary, through_id, token_count,
                           expected_through=None):
        if expected_through is not None \
                and int(expected_through) != int(self.through_id or 0):
            return False  # CAS 冲突
        self.saved = {"summary": summary, "through_id": through_id,
                      "token_count": token_count,
                      "expected_through": expected_through}
        self.through_id = through_id
        return True


@pytest.fixture(autouse=True)
def _no_redis_lock(monkeypatch):
    """存量测试统一降级 Redis 锁（单飞/CAS 行为在 test_stop_c_p1.py 专测）。"""
    monkeypatch.setattr(ac_mod, "_acquire_l5_lock", lambda sid: None,
                        raising=True)


@pytest.fixture()
def _fake_llm(monkeypatch):
    """摘要 LLM 假实现：返回结构化摘要文本。"""
    class _Resp:
        content = ("[用户目标]\n查询订单状态\n\n[关键实体]\n- SKU: ABC-123\n"
                   "- 金额: ¥5000")
        response_metadata = {"token_usage": {"prompt_tokens": 120,
                                             "completion_tokens": 40}}

    monkeypatch.setattr(ac_mod, "_invoke_llm_with_timeout",
                        lambda prompt: _Resp())


_DELTA_ROWS = [
    (31, "user", "帮我查订单 ORD20260922001 的退款进度，金额 ¥5000"),
    (32, "assistant", "好的，已记录 SKU ABC-123 的退款申请"),
    (33, "user", "预计 2026-09-25 到账吗"),
    (34, "assistant", "是的，预计 2026-09-25 到账"),
]


class TestRunIncrementalSummary:
    def test_incremental_only_sends_delta(self, _fake_llm):
        store = FakeStore(summary="旧摘要", through_id=30, boundary_id=40,
                          rows=_DELTA_ROWS)
        outcome = run_incremental_summary("s-1", store)
        assert outcome is not None
        # 只加载 (through, boundary) 区间
        assert store.load_calls == [(30, 40, 200)]
        # 水位线推进到 delta 最大 id
        assert store.saved["through_id"] == 34
        assert store.saved["summary"].startswith("[用户目标]")
        assert outcome.delta_message_count == 4

    def test_protected_facts_in_prompt(self, _fake_llm, monkeypatch):
        captured: dict = {}

        def _capture(prompt):
            captured["prompt"] = prompt
            class _R:
                content = "摘要正文"
                response_metadata = {}
            return _R()

        monkeypatch.setattr(ac_mod, "_invoke_llm_with_timeout", _capture)
        store = FakeStore(summary=None, through_id=None, boundary_id=40,
                          rows=_DELTA_ROWS)
        outcome = run_incremental_summary("s-1", store)
        assert outcome is not None
        assert "ORD20260922001" in captured["prompt"]
        assert "必须原样保留" in captured["prompt"]

    def test_llm_failure_safe_fallback(self, monkeypatch):
        def _boom(prompt):
            raise RuntimeError("LLM down")

        monkeypatch.setattr(ac_mod, "_invoke_llm_with_timeout", _boom)
        store = FakeStore(summary="旧摘要", through_id=30, boundary_id=40,
                          rows=_DELTA_ROWS)
        outcome = run_incremental_summary("s-1", store)
        assert outcome is None
        assert store.saved is None, "失败不得推进水位线/覆盖旧摘要"

    def test_no_new_delta(self, _fake_llm):
        store = FakeStore(summary="旧摘要", through_id=35, boundary_id=30)
        assert run_incremental_summary("s-1", store) is None
        assert store.load_calls == []

    def test_delta_too_small(self, _fake_llm):
        store = FakeStore(summary=None, through_id=None, boundary_id=40,
                          rows=[(31, "user", "单条消息")])
        assert run_incremental_summary("s-1", store) is None

    def test_missing_facts_patched_after_llm(self, _fake_llm, monkeypatch):
        """LLM 摘要漏掉的事实由确定性补丁追加，零额外 API 调用。"""

        class _Lossy:
            content = "[用户目标]\n查退款"  # 故意漏掉所有实体
            response_metadata = {}

        monkeypatch.setattr(ac_mod, "_invoke_llm_with_timeout",
                            lambda prompt: _Lossy())
        store = FakeStore(summary=None, through_id=None, boundary_id=40,
                          rows=_DELTA_ROWS)
        outcome = run_incremental_summary("s-1", store)
        assert outcome is not None
        assert outcome.patched_fact_count > 0
        assert "ORD20260922001" in store.saved["summary"]
        assert "¥5000" in store.saved["summary"]


# ── prepare_llm_context 的 L5 触发链路 ──────────────────────────

@pytest.fixture()
def _l5_env(monkeypatch):
    """L5 触发环境：L2/L4 不裁剪、预算固定、可精确控制 ratio。"""
    monkeypatch.setattr(config, "CONTEXT_OUTPUT_RESERVE_TOKENS", 0)
    monkeypatch.setattr(config, "CONTEXT_SAFETY_RESERVE_TOKENS", 0)
    monkeypatch.setattr(config, "HISTORY_TOKEN_BUDGET", 10 ** 9)
    monkeypatch.setattr("backend.config.llm.LLM_CONTEXT_LENGTH", 4096)
    monkeypatch.setattr(config, "CONTEXT_L4_TRIGGER_RATIO", 2.0)  # 禁 L4
    monkeypatch.setattr("backend.core.request_context.get_current_session_id",
                        lambda: "sess-l5")
    return ContextBudgetManager()


def _outcome(summary_text: str) -> SummaryOutcome:
    return SummaryOutcome(
        summary=summary_text, through_id=99, token_count=10,
        delta_message_count=6, protected_fact_count=0, patched_fact_count=0)


class TestPrepareL5Trigger:
    def _msgs(self):
        return [SystemMessage(content="系统指令")] + _history(12)

    def _ratio(self, msgs, manager):
        u = manager.calculate_usage(messages=msgs)
        return u.used_tokens, u.usage_ratio

    def test_triggers_after_threshold_and_rebuilds(self, _l5_env, monkeypatch):
        m = _l5_env
        msgs = self._msgs()
        used, ratio = self._ratio(msgs, m)
        # 阈值卡在真实 ratio 之下 → 必触发；L5 目标重建后用量大降
        monkeypatch.setattr(config, "CONTEXT_L5_TRIGGER_RATIO", ratio * 0.95)

        calls: list[str] = []

        def _fake(session_id, store, extra_facts=None):
            calls.append(session_id)
            assert isinstance(store, SyncMemorySummaryStore)
            return _outcome("[用户目标]\n测试摘要，包含早期对话要点")

        monkeypatch.setattr(ac_mod, "run_incremental_summary", _fake)
        prepared = m.prepare_llm_context(messages=msgs)

        assert calls == ["sess-l5"]
        contents = [getattr(x, "content", "") for x in prepared.messages]
        # 角色安全（P0-2）：重建后 = 固定 policy SystemMessage + 数据 AIMessage
        from backend.context_budget.role_safety import (
            HISTORICAL_CONTEXT_POLICY_TEXT,
            HISTORICAL_TAG_OPEN,
        )
        assert any(c == HISTORICAL_CONTEXT_POLICY_TEXT for c in contents)
        assert any(c.startswith(HISTORICAL_TAG_OPEN) and "测试摘要" in c
                   for c in contents)
        assert prepared.usage.used_tokens < used
        assert not prepared.overflow
        # 最近 4 轮保留
        assert sum(isinstance(x, HumanMessage) for x in prepared.messages) == 4
        # 红线：prepare 不改调用方持有的原始列表
        assert len(msgs) == 25

    def test_below_threshold_not_triggered(self, _l5_env, monkeypatch):
        m = _l5_env
        msgs = self._msgs()
        used, ratio = self._ratio(msgs, m)
        monkeypatch.setattr(config, "CONTEXT_L5_TRIGGER_RATIO", min(0.99, ratio * 1.05))

        def _fail(session_id, store):
            raise AssertionError("低于阈值不得调用摘要")

        monkeypatch.setattr(ac_mod, "run_incremental_summary", _fail)
        prepared = m.prepare_llm_context(messages=msgs)
        assert prepared.usage.used_tokens == used

    def test_disabled_switch(self, _l5_env, monkeypatch):
        m = _l5_env
        msgs = self._msgs()
        used, ratio = self._ratio(msgs, m)
        monkeypatch.setattr(config, "CONTEXT_L5_TRIGGER_RATIO", ratio * 0.95)
        monkeypatch.setattr(config, "CONTEXT_L5_ENABLED", False)

        def _fail(session_id, store):
            raise AssertionError("开关关闭不得调用摘要")

        monkeypatch.setattr(ac_mod, "run_incremental_summary", _fail)
        prepared = m.prepare_llm_context(messages=msgs)
        assert prepared.usage.used_tokens == used

    def test_summary_failure_safe_fallback(self, _l5_env, monkeypatch):
        m = _l5_env
        msgs = self._msgs()
        used, ratio = self._ratio(msgs, m)
        monkeypatch.setattr(config, "CONTEXT_L5_TRIGGER_RATIO", ratio * 0.95)

        def _boom(session_id, store, extra_facts=None):
            return None  # 摘要失败

        monkeypatch.setattr(ac_mod, "run_incremental_summary", _boom)
        prepared = m.prepare_llm_context(messages=msgs)
        # 安全回退：消息原样（未重建）、无异常、不溢出
        assert not prepared.overflow
        assert not any(
            isinstance(x, SystemMessage)
            and getattr(x, "content", "").startswith(L2_SUMMARY_MARKER)
            for x in prepared.messages)
        assert prepared.usage.used_tokens == used

    def test_no_session_context_skips(self, _l5_env, monkeypatch):
        m = _l5_env
        msgs = self._msgs()
        used, ratio = self._ratio(msgs, m)
        monkeypatch.setattr(config, "CONTEXT_L5_TRIGGER_RATIO", ratio * 0.95)
        monkeypatch.setattr("backend.core.request_context.get_current_session_id",
                            lambda: "default")

        def _fail(session_id, store):
            raise AssertionError("无会话上下文不得调用摘要")

        monkeypatch.setattr(ac_mod, "run_incremental_summary", _fail)
        m.prepare_llm_context(messages=msgs)


class TestShouldAutoCompact:
    def test_threshold(self):
        from backend.context_budget.models import ContextUsage
        m = ContextBudgetManager()
        assert m.should_auto_compact(ContextUsage(
            used_tokens=100, input_budget=3072, remaining_tokens=2972,
            usage_ratio=0.89)) is False
        assert m.should_auto_compact(ContextUsage(
            used_tokens=100, input_budget=3072, remaining_tokens=72,
            usage_ratio=0.91)) is True
