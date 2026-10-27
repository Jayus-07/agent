"""企业助手仅长任务持久化，控制入口校验用户、租户与产品。"""
from contextlib import nullcontext
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.app.api.routes import agent_chat_tasks as routes
from backend.app.api.schemas import ChatRequest
from backend.models.task import TaskRecord, TaskStatus


@pytest.fixture
def client(monkeypatch):
    # 只替换存储与队列边界，身份解析与路由控制真实执行。
    row = TaskRecord(id="owned", user_id="42", tenant_id="t1",
                     biz_type=routes.AGENT_LONG_TASK,
                     input={"query": "报告", "product_id": "agent", "chat_session_id": "s1"})
    monkeypatch.setattr(routes.task_service, "get_task_for_user", lambda tid, uid, tenant_id: (
        row if (tid, uid, tenant_id) == (row.id, row.user_id, row.tenant_id) else None
    ))
    monkeypatch.setattr(routes.task_service, "list_checkpoints", lambda *a: [
        {"id": 1, "node_name": "router", "created_at": "2026-10-27", "state_json": {"secret": "hidden"}},
    ])
    app = FastAPI()
    app.include_router(routes.router)
    return TestClient(app), row


@pytest.mark.parametrize("headers", [
    {}, {"X-User-Id": "43", "X-Tenant-Id": "t1"},
    {"X-User-Id": "42", "X-Tenant-Id": "t2"},
])
def test_other_actor_cannot_read_or_control(client, headers):
    api, _ = client
    for path in ("", "/pause", "/resume", "/cancel"):
        response = api.get('/chat/agent/tasks/owned', headers=headers) if not path else api.post(
            '/chat/agent/tasks/owned' + path, headers=headers, json={},
        )
        assert response.status_code in (401, 403, 404)


def test_owner_reads_sanitized_progress_but_not_other_products(client):
    api, row = client
    headers = {"X-User-Id": "42", "X-Tenant-Id": "t1"}
    response = api.get('/chat/agent/tasks/owned', headers=headers)
    assert response.status_code == 200
    assert response.json()["steps"][0]["node"] == "router"
    assert "secret" not in response.text
    row.biz_type = "other_product"
    assert api.post('/chat/agent/tasks/owned/resume', headers=headers, json={}).status_code == 404


def test_submit_uses_registered_identity_and_idempotency(monkeypatch):
    from backend.shared import idempotency
    from starlette.requests import Request

    created, enqueued, cancelled = [], [], []
    owner = SimpleNamespace(user_id="42", tenant_id="t1")
    old = TaskRecord(id="old", user_id="42", tenant_id="t1", status=TaskStatus.PAUSED)
    request = Request({"type": "http", "headers": [(b'idempotency-key', b'stable-key')]})
    req = ChatRequest(question="写50个段落的讲义", session_id="s1", request_id="r1")
    monkeypatch.setattr(routes, "_conversation_guard", lambda *a: nullcontext())
    monkeypatch.setattr(routes.task_service, "list_tasks_for_user", lambda *a, **kw: [old])
    monkeypatch.setattr(routes.task_manager, "cancel_task", lambda tid: cancelled.append(tid) or {"ok": True})

    def create(uid, query, **kwargs):
        created.append((uid, query, kwargs))
        return TaskRecord(id="new", user_id=uid)

    cache = {}

    def run(operation, payload, fn, **kwargs):
        assert kwargs["tenant_id"] == "t1" and kwargs["actor_id"] == "42"
        key = kwargs["client_key"]
        if key not in cache:
            cache[key] = fn()
        return cache[key]

    monkeypatch.setattr(routes.task_service, "create_task", create)
    monkeypatch.setattr(routes.task_manager, "enqueue_task", lambda row: enqueued.append(row.id))
    monkeypatch.setattr(idempotency, "run_idempotent_operation_for_identity", run)
    assert routes.submit_long_task(req, request, owner)["task_id"] == "new"
    assert routes.submit_long_task(req, request, owner)["task_id"] == "new"
    assert len(created) == 1 and enqueued == ["new"] and cancelled == ["old"]
    assert created[0][2]["extra_input"]["product_id"] == "agent"
    assert created[0][2]["tenant_id"] == "t1"


def test_conversation_lock_failure_does_not_create_task(monkeypatch):
    from fastapi import HTTPException

    lock = SimpleNamespace(acquire=lambda: False)
    monkeypatch.setattr(routes.task_manager, "_redis", lambda: SimpleNamespace(lock=lambda *a, **kw: lock))
    with pytest.raises(HTTPException) as caught:
        with routes._conversation_guard(SimpleNamespace(user_id="42", tenant_id="t1"), "s1"):
            pytest.fail("不能在未拿到会话锁时创建或取消任务")
    assert caught.value.status_code == 409
