"""确认动作幂等测试（P1 重构，audit-report §P0-2/§P0-3）

覆盖任务书 Error/Edge Path：
  - 同一动作重复确认 → 只执行一次（原子认领闸门）
  - 认领失败（已被处理）→ duplicate 回复，不重复执行
  - 并发认领（DB 条件 UPDATE 只有一方成功）→ 单执行
  - DB 不可用（非严格模式）→ L1 进程内认领兜底 + 告警
  - 过期终态 → expired（非 cancelled）
  - 疑问句不表态（"可以取消吗" → NONE，不误取消）
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from backend.customer_service.confirmation_flow import (
    ConfirmationOutcome,
    process_confirmation,
)
from backend.customer_service.confirmation import ConfirmationIntent


def _pending_action(**overrides) -> dict:
    base = {
        "action_id": "act-001",
        "action_type": "refund_request",
        "target_type": "order",
        "target_id": "order-9",
        "risk_level": "high",
        "proposal_text": "退款申请 proposal",
        "confirmation_state": "pending",
        "created_at": "2099-01-01T00:00:00+00:00",
        "expires_at": "2099-01-02T00:00:00+00:00",
        "retry_count": 0,
    }
    base.update(overrides)
    return base


def _make_store(claim_result):
    store = MagicMock()
    store.claim_for_execution.return_value = claim_result
    return store


class TestDuplicateConfirm:
    @patch("backend.customer_service.experts.action._simulate_execute")
    @patch("backend.customer_service.confirmation_store.get_confirmation_store")
    def test_duplicate_confirm_never_reexecutes(self, mock_store_fn, mock_exec):
        """认领返回 None（已被处理）→ duplicate，绝不触发执行。"""
        mock_store_fn.return_value = _make_store(claim_result=None)

        outcome = process_confirmation(
            _pending_action(), "确认", "u1", "s1",
        )

        assert outcome.kind == "duplicate"
        assert "已处理" in outcome.answer
        assert outcome.confirmation_state == "confirmed"
        assert outcome.audit_entry is not None
        assert outcome.audit_entry["result"] == "denied"
        mock_exec.assert_not_called()

    @patch("backend.customer_service.experts.action._simulate_execute")
    @patch("backend.customer_service.confirmation_store.get_confirmation_store")
    def test_first_confirm_executes_and_finalizes(self, mock_store_fn, mock_exec):
        """认领成功 → 执行一次 → 终态 success（final_state 写库）。"""
        store = _make_store(claim_result="act-001")
        mock_store_fn.return_value = store
        mock_exec.return_value = MagicMock(
            to_dict=lambda: {"action_id": "act-001"},
            action_id="act-001",
        )

        outcome = process_confirmation(
            _pending_action(), "确认", "u1", "s1",
        )

        assert outcome.kind == "success"
        assert outcome.action_result["status"] == "success"
        mock_exec.assert_called_once()
        # 终态写 success（而非旧实现一律 cancelled）
        store.clear.assert_called_once_with("u1", "s1", final_state="success")


class TestConcurrentClaim:
    def test_repo_claim_pending_conditional_update(self):
        """repo.claim_pending 生成带 state='pending' 前置条件的 UPDATE。"""
        import asyncio
        from unittest.mock import AsyncMock, MagicMock

        from sqlalchemy.dialects import postgresql

        from backend.customer_service.repository.confirmation_repo import (
            ConfirmationRepository,
        )

        repo = ConfirmationRepository(MagicMock())
        captured = {}

        def _capture(stmt, *a, **kw):
            captured["stmt"] = stmt
            m = MagicMock()
            m.scalars.return_value.all.return_value = ["act-001"]
            return m

        # claim_pending 是 async 方法：session.execute 必须 awaitable（AsyncMock）
        repo._s = AsyncMock()
        repo._s.execute.side_effect = _capture

        result = asyncio.run(repo.claim_pending("u1", "s1"))

        assert result == "act-001"
        compiled_stmt = captured["stmt"].compile(dialect=postgresql.dialect())
        sql_text = str(compiled_stmt)
        # 前置条件在 WHERE 里：只有 pending 行可被认领（幂等闸门）
        # SQLAlchemy 将字面量参数化（%(state_1)s），断言编译参数取值
        assert "confirmations.state = %(state_1)s" in sql_text
        assert compiled_stmt.params.get("state_1") == "pending"
        assert compiled_stmt.params.get("state") == "confirmed"

    def test_claim_gate_in_flow(self):
        """流程入口调用 store.claim_for_execution（闸门存在性）。"""
        import inspect

        from backend.customer_service import confirmation_flow

        src = inspect.getsource(confirmation_flow._handle_confirm)
        assert "claim_for_execution" in src


class TestDbUnavailableClaim:
    @patch("backend.customer_service.confirmation_flow.process_confirmation")
    def test_store_claim_falls_back_to_l1(self, _):
        """DB 认领失败（非严格模式）→ L1 进程内认领兜底。"""
        from backend.customer_service.confirmation_store import ConfirmationStore

        store = ConfirmationStore()
        pending = _pending_action()
        store._data[("u1", "s1")] = pending

        with patch.object(
            store, "_db_claim", side_effect=RuntimeError("DB down"),
        ):
            claimed = store.claim_for_execution("u1", "s1")

        assert claimed == "act-001"
        assert ("u1", "s1") not in store._data  # L1 条目已消费

        # 第二次认领（重复提交）→ None
        claimed_again = store.claim_for_execution("u1", "s1")
        assert claimed_again is None

    @patch("backend.customer_service.confirmation_flow.process_confirmation")
    def test_store_claim_strict_raises(self, _):
        """DB 认领失败（严格模式）→ 抛 StoreWriteError，不冒双执行之险。"""
        from backend.customer_service.confirmation_store import (
            ConfirmationStore,
            StoreWriteError,
        )

        store = ConfirmationStore()
        store._data[("u1", "s1")] = _pending_action()

        with patch(
            "backend.customer_service.confirmation_store._strict_writes",
            return_value=True,
        ):
            with patch.object(
                store, "_db_claim", side_effect=RuntimeError("DB down"),
            ):
                with pytest.raises(StoreWriteError):
                    store.claim_for_execution("u1", "s1")


class TestExpiredFinalState:
    @patch("backend.customer_service.confirmation_store.get_confirmation_store")
    def test_expired_uses_expired_final_state(self, mock_store_fn):
        store = MagicMock()
        mock_store_fn.return_value = store

        outcome = process_confirmation(
            _pending_action(expires_at="2000-01-01T00:00:00+00:00"),
            "确认", "u1", "s1",
        )

        assert outcome.kind == "expired"
        assert outcome.confirmation_state == "expired"
        store.clear.assert_called_once_with("u1", "s1", final_state="expired")


class TestQuestionNotIntent:
    @pytest.mark.parametrize("text", [
        "这个可以取消吗",
        "可不可以退款",
        "确认吗？",
        "需要确认么",
    ])
    def test_questions_are_none(self, text):
        assert ConfirmationIntent.NONE.value in (
            "none",
        ) and __import__(
            "backend.customer_service.confirmation", fromlist=["detect_confirmation_intent"]
        ).detect_confirmation_intent(text) == ConfirmationIntent.NONE

    def test_confirmed_cancellation_still_cancels(self):
        """「确认不要了」应判 CANCEL（不要）而非 CONFIRM。"""
        from backend.customer_service.confirmation import detect_confirmation_intent

        assert detect_confirmation_intent("确认不要了") == ConfirmationIntent.CANCEL

    def test_positive_confirms(self):
        from backend.customer_service.confirmation import detect_confirmation_intent

        assert detect_confirmation_intent("对的") == ConfirmationIntent.CONFIRM


class TestFailureFinalState:
    @patch("backend.customer_service.experts.action._simulate_execute")
    @patch("backend.customer_service.confirmation_store.get_confirmation_store")
    def test_execute_failure_writes_failed(self, mock_store_fn, mock_exec):
        store = _make_store(claim_result="act-001")
        mock_store_fn.return_value = store
        mock_exec.side_effect = RuntimeError("boom")

        outcome = process_confirmation(
            _pending_action(), "确认", "u1", "s1",
        )

        assert outcome.kind == "failed"
        store.clear.assert_called_once_with("u1", "s1", final_state="failed")
