"""P2 RBAC 管理端 API 契约测试。

大部分测试把 PostgreSQL 作为边界替身；权限、分页、版本保护、客服身份绑定
和审计响应均走真实路由代码。最后一个 active admin 另有真实 PostgreSQL
双连接并发回归，执行与生产一致的 advisory lock 与版本更新顺序。
"""

from __future__ import annotations

import asyncio
import copy
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timezone

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from sqlalchemy import text

from backend.app.api import deps
from backend.app.api.routes import rbac
from backend.app.api.routes.rbac import router as rbac_router
from backend.config import auth as auth_config
from backend.memory.database import AsyncSessionLocal
from backend.security.session_service import (
    SessionService,
    access_session_key,
    session_index_key,
)


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


class _FakeRedis:
    def __init__(self):
        self.store: dict[str, str] = {}
        self.sets: dict[str, set[str]] = {}

    def delete(self, key):
        deleted = 0
        if self.store.pop(key, None) is not None:
            deleted += 1
        if self.sets.pop(key, None) is not None:
            deleted += 1
        return deleted

    def set(self, key, value, ex=None):
        self.store[key] = value
        return True

    def sadd(self, key, *members):
        self.sets.setdefault(key, set()).update(members)

    def smembers(self, key):
        return set(self.sets.get(key, set()))

    def expire(self, *_args):
        return True


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
                "tenant_id": "tenant-a",
            },
            {
                "id": 2,
                "username": "agent-user",
                "real_name": "客服甲",
                "dept": "客服",
                "role": "viewer",
                "status": 1,
                "version": 0,
                "tenant_id": "tenant-a",
            },
            {
                "id": 3,
                "username": "disabled-user",
                "real_name": "停用用户",
                "dept": "客服",
                "role": "viewer",
                "status": 0,
                "version": 2,
                "tenant_id": "tenant-a",
            },
            {
                "id": 4,
                "username": "unbound-user",
                "real_name": "未绑定客服",
                "dept": "客服",
                "role": "viewer",
                "status": 1,
                "version": 0,
                "tenant_id": "tenant-a",
            },
            {
                "id": 5,
                "username": "other-tenant-user",
                "real_name": "租户乙用户",
                "dept": "客服",
                "role": "viewer",
                "status": 1,
                "version": 0,
                "tenant_id": "tenant-b",
            },
        ]
        self.agents = [
            {
                "agent_id": "agent-victim",
                "tenant_id": "tenant-a",
                "auth_user_id": "99",
                "display_name": "受害坐席",
                "role": "agent",
                "max_conversations": 10,
                "enabled": True,
                "accepting": True,
            },
            {
                "agent_id": "agent-target",
                "tenant_id": "tenant-a",
                "auth_user_id": "2",
                "display_name": "客服甲",
                "role": "agent",
                "max_conversations": 10,
                "enabled": True,
                "accepting": True,
            },
        ]
        self.session_counts = {1: 2, 2: 1, 3: 0, 4: 0, 5: 0}
        self.sessions = [
            {"id": "sid-2", "user_id": 2, "revoked_at": None},
        ]
        self.refresh_tokens = [
            {"id": "rt-2", "user_id": 2, "session_id": "sid-2", "revoked": False},
            {"id": "legacy-2", "user_id": 2, "session_id": None, "revoked": False},
        ]
        self.redis = _FakeRedis()
        self.redis.set(access_session_key(2, "old-jti"), "1")
        self.redis.sadd(session_index_key(2, "sid-2"), "old-jti")
        self.audits = []
        self.committed = False
        self.rolled_back = False
        self.fail_on_audit = False
        self.force_version_conflict = False
        self.advisory_lock_calls = 0
        self._snapshot = self._state_snapshot()

    def _state_snapshot(self):
        return {
            "users": copy.deepcopy(self.users),
            "agents": copy.deepcopy(self.agents),
            "sessions": copy.deepcopy(self.sessions),
            "refresh_tokens": copy.deepcopy(self.refresh_tokens),
            "audits": copy.deepcopy(self.audits),
        }

    async def execute(self, statement, params=None):
        sql = str(statement)
        normalized = " ".join(sql.lower().split())
        params = params or {}

        if "pg_advisory_xact_lock" in normalized:
            self.advisory_lock_calls += 1
            return _FakeResult()

        if "from auth.rbac_audits" in normalized:
            rows = list(reversed(self.audits))
            tenant_id = params.get("tenant_id")
            if tenant_id is not None and "tenant_id = :tenant_id" in normalized:
                rows = [row for row in rows if row["tenant_id"] == tenant_id]
            audit_user_id = params.get("uid", params.get("user_id"))
            if audit_user_id is not None:
                rows = [row for row in rows
                        if row["target_user_id"] == audit_user_id]
            if "count(*)" in normalized:
                return _FakeResult(scalar_value=len(rows))
            return _FakeResult(rows=rows)

        if "select count(*) from auth.users u" in normalized:
            users = list(self.users)
            if "u.tenant_id = :tenant_id" in normalized:
                users = [
                    user for user in users
                    if user["tenant_id"] == params["tenant_id"]
                ]
            search = (params.get("search") or "").strip("%").lower()
            if search:
                users = [
                    user for user in users
                    if search in " ".join(
                        str(user[key]).lower()
                        for key in ("username", "real_name", "dept")
                    )
                ]
            return _FakeResult(scalar_value=len(users))

        if "count(*)" in normalized and "from auth.sessions" in normalized:
            uid = params.get("uid")
            return _FakeResult(scalar_value=self.session_counts.get(uid, 0))

        if normalized.startswith("select id from auth.users"):
            tenant_id = params.get("tenant_id")
            return _FakeResult(rows=[
                {"id": user["id"]}
                for user in self.users
                if user["role"] == "admin" and user["status"] == 1
                and (tenant_id is None or user["tenant_id"] == tenant_id)
            ])

        if "select id, user_id from auth.sessions" in normalized:
            uid = params.get("uid")
            return _FakeResult(rows=[
                dict(session)
                for session in self.sessions
                if session["user_id"] == uid and session["revoked_at"] is None
            ])

        if "select id, username, real_name, dept, role, status, version" in normalized:
            user = next(
                (
                    item for item in self.users
                    if item["id"] == params["uid"]
                    and (
                        "tenant_id = :tenant_id" not in normalized
                        or item["tenant_id"] == params["tenant_id"]
                    )
                ),
                None,
            )
            return _FakeResult(rows=[dict(user)] if user else [])

        if "from auth.users u" in normalized and "select u.id as user_id" in normalized:
            search = (params.get("search") or "").strip("%").lower()
            rows = []
            for user in self.users:
                if "u.tenant_id = :tenant_id" in normalized \
                        and user["tenant_id"] != params["tenant_id"]:
                    continue
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
            if self.force_version_conflict:
                return _FakeResult()
            if "tenant_id" in params and user["tenant_id"] != params["tenant_id"]:
                return _FakeResult()
            if "expected_version" in params and user["version"] != params["expected_version"]:
                return _FakeResult()
            for key in ("role", "status"):
                if key in params:
                    user[key] = params[key]
            user["version"] += 1
            return _FakeResult(rows=[dict(user)])

        if normalized.startswith("update auth.sessions"):
            uid = params.get("uid")
            for session in self.sessions:
                if uid is None or session["user_id"] == uid:
                    session["revoked_at"] = "revoked"
            return _FakeResult()

        if normalized.startswith("update auth.refresh_tokens"):
            uid = params.get("uid")
            sid = params.get("sid")
            for token in self.refresh_tokens:
                if (uid is not None and token["user_id"] == uid) or (
                    sid is not None and token["session_id"] == sid
                ):
                    token["revoked"] = True
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
                "display_name": params["display_name"],
                "role": params["role"],
                "max_conversations": params["max_conversations"],
                "enabled": params["enabled"],
                "accepting": params["accepting"],
            }
            self.agents.append(agent)
            return _FakeResult(rows=[dict(agent)])

        if normalized.startswith("insert into auth.rbac_audits"):
            if self.fail_on_audit:
                raise RuntimeError("审计写入失败")
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
        state = self._snapshot
        self.users = copy.deepcopy(state["users"])
        self.agents = copy.deepcopy(state["agents"])
        self.sessions = copy.deepcopy(state["sessions"])
        self.refresh_tokens = copy.deepcopy(state["refresh_tokens"])
        self.audits = copy.deepcopy(state["audits"])


