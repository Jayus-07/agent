# -*- coding: utf-8 -*-
"""capability-governance G4：客服意图目录 T2 回归。

真库集成（agent_memory@5433，p9g- 前缀行用后即清）：
- 形状：intents 全集 = FINE_INTENTS 实况（目录是全集而非 Top），
  stats 三元组字段齐全
- 数值：种构造数据（消息/会话/未解决登记）→ calls / handoff_rate /
  unresolved 与手工计算一致
- 角色口径：supervisor 200 / agent 403（目录是跨坐席运营视图）
- 租户隔离：他租户会话与未解决登记不串数
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import psycopg2
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.app.api.routes import cs_agent_offers, cs_inventory

TENANT = "tenant-p9g"
TENANT_OTHER = "tenant-p9g-other"
_PREFIX = "p9g-"


def _pg():
    # CI 无 .env（批十/十二同款 .env 文件依赖）：统一 MEMORY_DB_CONFIG 口径
    from backend.config.database import MEMORY_DB_CONFIG

    return psycopg2.connect(**MEMORY_DB_CONFIG, connect_timeout=2)


def _cleanup():
    with _pg() as conn, conn.cursor() as cur:
        cur.execute("DELETE FROM ai.unanswered_questions WHERE tenant_id LIKE %s",
                    ("%p9g%",))
        cur.execute("DELETE FROM customer_service.messages WHERE message_id LIKE %s",
                    (_PREFIX + "%",))
        cur.execute(
            "DELETE FROM customer_service.handoffs"
            " WHERE tenant_id LIKE %s AND conversation_id LIKE %s",
            ("%p9g%", _PREFIX + "%"),
        )
        cur.execute(
            "DELETE FROM customer_service.conversations WHERE tenant_id LIKE %s",
            ("%p9g%",),
        )
        cur.execute("DELETE FROM customer_service.cs_agents WHERE tenant_id LIKE %s",
                    ("%p9g%",))
        conn.commit()


@pytest.fixture
def db():
    _cleanup()
    yield
    _cleanup()


def _context(*, agent_id: str, tenant_id: str = TENANT,
             cs_role: str = "agent") -> cs_agent_offers.AgentContext:
    return cs_agent_offers.AgentContext(
        agent_id=agent_id,
        tenant_id=tenant_id,
        user_id=f"{_PREFIX}user-{agent_id}",
        cs_role=cs_role,
        platform_roles=("viewer",),
    )


def _make_client(context) -> TestClient:
    app = FastAPI()
    app.include_router(cs_inventory.router)
    app.dependency_overrides[cs_inventory._require_inventory_viewer] = lambda: context
    # get_session 不 override：原依赖即真库 AsyncSessionLocal（yield 形态）
    return TestClient(app, raise_server_exceptions=False)


@pytest.fixture
def sup_client():
    return _make_client(_context(agent_id=_PREFIX + "sup", cs_role="supervisor"))


@pytest.fixture
def agent_client():
    return _make_client(_context(agent_id=_PREFIX + "agent-a"))


def _seed_conversation(conversation_id: str, *, tenant_id: str = TENANT) -> None:
    with _pg() as conn, conn.cursor() as cur:
        cur.execute(
            "INSERT INTO customer_service.conversations"
            " (conversation_id, user_id, tenant_id, conversation_status,"
            "  handling_mode, channel, priority)"
            " VALUES (%s, %s, %s, 'resolved', 'ai', 'web', 'medium')"
            " ON CONFLICT (conversation_id) DO NOTHING",
            (conversation_id, _PREFIX + "user", tenant_id),
        )
        conn.commit()


def _seed_message(message_id: str, conversation_id: str, intent: str, *,
                  created_hours_ago: float = 1.0) -> None:
    # messages 无 tenant_id 列：租户经 conversations join 收口（P7 同口径）
    created = datetime.now(timezone.utc) - timedelta(hours=created_hours_ago)
    with _pg() as conn, conn.cursor() as cur:
        cur.execute(
            "INSERT INTO customer_service.messages"
            " (message_id, conversation_id, role, content,"
            "  intent_name, created_at)"
            " VALUES (%s, %s, 'user', %s, %s, %s)"
            " ON CONFLICT (message_id) DO NOTHING",
            (message_id, conversation_id,
             _PREFIX + "content", intent, created),
        )
        conn.commit()


def _seed_agent(agent_id: str, *, tenant_id: str = TENANT) -> None:
    with _pg() as conn, conn.cursor() as cur:
        cur.execute(
            "INSERT INTO customer_service.cs_agents"
            " (agent_id, tenant_id, display_name, role, enabled, status)"
            " VALUES (%s, %s, %s, 'agent', TRUE, 'online')"
            " ON CONFLICT (agent_id, tenant_id) DO NOTHING",
            (agent_id, tenant_id, agent_id),
        )
        conn.commit()


def _seed_handoff(conversation_id: str, *, tenant_id: str = TENANT) -> None:
    _seed_agent(_PREFIX + "agent-x", tenant_id=tenant_id)
    now = datetime.now(timezone.utc)
    with _pg() as conn, conn.cursor() as cur:
        cur.execute(
            "INSERT INTO customer_service.handoffs"
            " (handoff_id, conversation_id, user_id, tenant_id,"
            "  handoff_state, assigned_agent_id, attempt_count,"
            "  created_at, updated_at, closed_at)"
            " VALUES (%s, %s, %s, %s, 'closed', %s, 1, %s, %s, %s)"
            " ON CONFLICT (handoff_id) DO NOTHING",
            (_PREFIX + "ho-" + conversation_id, conversation_id,
             _PREFIX + "user", tenant_id, _PREFIX + "agent-x",
             now, now, now),
        )
        conn.commit()


def _seed_unanswered(session_id: str, *, tenant_id: str = TENANT) -> None:
    with _pg() as conn, conn.cursor() as cur:
        cur.execute(
            "INSERT INTO ai.unanswered_questions"
            " (tenant_id, session_id, question, question_hash, source)"
            " VALUES (%s, %s, %s, %s, 'rag_miss')",
            (tenant_id, session_id, _PREFIX + "question",
             _PREFIX + "hash-" + session_id[-10:]),
        )
        conn.commit()


class TestShapeAndRole:
    def test_agent_forbidden(self, agent_client):
        r = agent_client.get("/cs/capabilities/inventory?range=d7")
        assert r.status_code == 403

    def test_supervisor_full_catalog_even_without_data(self, sup_client, db):
        """无数据时目录仍是全集（FINE_INTENTS 全量，stats 为 0/None）。"""
        r = sup_client.get("/cs/capabilities/inventory?range=d7")
        assert r.status_code == 200
        body = r.json()
        from backend.customer_service.router.intents import FINE_INTENTS
        assert body["domain"] == "customer_service"
        assert body["range"] == "d7"
        assert body["total"] == len(FINE_INTENTS)
        names = [i["intent"] for i in body["intents"]]
        assert set(names) == set(FINE_INTENTS)
        empty = next(i for i in body["intents"] if i["intent"] == "k_policy")
        assert empty["description"] == FINE_INTENTS["k_policy"]
        assert empty["stats"] == {"calls": 0, "handoff_rate": None, "unresolved": 0}

    def test_invalid_range_rejected(self, sup_client):
        r = sup_client.get("/cs/capabilities/inventory?range=d365")
        assert r.status_code == 422


class TestNumbers:
    def test_calls_handoff_unresolved_match_seeded(self, sup_client, db):
        """构造数据 → 三元组逐项与手工计算一致。

        场景（TENANT，窗口 d7）：
        - conv-a：k_policy ×3（转人工）+ t_logistics ×1 → 主导 k_policy，
          未解决 1
        - conv-b：as_refund ×1（未转人工）
        - 他租户 conv-x：k_policy ×9 + 未解决 1 → 不串数
        """
        _seed_conversation(_PREFIX + "conv-a")
        _seed_conversation(_PREFIX + "conv-b")
        _seed_conversation(_PREFIX + "conv-x", tenant_id=TENANT_OTHER)
        for i in range(3):
            _seed_message(f"{_PREFIX}m-a{i}", _PREFIX + "conv-a", "k_policy",
                          created_hours_ago=i + 1)
        _seed_message(_PREFIX + "m-a3", _PREFIX + "conv-a", "t_logistics",
                      created_hours_ago=4)
        _seed_message(_PREFIX + "m-b0", _PREFIX + "conv-b", "as_refund")
        for i in range(9):
            _seed_message(f"{_PREFIX}m-x{i}", _PREFIX + "conv-x", "k_policy",
                          created_hours_ago=i + 1)
        _seed_handoff(_PREFIX + "conv-a")
        _seed_unanswered(_PREFIX + "conv-a")
        _seed_unanswered(_PREFIX + "conv-x", tenant_id=TENANT_OTHER)

        r = sup_client.get("/cs/capabilities/inventory?range=d7")
        assert r.status_code == 200
        body = r.json()
        by_intent = {i["intent"]: i for i in body["intents"]}

        k_policy = by_intent["k_policy"]["stats"]
        assert k_policy["calls"] == 3, "他租户 9 条不得串入"
        # conv-a 转 handoffs，conv-b/x 不转 → 该意图 1/1 会话全接管
        assert k_policy["handoff_rate"] == 1.0
        assert k_policy["unresolved"] == 1, "他租户未解决不得串入"

        assert by_intent["t_logistics"]["stats"]["calls"] == 1
        assert by_intent["t_logistics"]["stats"]["handoff_rate"] == 1.0
        assert by_intent["t_logistics"]["stats"]["unresolved"] == 0

        assert by_intent["as_refund"]["stats"]["calls"] == 1
        assert by_intent["as_refund"]["stats"]["handoff_rate"] == 0.0

        assert body["totals"]["calls"] == 5
        assert body["totals"]["intents_with_data"] == 3
        assert body["totals"]["unresolved"] == 1
