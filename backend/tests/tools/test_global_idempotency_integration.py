"""WP2 首批写 Tool 接入：审批先行、幂等 claim、结果重放。"""

import pytest

from backend.shared.idempotency import (
    IdempotencyExecutor,
    IdempotencyKey,
    IdempotencyUnavailable,
    MemoryIdempotencyStore,
)


@pytest.fixture()
def tool_identity(monkeypatch):
    from backend.tools import session

    user_token = session._current_user_id.set("user-1")
    tenant_token = session._current_tenant_id.set("tenant-a")
    key_token = session._current_idempotency_key.set("")
    yield
    session._current_user_id.reset(user_token)
    session._current_tenant_id.reset(tenant_token)
    session._current_idempotency_key.reset(key_token)


@pytest.fixture()
def memory_idempotency(monkeypatch):
    from backend.shared import idempotency as module
    from backend.tools import session

    store = MemoryIdempotencyStore(lease_seconds=30)

    def run(operation, payload, callback, *, client_key=""):
        key = IdempotencyKey(
            tenant_id=session.get_tool_tenant_id(),
            actor_id=session.get_tool_user_id(),
            operation=operation,
            client_key=client_key or "derived-key",
        )
        return IdempotencyExecutor(store).execute(key, payload, callback)

    monkeypatch.setattr(module, "run_idempotent_operation", run)
    return store


def test_email_approval_precedes_global_idempotency_and_replays(
    tool_identity, memory_idempotency, monkeypatch
):
    import backend.config as config
    import backend.security.tool_approval as approval
    import backend.tools.email as email_module

    monkeypatch.setattr(config, "EMAIL_ENGINE", "agently")
    monkeypatch.setattr(approval, "ensure_approved", lambda *args, **kwargs: None)
    calls = []
    monkeypatch.setattr(
        email_module,
        "_send_email_after_approval",
        lambda *args: calls.append(args) or "邮件已发送",
    )

    from backend.tools.email import send_email_tool

    args = {
        "to": "a@example.com",
        "subject": "周报",
        "body": "正文",
        "idempotency_key": "request-1",
    }
    first = send_email_tool.invoke(args)
    replay = send_email_tool.invoke(args)

    assert first == replay == "邮件已发送"
    assert len(calls) == 1


def test_email_same_key_different_payload_is_rejected(
    tool_identity, memory_idempotency, monkeypatch
):
    import backend.config as config
    import backend.security.tool_approval as approval
    import backend.tools.email as email_module

    monkeypatch.setattr(config, "EMAIL_ENGINE", "agently")
    monkeypatch.setattr(approval, "ensure_approved", lambda *args, **kwargs: None)
    calls = []
    monkeypatch.setattr(
        email_module,
        "_send_email_after_approval",
        lambda *args: calls.append(args) or "邮件已发送",
    )

    from backend.tools.email import send_email_tool

    send_email_tool.invoke({
        "to": "a@example.com", "subject": "周报", "body": "正文",
        "idempotency_key": "request-2",
    })
    with pytest.raises(ValueError, match="IDEMPOTENCY_CONFLICT"):
        send_email_tool.invoke({
            "to": "a@example.com", "subject": "周报", "body": "不同正文",
            "idempotency_key": "request-2",
        })
    assert len(calls) == 1


def test_export_approval_precedes_global_idempotency_and_replays(
    tool_identity, memory_idempotency, monkeypatch
):
    import backend.security.tool_approval as approval
    import backend.tools.export as export_module

    monkeypatch.setattr(approval, "ensure_approved", lambda *args, **kwargs: None)
    calls = []
    monkeypatch.setattr(
        export_module,
        "_export_csv_after_approval",
        lambda *args: calls.append(args) or "已导出 1 行",
    )

    from backend.tools.export import export_csv_tool

    args = {"question": "查询订单", "filename": "orders",
            "idempotency_key": "export-1"}
    assert export_csv_tool.invoke(args) == export_csv_tool.invoke(args)
    assert len(calls) == 1


def test_global_idempotency_rejects_redis_unavailable_before_callback(monkeypatch):
    import backend.infra.redis.client as redis_client
    from backend.shared.idempotency import run_idempotent_operation
    from backend.tools import session

    monkeypatch.setattr(redis_client, "get_redis", lambda: None)
    user_token = session._current_user_id.set("user-1")
    tenant_token = session._current_tenant_id.set("tenant-a")
    calls = []
    try:
        with pytest.raises(IdempotencyUnavailable):
            run_idempotent_operation(
                "email.send", {"body": "x"},
                lambda: calls.append("side-effect") or {"ok": True},
                client_key="redis-down",
            )
    finally:
        session._current_user_id.reset(user_token)
        session._current_tenant_id.reset(tenant_token)
    assert calls == []
