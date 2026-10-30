"""RBAC 部门目录管理接口契约：租户隔离、版本并发、审计和安全停用。"""

from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import datetime, timezone

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.app.api import deps
from backend.app.api.routes import rbac
from backend.app.api.routes.rbac import router as rbac_router
from backend.config import auth as auth_config
from backend.security.session_service import SessionService


class _Result:
    def __init__(self, *, rows=None, scalar=None):
        self._rows = list(rows or [])
        self._scalar = scalar

    def mappings(self):
        return self

    def all(self):
        return list(self._rows)

    def first(self):
        return self._rows[0] if self._rows else None

    def scalar(self):
        return self._scalar


class _DepartmentSession:
    def __init__(self):
        now = datetime(2026, 10, 21, tzinfo=timezone.utc)
        self.departments = [
            {"tenant_id": "tenant-a", "code": "alpha", "name": "研发部",
             "status": 1, "version": 0, "updated_at": now,
             "department_type": "general", "manager_user_id": None},
            {"tenant_id": "tenant-a", "code": "paused", "name": "停用部门",
             "status": 0, "version": 2, "updated_at": now,
             "department_type": "general", "manager_user_id": None},
            {"tenant_id": "tenant-b", "code": "foreign", "name": "租户乙部门",
             "status": 1, "version": 0, "updated_at": now,
             "department_type": "general", "manager_user_id": None},
        ]
        self.users = [
            {"tenant_id": "tenant-a", "dept": "alpha"},
            {"tenant_id": "tenant-b", "dept": "alpha"},
        ]
        self.audits = []
        self.committed = False
        self.rolled_back = False

    def _department_row(self, department):
        row = dict(department)
        row["member_count"] = sum(
            user["tenant_id"] == department["tenant_id"]
            and user["dept"] == department["code"]
            for user in self.users
        )
        row.setdefault("manager_username", None)
        row.setdefault("manager_real_name", None)
        return row

    async def execute(self, statement, params=None):
        sql = " ".join(str(statement).lower().split())
        params = params or {}
        if "information_schema.tables" in sql:
            return _Result(scalar=True)
        if "from auth.departments d" in sql:
            if ("d.code = :code" in sql
                    and ("for update of d" in sql
                         or "m.username as manager_username" in sql)):
                # 单部门行读（_change_department 前后各一次）：不过滤停用部门
                department = next((
                    item for item in self.departments
                    if item["tenant_id"] == params["tenant_id"]
                    and item["code"] == params["code"]
                ), None)
                return _Result(
                    rows=[self._department_row(department)]
                    if department else [])
            rows = []
            for department in self.departments:
                if department["tenant_id"] != params["tenant_id"]:
                    continue
                if not params.get("include_inactive") and department["status"] != 1:
                    continue
                rows.append(self._department_row(department))
            rows.sort(key=lambda row: (-row["status"], row["name"], row["code"]))
            return _Result(rows=rows)
        if "select 1 from auth.departments" in sql and "code <> :code" in sql:
            # 同租户唯一启用客服部门校验
            exists = any(
                item["tenant_id"] == params["tenant_id"]
                and item["code"] != params["code"]
                and item["status"] == 1
                and item["department_type"] == "customer_service"
                for item in self.departments
            )
            return _Result(rows=[{"?column?": 1}] if exists else [])
        if sql.startswith("select 1 from auth.departments"):
            exists = any(
                item["tenant_id"] == params["tenant_id"]
                and item["code"] == params["code"]
                for item in self.departments
            )
            return _Result(rows=[{"?column?": 1}] if exists else [])
        if sql.startswith("select 1 from customer_service.cs_agents"):
            return _Result()
        if sql.startswith("select count(*) from auth.users"):
            count = sum(
                user["tenant_id"] == params["tenant_id"]
                and user["dept"] == params["code"]
                for user in self.users
            )
            return _Result(scalar=count)
        if sql.startswith("select id from auth.users"):
            exists = any(
                item["tenant_id"] == params["tenant_id"]
                and item["dept"] == params["code"]
                for item in self.users
            )
            return _Result(rows=[{"id": 1}] if exists else [])
        if sql.startswith("insert into auth.departments"):
            row = {
                "tenant_id": params["tenant_id"], "code": params["code"],
                "name": params["name"], "status": 1, "version": 0,
                "updated_at": datetime.now(timezone.utc),
                "department_type": params.get("department_type", "general"),
                "manager_user_id": params.get("manager_user_id"),
            }
            self.departments.append(row)
            return _Result(rows=[dict(row)])
        if sql.startswith("update auth.departments"):
            department = next((
                item for item in self.departments
                if item["tenant_id"] == params["tenant_id"]
                and item["code"] == params["code"]
                and item["version"] == params["version"]
            ), None)
            if department is None:
                return _Result()
            department.update({
                "name": params["name"], "status": params["status"],
                "department_type": params["department_type"],
                "manager_user_id": params["manager_user_id"],
                "version": department["version"] + 1,
                "updated_at": datetime.now(timezone.utc),
            })
            return _Result(rows=[dict(department)])
        if sql.startswith("insert into auth.department_audits"):
            self.audits.append(dict(params))
            return _Result()
        raise AssertionError(f"测试替身未覆盖 SQL: {sql}")

    async def commit(self):
        self.committed = True

    async def rollback(self):
        self.rolled_back = True


