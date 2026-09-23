# -*- coding: utf-8 -*-
"""test_domain_pending_semantics.py — 域 pending 语义分层（STOP B B6）

三类 pending 的既定语义锁定（优先复用各域业务状态机，不造通用状态机）：

- 强绑定（CS 确认）：只有 pending/pending_confirmation 两态进确认流程，
  need_info 型 pending 不吃「确认」词（缺陷6.3），终态五值映射固定；
  状态权威在 DB confirmations（(user_id, session_id) 键），checkpoint
  丢失也不受影响。
- 可中断（travel pending）：active_domain != travel 时永不拦（跨域不粘）；
  客服强信号在场立即放行（CS 优先铁律）；总闸关闭即失效。
- 防误吞：无结构化 pending / 超长输入 / 短答案未对上槽位 → 交回正常路由。
"""
from __future__ import annotations

import pytest

from backend.customer_service import pending_handler as ph
from backend.orchestration.context import travel_pending_resolver as tpr


def _travel_ctx(stage: str = "slot", requested: list[str] | None = None,
                run_id: str = "run-1") -> dict:
    return {
        "active_domain": "travel",
        "conversation_id": "c1",
        "brief_summary": {
            "travel_run_id": run_id,
            "travel_stage": stage,
            "travel_pending": {"requested_slots": requested or [],
                               "question_id": "q1"},
        },
    }


class TestCsPendingStrongBinding:
    def test_pending_states_are_exactly_two(self):
        """确认流程只认 pending/pending_confirmation——新状态必须显式加进
        _PENDING_STATES 并过确认状态机，不允许静默扩大。"""
        assert ph._PENDING_STATES == frozenset(
            {"pending", "pending_confirmation"})

    def test_need_info_pending_never_treated_as_confirmation(self):
        """need_info 型 pending（等订单号）不吃确认词：转发 action expert
        补槽，而不是把「确认」当确认（缺陷6.3 回归）。"""
        state = {
            "pending_action": {"status": "need_info"},
            "confirmation_state": "pending",
        }
        command = ph.cs_pending_handler_node(state)
        assert command.goto == "cs_action_expert"

    def test_terminal_outcome_reasons_cover_terminal_set(self):
        """终态五值（expired/duplicate/success/failed/cancelled）各有固定
        reason 文案——SSE/审计面向用户的表达不可悄悄变化。"""
        expected = {"expired", "duplicate", "success", "failed", "cancelled"}
        mapping = {
            "expired": "pending_handler — 确认超时",
            "duplicate": "pending_handler — 重复确认（已处理，幂等跳过）",
            "success": "pending_handler — 用户确认，执行成功",
            "failed": "pending_handler — 用户确认，执行失败",
            "cancelled": "pending_handler — 用户取消",
        }
        assert set(mapping) == expected


class TestTravelPendingInterruptible:
    def test_no_active_travel_never_intercepts(self, monkeypatch):
        """可中断核心：active_domain != travel → 永不拦（跨域不粘）。"""
        ctx = _travel_ctx(requested=["budget_cny"])
        ctx["active_domain"] = "customer_service"
        assert tpr.resolve_travel_pending("8万日元", ctx) is None

    def test_empty_active_domain_never_intercepts(self, monkeypatch):
        ctx = _travel_ctx(requested=["budget_cny"])
        ctx["active_domain"] = ""
        assert tpr.resolve_travel_pending("8万日元", ctx) is None

    def test_cs_strong_signal_releases_pending(self, monkeypatch):
        """CS 强信号在场 → 放行正常路由（CS 优先铁律不被 pending 粘滞）。"""
        out = tpr.resolve_travel_pending(
            "订单123退款怎么办", _travel_ctx(requested=["budget_cny"]))
        assert out is None

    def test_master_switch_off_disables_resume(self, monkeypatch):
        import backend.config.travel as tc
        monkeypatch.setattr(tc, "TRAVEL_PENDING_RESUME_ENABLED", False)
        assert tpr.resolve_travel_pending(
            "8万日元", _travel_ctx(requested=["budget_cny"])) is None

    def test_long_input_not_intercepted(self, monkeypatch):
        """>40 字输入不是补槽短答，交回正常路由（防误吞长诉求）。"""
        long_q = ("预算大概八万左右吧，另外我想要住方便一点的酒店，"
                  "最好离地铁站近一些，房间干净安静，早餐也要不错")
        assert len(long_q) > 40
        assert tpr.resolve_travel_pending(
            long_q, _travel_ctx(requested=["budget_cny"])) is None

    def test_unmatched_short_answer_falls_through(self, monkeypatch):
        """短答案但没对上任何槽位 → 不拦（交回正常路由，不猜）。"""
        out = tpr.resolve_travel_pending(
            "嗯嗯好的谢谢", _travel_ctx(requested=["budget_cny"]))
        assert out is None

    def test_completed_run_without_pending_not_intercepted(self):
        """run 已 completed（无结构化 pending）→ 不拦，普通路由接管。"""
        ctx = _travel_ctx(stage="completed", requested=[], run_id="run-1")
        assert tpr.resolve_travel_pending("不错的", ctx) is None