def _client(monkeypatch, session, role="admin", tenant="tenant-a"):
    app = FastAPI()
    app.include_router(rbac_router, prefix="/api")

    @asynccontextmanager
    async def fake_db():
        yield session

    monkeypatch.setattr(rbac, "_db", fake_db)
    monkeypatch.setattr(deps, "get_mode", lambda _key: "enforce")
    monkeypatch.setattr(auth_config, "IDENTITY_SOURCE", "header")
    client = TestClient(app, raise_server_exceptions=False)
    client.headers.update({
        "X-Auth-Type": "jwt",
        "X-User-Id": "1",
        "X-User-Roles": role,
    })
    if tenant is not None:
        client.headers["X-Tenant-Id"] = tenant
    monkeypatch.setattr(
        rbac,
        "_session_service",
        SessionService(redis_getter=lambda: session.redis),
    )
    return client


@pytest.mark.parametrize("role", ["viewer", "editor"])
def test_rbac_users_rejects_non_admin_roles(monkeypatch, role):
    client = _client(monkeypatch, _FakeRbacSession(), role=role)
    response = client.get("/api/sys/rbac/users")
    assert response.status_code == 403


def test_client_operator_role_header_cannot_elevate(monkeypatch):
    client = _client(monkeypatch, _FakeRbacSession(), role="viewer")

    response = client.get(
        "/api/sys/rbac/users",
        headers={"X-Operator-Role": "admin"},
    )
    assert response.status_code == 403


