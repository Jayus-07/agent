"""生产收口 B4 — PinnedContext 业务接线测试（2026-09-23）

覆盖（任务 §九 验收口径）：
  - 内容锚定 pin：业务实体值 → L2 裁剪永不丢包含该实体的历史消息
  - 请求级生命周期：order A → order B supersede；新请求（空上下文）expiry
  - manager.prepare_llm_context(pins=...) 显式消费 + _trim_semantic 请求级
    自动回退（proxy 无需改动的接线面）
  - L5 extra_facts：request_pin_values 经 _run_l5 传入 run_incremental_summary
  - 无 pin 时行为与原版完全一致（零回归）
"""
import contextvars

import pytest
from langchain_core.messages import HumanMessage, SystemMessage

import backend.config as config
import backend.context_budget.auto_compact as ac_mod
import backend.context_budget.pin as pin_mod
from backend.context_budget.manager import ContextBudgetManager
from backend.context_budget.pin import (
    PIN_CONFIRMATION,
    PIN_ENTITY,
    PinnedContext,
    pins_from_request,
    register_request_pin,
    request_pin_values,
)


@pytest.fixture(autouse=True)
def _cfg(monkeypatch):
    monkeypatch.setattr(config, "CONTEXT_BUDGET_ENABLED", True)
    monkeypatch.setattr(config, "HISTORY_TOKEN_BUDGET", 2048)
    monkeypatch.setattr(config, "PREVIOUS_OUTPUTS_MAX_TOKENS", 1024)
    monkeypatch.setattr(config, "CONTEXT_OUTPUT_RESERVE_TOKENS", 0)
    monkeypatch.setattr(config, "CONTEXT_SAFETY_RESERVE_TOKENS", 0)


@pytest.fixture(autouse=True)
def _reset_request_pins():
    """请求级 pin 是线程级 ContextVar：每个用例从空注册开始，防串扰。"""
    token = pin_mod._REQUEST_PINS.set(None)
    yield
    pin_mod._REQUEST_PINS.reset(token)


class TestContentAnchoredPins:
    def test_entity_message_survives_trim(self):
        """「订单 20260922001 怎么还没发货」在 L2 裁剪后仍保留。"""
        order_msg = HumanMessage(content="订单 20260922001 怎么还没发货？")
        filler = [HumanMessage(content=f"闲聊{i}" * 30) for i in range(40)]
        msgs = [SystemMessage(content="系统提示"), order_msg, *filler,
                HumanMessage(content="那就给我申请退款吧")]
        pins = PinnedContext().mark_content_match("20260922001", PIN_ENTITY)

        m = ContextBudgetManager()
        prepared = m.prepare_llm_context(
            messages=msgs, extra_reserved_tokens=0, pins=pins)
        kept_contents = [x.content for x in prepared.messages]
        assert any("20260922001" in c for c in kept_contents), \
            "被 pin 的业务实体消息不得被裁剪"

    def test_short_value_ignored(self):
        """短于 4 字符的锚定值直接忽略（防变相全量 pin）。"""
        pins = PinnedContext().mark_content_match("单", PIN_ENTITY)
        assert pins._content == []

    def test_recent_k_cap(self):
        """单锚定值最多 pin 最近 K 条命中（防御异常值全量命中）。"""
        hits = [HumanMessage(content="提到 20260922001") for _ in range(10)]
        pins = PinnedContext().mark_content_match("20260922001", PIN_ENTITY)
        matched = pins._resolve_content(hits)
        assert len(matched) == PinnedContext._MAX_MATCHES_PER_VALUE
        assert sorted(matched) == [7, 8, 9]  # 且是最近的 K 条


