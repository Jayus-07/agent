"""tests/api/test_tasks_api.py — Phase1 Task Runtime Step7 API/SSE 集成测试。

停点 7 验收（测试 Graph 实机链路，TestClient 进程内 + 真实 agent_memory）：
- create → run → pause → query → resume → success
- create → run → cancel（→ resume 被拒）
- GET /tasks/{id} 返回 TaskState 事实源（含 workflow/error_code/conversation_id 口径）
- SSE：snapshot 帧 + 终态 done/error 别名帧（旧 completed/failed 帧保留兼容）
"""
from __future__ import annotations

import uuid
from typing import TypedDict

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.app.api.routes import tasks as tasks_route


@pytest.fixture()
def client(monkeypatch, pg):
    """轻量 FastAPI app（仅 tasks 路由）+ 入队桩（不依赖 broker）。"""
    from backend.tasks import task_manager

    counter = {"n": 0}

    def _fake_enqueue(record):
        counter["n"] += 1

    monkeypatch.setattr(task_manager, "enqueue_task", _fake_enqueue)
    app = FastAPI()
    app.include_router(tasks_route.router)
    with TestClient(app, raise_server_exceptions=False) as c:
        c.enqueued = counter  # type: ignore[attr-defined]
        yield c


@pytest.fixture(scope="module")
def pg():
    pytest.importorskip("psycopg")
    try:
        from backend.services import task_service

        task_service.ensure_schema()
    except Exception as e:  # noqa: BLE001
        pytest.skip(f"agent_memory 不可达，跳过任务 API 测试: {e}")
    from backend.services import task_service

    return task_service


def _user() -> str:
    return f"api-user-{uuid.uuid4().hex[:8]}"


def _h(user: str) -> dict:
    """网关身份头（IDENTITY_SOURCE=header 模式下 body/query 身份被忽略）。"""
    return {"X-User-Id": user}


def _create(client, query: str = "API 集成测试") -> str:
    user = _user()
    resp = client.post("/tasks", json={"query": query}, headers=_h(user))
    assert resp.status_code == 200, resp.text
    return user, resp.json()["task_id"]


def _run_to_success(pg, task_id: str, monkeypatch) -> None:
    """桩图经 impl 执行到 SUCCESS（模拟 Worker 拾取）。"""
    from langgraph.checkpoint.memory import MemorySaver
    from langgraph.graph import END, START, StateGraph

    class S(TypedDict, total=False):
        step_results: dict
        final_answer: str

    def _node(state: dict) -> dict:
        return {"step_results": {"only": "ok"}, "final_answer": "done"}

    wf = StateGraph(S)
    wf.add_node("only_step", _node)
    wf.add_edge(START, "only_step")
    wf.add_edge("only_step", END)

    from backend.orchestration.checkpoint import task_executor as te

    monkeypatch.setattr(te, "build_task_graph",
                        lambda: wf.compile(checkpointer=MemorySaver()))
    monkeypatch.setattr("backend.tasks.task_manager.is_cancel_requested",
                        lambda t: False)
    monkeypatch.setattr("backend.tasks.task_manager.is_pause_requested",
                        lambda t: False)
    from backend.tasks.agent_tasks import execute_agent_task_impl

    result = execute_agent_task_impl(task_id)
    assert result["status"] == "SUCCESS"


# ═══════════════════════════════════════════════════
# 链路 1：create → run → pause → query → resume → success
# ═══════════════════════════════════════════════════

def test_create_run_pause_query_resume_success(pg, client, monkeypatch):
    user, task_id = _create(client)
    assert client.enqueued["n"] == 1

    # run：跑到一半（先手动推进到 RUNNING + 一个节点边界）
    from backend.services.task_state import TaskManager

    TaskManager.mark_running(task_id, progress="开始执行")
    pg.update_progress(task_id, "step_a", progress="节点 step_a 完成")

    # pause（RUNNING → 标志路径；Redis 标志桩避免环境依赖）
    monkeypatch.setattr("backend.tasks.task_manager.request_pause",
                        lambda tid: True)
    resp = client.post(f"/tasks/{task_id}/pause", headers=_h(user))
    assert resp.status_code == 200, resp.text
    assert resp.json()["status"] == "RUNNING"

    # Worker 捕获标志落 PAUSED（复刻 impl._fail）
    TaskManager.mark_paused(task_id)

    # query：TaskState 事实源（含 Phase1 新口径字段）
    resp = client.get(f"/tasks/{task_id}", headers=_h(user))
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "PAUSED"
    assert body["current_node"] == "step_a"
    assert body["workflow"] == "main"                     # 新口径别名
    assert "error_code" in body
    assert "conversation_id" in body

    # resume：PAUSED → PENDING 重新入队
    resp = client.post(f"/tasks/{task_id}/resume", headers=_h(user),
                       json={"user_input": ""})
    assert resp.status_code == 200, resp.text
    assert resp.json()["status"] == "PENDING"
    assert client.enqueued["n"] == 2

    # 第二次 resume：幂等（不重复入队）
    resp = client.post(f"/tasks/{task_id}/resume", headers=_h(user),
                       json={"user_input": ""})
    assert resp.status_code == 200
    assert client.enqueued["n"] == 2

    # Worker 从 checkpoint 续跑到 SUCCESS
    _run_to_success(pg, task_id, monkeypatch)
    resp = client.get(f"/tasks/{task_id}", headers=_h(user))
    assert resp.json()["status"] == "SUCCESS"
    assert resp.json()["result"]["answer"] == "done"


