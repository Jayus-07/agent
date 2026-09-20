"""P2 RBAC 管理端 API 契约测试。

这些测试只把 PostgreSQL 作为边界替身；权限、分页、版本保护、客服身份绑定
和审计响应均走真实路由代码。
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import datetime, timezone

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.app.api import deps
from backend.app.api.deps import OperatorIdentity
from backend.app.api.routes import rbac
from backend.app.api.routes.rbac import router as rbac_router


class _FakeResult:
    def __init__(self, rows=None, scalar_value=None):
        self._rows = list(rows or [])
        self._scalar_value = scalar_value

    def mappings(self):
        return self

    def all(self):
        return list(self._rows)

    def first(self):
        return self._rows[0] if self._rows else None

    def scalar(self):
        return self._scalar_value

    def scalar_one(self):
        return self._scalar_value

    def scalar_one_or_none(self):
        if self._rows:
            return self._rows[0]
        return self._scalar_value


class _FakeRbacSession:
    """用最小内存状态模拟 RBAC 路由依赖的数据库边界。"""

    def __init__(self):
        self.users = [
            {
                "id": 1,
                "username": "admin",
                "real_name": "管理员",
                "dept": "运营",
                "role": "admin",
                "status": 1,
                "version": 0,
            },
            {
                "id": 2,
                "username": "agent-user",
                "real_name": "客服甲",
                "dept": "客服",
                "role": "viewer",
                "status": 1,
                "version": 0,
            },
            {
                "id": 3,
                "username": "disabled-user",
                "real_name": "停用用户",
                "dept": "客服",
                "role": "viewer",
                "status": 0,
                "version": 2,
            },
        ]
        self.agents = [
            {
                "agent_id": "agent-victim",
                "tenant_id": "tenant-a",
                "auth_user_id": "99",
                "role": "agent",
                "max_conversations": 10,
                "enabled": True,
                "accepting": True,
            },
        ]
        self.session_counts = {1: 2, 2: 1, 3: 0}
        self.audits = []
        self.committed = False
        self.rolled_back = False

    async def execute(self, statement, params=None):
        sql = str(statement)
        normalized = " ".join(sql.lower().split())
        params = params or {}

        if "from auth.rbac_audits" in normalized:
            rows = list(reversed(self.audits))
            audit_user_id = params.get("uid", params.get("user_id"))
            if audit_user_id is not None:
                rows = [row for row in rows
                        if row["target_user_id"] == audit_user_id]
            if "count(*)" in normalized:
                return _FakeResult(scalar_value=len(rows))
            return _FakeResult(rows=rows)

        if "count(*)" in normalized and "from auth.sessions" in normalized:
            uid = params.get("uid")
            return _FakeResult(scalar_value=self.session_counts.get(uid, 0))

        if "count(*)" in normalized and "from auth.users" in normalized:
            active_admins = sum(
                1 for user in self.users
                if user["role"] == "admin" and user["status"] == 1
            )
            return _FakeResult(scalar_value=active_admins)

        if normalized.startswith("select id from auth.users"):
            return _FakeResult(rows=[
                {"id": user["id"]}
                for user in self.users
                if user["role"] == "admin" and user["status"] == 1
            ])

        if "select id, user_id from auth.sessions" in normalized:
            uid = params.get("uid")
            return _FakeResult(rows=[
                {"id": f"sid-{uid}-1", "user_id": uid},
            ] if self.session_counts.get(uid, 0) else [])

        if "select id, username, real_name, dept, role, status, version" in normalized:
            user = next(
                (item for item in self.users if item["id"] == params["uid"]),
                None,
            )
            return _FakeResult(rows=[dict(user)] if user else [])

        if "from auth.users u" in normalized and "select u.id as user_id" in normalized:
            search = (params.get("search") or "").strip("%").lower()
            rows = []
            for user in self.users:
                haystack = " ".join(
                    str(user[key]).lower()
                    for key in ("username", "real_name", "dept")
                )
                if search and search not in haystack:
                    continue
                rows.append({
                    "user_id": user["id"],
                    "username": user["username"],
                    "real_name": user["real_name"],
                    "dept": user["dept"],
                    "platform_role": user["role"],
                    "status": user["status"],
                    "version": user["version"],
                    "session_count": self.session_counts.get(user["id"], 0),
                    "cs_role": None,
                    "cs_agent_id": None,
                    "cs_max_conversations": None,
                    "cs_enabled": None,
                    "cs_accepting": None,
                })
            return _FakeResult(rows=rows)

        if "from customer_service.cs_agents" in normalized and "select" in normalized:
            agent = next(
                (item for item in self.agents
                 if str(item.get("auth_user_id")) == str(params.get("uid"))
                 and item.get("tenant_id") == params.get("tenant_id", "default")),
                None,
            )
            return _FakeResult(rows=[dict(agent)] if agent else [])

        if normalized.startswith("update auth.users"):
            user = next(item for item in self.users if item["id"] == params["uid"])
            if "expected_version" in params and user["version"] != params["expected_version"]:
                return _FakeResult()
            for key in ("role", "status"):
                if key in params:
                    user[key] = params[key]
            user["version"] += 1
            return _FakeResult(rows=[dict(user)])

        if normalized.startswith("update auth.sessions"):
            return _FakeResult()

        if normalized.startswith("update auth.refresh_tokens"):
            return _FakeResult()

        if normalized.startswith("update customer_service.cs_agents"):
            agent = next(
                item for item in self.agents
                if item["agent_id"] == params["agent_id"]
                and item["tenant_id"] == params["tenant_id"]
            )
            for key in ("role", "max_conversations", "enabled", "accepting"):
                if key in params:
                    agent[key] = params[key]
            return _FakeResult(rows=[dict(agent)])

        if normalized.startswith("insert into customer_service.cs_agents"):
            agent = {
                "agent_id": params["agent_id"],
                "tenant_id": params["tenant_id"],
                "auth_user_id": str(params["auth_user_id"]),
                "role": params["role"],
                "max_conversations": params["max_conversations"],
                "enabled": params["enabled"],
                "accepting": params["accepting"],
            }
            self.agents.append(agent)
            return _FakeResult(rows=[dict(agent)])

        if normalized.startswith("insert into auth.rbac_audits"):
            self.audits.append({
                "id": len(self.audits) + 1,
                "tenant_id": params["tenant_id"],
                "actor_user_id": params["actor_user_id"],
                "target_user_id": params["target_user_id"],
                "action": params["action"],
                "before_state": params["before_state"],
                "after_state": params["after_state"],
                "result": params["result"],
                "created_at": datetime.now(timezone.utc),
            })
            return _FakeResult()

        raise AssertionError(f"测试替身未覆盖 SQL: {sql}")

    async def commit(self):
        self.committed = True

    async def rollback(self):
        self.rolled_back = True


def _client(monkeypatch, session, role="admin"):
    app = FastAPI()
    app.include_router(rbac_router, prefix="/api")

    @asynccontextmanager
    async def fake_db():
        yield session

    monkeypatch.setattr(rbac, "_db", fake_db)
    if role == "admin":
        app.dependency_overrides[rbac.require_admin_user] = lambda: OperatorIdentity(
            role=role, actor="user:1"
        )
    else:
        def deny_non_admin():
            from fastapi import HTTPException
            raise HTTPException(status_code=403, detail="仅 admin")

        app.dependency_overrides[rbac.require_admin_user] = deny_non_admin
    return TestClient(app, raise_server_exceptions=False)


@pytest.mark.parametrize("role", ["viewer", "editor"])
def test_rbac_users_rejects_non_admin_roles(monkeypatch, role):
    client = _client(monkeypatch, _FakeRbacSession(), role=role)
    response = client.get("/api/sys/rbac/users")
    assert response.status_code == 403


def test_client_operator_role_header_cannot_elevate(monkeypatch):
    app = FastAPI()
    app.include_router(rbac_router, prefix="/api")
    monkeypatch.setattr(deps, "get_mode", lambda _key: "enforce")
    app.dependency_overrides[deps.resolve_operator_role] = lambda: OperatorIdentity(
        role="viewer", actor="user:2"
    )
    client = TestClient(app, raise_server_exceptions=False)

    response = client.get(
        "/api/sys/rbac/users",
        headers={"X-Operator-Role": "admin"},
    )
    assert response.status_code == 403


def test_admin_user_list_returns_paginated_safe_fields(monkeypatch):
    session = _FakeRbacSession()
    client = _client(monkeypatch, session)
    response = client.get("/api/sys/rbac/users?page=1&page_size=2&search=客服")

    assert response.status_code == 200
    body = response.json()
    assert body["page"] == 1
    assert body["pageSize"] == 2
    assert body["total"] == 1
    assert body["items"][0]["username"] == "agent-user"
    assert set(body["items"][0]) == {
        "userId", "username", "realName", "dept", "platformRole", "status",
        "version", "sessionCount", "csAgent",
    }
    assert "password_hash" not in response.text


def test_update_rejects_invalid_role_and_version_conflict(monkeypatch):
    session = _FakeRbacSession()
    client = _client(monkeypatch, session)

    invalid = client.patch(
        "/api/sys/rbac/users/2",
        json={"version": 0, "platformRole": "owner"},
    )
    assert invalid.status_code == 400

    conflict = client.patch(
        "/api/sys/rbac/users/2",
        json={"version": 9, "platformRole": "editor"},
    )
    assert conflict.status_code == 409
    assert session.committed is False


def test_last_active_admin_cannot_be_downgraded_or_disabled(monkeypatch):
    session = _FakeRbacSession()
    client = _client(monkeypatch, session)

    response = client.patch(
        "/api/sys/rbac/users/1",
        json={"version": 0, "platformRole": "viewer"},
    )
    assert response.status_code == 409
    assert session.users[0]["role"] == "admin"
    assert session.committed is False


def test_cs_profile_ignores_browser_agent_id_and_binds_target_user(monkeypatch):
    session = _FakeRbacSession()
    client = _client(monkeypatch, session)

    response = client.patch(
        "/api/sys/rbac/users/2",
        json={
            "version": 0,
            "csRole": "agent",
            "agentId": "agent-victim",
            "maxConversations": 12,
        },
    )
    assert response.status_code == 200, response.text
    victim = next(item for item in session.agents
                  if item["agent_id"] == "agent-victim")
    assert victim["auth_user_id"] == "99"
    target = [item for item in session.agents if item["auth_user_id"] == "2"]
    assert len(target) == 1
    assert target[0]["role"] == "agent"


def test_update_writes_complete_audit_record(monkeypatch):
    session = _FakeRbacSession()
    client = _client(monkeypatch, session)

    response = client.patch(
        "/api/sys/rbac/users/2",
        json={
            "version": 0,
            "platformRole": "editor",
            "csRole": "supervisor",
            "maxConversations": 20,
            "enabled": False,
            "accepting": False,
        },
    )
    assert response.status_code == 200, response.text
    assert session.committed is True

    audit = client.get("/api/sys/rbac/audit?page=1&page_size=50")
    assert audit.status_code == 200
    record = audit.json()["items"][0]
    assert record["actorUserId"] == 1
    assert record["targetUserId"] == 2
    assert record["oldPlatformRole"] == "viewer"
    assert record["newPlatformRole"] == "editor"
    assert record["csChanges"]["role"] == "supervisor"
    assert record["result"] == "success"
    assert record["createdAt"]
