"""cs-workstation-v2 P4：快捷回复话术 CRUD 权限矩阵 T2 回归。

真库集成（agent_memory@5433，p4t- 前缀行用后即清）：
- POST scope=personal：任何坐席可建，owner 归属正确
- POST scope=team：supervisor 可建、普通坐席 403
- GET：本人 personal + 全部 team；他人 personal 不出现
- PATCH/DELETE：本人 personal 可操作；他人 personal 404（口径一致）；
  team 由 supervisor 可写、普通坐席 403
- 跨租户 id → 404；非法 body → 422
"""
from __future__ import annotations

import psycopg2
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.app.api.routes import cs_agent_offers, cs_canned

TENANT = "tenant-p4t"
TENANT_OTHER = "tenant-p4t-other"
_PREFIX = "p4t-"


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
            "DELETE FROM customer_service.cs_canned_responses"
            " WHERE tenant_id LIKE %s",
            (_PREFIX + "%",),
        )
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
        user_id=f"p4t-user-{agent_id}",
        cs_role=cs_role,
        platform_roles=("viewer",),
    )


@pytest.fixture
def client(monkeypatch):
    app = FastAPI()
    app.include_router(cs_canned.router)
    ctx = _context(agent_id=_PREFIX + "agent-a")
    app.dependency_overrides[cs_canned.require_agent] = lambda: ctx
    return TestClient(app, raise_server_exceptions=False), ctx


@pytest.fixture
def supervisor_client():
    app = FastAPI()
    app.include_router(cs_canned.router)
    ctx = _context(
        agent_id=_PREFIX + "sup", cs_role="supervisor",
    )
    app.dependency_overrides[cs_canned.require_agent] = lambda: ctx
    return TestClient(app, raise_server_exceptions=False), ctx


def test_create_personal_returns_owner(db, client):
    http, _ctx = client
    response = http.post(
        "/cs/canned-responses",
        json={"title": "开场问候", "content": "您好，很高兴为您服务",
              "scope": "personal"},
    )
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["scope"] == "personal"
    with _pg() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT tenant_id, scope, owner_agent_id, category"
            " FROM customer_service.cs_canned_responses WHERE id = %s",
            (body["id"],),
        )
        tenant_id, scope, owner, category = cur.fetchone()
    assert tenant_id == TENANT
    assert scope == "personal"
    assert owner == _PREFIX + "agent-a"
    assert category == "通用"


def test_create_team_requires_supervisor(db, client, supervisor_client):
    http, _ctx = client
    response = http.post(
        "/cs/canned-responses",
        json={"title": "退款政策", "content": "7 天无理由", "scope": "team"},
    )
    assert response.status_code == 403

    sup_http, _ctx2 = supervisor_client
    response = sup_http.post(
        "/cs/canned-responses",
        json={"title": "退款政策", "content": "7 天无理由", "scope": "team"},
    )
    assert response.status_code == 201, response.text
    assert response.json()["scope"] == "team"


def test_create_rejects_invalid_body(db, client):
    http, _ctx = client
    response = http.post(
        "/cs/canned-responses",
        json={"title": "  ", "content": "x", "scope": "personal"},
    )
    assert response.status_code == 422
    response = http.post(
        "/cs/canned-responses",
        json={"title": "t", "content": "x", "scope": "org"},
    )
    assert response.status_code == 422


def test_list_shows_own_personal_and_team_only(db, client, supervisor_client):
    http, ctx = client
    own = http.post(
        "/cs/canned-responses",
        json={"title": "我的话术", "content": "a", "scope": "personal"},
    ).json()
    sup_http, _ctx2 = supervisor_client
    team = sup_http.post(
        "/cs/canned-responses",
        json={"title": "团队话术", "content": "b", "scope": "team"},
    ).json()
    # 他人（supervisor 的）个人话术不应出现在 agent-a 的列表
    other_personal = sup_http.post(
        "/cs/canned-responses",
        json={"title": "主管私人", "content": "c", "scope": "personal"},
    ).json()

    response = http.get("/cs/canned-responses")
    assert response.status_code == 200
    ids = {item["id"] for item in response.json()["items"]}
    assert own["id"] in ids
    assert team["id"] in ids
    assert other_personal["id"] not in ids

    response = sup_http.get("/cs/canned-responses")
    ids = {item["id"] for item in response.json()["items"]}
    assert own["id"] not in ids  # agent-a 的个人话术对主管不可见
    assert other_personal["id"] in ids


def test_update_own_personal(db, client):
    http, _ctx = client
    created = http.post(
        "/cs/canned-responses",
        json={"title": "旧标题", "content": "旧内容", "scope": "personal"},
    ).json()
    response = http.patch(
        f"/cs/canned-responses/{created['id']}",
        json={"title": "新标题", "enabled": False},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["title"] == "新标题"
    assert body["content"] == "旧内容"
    assert body["enabled"] is False


def test_update_other_personal_is_404(db, client, supervisor_client):
    http, _ctx = client
    sup_http, _ctx2 = supervisor_client
    other = sup_http.post(
        "/cs/canned-responses",
        json={"title": "主管私人", "content": "c", "scope": "personal"},
    ).json()
    response = http.patch(
        f"/cs/canned-responses/{other['id']}",
        json={"title": "抢改"},
    )
    assert response.status_code == 404


def test_update_team_requires_supervisor(db, client, supervisor_client):
    sup_http, _ctx2 = supervisor_client
    team = sup_http.post(
        "/cs/canned-responses",
        json={"title": "团队话术", "content": "b", "scope": "team"},
    ).json()
    http, _ctx = client
    response = http.patch(
        f"/cs/canned-responses/{team['id']}",
        json={"content": "越权改"},
    )
    assert response.status_code == 403
    response = sup_http.patch(
        f"/cs/canned-responses/{team['id']}",
        json={"content": "主管改"},
    )
    assert response.status_code == 200


def test_delete_permissions(db, client, supervisor_client):
    http, ctx = client
    own = http.post(
        "/cs/canned-responses",
        json={"title": "删我", "content": "x", "scope": "personal"},
    ).json()
    response = http.delete(f"/cs/canned-responses/{own['id']}")
    assert response.status_code == 204
    # 删除后不可再删（404）
    response = http.delete(f"/cs/canned-responses/{own['id']}")
    assert response.status_code == 404

    sup_http, _ctx2 = supervisor_client
    team = sup_http.post(
        "/cs/canned-responses",
        json={"title": "团队删", "content": "y", "scope": "team"},
    ).json()
    response = http.delete(f"/cs/canned-responses/{team['id']}")
    assert response.status_code == 403
    response = sup_http.delete(f"/cs/canned-responses/{team['id']}")
    assert response.status_code == 204


def test_cross_tenant_is_404(db, client):
    http, _ctx = client
    with _pg() as conn, conn.cursor() as cur:
        cur.execute(
            "INSERT INTO customer_service.cs_canned_responses"
            " (tenant_id, scope, owner_agent_id, title, content)"
            " VALUES (%s, 'personal', %s, '别家', 'secret') RETURNING id",
            (TENANT_OTHER, _PREFIX + "agent-z"),
        )
        foreign_id = cur.fetchone()[0]
        conn.commit()
    response = http.get(f"/cs/canned-responses")
    ids = {item["id"] for item in response.json()["items"]}
    assert foreign_id not in ids
    assert http.patch(
        f"/cs/canned-responses/{foreign_id}", json={"title": "x"},
    ).status_code == 404
    assert http.delete(f"/cs/canned-responses/{foreign_id}").status_code == 404
