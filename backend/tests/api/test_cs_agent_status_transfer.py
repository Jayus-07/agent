"""cs-workstation-v2 P3：坐席状态切换 / 坐席间转接 / 用户→坐席消息 T2 回归。

真库集成（agent_memory@5433，p3t- 前缀行用后即清）：
- PUT /cs/agents/me/status：状态持久 + 非法值 422
- POST /cs/conversations/{id}/transfer：非本人 403、目标小休 422、目标不存在
  422、原因必填 422、成功路径（agent_offered + assignment_version+1 +
  transfer_requested/reassigned 事件落库）
- POST /my/{id}/messages：人工态放行落库、AI/关闭态 409、跨用户 403
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import psycopg2
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.app.api.routes import cs_admin, cs_agent_offers

TENANT = "tenant-p3t"
_PREFIX = "p3t-"


def _pg():
    env = {}
    for line in open(".env", encoding="utf-8"):
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            env[k] = v
    return psycopg2.connect(
        host=env.get("PGHOST", "localhost"),
        port=int(env.get("PGPORT", "5433")),
        dbname=env.get("PGDATABASE", "agent_memory"),
        user=env.get("PGUSER", "postgres"),
        password=env.get("PGPASSWORD", ""),
    )


def _cleanup():
    with _pg() as conn, conn.cursor() as cur:
        cur.execute(
            "DELETE FROM customer_service.events WHERE conversation_id LIKE %s",
            (_PREFIX + "%",),
        )
        cur.execute(
            "DELETE FROM customer_service.handoffs WHERE handoff_id LIKE %s",
            (_PREFIX + "%",),
        )
        cur.execute(
            "DELETE FROM customer_service.messages WHERE conversation_id LIKE %s",
            (_PREFIX + "%",),
        )
        cur.execute(
            "DELETE FROM customer_service.conversations WHERE conversation_id LIKE %s",
            (_PREFIX + "%",),
        )
        cur.execute(
            "DELETE FROM customer_service.cs_agents WHERE agent_id LIKE %s",
            (_PREFIX + "%",),
        )
        conn.commit()


@pytest.fixture(autouse=True)
def _quiet_hub(monkeypatch):
    """静默事件广播：测试进程的 hub loop 与请求 loop 生命周期不一致，
    publish(persist=True) 会触发 closed-loop 噪音；事件落库走 outbox
    （session.add），断言不依赖广播。SLA 幂等闸保留真实现。"""
    import backend.customer_service.realtime as realtime_mod

    real = realtime_mod.get_agent_hub()
    stub = SimpleNamespace(
        mark_sla_alert_once=real.mark_sla_alert_once,
        publish=lambda *args, **kwargs: None,
    )
    monkeypatch.setattr(realtime_mod, "get_agent_hub", lambda: stub)


@pytest.fixture
def db():
    _cleanup()
    yield
    _cleanup()


def _seed_agent(agent_id: str, *, status: str = "online",
                auth_user_id: str | None = None, enabled: bool = True) -> str:
    with _pg() as conn, conn.cursor() as cur:
        cur.execute(
            "INSERT INTO customer_service.cs_agents"
            " (agent_id, tenant_id, display_name, role, available, skill,"
            "  auth_user_id, enabled, accepting, status, version,"
            "  max_conversations)"
            " VALUES (%s, %s, %s, 'agent', true, 'general', %s, %s, true, %s, 0, 10)"
            " ON CONFLICT (agent_id) DO UPDATE SET status = EXCLUDED.status,"
            " enabled = EXCLUDED.enabled, auth_user_id = EXCLUDED.auth_user_id",
            (agent_id, TENANT, agent_id, auth_user_id, enabled, status),
        )
        conn.commit()
    return agent_id


def _seed_conversation(conversation_id: str, user_id: str) -> None:
    with _pg() as conn, conn.cursor() as cur:
        cur.execute(
            "INSERT INTO customer_service.conversations"
            " (conversation_id, user_id, tenant_id, conversation_status,"
            "  handling_mode, channel)"
            " VALUES (%s, %s, %s, 'open', 'human', 'web')"
            " ON CONFLICT (conversation_id) DO NOTHING",
            (conversation_id, user_id, TENANT),
        )
        conn.commit()


def _seed_handoff(conversation_id: str, user_id: str, *, state: str,
                  assigned: str | None = None) -> str:
    handoff_id = _PREFIX + uuid.uuid4().hex[:12]
    with _pg() as conn, conn.cursor() as cur:
        cur.execute(
            "INSERT INTO customer_service.handoffs"
            " (handoff_id, conversation_id, user_id, tenant_id, handoff_state,"
            "  trigger_type, priority, assigned_agent_id, assignment_version,"
            "  attempt_count, total_deadline_at)"
            " VALUES (%s, %s, %s, %s, %s, 'manual', 50, %s, 1, 1,"
            "  now() + interval '10 minutes')",
            (handoff_id, conversation_id, user_id, TENANT, state, assigned),
        )
        conn.commit()
    return handoff_id


@pytest.fixture(autouse=True)
def identity(monkeypatch):
    """统一可信身份（offers 与 messages 两组端点共用）。"""
    import backend.app.api.identity as identity_mod

    ident = SimpleNamespace(
        user_id="p3t-auth-user", tenant_id=TENANT, authenticated=True,
        roles=("agent",), user_name="p3t-agent",
    )
    monkeypatch.setattr(identity_mod, "resolve_identity", lambda request: ident)
    return ident


@pytest.fixture
def offers_client(monkeypatch):
    app = FastAPI()
    app.include_router(cs_agent_offers.router)
    ctx = cs_agent_offers.AgentContext(
        agent_id=_PREFIX + "agent-a",
        tenant_id=TENANT,
        user_id="p3t-auth-user",
        cs_role="agent",
        platform_roles=("viewer",),
    )
    app.dependency_overrides[cs_agent_offers.require_agent] = lambda: ctx
    return TestClient(app, raise_server_exceptions=False)


def test_status_update_persists(db, offers_client):
    _seed_agent(_PREFIX + "agent-a", auth_user_id="p3t-auth-user")
    response = offers_client.put(
        "/cs/agents/me/status", json={"status": "away"},
    )
    assert response.status_code == 200
    assert response.json()["status"] == "away"
    with _pg() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT status FROM customer_service.cs_agents WHERE agent_id = %s",
            (_PREFIX + "agent-a",),
        )
        assert cur.fetchone()[0] == "away"


def test_status_rejects_invalid_value(db, offers_client):
    response = offers_client.put("/cs/agents/me/status", json={"status": "lunch"})
    assert response.status_code == 422


def test_status_requires_bound_agent(db, offers_client):
    response = offers_client.put(
        "/cs/agents/me/status", json={"status": "online"},
    )
    assert response.status_code == 403


def test_transfer_success_flow(db, offers_client):
    agent_a = _seed_agent(_PREFIX + "agent-a", auth_user_id="p3t-auth-user")
    agent_b = _seed_agent(_PREFIX + "agent-b", status="online")
    conversation_id = _PREFIX + uuid.uuid4().hex[:10]
    _seed_conversation(conversation_id, "user-p3t")
    _seed_handoff(
        conversation_id, "user-p3t", state="human_active", assigned=agent_a,
    )

    response = offers_client.post(
        f"/cs/conversations/{conversation_id}/transfer",
        json={"target_agent_id": agent_b, "reason": "技能更匹配"},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["handoff_state"] == "agent_offered"
    assert body["to_agent_id"] == agent_b

    with _pg() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT handoff_state, assigned_agent_id, assignment_version"
            " FROM customer_service.handoffs WHERE conversation_id = %s",
            (conversation_id,),
        )
        state, assigned, version = cur.fetchone()
        assert state == "agent_offered"
        assert assigned == agent_b
        assert version >= 2
        cur.execute(
            "SELECT type FROM customer_service.events"
            " WHERE conversation_id = %s ORDER BY id",
            (conversation_id,),
        )
        types = [row[0] for row in cur.fetchall()]
    assert "conversation.transfer_requested" in types
    assert "conversation.reassigned" in types
    assert "conversation.offered" in types


def test_transfer_rejects_away_target(db, offers_client):
    agent_a = _seed_agent(_PREFIX + "agent-a", auth_user_id="p3t-auth-user")
    agent_b = _seed_agent(_PREFIX + "agent-b", status="away")
    conversation_id = _PREFIX + uuid.uuid4().hex[:10]
    _seed_conversation(conversation_id, "user-p3t")
    _seed_handoff(
        conversation_id, "user-p3t", state="human_active", assigned=agent_a,
    )
    response = offers_client.post(
        f"/cs/conversations/{conversation_id}/transfer",
        json={"target_agent_id": agent_b, "reason": "x"},
    )
    assert response.status_code == 422


def test_transfer_rejects_non_owner(db, offers_client):
    _seed_agent(_PREFIX + "agent-a", auth_user_id="p3t-auth-user")
    agent_b = _seed_agent(_PREFIX + "agent-b")
    agent_c = _seed_agent(_PREFIX + "agent-c")
    conversation_id = _PREFIX + uuid.uuid4().hex[:10]
    _seed_conversation(conversation_id, "user-p3t")
    _seed_handoff(
        conversation_id, "user-p3t", state="human_active", assigned=agent_c,
    )
    response = offers_client.post(
        f"/cs/conversations/{conversation_id}/transfer",
        json={"target_agent_id": agent_b, "reason": "x"},
    )
    assert response.status_code == 403


def test_transfer_rejects_missing_reason(db, offers_client):
    agent_a = _seed_agent(_PREFIX + "agent-a", auth_user_id="p3t-auth-user")
    agent_b = _seed_agent(_PREFIX + "agent-b")
    conversation_id = _PREFIX + uuid.uuid4().hex[:10]
    _seed_conversation(conversation_id, "user-p3t")
    _seed_handoff(
        conversation_id, "user-p3t", state="human_active", assigned=agent_a,
    )
    response = offers_client.post(
        f"/cs/conversations/{conversation_id}/transfer",
        json={"target_agent_id": agent_b, "reason": "  "},
    )
    assert response.status_code == 422


@pytest.fixture
def messages_client(monkeypatch):
    async def ensure(request, conversation_id):
        return None

    monkeypatch.setattr(cs_admin, "_ensure_my_conversation", ensure)
    app = FastAPI()
    app.include_router(cs_admin.router)
    return TestClient(app, raise_server_exceptions=False)


def test_my_message_persists_in_human_states(db, messages_client, monkeypatch):
    import backend.customer_service.handoff.lifecycle as lifecycle_mod

    conversation_id = _PREFIX + uuid.uuid4().hex[:10]
    _seed_conversation(conversation_id, "p3t-auth-user")
    _seed_handoff(conversation_id, "p3t-auth-user", state="waiting_human")
    monkeypatch.setattr(
        lifecycle_mod, "load_active_handoff_sync",
        lambda tenant_id, conversation_id, **kw: {"handoff_state": "waiting_human"},
    )

    response = messages_client.post(
        f"/cs/conversations/my/{conversation_id}/messages",
        json={"content": "补充一下订单号 DEMO-1001"},
    )
    assert response.status_code == 200, response.text
    assert response.json()["handoff_state"] == "waiting_human"
    with _pg() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT sender_type, content FROM customer_service.messages"
            " WHERE conversation_id = %s",
            (conversation_id,),
        )
        rows = cur.fetchall()
    assert rows and rows[0][0] == "user" and "DEMO-1001" in rows[0][1]


def test_my_message_rejected_in_ai_state(db, messages_client, monkeypatch):
    import backend.customer_service.handoff.lifecycle as lifecycle_mod

    conversation_id = _PREFIX + uuid.uuid4().hex[:10]
    _seed_conversation(conversation_id, "p3t-auth-user")
    monkeypatch.setattr(
        lifecycle_mod, "load_active_handoff_sync",
        lambda tenant_id, conversation_id, **kw: {"handoff_state": "none"},
    )
    response = messages_client.post(
        f"/cs/conversations/my/{conversation_id}/messages",
        json={"content": "hi"},
    )
    assert response.status_code == 409


def test_my_message_rejects_cross_user(db, messages_client, monkeypatch):
    import backend.customer_service.handoff.lifecycle as lifecycle_mod

    conversation_id = _PREFIX + uuid.uuid4().hex[:10]
    _seed_conversation(conversation_id, "someone-else")
    _seed_handoff(conversation_id, "someone-else", state="human_active")
    monkeypatch.setattr(
        lifecycle_mod, "load_active_handoff_sync",
        lambda tenant_id, conversation_id, **kw: {"handoff_state": "human_active"},
    )
    response = messages_client.post(
        f"/cs/conversations/my/{conversation_id}/messages",
        json={"content": "hi"},
    )
    assert response.status_code == 403