def test_rbac_rejects_missing_trusted_tenant_instead_of_using_default(monkeypatch):
    client = _client(monkeypatch, _FakeRbacSession())
    client.headers.pop("X-Tenant-Id")

    response = client.get("/api/sys/rbac/users")

    assert response.status_code == 403


def test_admin_user_list_returns_paginated_safe_fields(monkeypatch):
    session = _FakeRbacSession()
    client = _client(monkeypatch, session)
    response = client.get("/api/sys/rbac/users?page=1&page_size=2&search=agent-user")

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


def test_concurrent_admin_conflict_is_serialized_and_mapped_to_409(monkeypatch):
    session = _FakeRbacSession()
    session.force_version_conflict = True
    client = _client(monkeypatch, session)

    response = client.patch(
        "/api/sys/rbac/users/2",
        json={"version": 0, "platformRole": "editor"},
    )

    assert response.status_code == 409
    assert session.advisory_lock_calls == 1
    assert session.rolled_back is True
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


def test_cs_profile_insert_uses_stable_display_name(monkeypatch):
    session = _FakeRbacSession()
    client = _client(monkeypatch, session)

    response = client.patch(
        "/api/sys/rbac/users/4",
        json={"version": 0, "csRole": "agent"},
    )

    assert response.status_code == 200, response.text
    created = next(item for item in session.agents if item["auth_user_id"] == "4")
    assert created["display_name"] == "未绑定客服"


@pytest.mark.parametrize("body", [{"csRole": "supervisor"}, {"csRole": None}])
def test_cs_role_change_revokes_access_and_refresh_authority(monkeypatch, body):
    session = _FakeRbacSession()
    client = _client(monkeypatch, session)

    response = client.patch(
        "/api/sys/rbac/users/2",
        json={"version": 0, **body},
    )

    assert response.status_code == 200, response.text
    assert response.json()["revokedSessionCount"] == 1
    assert session.sessions[0]["revoked_at"] is not None
    assert all(token["revoked"] for token in session.refresh_tokens)
    assert access_session_key(2, "old-jti") not in session.redis.store
    assert session.redis.smembers(session_index_key(2, "sid-2")) == set()


def test_accepting_change_revokes_session_authority(monkeypatch):
    session = _FakeRbacSession()
    client = _client(monkeypatch, session)

    response = client.patch(
        "/api/sys/rbac/users/2",
        json={"version": 0, "accepting": False},
    )

    assert response.status_code == 200, response.text
    assert response.json()["revokedSessionCount"] == 1
    assert session.sessions[0]["revoked_at"] is not None
    assert all(token["revoked"] for token in session.refresh_tokens)