class TestRequestLevelLifecycle:
    def test_register_and_read(self):
        register_request_pin("20260922001", PIN_CONFIRMATION)
        register_request_pin("ORD-777", PIN_ENTITY)
        values = request_pin_values()
        assert ("20260922001", PIN_CONFIRMATION) in values
        assert ("ORD-777", PIN_ENTITY) in values
        assert pins_from_request() is not None

    def test_register_is_idempotent(self):
        register_request_pin("20260922001", PIN_CONFIRMATION)
        register_request_pin("20260922001", PIN_CONFIRMATION)
        assert request_pin_values() == (("20260922001", PIN_CONFIRMATION),)

    def test_supersede_order_a_to_order_b(self):
        """order A → order B：新请求（全新 Context）从空注册开始，只有 B。"""
        register_request_pin("20260922001", PIN_ENTITY)
        assert any(v == "20260922001" for v, _ in request_pin_values())

        def _new_request():
            # 新请求 = 新上下文：上一轮的 pin 不可见（天然 expiry）
            assert request_pin_values() == ()
            register_request_pin("20260923002", PIN_ENTITY)
            return request_pin_values()

        new_values = contextvars.Context().run(_new_request)
        assert ("20260923002", PIN_ENTITY) in new_values
        assert all(v != "20260922001" for v, _ in new_values)

    def test_fresh_context_expiry(self):
        """未注册任何 pin → pins_from_request() 返回 None（零开销路径）。"""
        assert request_pin_values() == ()
        assert pins_from_request() is None

    def test_manager_auto_consumes_request_pins(self):
        """proxy 不传 pins 时，_trim_semantic 自动消费请求级注册。"""
        register_request_pin("20260922001", PIN_ENTITY)
        order_msg = HumanMessage(content="订单 20260922001 发货了吗")
        filler = [HumanMessage(content="无实体内容" * 10) for _ in range(80)]
        msgs = [order_msg, *filler, HumanMessage(content="现在怎么样了")]
        m = ContextBudgetManager()
        prepared = m.prepare_llm_context(messages=msgs)
        assert any("20260922001" in x.content for x in prepared.messages), \
            "请求级注册的实体消息必须被自动 pin 保留"


class TestManagerPinsParam:
    def test_explicit_pins_param_consumed(self):
        """显式 pins 与自动 pin 并集生效。"""
        old = HumanMessage(content="售后单 AS-998877 处理中")
        filler = [HumanMessage(content="无实体内容" * 35) for _ in range(30)]
        msgs = [old, *filler, HumanMessage(content="最新问题")]
        pins = PinnedContext().mark_content_match("AS-998877", PIN_CONFIRMATION)
        m = ContextBudgetManager()
        prepared = m.prepare_llm_context(messages=msgs, pins=pins)
        assert any("AS-998877" in x.content for x in prepared.messages)

    def test_no_pins_behavior_unchanged(self):
        """无 pin 注册 + 不传 pins → 自动 pin 语义不变（零回归）。"""
        msgs = [SystemMessage(content="s"),
                HumanMessage(content="历史" * 50),
                HumanMessage(content="当前问题")]
        m = ContextBudgetManager()
        prepared = m.prepare_llm_context(messages=msgs)
        assert type(prepared.messages[0]).__name__ == "SystemMessage"
        assert prepared.messages[-1].content == "当前问题"


class TestL5ExtraFactsWiring:
    def test_run_l5_passes_request_pins_as_extra_facts(self, monkeypatch):
        """_run_l5 把请求级 pin 值转为 (kind, value) 传给增量摘要。

        manager._run_l5 内部 `from backend.context_budget.auto_compact import
        run_incremental_summary` 是调用期解析 → 必须打补丁在 ac_mod 上。
        """
        captured: dict = {}

        def _fake_summary(session_id, store, extra_facts=None):
            captured["extra_facts"] = list(extra_facts or [])
            return None  # None = 安全回退路径，不影响断言

        monkeypatch.setattr(ac_mod, "run_incremental_summary", _fake_summary)
        register_request_pin("20260922001", PIN_CONFIRMATION)
        m = ContextBudgetManager()
        msgs = [HumanMessage(content="订单 20260922001" * 100),
                HumanMessage(content="当前问题")]
        m._run_l5(msgs, 9999, 1000, "s-extra")
        assert (PIN_CONFIRMATION, "20260922001") in captured["extra_facts"]

    def test_run_l5_without_pins_passes_empty(self, monkeypatch):
        """无请求级 pin → extra_facts 为空列表（行为与接线前一致）。"""
        captured: dict = {}

        def _fake_summary(session_id, store, extra_facts=None):
            captured["extra_facts"] = list(extra_facts or [])
            return None

        monkeypatch.setattr(ac_mod, "run_incremental_summary", _fake_summary)
        m = ContextBudgetManager()
        msgs = [HumanMessage(content="普通对话" * 100),
                HumanMessage(content="当前问题")]
        m._run_l5(msgs, 9999, 1000, "s-no-extra")
        assert captured["extra_facts"] == []

    def test_auto_compact_async_passthrough(self, monkeypatch):
        """run_auto_compact_async 的 extra_facts 形参透传到同步实现。"""
        import asyncio

        captured: dict = {}

        def _fake_sync(session_id, store, extra_facts=None):
            captured["extra_facts"] = list(extra_facts or [])
            return None

        monkeypatch.setattr(ac_mod, "run_incremental_summary", _fake_sync)
        from backend.context_budget.auto_compact import run_auto_compact_async
        asyncio.run(run_auto_compact_async(
            "s-async", extra_facts=[("pin", "V1")]))
        assert captured["extra_facts"] == [("pin", "V1")]
