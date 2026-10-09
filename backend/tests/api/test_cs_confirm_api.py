"""test_cs_confirm_api.py — POST /cs/confirm 确认卡片端点测试（P3.1）。

Covers:
- 422: decision 非法（非 confirm/cancel）
- 409: 无待确认项（幂等语义 —— 已处理/过期后重复提交，前端静默清卡片）
- 200: confirm / cancel 路径，process_confirmation 唯一实现复用，
       decision 大小写归一（"CONFIRM" → confirm → 传「确认」）
- 透传: outcome 的 status/answer/confirmation_state/action_result 原样返回

端点内部 import（resolve_identity / get_confirmation_store / process_confirmation
均在函数体内 import），因此 patch 各自宿主模块的名字即可生效。
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.app.api.routes import cs_admin
from backend.customer_service.confirmation_flow import ConfirmationOutcome


@pytest.fixture
def client():
    """Minimal FastAPI app with only the confirm router."""
    app = FastAPI()
    app.include_router(cs_admin.confirm_router)
    return TestClient(app, raise_server_exceptions=False)


@pytest.fixture
def fake_identity(monkeypatch):
    """登录用户身份（user_id=user-1）。"""
    import backend.app.api.identity as identity_mod

    ident = SimpleNamespace(
        user_id="user-1", tenant_id="tenant-1", authenticated=True,
    )
    monkeypatch.setattr(identity_mod, "resolve_identity", lambda request: ident)
    return ident


@pytest.fixture
def fake_store(monkeypatch):
    """可编程的 ConfirmationStore 替身：记录 load 参数，load 结果可设。"""
    import backend.customer_service.confirmation_store as store_mod

    store = SimpleNamespace(load_calls=[], load_result=None)

    def fake_load_authoritative(user_id: str, session_id: str, tenant_id: str):
        store.load_calls.append((user_id, session_id, tenant_id))
        return store.load_result

    monkeypatch.setattr(
        store_mod,
        "get_confirmation_store",
        lambda: SimpleNamespace(load_authoritative=fake_load_authoritative),
    )
    return store


@pytest.fixture(autouse=True)
def fake_handoff(monkeypatch):
    """Handoff PostgreSQL read is an external boundary in route unit tests."""
    import backend.customer_service.handoff.lifecycle as lifecycle_mod

    holder = SimpleNamespace(result=None)
    monkeypatch.setattr(
        lifecycle_mod,
        "load_active_handoff_sync",
        lambda tenant_id, conversation_id, **kwargs: holder.result,
    )
    return holder


@pytest.fixture
def fake_process(monkeypatch):
    """可编程的 process_confirmation 替身：记录入参，返回值可设。"""
    import backend.customer_service.confirmation_flow as flow_mod

    holder = SimpleNamespace(calls=[], result=None)

    def fake_process(pending, decision_text, user_id, session_id, **kwargs):
        holder.calls.append(
            {
                "pending": pending,
                "decision_text": decision_text,
                "user_id": user_id,
                "session_id": session_id,
                "kwargs": kwargs,
            }
        )
        return holder.result

    monkeypatch.setattr(flow_mod, "process_confirmation", fake_process)
    return holder


def _success_outcome() -> ConfirmationOutcome:
    return ConfirmationOutcome(
        kind="success",
        answer="好的，已为您提交退款申请。",
        confirmation_state="success",
        action_result={"order_id": "A1001", "refund_id": "R001"},
    )


class TestConfirmEndpoint:

    def test_unversioned_confirmation_is_rejected_as_ambiguous(
        self, client, fake_identity, fake_store, fake_process
    ):
        fake_store.load_result = {
            "action_id": "act-current", "proposal_id": "act-current",
            "version": 1, "tenant_id": "tenant-1",
        }

        resp = client.post(
            "/cs/confirm",
            json={"session_id": "s1", "decision": "confirm"},
        )

        assert resp.status_code == 409
        assert "版本" in resp.json()["detail"]
        assert fake_store.load_calls == []
        assert fake_process.calls == []

    def test_partial_versioned_contract_is_rejected(
        self, client, fake_identity, fake_store,
    ):
        resp = client.post(
            "/cs/confirm",
            json={
                "session_id": "s1", "decision": "confirm",
                "proposal_id": "proposal-1", "expected_version": 1,
            },
        )
        assert resp.status_code == 422
        assert fake_store.load_calls == []

    def test_missing_tenant_identity_is_denied(
        self, client, monkeypatch, fake_store,
    ):
        import backend.app.api.identity as identity_mod

        monkeypatch.setattr(
            identity_mod, "resolve_identity",
            lambda request: SimpleNamespace(
                user_id="user-1", authenticated=True, tenant_id="",
            ),
        )
        resp = client.post(
            "/cs/confirm",
            json={
                "session_id": "s1", "decision": "confirm",
                "proposal_id": "proposal-1", "expected_version": 1,
                "client_action_id": "d2c8f8bb-5a87-4e3c-98c4-2dcb3a8f2a11",
            },
        )
        assert resp.status_code == 403
        assert fake_store.load_calls == []

    def test_untrusted_authenticated_flag_is_denied(
        self, client, monkeypatch, fake_store,
    ):
        import backend.app.api.identity as identity_mod

        monkeypatch.setattr(
            identity_mod, "resolve_identity",
            lambda request: SimpleNamespace(
                user_id="user-1", authenticated=False, tenant_id="tenant-1",
            ),
        )
        resp = client.post(
            "/cs/confirm",
            json={
                "session_id": "s1", "decision": "confirm",
                "proposal_id": "proposal-1", "expected_version": 1,
                "client_action_id": "d2c8f8bb-5a87-4e3c-98c4-2dcb3a8f2a11",
            },
        )
        assert resp.status_code == 401
        assert fake_store.load_calls == []

    def test_tenant_mismatch_pending_is_rejected(
        self, client, fake_identity, fake_store, fake_process,
    ):
        fake_store.load_result = {
            "action_id": "act-1", "proposal_id": "act-1", "version": 1,
            "tenant_id": "tenant-other",
        }
        resp = client.post(
            "/cs/confirm",
            json={
                "session_id": "s1", "decision": "confirm",
                "proposal_id": "act-1", "expected_version": 1,
                "client_action_id": "d2c8f8bb-5a87-4e3c-98c4-2dcb3a8f2a11",
            },
        )
        assert resp.status_code == 409
        assert fake_process.calls == []

    def test_handoff_read_error_fails_closed_before_pending_load(
        self, client, fake_identity, fake_store, fake_process, fake_handoff, monkeypatch,
    ):
        fake_handoff.error = RuntimeError("database unavailable")
        import backend.customer_service.handoff.lifecycle as lifecycle_mod

        def unavailable(*args, **kwargs):
            raise fake_handoff.error

        monkeypatch.setattr(lifecycle_mod, "load_active_handoff_sync", unavailable)
        resp = client.post(
            "/cs/confirm",
            json={
                "session_id": "s1", "decision": "confirm",
                "proposal_id": "act-1", "expected_version": 1,
                "client_action_id": "d2c8f8bb-5a87-4e3c-98c4-2dcb3a8f2a11",
            },
        )
        assert resp.status_code == 503
        assert fake_store.load_calls == []
        assert fake_process.calls == []

    def test_invalid_decision_422(self, client, fake_identity, fake_store):
        """decision 不是 confirm/cancel → 422，不触碰 store。"""
        resp = client.post(
            "/cs/confirm",
            json={"session_id": "s1", "decision": "execute"},
        )
        assert resp.status_code == 422
        assert fake_store.load_calls == []

    def test_no_pending_409(self, client, fake_identity, fake_store, fake_process):
        """无待确认项 → 409（幂等兜底：已处理/过期后重复提交）。"""
        fake_store.load_result = None
        resp = client.post(
            "/cs/confirm",
            json={
                "session_id": "s1", "decision": "confirm",
                "proposal_id": "proposal-1", "expected_version": 1,
                "client_action_id": "d2c8f8bb-5a87-4e3c-98c4-2dcb3a8f2a11",
            },
        )
        assert resp.status_code == 409
        assert "待确认" in resp.json()["detail"]
        # 未执行任何确认逻辑
        assert fake_process.calls == []

    def test_stale_proposal_version_is_rejected_before_confirmation(
        self, client, fake_identity, fake_store, fake_process
    ):
        fake_store.load_result = {
            "action_id": "act-current",
            "proposal_id": "proposal-current",
            "version": 3,
            "tenant_id": "tenant-1",
        }
        fake_process.result = _success_outcome()

        resp = client.post(
            "/cs/confirm",
            json={
                "session_id": "s1",
                "decision": "confirm",
                "proposal_id": "proposal-old",
                "expected_version": 2,
                "client_action_id": "d2c8f8bb-5a87-4e3c-98c4-2dcb3a8f2a11",
            },
        )

        assert resp.status_code == 409
        assert fake_process.calls == []

    def test_confirm_success(self, client, fake_identity, fake_store, fake_process):
        """confirm → 复用 process_confirmation，outcome 字段透传。"""
        fake_store.load_result = {"action_id": "act-1", "proposal": "退款 100 元"}
        fake_store.load_result.update(
            proposal_id="act-1", version=1, tenant_id="tenant-1",
        )
        fake_process.result = _success_outcome()

        resp = client.post(
            "/cs/confirm",
            json={
                "session_id": "sess-9", "decision": "confirm",
                "proposal_id": "act-1", "expected_version": 1,
                "client_action_id": "d2c8f8bb-5a87-4e3c-98c4-2dcb3a8f2a11",
            },
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "success"
        assert data["answer"] == "好的，已为您提交退款申请。"
        assert data["confirmation_state"] == "success"
        assert data["action_result"] == {"order_id": "A1001", "refund_id": "R001"}

        # process_confirmation 收到的入参：pending 原样、decision 归一为「确认」
        call = fake_process.calls[0]
        assert call["pending"] == {
            "action_id": "act-1", "proposal": "退款 100 元",
            "proposal_id": "act-1", "version": 1, "tenant_id": "tenant-1",
        }
        assert call["decision_text"] == "确认"
        assert call["user_id"] == "user-1"
        assert call["session_id"] == "sess-9"
        # store.load 用解析出的登录身份查询
        assert fake_store.load_calls == [("user-1", "sess-9", "tenant-1")]

    def test_cancel_success(self, client, fake_identity, fake_store, fake_process):
        """cancel → decision 归一为「取消」，状态回 cancelled。"""
        fake_store.load_result = {
            "action_id": "act-2",
            "proposal_id": "act-2",
            "version": 2,
            "tenant_id": "tenant-1",
        }
        fake_process.result = ConfirmationOutcome(
            kind="cancelled",
            answer="操作已取消。如有其他问题，请随时咨询。",
            confirmation_state="user_cancelled",
            action_result=None,
        )

        resp = client.post(
            "/cs/confirm",
            json={
                "session_id": "s1", "decision": "cancel", "proposal_id": "act-2",
                "expected_version": 2,
                "client_action_id": "d2c8f8bb-5a87-4e3c-98c4-2dcb3a8f2a11",
            },
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "cancelled"
        assert data["action_result"] is None
        assert fake_process.calls[0]["decision_text"] == "取消"
        assert fake_process.calls[0]["kwargs"]["expected_version"] == 2

    def test_decision_case_normalized(
        self, client, fake_identity, fake_store, fake_process
    ):
        """"CONFIRM" / " Cancel " 等大小写与空白差异统一归一。"""
        fake_store.load_result = {
            "action_id": "act-3", "proposal_id": "act-3",
            "version": 1, "tenant_id": "tenant-1",
        }
        fake_process.result = _success_outcome()

        resp = client.post(
            "/cs/confirm",
            json={
                "session_id": "s1", "decision": "CONFIRM",
                "proposal_id": "act-3", "expected_version": 1,
                "client_action_id": "d2c8f8bb-5a87-4e3c-98c4-2dcb3a8f2a11",
            },
        )
        assert resp.status_code == 200
        assert fake_process.calls[0]["decision_text"] == "确认"

    def test_guest_identity_is_rejected_without_loading_pending(
        self, client, monkeypatch, fake_store, fake_process
    ):
        """guest 不是用户身份，不能借匿名共享账号确认副作用。"""
        import backend.app.api.identity as identity_mod

        monkeypatch.setattr(
            identity_mod,
            "resolve_identity",
            lambda request: SimpleNamespace(user_id=""),
        )
        resp = client.post(
            "/cs/confirm",
            json={"session_id": "s1", "decision": "confirm"},
        )
        assert resp.status_code == 401
        assert fake_store.load_calls == []

    def test_confirm_binds_proposal_version_and_client_action_id(
        self, client, fake_identity, fake_store, fake_process
    ):
        action_id = "d2c8f8bb-5a87-4e3c-98c4-2dcb3a8f2a11"
        fake_store.load_result = {
            "action_id": "proposal-3",
            "proposal_id": "proposal-3",
            "version": 4,
            "tenant_id": "tenant-1",
        }
        fake_process.result = _success_outcome()

        resp = client.post(
            "/cs/confirm",
            json={
                "session_id": "s1",
                "decision": "confirm",
                "proposal_id": "proposal-3",
                "expected_version": 4,
                "client_action_id": action_id,
            },
        )

        assert resp.status_code == 200
        assert fake_process.calls[0]["kwargs"] == {
            "tenant_id": "tenant-1",
            "proposal_id": "proposal-3",
            "expected_version": 4,
            "client_action_id": action_id,
        }

    def test_human_handoff_blocks_confirmation(
        self, client, fake_identity, fake_store, fake_process, fake_handoff,
    ):
        fake_handoff.result = {"handoff_state": "waiting_human"}

        resp = client.post(
            "/cs/confirm",
            json={
                "session_id": "s1", "decision": "confirm",
                "proposal_id": "act-1", "expected_version": 1,
                "client_action_id": "d2c8f8bb-5a87-4e3c-98c4-2dcb3a8f2a11",
            },
        )

        assert resp.status_code == 409
        assert fake_store.load_calls == []
        assert fake_process.calls == []

    def test_agent_offer_blocks_confirmation(
        self, client, fake_identity, fake_store, fake_process, fake_handoff,
    ):
        fake_handoff.result = {"handoff_state": "agent_offered"}
        resp = client.post(
            "/cs/confirm",
            json={
                "session_id": "s1", "decision": "confirm",
                "proposal_id": "act-1", "expected_version": 1,
                "client_action_id": "d2c8f8bb-5a87-4e3c-98c4-2dcb3a8f2a11",
            },
        )
        assert resp.status_code == 409
        assert fake_store.load_calls == []
        assert fake_process.calls == []

    def test_process_failure_propagates_500(
        self, client, fake_identity, fake_store, monkeypatch
    ):
        """process_confirmation 内部异常不吞 — 网关层可见 500（便于排障）。"""
        import backend.customer_service.confirmation_flow as flow_mod

        fake_store.load_result = {
            "action_id": "act-4", "proposal_id": "act-4",
            "version": 1, "tenant_id": "tenant-1",
        }

        def boom(*args, **kwargs):
            raise RuntimeError("action executor crashed")

        monkeypatch.setattr(flow_mod, "process_confirmation", boom)
        resp = client.post(
            "/cs/confirm",
            json={
                "session_id": "s1", "decision": "confirm",
                "proposal_id": "act-4", "expected_version": 1,
                "client_action_id": "d2c8f8bb-5a87-4e3c-98c4-2dcb3a8f2a11",
            },
        )
        assert resp.status_code == 500