def _client(monkeypatch, session, *, role="admin", tenant="tenant-a"):
    app = FastAPI()
    app.include_router(rbac_router, prefix="/api")

    @asynccontextmanager
    async def fake_db():
        yield session

    monkeypatch.setattr(rbac, "_db", fake_db)
    monkeypatch.setattr(deps, "get_mode", lambda _key: "enforce")
    monkeypatch.setattr(auth_config, "IDENTITY_SOURCE", "header")
    monkeypatch.setattr(
        rbac, "_session_service", SessionService(redis_getter=lambda: None),
    )
    client = TestClient(app, raise_server_exceptions=False)
    client.headers.update({
        "X-Auth-Type": "jwt", "X-User-Id": "7", "X-User-Roles": role,
    })
    if tenant is not None:
        client.headers["X-Tenant-Id"] = tenant
    return client


def test_department_list_is_tenant_scoped_and_inactive_is_opt_in(monkeypatch):
    session = _DepartmentSession()
    client = _client(monkeypatch, session)

    active = client.get("/api/sys/rbac/departments")
    all_departments = client.get("/api/sys/rbac/departments?include_inactive=true")

    assert active.status_code == 200
    assert [item["code"] for item in active.json()["items"]] == ["alpha"]
    assert active.json()["items"][0]["memberCount"] == 1
    assert {item["code"] for item in all_departments.json()["items"]} == {"alpha", "paused"}


def test_department_create_uses_trusted_tenant_and_audits(monkeypatch):
    session = _DepartmentSession()
    client = _client(monkeypatch, session)

    created = client.post("/api/sys/rbac/departments", json={
        "code": "Mkt_Team", "name": "市场部", "tenantId": "tenant-a",
    })
    cross_tenant = client.post("/api/sys/rbac/departments", json={
        "code": "other", "name": "其他租户", "tenantId": "tenant-b",
    })

    assert created.status_code == 201
    assert created.json()["code"] == "mkt_team"
    assert session.audits[0]["actor_user_id"] == 7
    assert session.audits[0]["action"] == "department.create"
    assert cross_tenant.status_code == 403


def test_department_update_uses_version_and_writes_before_after_audit(monkeypatch):
    session = _DepartmentSession()
    client = _client(monkeypatch, session)

    updated = client.patch(
        "/api/sys/rbac/departments/alpha",
        json={"version": 0, "name": "平台研发部"},
    )
    stale = client.patch(
        "/api/sys/rbac/departments/alpha",
        json={"version": 0, "name": "过期修改"},
    )

    assert updated.status_code == 200
    assert updated.json()["name"] == "平台研发部"
    assert updated.json()["version"] == 1
    assert updated.json()["memberCount"] == 1
    assert stale.status_code == 409
    assert session.audits[0]["action"] == "department.update"
    assert '"name": "研发部"' in session.audits[0]["before_state"]
    assert '"name": "平台研发部"' in session.audits[0]["after_state"]


def test_department_deactivation_requires_no_members_and_is_reversible(monkeypatch):
    session = _DepartmentSession()
    client = _client(monkeypatch, session)

    blocked = client.delete("/api/sys/rbac/departments/alpha?version=0")
    assert blocked.status_code == 409
    assert session.departments[0]["status"] == 1

    session.users = [user for user in session.users if user["tenant_id"] != "tenant-a"]
    stopped = client.delete("/api/sys/rbac/departments/alpha?version=0")
    enabled = client.patch(
        "/api/sys/rbac/departments/alpha",
        json={"version": 1, "status": 1},
    )

    assert stopped.status_code == 200
    assert stopped.json()["status"] == 0
    assert enabled.status_code == 200
    assert enabled.json()["status"] == 1
    assert [audit["action"] for audit in session.audits] == [
        "department.deactivate", "department.activate",
    ]


@pytest.mark.parametrize("role", ["viewer", "editor"])
def test_department_writes_reject_non_admin(monkeypatch, role):
    client = _client(monkeypatch, _DepartmentSession(), role=role)

    response = client.patch(
        "/api/sys/rbac/departments/alpha",
        json={"version": 0, "name": "越权改名"},
    )

    assert response.status_code == 403
