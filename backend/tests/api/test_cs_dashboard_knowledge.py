"""cs-workstation-v2 P7：数据看板 + 知识助手 T2 回归。

真库集成（agent_memory@5433，p7t- 前缀行用后即清）：
- dashboard 聚合正确性：种会话/handoffs/评分/意图/质检分 → 六卡数值、
  近7日趋势、意图分布、坐席绩效表与构造数据一致
- 角色口径：supervisor 全员绩效（scope=all）/ agent 仅本人一行（scope=self）
- 租户隔离：他租户会话/坐席/质检分不串数
- 知识助手：未解决聚合（question_hash 聚类+变体分布+来源显示）、
  FAQ 草稿（status=draft 幂等、不进线上 published）、
  知识检索（精确 1.0/模糊命中、零台账）
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import psycopg2
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.app.api.routes import cs_agent_offers, cs_dashboard, cs_knowledge

TENANT = "tenant-p7t"
TENANT_OTHER = "tenant-p7t-other"
_PREFIX = "p7t-"


def _pg():
    # CI 无 .env（批十/十二同款 .env 文件依赖）：统一 MEMORY_DB_CONFIG 口径
    from backend.config.database import MEMORY_DB_CONFIG

    return psycopg2.connect(**MEMORY_DB_CONFIG, connect_timeout=2)


def _cleanup():
    with _pg() as conn, conn.cursor() as cur:
        cur.execute(
            "DELETE FROM customer_service.cs_qa_scores"
            " WHERE tenant_id LIKE %s OR conversation_id LIKE %s",
            ("%p7t%", _PREFIX + "%"),
        )
        cur.execute("DELETE FROM ai.unanswered_questions WHERE tenant_id LIKE %s",
                    ("%p7t%",))
        cur.execute("DELETE FROM ai.cs_faq WHERE question LIKE %s",
                    (_PREFIX + "%",))
        cur.execute(
            "DELETE FROM customer_service.messages WHERE message_id LIKE %s",
            (_PREFIX + "%",),
        )
        cur.execute(
            "DELETE FROM customer_service.handoffs"
            " WHERE tenant_id LIKE %s AND conversation_id LIKE %s",
            ("%p7t%", _PREFIX + "%"),
        )
        cur.execute(
            "DELETE FROM customer_service.conversations"
            " WHERE tenant_id LIKE %s AND conversation_id LIKE %s",
            ("%p7t%", _PREFIX + "%"),
        )
        cur.execute("DELETE FROM customer_service.cs_agents WHERE tenant_id LIKE %s",
                    ("%p7t%",))
        conn.commit()


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
    app.include_router(cs_dashboard.router)
    app.include_router(cs_knowledge.router)
    app.dependency_overrides[cs_dashboard.require_agent] = lambda: context
    app.dependency_overrides[cs_knowledge.require_agent] = lambda: context
    return TestClient(app, raise_server_exceptions=False)


@pytest.fixture
def db():
    from backend.customer_service.faq import reset_faq_store_for_tests

    reset_faq_store_for_tests()
    _cleanup()
    yield
    _cleanup()
    reset_faq_store_for_tests()


@pytest.fixture
def agent_client():
    return _make_client(_context(agent_id=_PREFIX + "agent-a"))


@pytest.fixture
def sup_client():
    return _make_client(_context(agent_id=_PREFIX + "sup", cs_role="supervisor"))


def _seed_agent(agent_id: str, tenant_id: str = TENANT) -> None:
    with _pg() as conn, conn.cursor() as cur:
        cur.execute(
            "INSERT INTO customer_service.cs_agents"
            " (agent_id, tenant_id, display_name, role, enabled, status)"
            " VALUES (%s, %s, %s, 'agent', TRUE, 'online')",
            (agent_id, tenant_id, agent_id),
        )
        conn.commit()


def _seed_session(
    conversation_id: str,
    *,
    agent_id: str,
    tenant_id: str = TENANT,
    rating: int | None = None,
    first_reply_seconds: float | None = None,
    created_days_ago: float = 0.0,   # 0=今天窗口；6.5=趋势第 7 天
    handle_minutes: float | None = 8.0,
    intent: str | None = None,
) -> None:
    now = datetime.now(timezone.utc)
    created = now - timedelta(days=created_days_ago)
    first_reply = (
        created + timedelta(seconds=first_reply_seconds)
        if first_reply_seconds is not None
        else None
    )
    closed = created + timedelta(
        minutes=handle_minutes if handle_minutes is not None else 0
    )
    with _pg() as conn, conn.cursor() as cur:
        cur.execute(
            "INSERT INTO customer_service.conversations"
            " (conversation_id, user_id, tenant_id, conversation_status,"
            "  handling_mode, channel, priority, rating, created_at,"
            "  updated_at, closed_at, first_reply_at)"
            " VALUES (%s, %s, %s, 'resolved', 'ai', 'web', 'medium', %s,"
            "         %s, %s, %s, %s)",
            (
                conversation_id, _PREFIX + "user-1", tenant_id, rating,
                created, created, closed if handle_minutes is not None else None,
                first_reply,
            ),
        )
        cur.execute(
            "INSERT INTO customer_service.handoffs"
            " (handoff_id, conversation_id, user_id, tenant_id,"
            "  handoff_state, assigned_agent_id, attempt_count,"
            "  created_at, updated_at, closed_at)"
            " VALUES (%s, %s, %s, %s, 'closed', %s, 1, %s, %s, %s)",
            (
                _PREFIX + "ho-" + conversation_id, conversation_id,
                _PREFIX + "user-1", tenant_id, agent_id, created, created, closed,
            ),
        )
        if intent:
            cur.execute(
                "INSERT INTO customer_service.messages"
                " (message_id, conversation_id, role, sender_type, content,"
                "  content_type, intent_name, created_at)"
                " VALUES (%s, %s, 'user', 'user', 'hi', 'text', %s, %s)",
                (
                    _PREFIX + "msg-" + conversation_id, conversation_id,
                    intent, created,
                ),
            )
        conn.commit()


def _seed_qa(conversation_id: str, agent_id: str, total: int,
             tenant_id: str = TENANT) -> None:
    with _pg() as conn, conn.cursor() as cur:
        cur.execute(
            "INSERT INTO customer_service.cs_qa_scores"
            " (tenant_id, conversation_id, agent_id, batch_date, source,"
            "  total_score, verdict, status)"
            " VALUES (%s, %s, %s, (now() AT TIME ZONE 'UTC')::date, 'human',"
            "         %s, 'pass', 'scored')",
            (tenant_id, conversation_id, agent_id, total),
        )
        conn.commit()


def _seed_unanswered(question: str, source: str, tenant_id: str = TENANT,
                     times: int = 1) -> None:
    # question_hash 口径对齐 070 迁移（sha1 前 32 位，存量事实源，仅聚类去重
    # 用途非加密），换 sha256 将无法命中既有簇
    import hashlib

    qhash = hashlib.sha1(question.strip().lower().encode()).hexdigest()[:32]
    with _pg() as conn, conn.cursor() as cur:
        for _ in range(times):
            cur.execute(
                "INSERT INTO ai.unanswered_questions"
                " (tenant_id, user_id, session_id, question, question_hash,"
                "  source)"
                " VALUES (%s, '', '', %s, %s, %s)",
                (tenant_id, question, qhash, source),
            )
        conn.commit()


def test_dashboard_supervisor_aggregation(db, sup_client):
    _seed_agent(_PREFIX + "agent-a")
    _seed_agent(_PREFIX + "agent-b")
    _seed_agent(_PREFIX + "agent-x", tenant_id=TENANT_OTHER)  # agent_id 全局唯一
    # 今日：A 两个会话（rating 4/首响 60s/处理 10min；rating 2/首响 120s/处理 20min）
    _seed_session(_PREFIX + "conv-d1", agent_id=_PREFIX + "agent-a",
                  rating=4, first_reply_seconds=60, handle_minutes=10,
                  intent="退款/退货")
    _seed_session(_PREFIX + "conv-d2", agent_id=_PREFIX + "agent-a",
                  rating=2, first_reply_seconds=120, handle_minutes=20,
                  intent="退款/退货")
    # B 一个会话（无评分）
    _seed_session(_PREFIX + "conv-d3", agent_id=_PREFIX + "agent-b",
                  first_reply_seconds=30, handle_minutes=5, intent="物流查询")
    # 他租户会话（不得串数）
    _seed_session(_PREFIX + "conv-x1", agent_id=_PREFIX + "agent-x",
                  tenant_id=TENANT_OTHER, rating=1)
    # 质检分：A=88、B=70（他租户 99 不串）
    _seed_qa(_PREFIX + "conv-d1", _PREFIX + "agent-a", 88)
    _seed_qa(_PREFIX + "conv-d2", _PREFIX + "agent-a", 88)
    _seed_qa(_PREFIX + "conv-d3", _PREFIX + "agent-b", 70)
    _seed_qa(_PREFIX + "conv-x1", _PREFIX + "agent-a", 99,
             tenant_id=TENANT_OTHER)

    response = sup_client.get("/cs/dashboard", params={"range": "today"})
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["scope"] == "all"

    cards = body["cards"]
    assert cards["received"]["value"] == 3      # 他租户不计
    assert cards["handoff_rate"]["value"] == 1.0  # 3 会话 3 人工 handoff
    # 首响均值 (60+120+30)/3 = 70s
    assert cards["avg_first_reply_s"]["value"] == 70
    assert cards["csat"]["value"] == 3.0        # (4+2)/2
    assert cards["queue_waiting"] >= 0 and cards["in_progress"] >= 0

    # 意图分布：退款/退货 2 条 > 物流查询 1 条
    intents = {item["intent"]: item for item in body["intents"]}
    assert intents["退款/退货"]["count"] == 2
    assert intents["物流查询"]["count"] == 1

    # 绩效表：全员；A 接待 2、首响 90s、AHT 15min、CSAT 3.0、质检分 88
    agents = {a["agent_id"]: a for a in body["agents"]}
    assert set(agents) == {_PREFIX + "agent-a", _PREFIX + "agent-b"}
    a = agents[_PREFIX + "agent-a"]
    assert a["handled"] == 2
    assert a["avg_first_reply_s"] == 90
    assert a["aht_s"] == 15 * 60
    assert a["csat"] == 3.0
    assert a["qa_score"] == 88.0
    assert a["status"] == "online"
    b = agents[_PREFIX + "agent-b"]
    assert b["handled"] == 1 and b["qa_score"] == 70.0

    # 趋势：7 天序列，今天 received>=3
    trend = body["trend"]
    assert len(trend) == 7
    assert trend[-1]["received"] >= 3


def test_dashboard_agent_scope_self_only(db, agent_client):
    _seed_agent(_PREFIX + "agent-a")
    _seed_agent(_PREFIX + "agent-b")
    _seed_session(_PREFIX + "conv-m1", agent_id=_PREFIX + "agent-a",
                  rating=5, first_reply_seconds=30, handle_minutes=5,
                  intent="退款/退货")
    _seed_session(_PREFIX + "conv-m2", agent_id=_PREFIX + "agent-b",
                  rating=1, first_reply_seconds=300, handle_minutes=30,
                  intent="投诉")

    response = agent_client.get("/cs/dashboard", params={"range": "d7"})
    assert response.status_code == 200
    body = response.json()
    assert body["scope"] == "self"
    # 仅本人会话计入
    assert body["cards"]["received"]["value"] == 1
    # 绩效表仅本人一行
    assert len(body["agents"]) == 1
    assert body["agents"][0]["agent_id"] == _PREFIX + "agent-a"
    assert body["agents"][0]["handled"] == 1
    assert body["agents"][0]["csat"] == 5.0
    # 意图分布仅本人会话
    intents = [item["intent"] for item in body["intents"]]
    assert intents == ["退款/退货"]


def test_dashboard_invalid_range_and_cross_tenant(db, agent_client):
    assert agent_client.get(
        "/cs/dashboard", params={"range": "bogus"},
    ).status_code == 422
    _seed_agent(_PREFIX + "agent-x", tenant_id=TENANT_OTHER)
    _seed_session(_PREFIX + "conv-o1", agent_id=_PREFIX + "agent-x",
                  tenant_id=TENANT_OTHER)
    other = _make_client(
        _context(agent_id=_PREFIX + "agent-x", tenant_id=TENANT_OTHER),
    )
    response = other.get("/cs/dashboard", params={"range": "today"})
    assert response.status_code == 200
    assert response.json()["cards"]["received"]["value"] == 1
    # 本租户视角看不到他租户
    response = agent_client.get("/cs/dashboard", params={"range": "today"})
    assert response.json()["cards"]["received"]["value"] == 0


def test_unresolved_aggregation(db, agent_client):
    # question_hash = 归一化问题的精确哈希（070 口径）：同问题聚簇、
    # 不同问题各成一簇（不做语义归并）
    _seed_unanswered("发票怎么开", "rag_miss", times=3)
    _seed_unanswered("怎么开发票", "rag_miss", times=2)
    _seed_unanswered("可以发顺丰吗", "sql_empty", times=1)
    _seed_unanswered("别家租户问题", "rag_miss", tenant_id=TENANT_OTHER)

    response = agent_client.get("/cs/knowledge/unresolved")
    assert response.status_code == 200, response.text
    items = response.json()["items"]
    assert len(items) == 3  # 他租户不计
    top = items[0]
    assert top["question"] == "发票怎么开"
    assert top["hits"] == 3
    assert top["source_display"] == "拒答"
    assert top["variants"] == {"发票怎么开": 3}
    second = items[1]
    assert second["question"] == "怎么开发票" and second["hits"] == 2
    sq = next(i for i in items if i["question"] == "可以发顺丰吗")
    assert sq["source_display"] == "查询空结果"


def test_faq_draft_idempotent_and_not_published(db, agent_client):
    from backend.customer_service.faq import get_faq_store

    response = agent_client.post(
        "/cs/knowledge/faq-draft",
        json={"question": "p7t-发票怎么开", "variants": ["怎么开发票", " "],
              },
    )
    assert response.status_code == 200, response.text
    first = response.json()
    assert first["status"] == "draft"

    # 幂等：重复生成同 key 不建新行（upsert 同一行）
    second = agent_client.post(
        "/cs/knowledge/faq-draft",
        json={"question": "p7t-发票怎么开", "variants": []},
    ).json()
    assert second["faq_id"] == first["faq_id"]

    with _pg() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT status, kb_refs FROM ai.cs_faq WHERE id = %s",
            (first["faq_id"],),
        )
        status, kb_refs = cur.fetchone()
        cur.execute(
            "SELECT count(*) FROM ai.cs_faq WHERE question LIKE 'p7t-%'",
        )
        assert cur.fetchone()[0] == 1
    assert status == "draft"
    assert "p7t" in kb_refs
    # 草稿不进线上
    assert get_faq_store().match("p7t-发票怎么开") is None


def test_knowledge_search_hits_without_ledger(db, agent_client):
    from backend.customer_service.faq import get_faq_store

    store = get_faq_store()
    store.upsert_faq(
        "p7t-运费险怎么使用", "在订单页申请退货运费理赔。", status="published",
    )
    with _pg() as conn, conn.cursor() as cur:
        cur.execute("SELECT count(*) FROM ai.cs_faq_query_log")
        log_before = cur.fetchone()[0]

    # 精确命中
    response = agent_client.get(
        "/cs/knowledge/search", params={"q": "p7t-运费险怎么使用"},
    )
    assert response.status_code == 200, response.text
    items = response.json()["items"]
    assert items and items[0]["score"] == 1.0 and items[0]["matched"] is True

    # 模糊命中
    response = agent_client.get(
        "/cs/knowledge/search", params={"q": "p7t-运费险怎么用"},
    )
    items = response.json()["items"]
    assert any("运费险" in item["question"] for item in items)

    # 零台账：检索不写 cs_faq_query_log
    with _pg() as conn, conn.cursor() as cur:
        cur.execute("SELECT count(*) FROM ai.cs_faq_query_log")
        assert cur.fetchone()[0] == log_before
