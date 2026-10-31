"""cs-workstation-v2 P6：质检中心（抽样/评分/申诉/终审/日报）T2 回归。

真库集成（agent_memory@5433，p6t- 前缀行用后即清）：
- 抽样去重：同会话全生命周期至多一条抽检任务（UNIQUE + 二次抽样 0 新增），
  任务创建即带 AI 预填（source=ai / status=pending）
- 评分状态机：pending → draft → scored（不可回退，scored 再评 409）
- 申诉状态机：scored → appealing → upheld|overturned（终态锁定，再申诉/再终审 409）
- 权限矩阵：agent 仅本人任务（他人任务 404/403 口径）、终审仅 supervisor、
  改判必须带终审分+结论（422）、跨租户 404
- AI 预填纯函数扣分口径 + 端点幂等
- 日报聚合正确性（/cs/qa/report 与 qa_report._collect_metrics 的 qa_sampling 段
  含人工抽检数据；改判以终审分/结论为有效口径）
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import psycopg2
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.app.api.routes import cs_agent_offers, cs_qa
from backend.customer_service.qa_sampling import compute_ai_prefill

TENANT = "tenant-p6t"
TENANT_OTHER = "tenant-p6t-other"
_PREFIX = "p6t-"
_BATCH = datetime.now(timezone.utc).date()


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
    # tenant_id 形如 tenant-p6t / tenant-p6t-other：用包含匹配，避免前缀
    # 模式删不到残留行（p6t-conv-* UniqueViolation 实证）
    with _pg() as conn, conn.cursor() as cur:
        cur.execute(
            "DELETE FROM customer_service.cs_qa_scores"
            " WHERE tenant_id LIKE %s OR conversation_id LIKE %s",
            ("%p6t%", _PREFIX + "%"),
        )
        cur.execute(
            "DELETE FROM customer_service.handoffs"
            " WHERE tenant_id LIKE %s AND conversation_id LIKE %s",
            ("%p6t%", _PREFIX + "%"),
        )
        cur.execute(
            "DELETE FROM customer_service.conversations"
            " WHERE tenant_id LIKE %s AND conversation_id LIKE %s",
            ("%p6t%", _PREFIX + "%"),
        )
        cur.execute(
            "DELETE FROM customer_service.cs_agents WHERE tenant_id LIKE %s",
            ("%p6t%",),
        )
        conn.commit()


def _seed_agent(agent_id: str, tenant_id: str = TENANT) -> None:
    with _pg() as conn, conn.cursor() as cur:
        cur.execute(
            "INSERT INTO customer_service.cs_agents"
            " (agent_id, tenant_id, display_name, enabled, status)"
            " VALUES (%s, %s, %s, TRUE, 'online')"
            " ON CONFLICT DO NOTHING",
            (agent_id, tenant_id, agent_id),
        )
        conn.commit()


def _seed_conversation(
    conversation_id: str,
    *,
    tenant_id: str = TENANT,
    rating: int | None = None,
    first_reply_seconds: float | None = None,
) -> None:
    """会话行（rating/首响供 AI 预填事实用）。

    注意：conversations 无 'closed' 状态（CHECK 限 open/pending/resolved/
    snoozed），关闭事实在 handoffs —— 见 _seed_closed_handoff。
    """
    now = datetime.now(timezone.utc)
    first_reply = (
        now + timedelta(seconds=first_reply_seconds)
        if first_reply_seconds is not None
        else None
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
                conversation_id,
                _PREFIX + "user-1",
                tenant_id,
                rating,
                now,
                now,
                now,
                first_reply,
            ),
        )
        conn.commit()


def _seed_closed_handoff(
    conversation_id: str,
    *,
    agent_id: str = _PREFIX + "agent-a",
    tenant_id: str = TENANT,
    attempt_count: int = 1,
) -> None:
    """关闭态 handoff 行：抽样主表口径（关闭事实+被检坐席在此）。"""
    now = datetime.now(timezone.utc)
    with _pg() as conn, conn.cursor() as cur:
        cur.execute(
            "INSERT INTO customer_service.handoffs"
            " (handoff_id, conversation_id, user_id, tenant_id,"
            "  handoff_state, assigned_agent_id, attempt_count,"
            "  created_at, updated_at, closed_at)"
            " VALUES (%s, %s, %s, %s, 'closed', %s, %s, %s, %s, %s)",
            (
                _PREFIX + "ho-" + conversation_id,
                conversation_id,
                _PREFIX + "user-1",
                tenant_id,
                agent_id,
                attempt_count,
                now,
                now,
                now,
            ),
        )
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


def _make_client(context: cs_agent_offers.AgentContext) -> TestClient:
    app = FastAPI()
    app.include_router(cs_qa.router)
    app.dependency_overrides[cs_qa.require_agent] = lambda: context
    return TestClient(app, raise_server_exceptions=False)


@pytest.fixture
def db():
    _cleanup()
    yield
    _cleanup()


@pytest.fixture
def agent_client():
    return _make_client(_context(agent_id=_PREFIX + "agent-a"))


@pytest.fixture
def other_agent_client():
    return _make_client(_context(agent_id=_PREFIX + "agent-b"))


@pytest.fixture
def sup_client():
    return _make_client(_context(agent_id=_PREFIX + "sup", cs_role="supervisor"))


def _seed_task(
    *,
    conversation_id: str,
    agent_id: str,
    tenant_id: str = TENANT,
    status: str = "pending",
    scores: tuple[int, int, int, int] | None = None,
    verdict: str | None = None,
    appeal_status: str = "none",
    final: tuple[int, str] | None = None,
    batch_date=None,
) -> int:
    total = sum(scores) if scores else None
    with _pg() as conn, conn.cursor() as cur:
        cur.execute(
            "INSERT INTO customer_service.cs_qa_scores"
            " (tenant_id, conversation_id, agent_id, batch_date, source,"
            "  score_service, score_accuracy, score_compliance,"
            "  score_timeliness, total_score, verdict, status, scored_by,"
            "  appeal_status, final_score, final_verdict)"
            " VALUES (%s, %s, %s, %s, 'human', %s, %s, %s, %s, %s, %s, %s,"
            "         %s, %s, %s, %s) RETURNING id",
            (
                tenant_id,
                conversation_id,
                agent_id,
                batch_date or _BATCH,
                scores[0] if scores else None,
                scores[1] if scores else None,
                scores[2] if scores else None,
                scores[3] if scores else None,
                total,
                verdict,
                status,
                _PREFIX + "sup" if status == "scored" else None,
                appeal_status,
                final[0] if final else None,
                final[1] if final else None,
            ),
        )
        task_id = cur.fetchone()[0]
        conn.commit()
    return task_id


# ── AI 预填纯函数（零 LLM 确定性口径）──────────────────────────


def test_ai_prefill_no_deduction_is_full_score(db):
    prefill = compute_ai_prefill({})
    assert prefill["scores"] == {
        "service": 25, "accuracy": 25, "compliance": 25, "timeliness": 25,
    }
    assert prefill["total"] == 100
    assert prefill["verdict"] == "pass"
    assert "source=ai" in prefill["note"]
    assert "人工终审" in prefill["note"]


def test_ai_prefill_deduction_rules(db):
    prefill = compute_ai_prefill({
        "rating": 2,                # 服务规范 −15
        "first_reply_seconds": 120, # 处理时效 −5（>60s）
        "attempt_count": 2,         # 准确性 −4（转接/重派一次）
        "sla_breach": True,         # 合规 −12
    })
    assert prefill["scores"] == {
        "service": 10, "accuracy": 21, "compliance": 13, "timeliness": 20,
    }
    assert prefill["total"] == 64
    assert prefill["verdict"] == "pass"

    fail = compute_ai_prefill({
        "rating": 1, "first_reply_seconds": 700, "attempt_count": 3,
        "sla_breach": True,
    })
    # 10 + 17 + 13 + 13 = 53：严重信号可给出 fail 建议
    assert fail["verdict"] == "fail"
    # clamp 下限 0
    zero = compute_ai_prefill({"rating": 1, "first_reply_seconds": 10**6})
    assert min(zero["scores"].values()) >= 0


# ── 抽样去重（真库）────────────────────────────────────────────


def test_sampling_dedup_and_ai_prefill_created(db):
    import asyncio

    from backend.memory.database import AsyncSessionLocal
    from backend.customer_service.qa_sampling import sample_for_tenant

    _seed_agent(_PREFIX + "agent-a")
    _seed_conversation(_PREFIX + "conv-s1", rating=2, first_reply_seconds=120)
    _seed_closed_handoff(_PREFIX + "conv-s1")

    async def _run():
        async with AsyncSessionLocal() as session, session.begin():
            first = await sample_for_tenant(
                session, tenant_id=TENANT, batch_date=_BATCH, limit=10,
            )
            second = await sample_for_tenant(
                session, tenant_id=TENANT, batch_date=_BATCH, limit=10,
            )
        return first, second

    first, second = asyncio.run(_run())
    assert first == 1
    assert second == 0  # 抽样去重：同会话至多一条

    with _pg() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT source, status, ai_prefill, agent_id"
            " FROM customer_service.cs_qa_scores"
            " WHERE tenant_id = %s AND conversation_id = %s",
            (TENANT, _PREFIX + "conv-s1"),
        )
        source, status, prefill, agent_id = cur.fetchone()
    assert source == "ai"
    assert status == "pending"
    assert agent_id == _PREFIX + "agent-a"
    payload = prefill if isinstance(prefill, dict) else json.loads(prefill)
    assert payload["scores"]["service"] == 10  # rating=2 → −15
    assert payload["scores"]["timeliness"] == 20  # 120s → −5


# ── 评分状态机 + 权限矩阵（真库）───────────────────────────────


def test_score_state_machine_draft_then_submit_locked(db, agent_client):
    _seed_agent(_PREFIX + "agent-a")
    task_id = _seed_task(conversation_id=_PREFIX + "conv-m1",
                         agent_id=_PREFIX + "agent-a")

    # draft
    response = agent_client.post(
        f"/cs/qa/tasks/{task_id}/score",
        json={"service": 20, "accuracy": 22, "compliance": 21,
              "timeliness": 17, "verdict": "pass", "draft": True},
    )
    assert response.status_code == 200, response.text
    assert response.json()["task"]["status"] == "draft"

    # submit
    response = agent_client.post(
        f"/cs/qa/tasks/{task_id}/score",
        json={"service": 22, "accuracy": 23, "compliance": 22,
              "timeliness": 18, "verdict": "pass"},
    )
    assert response.status_code == 200, response.text
    body = response.json()["task"]
    assert body["status"] == "scored"
    assert body["total_score"] == 85
    assert body["source"] == "human"

    # scored 锁定：再评 409
    response = agent_client.post(
        f"/cs/qa/tasks/{task_id}/score",
        json={"service": 25, "accuracy": 25, "compliance": 25,
              "timeliness": 25, "verdict": "pass"},
    )
    assert response.status_code == 409

    # 非法维度越界 422
    response = agent_client.post(
        f"/cs/qa/tasks/{task_id}/score",
        json={"service": 26, "accuracy": 0, "compliance": 0,
              "timeliness": 0, "verdict": "pass"},
    )
    assert response.status_code == 422


def test_score_permission_agent_only_own(db, agent_client, sup_client):
    _seed_agent(_PREFIX + "agent-a")
    _seed_agent(_PREFIX + "agent-b")
    other_task = _seed_task(conversation_id=_PREFIX + "conv-p1",
                            agent_id=_PREFIX + "agent-b")
    body = {"service": 20, "accuracy": 20, "compliance": 20,
            "timeliness": 20, "verdict": "pass"}
    # agent-b 的任务对 agent-a 不可见（他人任务防枚举 404，列表同口径）
    response = agent_client.post(f"/cs/qa/tasks/{other_task}/score", json=body)
    assert response.status_code == 404
    # supervisor 可评任意任务
    response = sup_client.post(f"/cs/qa/tasks/{other_task}/score", json=body)
    assert response.status_code == 200, response.text


def test_cross_tenant_task_is_404(db, agent_client):
    task_id = _seed_task(
        conversation_id=_PREFIX + "conv-x1",
        agent_id=_PREFIX + "agent-a",
        tenant_id=TENANT_OTHER,
    )
    response = agent_client.post(
        f"/cs/qa/tasks/{task_id}/score",
        json={"service": 20, "accuracy": 20, "compliance": 20,
              "timeliness": 20, "verdict": "pass"},
    )
    assert response.status_code == 404


def test_appeal_state_machine_and_permissions(db, agent_client,
                                              other_agent_client, sup_client):
    _seed_agent(_PREFIX + "agent-a")
    _seed_agent(_PREFIX + "agent-b")
    task_id = _seed_task(
        conversation_id=_PREFIX + "conv-a1",
        agent_id=_PREFIX + "agent-a",
        status="scored",
        scores=(12, 13, 13, 12),
        verdict="fail",
    )

    # 未评分任务不可申诉
    pending_id = _seed_task(conversation_id=_PREFIX + "conv-a2",
                            agent_id=_PREFIX + "agent-a")
    response = agent_client.post(
        f"/cs/qa/tasks/{pending_id}/appeal", json={"reason": "太早了"},
    )
    assert response.status_code == 409

    # 他人任务不可见（防枚举 404）；supervisor 可见但不可代为申诉 → 403
    response = other_agent_client.post(
        f"/cs/qa/tasks/{task_id}/appeal", json={"reason": "不是我的任务"},
    )
    assert response.status_code == 404
    response = sup_client.post(
        f"/cs/qa/tasks/{task_id}/appeal", json={"reason": "主管代申诉"},
    )
    assert response.status_code == 403

    # 本人申诉 → appealing；重复申诉 409
    response = agent_client.post(
        f"/cs/qa/tasks/{task_id}/appeal",
        json={"reason": "已提前告知物流延迟，处理仍在时效内"},
    )
    assert response.status_code == 200, response.text
    assert response.json()["task"]["appeal_status"] == "appealing"
    response = agent_client.post(
        f"/cs/qa/tasks/{task_id}/appeal", json={"reason": "再试"},
    )
    assert response.status_code == 409
    # 申诉中不可再评分
    response = agent_client.post(
        f"/cs/qa/tasks/{task_id}/score",
        json={"service": 25, "accuracy": 25, "compliance": 25,
              "timeliness": 25, "verdict": "pass"},
    )
    assert response.status_code == 409

    # 终审仅 supervisor → 403；改判缺终审分 → 422
    response = agent_client.post(
        f"/cs/qa/tasks/{task_id}/appeal/decision",
        json={"decision": "upheld", "opinion": "坐席自己批"},
    )
    assert response.status_code == 403
    response = sup_client.post(
        f"/cs/qa/tasks/{task_id}/appeal/decision",
        json={"decision": "overturned", "opinion": "改判"},
    )
    assert response.status_code == 422

    # 改判 → 终态锁定；再终审/再申诉 409；有效口径切终审分
    response = sup_client.post(
        f"/cs/qa/tasks/{task_id}/appeal/decision",
        json={"decision": "overturned", "opinion": "回复仍在时效内",
              "final_score": 75, "final_verdict": "pass"},
    )
    assert response.status_code == 200, response.text
    body = response.json()["task"]
    assert body["appeal_status"] == "overturned"
    assert body["total_score"] == 75  # 展示口径切终审分
    assert body["verdict"] == "pass"
    assert body["final_score"] == 75
    response = sup_client.post(
        f"/cs/qa/tasks/{task_id}/appeal/decision",
        json={"decision": "upheld", "opinion": "再改"},
    )
    assert response.status_code == 409
    response = agent_client.post(
        f"/cs/qa/tasks/{task_id}/appeal", json={"reason": "终态后再申诉"},
    )
    assert response.status_code == 409

    # 维持路径（另一任务）
    keep_id = _seed_task(
        conversation_id=_PREFIX + "conv-a3",
        agent_id=_PREFIX + "agent-a",
        status="scored",
        scores=(20, 20, 20, 20),
        verdict="pass",
    )
    agent_client.post(f"/cs/qa/tasks/{keep_id}/appeal", json={"reason": "异议"})
    response = sup_client.post(
        f"/cs/qa/tasks/{keep_id}/appeal/decision",
        json={"decision": "upheld", "opinion": "评分无误"},
    )
    assert response.status_code == 200
    assert response.json()["task"]["appeal_status"] == "upheld"
    assert response.json()["task"]["final_score"] is None


def test_list_scope_agent_self_supervisor_all(db, agent_client, sup_client):
    _seed_agent(_PREFIX + "agent-a")
    _seed_agent(_PREFIX + "agent-b")
    mine = _seed_task(conversation_id=_PREFIX + "conv-l1",
                      agent_id=_PREFIX + "agent-a")
    theirs = _seed_task(conversation_id=_PREFIX + "conv-l2",
                        agent_id=_PREFIX + "agent-b")

    response = agent_client.get("/cs/qa/tasks")
    assert response.status_code == 200
    body = response.json()
    ids = {item["id"] for item in body["items"]}
    assert mine in ids and theirs not in ids
    assert body["scope"] == "self"

    response = sup_client.get("/cs/qa/tasks")
    ids = {item["id"] for item in response.json()["items"]}
    assert mine in ids and theirs in ids
    assert response.json()["scope"] == "all"

    # status 过滤非法值 422
    assert agent_client.get(
        "/cs/qa/tasks", params={"status": "bogus"},
    ).status_code == 422


def test_ai_prefill_endpoint_idempotent(db, agent_client):
    _seed_agent(_PREFIX + "agent-a")
    _seed_conversation(_PREFIX + "conv-f1", rating=3)
    _seed_closed_handoff(_PREFIX + "conv-f1")
    task_id = _seed_task(conversation_id=_PREFIX + "conv-f1",
                         agent_id=_PREFIX + "agent-a")
    with _pg() as conn, conn.cursor() as cur:
        cur.execute(
            "UPDATE customer_service.cs_qa_scores SET ai_prefill = NULL"
            " WHERE id = %s", (task_id,),
        )
        conn.commit()

    response = agent_client.post(f"/cs/qa/tasks/{task_id}/ai-prefill")
    assert response.status_code == 200, response.text
    first = response.json()["task"]["ai_prefill"]
    assert first["scores"]["service"] == 21  # rating=3 → −4

    response = agent_client.post(f"/cs/qa/tasks/{task_id}/ai-prefill")
    assert response.json()["task"]["ai_prefill"] == first  # 幂等不重算


# ── 日报聚合（含人工抽检数据）──────────────────────────────────


def test_qa_report_aggregation(db, sup_client, agent_client):
    _seed_agent(_PREFIX + "agent-a")
    # t1: scored pass 80（服务规范 20 扣 5）
    _seed_task(conversation_id=_PREFIX + "conv-r1",
               agent_id=_PREFIX + "agent-a", status="scored",
               scores=(20, 22, 19, 19), verdict="pass")
    # t2: scored fail 50 → 申诉改判 75 pass（有效口径切终审）
    appealed = _seed_task(conversation_id=_PREFIX + "conv-r2",
                          agent_id=_PREFIX + "agent-a", status="scored",
                          scores=(12, 13, 13, 12), verdict="fail")
    agent_client.post(f"/cs/qa/tasks/{appealed}/appeal",
                      json={"reason": "时效异议"})
    sup_client.post(
        f"/cs/qa/tasks/{appealed}/appeal/decision",
        json={"decision": "overturned", "opinion": "改判",
              "final_score": 75, "final_verdict": "pass"},
    )
    # t3: scored fail 50（各维全 <25）
    _seed_task(conversation_id=_PREFIX + "conv-r3",
               agent_id=_PREFIX + "agent-a", status="scored",
               scores=(12, 12, 13, 13), verdict="fail")
    # t4: pending（未评分，计抽检数不计合格率）
    _seed_task(conversation_id=_PREFIX + "conv-r4",
               agent_id=_PREFIX + "agent-a", status="pending")
    # 他租户行不串数
    _seed_task(conversation_id=_PREFIX + "conv-r5",
               agent_id=_PREFIX + "agent-a", tenant_id=TENANT_OTHER,
               status="scored", scores=(25, 25, 25, 25), verdict="pass")

    response = sup_client.get("/cs/qa/report", params={"days": 7})
    assert response.status_code == 200, response.text
    today = _BATCH.isoformat()
    rows = {item["batch_date"]: item for item in response.json()["items"]}
    assert today in rows
    row = rows[today]
    assert row["sampled"] == 4      # 本租户 4 条（他租户不计）
    assert row["scored"] == 3
    assert row["pass_count"] == 2   # 80 + 改判 75
    assert row["pass_rate"] == round(2 / 3, 4)
    assert row["avg_score"] == round((80 + 75 + 50) / 3, 1)  # 68.3
    assert row["appeals"] == 1
    # 扣分维度计数：service=2(20,12,12→3 条中 20/12/12 都<25 → 3)、
    # accuracy=3、compliance=3、timeliness=3 → top 允许任一 3 计数维，
    # 但 80 分行 service=20<25 → service 计 3；验证 top 是 3 计数之一
    assert row["top_deduction"] is not None
    assert "（3）" in row["top_deduction"]

    # agent 不可看日报
    assert agent_client.get("/cs/qa/report").status_code == 403

    # 日报（beat 口径）qa_sampling 段含人工抽检数据
    import asyncio

    from backend.customer_service.qa_report import _collect_metrics

    metrics = asyncio.run(_collect_metrics(_BATCH, TENANT))
    sampling = metrics["qa_sampling"]
    assert sampling["sampled"] == 4
    assert sampling["scored"] == 3
    assert sampling["pass_count"] == 2
    assert sampling["pass_rate"] == round(2 / 3, 4)
    assert sampling["appeals"] == 1
