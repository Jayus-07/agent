"""tests/customer_service/test_pending_handler.py — Phase 5: Pending Handler 测试

验证:
  1. 无 pending_action → 直通 cs_supervisor
  2. pending 已过期 → 清理 + 超时回复 → cs_reporter
  3. 用户确认 → 执行成功 → cs_reporter
  4. 用户确认 → 执行失败 → cs_reporter
  5. 用户取消 → 清理 + 取消回复 → cs_reporter
  6. 用户意图不明确 → 重新追问 → cs_reporter
  7. confirmation_state 非 pending → 直通 cs_supervisor
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from langgraph.types import Command

from backend.customer_service.pending_handler import cs_pending_handler_node


def _pending_action(**overrides) -> dict:
    base = {
        "action_type": "refund_request",
        "target_type": "order",
        "target_id": "ORD-001",
        "risk_level": "high",
        "proposal_text": "确认退款 ¥99.00 到订单 ORD-001？",
        "confirmation_state": "pending",
        "expires_at": "2099-12-31T23:59:59+00:00",
    }
    base.update(overrides)
    return base


def _state(**overrides) -> dict:
    base = {
        "user_id": "u1",
        "tenant_id": "tenant-1",
        "session_id": "s1",
        "conversation_id": "c1",
        "user_message": "确认",
        "cs_route": {
            "domain": "AFTER_SALES",
            "route_path": "business_action",
            "intent": "as_refund",
            "confidence": 0.9,
        },
        "pending_action": None,
        "confirmation_state": "not_required",
    }
    base.update(overrides)
    return base


@pytest.fixture(autouse=True)
def _no_active_handoff(monkeypatch):
    """Pending 单测默认没有 PG 活跃工单；需要时由用例覆盖该权威读取。"""
    monkeypatch.setattr(
        "backend.customer_service.handoff.lifecycle.load_active_handoff_sync",
        lambda *_args, **_kwargs: None,
    )


class TestPendingHandlerPassthrough:
    def test_no_pending_action_routes_to_supervisor(self):
        cmd = cs_pending_handler_node(_state())
        assert isinstance(cmd, Command)
        assert cmd.goto == "cs_supervisor"

    def test_non_pending_confirmation_state_routes_to_supervisor(self):
        cmd = cs_pending_handler_node(_state(
            pending_action=_pending_action(),
            confirmation_state="success",
        ))
        assert cmd.goto == "cs_supervisor"

    def test_empty_pending_action_routes_to_supervisor(self):
        cmd = cs_pending_handler_node(_state(pending_action={}))
        assert cmd.goto == "cs_supervisor"


class TestPendingHandlerExpiry:
    @patch("backend.customer_service.confirmation.is_expired", return_value=True)
    @patch("backend.customer_service.confirmation_store.get_confirmation_store")
    @patch("backend.observability.metrics.record_cs_confirmation")
    def test_expired_pending_routes_to_reporter(
        self, mock_metric, mock_store_fn, mock_expired,
    ):
        mock_store = MagicMock()
        mock_store_fn.return_value = mock_store

        cmd = cs_pending_handler_node(_state(
            pending_action=_pending_action(),
            confirmation_state="pending",
            user_message="退款一般多久到账？",
            cs_route={
                "domain": "KNOWLEDGE", "route_path": "knowledge_query",
                "intent": "k_faq", "confidence": 0.9,
            },
        ))

        assert cmd.goto == "cs_reporter"
        assert cmd.update["confirmation_state"] == "expired"
        # P1 重构（2026-09-17）：过期走独立终态 expired（此前被 clear
        # 一律写成 cancelled，审计口径失真）
        mock_store.clear.assert_called_once_with("u1", "s1", final_state="expired")


class TestPendingHandlerConfirm:
    @patch("backend.customer_service.confirmation.is_expired", return_value=False)
    @patch("backend.customer_service.experts.action._simulate_execute")
    @patch("backend.customer_service.confirmation_store.get_confirmation_store")
    @patch("backend.observability.metrics.record_cs_confirmation")
    @patch("backend.observability.metrics.record_cs_action")
    def test_confirm_success(
        self, mock_action, mock_confirm, mock_store_fn, mock_execute, mock_expired,
    ):
        mock_store = MagicMock()
        mock_store_fn.return_value = mock_store

        mock_record = MagicMock()
        mock_record.to_dict.return_value = {"action_id": "ACT-001"}
        mock_record.action_id = "ACT-001"
        mock_execute.return_value = mock_record

        cmd = cs_pending_handler_node(_state(
            pending_action=_pending_action(),
            confirmation_state="pending",
            user_message="确认",
        ))

        assert cmd.goto == "cs_reporter"
        assert cmd.update["confirmation_state"] == "success"
        assert "cs_action_result" in cmd.update
        mock_store.clear.assert_called_once()

    @patch("backend.customer_service.confirmation.is_expired", return_value=False)
    @patch("backend.customer_service.experts.action._simulate_execute")
    @patch("backend.customer_service.confirmation_store.get_confirmation_store")
    @patch("backend.observability.metrics.record_cs_confirmation")
    @patch("backend.observability.metrics.record_cs_action")
    def test_confirm_failure(
        self, mock_action, mock_confirm, mock_store_fn, mock_execute, mock_expired,
    ):
        mock_store = MagicMock()
        mock_store_fn.return_value = mock_store
        mock_execute.side_effect = RuntimeError("DB down")

        cmd = cs_pending_handler_node(_state(
            pending_action=_pending_action(),
            confirmation_state="pending",
            user_message="确定",
        ))

        assert cmd.goto == "cs_reporter"
        assert cmd.update["confirmation_state"] == "failed"
        mock_store.clear.assert_called_once()


class TestPendingHandlerCancel:
    @patch("backend.customer_service.confirmation.is_expired", return_value=False)
    @patch("backend.customer_service.confirmation_store.get_confirmation_store")
    @patch("backend.observability.metrics.record_cs_confirmation")
    def test_cancel_routes_to_reporter(self, mock_metric, mock_store_fn, mock_expired):
        mock_store = MagicMock()
        mock_store_fn.return_value = mock_store

        cmd = cs_pending_handler_node(_state(
            pending_action=_pending_action(),
            confirmation_state="pending",
            user_message="取消",
        ))

        assert cmd.goto == "cs_reporter"
        assert cmd.update["confirmation_state"] == "cancelled"
        mock_store.cancel_pending.assert_called_once_with(
            "u1", "s1", tenant_id="tenant-1", proposal_id="", expected_version=1,
            client_action_id=None,
        )


class TestPendingHandlerUnclear:
    @patch("backend.customer_service.confirmation.is_expired", return_value=False)
    def test_unclear_intent_reprompts(self, mock_expired):
        cmd = cs_pending_handler_node(_state(
            pending_action=_pending_action(),
            confirmation_state="pending",
            user_message="今天天气怎么样",
        ))

        assert cmd.goto == "cs_reporter"
        draft = cmd.update["last_expert_result"]["response_draft"]
        assert "确认" in draft
        assert "取消" in draft


# ── need_info 阶段取消短路（设计方案 场景4，迁移 B6 实机验证补齐）──
# 此前 need_info 无取消消费者，「算了，取消」被当答非所问继续追问；
# 在 action expert 内处理则取消后消息会回 supervisor 再路由，低置信时
# 被兜底话术覆盖取消确认（B6 实机复现），故与 proposal 阶段取消同层
# 就地短路。


def _need_info_pending(**overrides) -> dict:
    base = {
        "action_id": "act-1",
        "action_type": "return_request",
        "intent": "as_return",
        "status": "need_info",
        "missing_slots": ["order_id"],
        "collected_slots": {},
        "confirmation_state": "pending_confirmation",
        "retry_count": 0,
    }
    base.update(overrides)
    return base


class TestNeedInfoCancel:

    @patch("backend.customer_service.confirmation.is_expired", return_value=False)
    def test_cancel_short_circuits_to_reporter(self, _mock_expired):
        """「算了，取消」→ 释放 pending + 取消确认直达 reporter，不进
        supervisor 再路由（finish 决策短路）。"""
        store = MagicMock()
        with patch(
            "backend.customer_service.confirmation_store.get_confirmation_store",
            return_value=store,
        ):
            cmd = cs_pending_handler_node(_state(
                pending_action=_need_info_pending(),
                confirmation_state="pending_confirmation",
                user_message="算了，取消",
            ))

        assert cmd.goto == "cs_reporter"
        assert cmd.update["confirmation_state"] == "not_required"
        assert cmd.update["pending_action"]["status"] == "cancelled"
        assert "取消" in cmd.update["last_expert_result"]["response_draft"]
        assert cmd.update["supervisor_decision"]["next_action"] == "finish"
        store.clear.assert_called_once_with("u1", "s1", final_state="cancelled")
        assert cmd.update["cs_audit_entries"][0]["action_type"] == "pending_cancelled"

    @patch("backend.customer_service.confirmation.is_expired", return_value=False)
    def test_question_mark_not_cancelled_asks_for_clarity(self, _mock_expired):
        """疑问句不算取消，也不能当作缺槽答案或确认。"""
        with patch(
            "backend.customer_service.confirmation_store.get_confirmation_store",
        ), patch(
            "backend.customer_service.confirmation.is_expired", return_value=False,
        ):
            cmd = cs_pending_handler_node(_state(
                pending_action=_need_info_pending(),
                confirmation_state="pending_confirmation",
                user_message="可以取消吗",
            ))

        assert cmd.goto == "cs_reporter"
        assert cmd.update["pending_turn_decision"] == "AMBIGUOUS"

    @patch("backend.customer_service.confirmation.is_expired", return_value=False)
    def test_readonly_policy_question_routes_to_supervisor(self, _mock_expired):
        """need_info 期间的知识问句可以只读回答，不消费缺槽 pending。"""
        with patch(
            "backend.customer_service.confirmation_store.get_confirmation_store",
        ), patch(
            "backend.customer_service.confirmation.is_expired", return_value=False,
        ):
            cmd = cs_pending_handler_node(_state(
                pending_action=_need_info_pending(),
                confirmation_state="pending_confirmation",
                user_message="帮我查一下保修政策",
                cs_route={
                    "domain": "KNOWLEDGE", "route_path": "knowledge_query",
                    "intent": "k_faq", "confidence": 0.9,
                },
            ))

        assert cmd.goto == "cs_supervisor"
        assert cmd.update["pending_turn_decision"] == "READ_ONLY_QUERY"


class TestNeedInfoHandoffEscape:
    """转人工逃生（2026-10-05 浏览器走查发现）：need_info 追问期显式转人工
    必须释放 pending 回 supervisor（紧急通道优先于补槽追问）。"""

    def test_need_info_handoff_escape(self, monkeypatch):
        from backend.customer_service import pending_handler as ph
        from backend.customer_service.confirmation_store import (
            get_confirmation_store,
        )

        cleared = {}

        def _fake_clear(user_id, session_id, final_state="cancelled"):
            cleared["key"] = (user_id, session_id, final_state)

        monkeypatch.setattr(get_confirmation_store(), "clear", _fake_clear)

        state = _state(
            user_message="转人工",
            user_id="u-esc",
            session_id="s-esc",
            conversation_id="c-esc",
            confirmation_state="",
            pending_action=_need_info_pending(),
        )
        cmd = ph.cs_pending_handler_node(state)
        assert cmd.goto == "cs_supervisor", f"应回 supervisor 重分诊: {cmd.goto}"
        assert cleared.get("key") == ("u-esc", "s-esc", "cancelled"),             f"need_info pending 未释放: {cleared}"


class TestPendingTurnClassification:
    def test_need_info_faq_accepts_enum_route_path_from_router_model_dump(self):
        """生产路由的 model_dump 保留 StrEnum，PendingHandler 仍须识别只读问句。"""
        from backend.customer_service.router.types import CSRoutePath

        pending = _need_info_pending()
        cmd = cs_pending_handler_node(_state(
            user_message="退款一般多久到账？",
            cs_route={
                "domain": "KNOWLEDGE",
                "route_path": CSRoutePath.KNOWLEDGE_QUERY,
                "intent": "k_faq",
                "confidence": 0.9,
            },
            pending_action=pending,
            confirmation_state="pending",
        ))

        assert cmd.goto == "cs_supervisor"
        assert cmd.update["pending_turn_decision"] == "READ_ONLY_QUERY"
        assert cmd.update["pending_action"] == pending

    def test_readonly_question_keeps_proposal_and_routes_supervisor(self):
        """Pending proposal 期间的常见问题允许单轮只读分流，proposal 不变。"""
        pending = _pending_action(
            action_id="act-readonly", proposal_id="proposal-v4", version=4,
        )
        state = _state(
            user_message="退款一般多久到账？",
            cs_route={
                "domain": "KNOWLEDGE", "route_path": "knowledge_query",
                "intent": "k_faq", "confidence": 0.9,
            },
            pending_action=pending,
            confirmation_state="pending",
        )

        cmd = cs_pending_handler_node(state)

        assert cmd.goto == "cs_supervisor"
        assert cmd.update["pending_turn_decision"] == "READ_ONLY_QUERY"
        assert cmd.update["pending_action"] == pending
        assert cmd.update["pending_action"]["proposal_id"] == "proposal-v4"
        assert cmd.update["pending_action"]["version"] == 4

    def test_logistics_read_routes_to_query_expert_path(self):
        pending = _pending_action(action_id="act-logistics", version=3)
        cmd = cs_pending_handler_node(_state(
            user_message="查一下我的订单物流",
            cs_route={
                "domain": "TRANSACTION", "route_path": "business_query",
                "intent": "t_logistics", "confidence": 0.9,
            },
            pending_action=pending,
            confirmation_state="pending",
        ))

        assert cmd.goto == "cs_supervisor"
        assert cmd.update["pending_turn_decision"] == "READ_ONLY_QUERY"
        assert cmd.update["pending_action"] == pending

    @patch("backend.customer_service.confirmation_flow.process_confirmation")
    def test_question_about_return_is_never_confirmation(self, process, monkeypatch):
        pending = _pending_action()
        state = _state(
            user_message="可以退吗？",
            pending_action=pending,
            confirmation_state="pending",
        )

        cmd = cs_pending_handler_node(state)

        process.assert_not_called()
        assert cmd.goto == "cs_reporter"
        assert cmd.update["pending_turn_decision"] == "AMBIGUOUS"
        assert cmd.update.get("pending_action", pending) == pending
        assert "确认" in cmd.update["last_expert_result"]["response_draft"]

    def test_new_write_request_conflicts_without_replacing_pending(self):
        pending = _pending_action(action_id="act-old", proposal_id="proposal-old", version=2)
        state = _state(
            user_message="帮我申请退款",
            pending_action=pending,
            confirmation_state="pending",
        )

        cmd = cs_pending_handler_node(state)

        assert cmd.goto == "cs_reporter"
        assert cmd.update["pending_turn_decision"] == "NEW_WRITE_CONFLICT"
        assert cmd.update["pending_action"] == pending
        assert "取消" in cmd.update["last_expert_result"]["response_draft"]

    @patch("backend.customer_service.confirmation.is_expired", return_value=False)
    @patch("backend.customer_service.confirmation_flow.process_confirmation")
    def test_confirm_passes_tenant_and_proposal_version(
        self, process, _expired,
    ):
        from types import SimpleNamespace

        process.return_value = SimpleNamespace(
            kind="duplicate", confirmation_state="confirmed",
            answer="该操作已处理。", action_type="refund_request",
            action_result=None, audit_entry=None, pending_action=None,
        )
        pending = _pending_action(
            action_id="act-4", proposal_id="proposal-4", version=4,
        )

        cs_pending_handler_node(_state(
            user_message="确认", pending_action=pending,
            confirmation_state="pending",
        ))

        process.assert_called_once_with(
            pending, "确认", "u1", "s1", tenant_id="tenant-1",
            proposal_id="proposal-4", expected_version=4,
        )

    def test_need_info_faq_routes_knowledge_without_changing_slot_pending(self):
        pending = _need_info_pending()
        state = _state(
            user_message="退款一般多久到账？",
            cs_route={
                "domain": "KNOWLEDGE", "route_path": "knowledge_query",
                "intent": "k_faq", "confidence": 0.9,
            },
            pending_action=pending,
            confirmation_state="pending_confirmation",
        )

        cmd = cs_pending_handler_node(state)

        assert cmd.goto == "cs_supervisor"
        assert cmd.update["pending_turn_decision"] == "READ_ONLY_QUERY"
        assert cmd.update["pending_action"] == pending

    def test_need_info_order_choice_continues_slot_fill(self):
        pending = _need_info_pending()
        state = _state(
            user_message="第二个订单",
            cs_route={
                "domain": "AFTER_SALES", "route_path": "business_action",
                "intent": "as_return", "confidence": 0.9,
            },
            pending_action=pending,
            confirmation_state="pending_confirmation",
        )

        cmd = cs_pending_handler_node(state)

        assert cmd.goto == "cs_action_expert"
        assert cmd.update["pending_turn_slot_fill"] is True
        assert cmd.update["pending_action"] == pending

    @pytest.mark.parametrize("handoff_state", ["waiting_human", "human_active"])
    @patch("backend.customer_service.confirmation_flow.process_confirmation")
    @patch("backend.customer_service.experts.action.action_expert_node")
    def test_handoff_state_blocks_pending_execution(
        self, action_node, process, monkeypatch, handoff_state,
    ):
        from backend.customer_service.handoff import lifecycle

        monkeypatch.setattr(
            lifecycle,
            "load_active_handoff_sync",
            lambda *_args, **_kwargs: {"handoff_state": handoff_state},
        )
        pending = _pending_action()
        cmd = cs_pending_handler_node(_state(
            user_message="确认",
            pending_action=pending,
            confirmation_state="pending",
        ))

        process.assert_not_called()
        action_node.assert_not_called()
        assert cmd.goto == "cs_reporter"
        assert cmd.update["handoff_state"] == handoff_state
        assert cmd.update["pending_action"] == pending
        assert cmd.update["supervisor_decision"]["direct_reply"]

    @patch("backend.customer_service.confirmation_flow.process_confirmation")
    def test_handoff_authoritative_read_error_fails_closed(self, process, monkeypatch):
        from backend.customer_service.handoff import lifecycle

        def _fail(*_args, **_kwargs):
            raise RuntimeError("database unavailable")

        monkeypatch.setattr(lifecycle, "load_active_handoff_sync", _fail)
        pending = _pending_action()
        cmd = cs_pending_handler_node(_state(
            user_message="确认", pending_action=pending,
            confirmation_state="pending",
        ))

        process.assert_not_called()
        assert cmd.goto == "cs_reporter"
        assert cmd.update["pending_action"] == pending
        assert "暂时无法核实" in cmd.update["last_expert_result"]["response_draft"]
        assert cmd.update["supervisor_decision"]["direct_reply"]

    def test_handoff_read_is_tenant_and_conversation_scoped(self, monkeypatch):
        from backend.customer_service.handoff import lifecycle

        calls = []
        monkeypatch.setattr(
            lifecycle,
            "load_active_handoff_sync",
            lambda tenant, conversation, **kwargs: calls.append(
                (tenant, conversation, kwargs),
            ) or None,
        )

        cmd = cs_pending_handler_node(_state(
            pending_action=_pending_action(), confirmation_state="pending",
            user_message="退款一般多久到账？",
            cs_route={"route_path": "knowledge_query", "domain": "KNOWLEDGE"},
        ))

        assert cmd.goto == "cs_supervisor"
        assert calls == [("tenant-1", "c1", {"raise_on_error": True})]

    def test_need_info_normal_slot_fill_unchanged(self, monkeypatch):
        """回归：非转人工的补槽消息照旧转发 action expert（不误放行）。"""
        from backend.customer_service import pending_handler as ph

        cleared = []
        from backend.customer_service.confirmation_store import (
            get_confirmation_store,
        )
        monkeypatch.setattr(
            get_confirmation_store(), "clear",
            lambda *a, **kw: cleared.append(1))

        state = _state(
            user_message="订单 MO-63934327",
            user_id="u-n",
            session_id="s-n",
            confirmation_state="pending_confirmation",
            pending_action=_need_info_pending(),
        )
        cmd = ph.cs_pending_handler_node(state)
        assert cmd.goto == "cs_action_expert", f"补槽应转发 action: {cmd.goto}"
        assert cmd.update["pending_turn_slot_fill"] is True
        assert not cleared, "非转人工不应释放 pending"