def test_cross_tenant_user_list_and_update_are_isolated(monkeypatch):
    session = _FakeRbacSession()
    client = _client(monkeypatch, session, tenant="tenant-b")

    listed = client.get("/api/sys/rbac/users")
    assert listed.status_code == 200, listed.text
    assert [item["username"] for item in listed.json()["items"]] == [
        "other-tenant-user"
    ]

    cross_tenant_update = client.patch(
        "/api/sys/rbac/users/2",
        json={"version": 0, "platformRole": "editor"},
    )
    assert cross_tenant_update.status_code == 404
    assert session.users[1]["role"] == "viewer"


def test_cross_tenant_audit_isolation(monkeypatch):
    session = _FakeRbacSession()
    session.audits = [
        {
            "id": 1,
            "tenant_id": "tenant-a",
            "actor_user_id": 1,
            "target_user_id": 2,
            "action": "user.update",
            "before_state": {"platformRole": "viewer"},
            "after_state": {"platformRole": "editor"},
            "result": "success",
            "created_at": "2026-09-20T00:00:00+00:00",
        },
        {
            "id": 2,
            "tenant_id": "tenant-b",
            "actor_user_id": 5,
            "target_user_id": 5,
            "action": "user.update",
            "before_state": {"platformRole": "viewer"},
            "after_state": {"platformRole": "editor"},
            "result": "success",
            "created_at": "2026-09-20T00:01:00+00:00",
        },
    ]
    client = _client(monkeypatch, session, tenant="tenant-b")

    response = client.get("/api/sys/rbac/audit")

    assert response.status_code == 200, response.text
    assert [item["id"] for item in response.json()["items"]] == [2]