def test_full_lifecycle_via_api_e2e(pg, client, monkeypatch):
    """pause 前置到队列内（PENDING 直落 PAUSED）再 resume 跑完——全程 API 视角。"""
    user, task_id = _create(client, query="队列内暂停链路")

    resp = client.post(f"/tasks/{task_id}/pause", headers=_h(user))
    assert resp.status_code == 200
    assert resp.json()["status"] == "PAUSED"
    assert pg.get_task(task_id).status.value == "PAUSED"

    resp = client.post(f"/tasks/{task_id}/resume", headers=_h(user),
                       json={"user_input": ""})
    assert resp.status_code == 200
    assert client.enqueued["n"] == 2

    _run_to_success(pg, task_id, monkeypatch)
    assert pg.get_task(task_id).status.value == "SUCCESS"


# ═══════════════════════════════════════════════════
# 链路 2：create → run → cancel（→ resume 被拒）
# ═══════════════════════════════════════════════════

def test_create_run_cancel_resume_rejected(pg, client, monkeypatch):
    user, task_id = _create(client, query="取消链路")
    from backend.services.task_state import TaskManager

    TaskManager.mark_running(task_id)
    monkeypatch.setattr("backend.tasks.task_manager.request_cancel",
                        lambda tid: True)
    resp = client.post(f"/tasks/{task_id}/cancel", headers=_h(user))
    assert resp.status_code == 200
    assert resp.json()["status"] == "RUNNING"            # 标志已下发

    # Worker 捕获落 CANCELLED
    TaskManager.mark_cancelled(task_id)

    resp = client.post(f"/tasks/{task_id}/cancel", headers=_h(user))
    assert resp.status_code == 200
    assert resp.json()["message"] == "任务已结束，无需取消"   # 重复 cancel 幂等

    resp = client.post(f"/tasks/{task_id}/resume", headers=_h(user),
                       json={"user_input": ""})
    assert resp.status_code == 409                       # Cancel 后 resume 拒绝
    assert client.enqueued["n"] == 1


def test_cancel_pending_task_directly(pg, client):
    user, task_id = _create(client, query="队列内取消")
    resp = client.post(f"/tasks/{task_id}/cancel", headers=_h(user))
    assert resp.status_code == 200
    assert resp.json()["status"] == "CANCELLED"
    assert pg.get_task(task_id).status.value == "CANCELLED"


# ═══════════════════════════════════════════════════
# SSE：snapshot + 终态 done/error 别名帧
# ═══════════════════════════════════════════════════

def _parse_sse(lines: list[str]) -> list[tuple[str, str]]:
    events: list[tuple[str, str]] = []
    name, data = None, []
    for line in lines:
        if line.startswith("event: "):
            name = line[len("event: "):]
        elif line.startswith("data: ") and name:
            events.append((name, line[len("data: "):]))
            name = None
    return events


def test_sse_terminal_success_emits_done_alias(pg, client):
    user, task_id = _create(client)
    from backend.services.task_state import TaskManager

    TaskManager.mark_running(task_id)
    TaskManager.mark_success(task_id, output={"answer": "ok"})

    with client.stream("GET", f"/tasks/{task_id}/stream",
                       headers=_h(user)) as resp:
        assert resp.status_code == 200
        lines = [l for l in resp.iter_lines() if l]

    events = _parse_sse(lines)
    names = [e for e, _ in events]
    assert names[0] == "snapshot"
    assert "completed" in names                          # 旧协议保留
    assert "done" in names                               # 新协议别名
    snapshot = __import__("json").loads(dict(events)["snapshot"])
    assert snapshot["status"] == "SUCCESS"
    assert snapshot["workflow"] == "main"


def test_sse_terminal_cancelled_emits_cancelled(pg, client):
    user, task_id = _create(client)
    from backend.services.task_state import TaskManager

    TaskManager.mark_cancelled(task_id)
    with client.stream("GET", f"/tasks/{task_id}/stream",
                       headers=_h(user)) as resp:
        lines = [l for l in resp.iter_lines() if l]
    names = [e for e, _ in _parse_sse(lines)]
    assert "cancelled" in names


def test_api_requires_identity(client):
    resp = client.post("/tasks", json={"query": "no user"})  # 无身份头
    assert resp.status_code == 401


def test_api_isolation_between_users(pg, client):
    user, task_id = _create(client)
    stranger = _user()
    resp = client.get(f"/tasks/{task_id}", headers=_h(stranger))
    assert resp.status_code == 404                        # 不暴露存在性