def test_real_postgres_last_admin_concurrency_is_serialized():
    """通过生产更新函数验证最后 admin 并发降权为一成功、一 409。"""
    psycopg2 = pytest.importorskip("psycopg2")
    from backend.config.database import MEMORY_DB_CONFIG

    try:
        probe = psycopg2.connect(**MEMORY_DB_CONFIG, connect_timeout=2)
    except psycopg2.OperationalError as exc:
        pytest.skip(f"PostgreSQL 不可用，跳过真实 RBAC 并发测试: {exc}")

    required_columns = {
        ("auth", "users"): {
            "id", "username", "password_hash", "real_name", "dept", "role",
            "status", "tenant_id", "version",
        },
        ("auth", "sessions"): {"user_id", "revoked_at", "revoke_reason"},
        ("auth", "refresh_tokens"): {"user_id", "revoked", "revoked_at"},
        ("auth", "rbac_audits"): {
            "tenant_id", "actor_user_id", "target_user_id", "action",
            "before_state", "after_state", "result",
        },
        ("customer_service", "cs_agents"): {
            "agent_id", "tenant_id", "auth_user_id", "display_name", "role",
            "max_conversations", "enabled", "accepting",
        },
    }
    try:
        with probe.cursor() as cursor:
            for (schema, table), columns in required_columns.items():
                cursor.execute(
                    "SELECT column_name FROM information_schema.columns "
                    "WHERE table_schema = %s AND table_name = %s "
                    "AND column_name = ANY(%s)",
                    (schema, table, list(columns)),
                )
                present = {row[0] for row in cursor.fetchall()}
                if present != columns:
                    pytest.skip(
                        f"缺少真实 RBAC 并发所需 schema: {schema}.{table} "
                        f"{sorted(columns - present)}"
                    )
    finally:
        probe.close()

    suffix = uuid.uuid4().hex[:12]
    tenant_id = f"rbac-conc-{suffix}"
    usernames = [f"rbac-a-{suffix}", f"rbac-b-{suffix}"]
    user_ids: list[int] = []
    try:
        setup = psycopg2.connect(**MEMORY_DB_CONFIG, connect_timeout=2)
    except psycopg2.OperationalError as exc:
        pytest.skip(f"PostgreSQL 在测试初始化时不可用: {exc}")
    try:
        with setup.cursor() as cursor:
            for username in usernames:
                cursor.execute(
                    "INSERT INTO auth.users "
                    "(username, password_hash, real_name, dept, role, status, "
                    "tenant_id, version) VALUES (%s, %s, %s, %s, 'admin', 1, %s, 0) "
                    "RETURNING id",
                    (username, "test-hash", username, "测试", tenant_id),
                )
                user_ids.append(cursor.fetchone()[0])
        setup.commit()
    finally:
        setup.close()

    async def run_real_rbac_concurrency() -> tuple[list[dict | None], int]:
        start_event = asyncio.Event()
        ready_lock = asyncio.Lock()
        ready_count = 0
        outcomes: list[dict | None] = [None, None]

        async def downgrade(index: int, target_id: int) -> None:
            nonlocal ready_count
            async with AsyncSessionLocal() as session:
                try:
                    async with ready_lock:
                        ready_count += 1
                        if ready_count == 2:
                            start_event.set()
                    await start_event.wait()
                    outcome = await rbac.update_user_in_transaction(
                        session,
                        user_id=target_id,
                        body={"version": 0, "platformRole": "viewer", "status": 0},
                        operator=deps.OperatorIdentity(
                            role="admin", actor=f"user:{user_ids[0]}"
                        ),
                        tenant_id=tenant_id,
                    )
                    await session.commit()
                    outcomes[index] = {
                        "status": 200,
                        "user_id": outcome.user["userId"],
                    }
                except HTTPException as exc:
                    await session.rollback()
                    outcomes[index] = {"status": exc.status_code}
                except Exception as exc:  # surfaced by the assertion below
                    await session.rollback()
                    outcomes[index] = {"status": "error", "error": repr(exc)}

        from backend.memory import database

        try:
            await asyncio.wait_for(
                asyncio.gather(
                    downgrade(0, user_ids[0]),
                    downgrade(1, user_ids[1]),
                ),
                timeout=15,
            )
            async with AsyncSessionLocal() as verify_session:
                active_admins = (await verify_session.execute(text(
                    "SELECT COUNT(*) FROM auth.users "
                    "WHERE tenant_id = :tenant_id AND role = 'admin' AND status = 1"),
                    {"tenant_id": tenant_id})).scalar_one()
            return outcomes, int(active_admins)
        finally:
            if database._engine is not None:
                await database._engine.dispose()
                database._engine = None
                database._sessionmaker = None
                database._engine_loop = None

    try:
        outcomes, active_admin_count = asyncio.run(run_real_rbac_concurrency())
        assert all(outcome is not None for outcome in outcomes), outcomes
        assert not [
            outcome for outcome in outcomes if outcome["status"] == "error"
        ], outcomes
        assert sorted(outcome["status"] for outcome in outcomes) == [200, 409]
        assert active_admin_count == 1
    finally:
        cleanup = psycopg2.connect(**MEMORY_DB_CONFIG, connect_timeout=2)
        try:
            with cleanup.cursor() as cursor:
                cursor.execute(
                    "DELETE FROM auth.refresh_tokens WHERE user_id = ANY(%s)",
                    (user_ids,),
                )
                cursor.execute(
                    "DELETE FROM auth.sessions WHERE user_id = ANY(%s)",
                    (user_ids,),
                )
                cursor.execute(
                    "DELETE FROM auth.rbac_audits WHERE tenant_id = %s",
                    (tenant_id,),
                )
                cursor.execute(
                    "DELETE FROM customer_service.cs_agents WHERE tenant_id = %s",
                    (tenant_id,),
                )
                cursor.execute(
                    "DELETE FROM auth.users WHERE id = ANY(%s)",
                    (user_ids,),
                )
            cleanup.commit()
        finally:
            cleanup.close()


def test_update_rolls_back_when_audit_write_fails(monkeypatch):
    session = _FakeRbacSession()
    session.fail_on_audit = True
    client = _client(monkeypatch, session)

    response = client.patch(
        "/api/sys/rbac/users/2",
        json={"version": 0, "platformRole": "editor"},
    )

    assert response.status_code == 500
    assert session.rolled_back is True
    assert session.committed is False
    assert session.users[1]["role"] == "viewer"
    assert session.users[1]["version"] == 0
    assert session.audits == []


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
