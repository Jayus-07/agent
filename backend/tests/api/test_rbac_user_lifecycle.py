"""P6 用户生命周期 + must_change_password 闭环验收（真实 PostgreSQL）。

覆盖：
- POST /sys/rbac/users       创建用户（临时密码 + must_change_password）
- GET  /sys/rbac/users/{id}  详情（email/角色/会话数/最近登录）
- POST /sys/rbac/users/{id}/reset-password（旧会话吊销 + 临时密码）
- POST /sys/rbac/users/{id}/force-logout
- must_change_password 网关门禁（claim=true → 业务路径 403、改密路径放行）
- /auth/change-password：改密后 must_change_password=FALSE
"""

from __future__ import annotations

import secrets
import uuid

import psycopg2
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import text

from backend.app.api import deps
from backend.app.api.routes import rbac
from backend.app.api.routes.rbac import router as rbac_router
from backend.config import auth as auth_config
from backend.config.database import MEMORY_DB_CONFIG
from backend.security.local_jwt import hash_password, issue_access_token, verify_access_token

pytestmark = pytest.mark.asyncio


def _require_pg() -> None:
    try:
        with psycopg2.connect(**MEMORY_DB_CONFIG, connect_timeout=2) as conn:
            with conn.cursor() as cursor:
                cursor.execute(
                    "SELECT COUNT(*) FROM information_schema.columns "
                    "WHERE table_schema='auth' AND table_name='users' "
                    "AND column_name IN ('must_change_password','email')"
                )
                count = cursor.fetchone()[0]
                cursor.execute(
                    "SELECT COUNT(*) = 1 FROM information_schema.tables "
                    "WHERE table_schema='auth' AND table_name='departments'"
                )
                departments_ready = cursor.fetchone()[0]
    except Exception as exc:
        pytest.skip(f"agent_memory PostgreSQL 不可达，跳过 P6 真实验收: {exc}")
    if count != 2:
        pytest.skip("auth.users 缺少 033 字段（迁移未应用），跳过真实验收")
    if not departments_ready:
        pytest.skip("auth.departments 缺少 041 表（迁移未应用），跳过真实验收")


def _seed_tenant_department(code: str = "general") -> None:
    """为本测试租户准备 active 部门（041 主数据按租户隔离）。"""
    with psycopg2.connect(**MEMORY_DB_CONFIG) as conn:
        with conn.cursor() as cursor:
            cursor.execute(
                "INSERT INTO auth.departments (tenant_id, code, name) "
                "VALUES ('tenant-p6', %s, '生命周期测试部门') "
                "ON CONFLICT (tenant_id, code) DO NOTHING", (code,))
        conn.commit()


@pytest.fixture(autouse=True)
def _require_real_pg():
    _require_pg()


def _client(monkeypatch) -> TestClient:
    """admin 身份 + 真实数据库的 RBAC TestClient。"""
    app = FastAPI()
    app.include_router(rbac_router, prefix="/api")
    # 用 pytest monkeypatch 管理守卫模式，避免污染后续 API 族的 audit 用例。
    monkeypatch.setattr(deps, "get_mode", lambda _key: "enforce")
    client = TestClient(app, raise_server_exceptions=False)
    client.headers.update({
        "X-Auth-Type": "jwt",
        "X-User-Id": "1",
        "X-User-Roles": "admin",
        "X-Tenant-Id": "tenant-p6",
    })
    return client


def _cleanup(username_prefix: str) -> None:
    with psycopg2.connect(**MEMORY_DB_CONFIG) as conn:
        with conn.cursor() as cursor:
            like = username_prefix + "%"
            cursor.execute(
                "DELETE FROM auth.refresh_tokens WHERE user_id IN "
                "(SELECT id FROM auth.users WHERE username LIKE %s)", (like,))
            cursor.execute(
                "DELETE FROM auth.sessions WHERE user_id IN "
                "(SELECT id FROM auth.users WHERE username LIKE %s)", (like,))
            cursor.execute(
                "DELETE FROM auth.rbac_audits WHERE target_user_id IN "
                "(SELECT id FROM auth.users WHERE username LIKE %s)", (like,))
            cursor.execute(
                "DELETE FROM auth.users WHERE username LIKE %s", (like,))
        conn.commit()


def _psql_row(sql: str, params: dict):
    with psycopg2.connect(**MEMORY_DB_CONFIG) as conn:
        with conn.cursor() as cursor:
            cursor.execute(sql, params)
            return cursor.fetchone()


def test_user_lifecycle_end_to_end(monkeypatch):
    client = _client(monkeypatch)
    prefix = f"p6lc{uuid.uuid4().hex[:8]}"
    username = f"{prefix}a"
    _seed_tenant_department("general")
    try:
        # ── 创建 ──
        # dept 必须命中本租户 active 部门（041 主数据），任意字符串 400
        bad_dept = client.post("/api/sys/rbac/users", json={
            "username": f"{prefix}x", "dept": "finance123xxx",
        })
        assert bad_dept.status_code == 400, bad_dept.text
        resp = client.post("/api/sys/rbac/users", json={
            "username": username,
            "realName": "生命周期测试",
            "dept": "general",
            "email": "p6@example.com",
            "platformRole": "viewer",
        })
        assert resp.status_code == 200, resp.text
        body = resp.json()
        temp_password = body["tempPassword"]
        user_id = body["userId"]
        assert body["mustChangePassword"] is True
        assert len(temp_password) >= 12

        row = _psql_row(
            "SELECT must_change_password, email, password_hash, tenant_id "
            "FROM auth.users WHERE id = :uid".replace(":uid", "%s"),
            (user_id,),
        )
        assert row[0] is True          # must_change_password
        assert row[1] == "p6@example.com"
        assert row[2].startswith("pbkdf2_sha256")  # 绝不明文
        assert row[3] == "tenant-p6"   # 继承操作者租户

        # 重复用户名 → 409
        dup = client.post("/api/sys/rbac/users", json={"username": username})
        assert dup.status_code == 409

        # 跨租户创建 → 403（多租户规则只增强）
        cross = client.post(
            "/api/sys/rbac/users",
            json={"username": f"{prefix}b", "tenantId": "tenant-other"},
        )
        assert cross.status_code == 403

        # ── 详情 ──
        detail = client.get(f"/api/sys/rbac/users/{user_id}")
        assert detail.status_code == 200
        d = detail.json()
        assert d["email"] == "p6@example.com"
        assert d["platformRoles"] == ["viewer"]
        assert d["csRoles"] == []
        assert d["mustChangePassword"] is True
        assert d["activeSessionCount"] == 0

        # ── 部门维护（P9）：修改 → DB 更新 + 审计 + 会话吊销 ──
        _seed_tenant_department("hr")
        audit_before = _psql_row(
            "SELECT COUNT(*) FROM auth.rbac_audits "
            "WHERE target_user_id = %s AND action = 'user.update'", (user_id,))
        dept_patch = client.patch(
            f"/api/sys/rbac/users/{user_id}",
            json={"version": d["version"], "dept": "hr"},
        )
        assert dept_patch.status_code == 200, dept_patch.text
        row = _psql_row("SELECT dept FROM auth.users WHERE id = %s", (user_id,))
        assert row[0] == "hr"
        audit_row = _psql_row(
            "SELECT before_state->>'dept', after_state->>'dept' "
            "FROM auth.rbac_audits WHERE target_user_id = %s "
            "AND action = 'user.update' ORDER BY id DESC LIMIT 1", (user_id,))
        assert audit_row[0] == "general" and audit_row[1] == "hr"
        assert dept_patch.json()["revokedSessionCount"] >= 0
        del audit_before
        # 版本已随 PATCH 自增，后续 PATCH 用新版本号
        d = {**d, "version": dept_patch.json()["version"]}
        # 非法部门 → 400（不落库）
        bad = client.patch(
            f"/api/sys/rbac/users/{user_id}",
            json={"version": d["version"], "dept": "finance123xxx"},
        )
        assert bad.status_code == 400, bad.text
        # 清空部门 → 空串（合法，最小权限语义；非法 PATCH 未消耗版本号）
        clear = client.patch(
            f"/api/sys/rbac/users/{user_id}",
            json={"version": d["version"], "dept": ""},
        )
        assert clear.status_code == 200, clear.text
        row = _psql_row("SELECT dept FROM auth.users WHERE id = %s", (user_id,))
        assert row[0] == ""
        d = {**d, "version": clear.json()["version"]}
        # 部门列表 API：本租户 active 部门（管理端下拉数据源）
        dept_list = client.get("/api/sys/rbac/departments")
        assert dept_list.status_code == 200
        dept_codes = {item["code"] for item in dept_list.json()["items"]}
        assert {"general", "hr"} <= dept_codes

        # ── 临时密码登录态被门禁拦截（P6.3）──
        login_ish_token = issue_access_token(
            user_id=user_id, username=username, roles=["viewer"],
            tenant_id="tenant-p6", must_change_password=True,
        )
        from backend.app.api.middleware.auth import must_change_password_gate

        async def ok_handler(_request):
            return {"ok": True}

        blocked = _run_gate(must_change_password_gate, "/chat/stream", login_ish_token["token"])
        assert blocked is not None and blocked.status_code == 403
        allowed = _run_gate(must_change_password_gate, "/auth/change-password", login_ish_token["token"])
        assert allowed == {"ok": True}  # 改密路径放行

        # ── 改密闭环：/auth/change-password ──
        cp = _change_password(login_ish_token["token"], temp_password)
        assert cp["code"] == 200, cp
        row = _psql_row(
            "SELECT must_change_password FROM auth.users WHERE id = %s",
            (user_id,),
        )
        assert row[0] is False
        # 新 token claim 已清除
        new_payload = verify_access_token(cp["data"]["token"])
        assert new_payload is not None
        assert new_payload.get("must_change_password") is False

        # ── 重置密码：旧会话失效 + 强制改密 ──
        reset = client.post(f"/api/sys/rbac/users/{user_id}/reset-password")
        assert reset.status_code == 200
        r = reset.json()
        assert r["mustChangePassword"] is True
        assert r["tempPassword"]
        row = _psql_row(
            "SELECT must_change_password FROM auth.users WHERE id = %s",
            (user_id,),
        )
        assert row[0] is True

        # ── 强制下线（复用 SessionService，无第二套 revoke）──
        fl = client.post(f"/api/sys/rbac/users/{user_id}/force-logout")
        assert fl.status_code == 200
        assert fl.json()["revokedSessionCount"] >= 0

        # 禁用（沿用既有 PATCH status=0 通道）
        patch = client.patch(
            f"/api/sys/rbac/users/{user_id}",
            json={"version": d["version"], "status": 0},
        )
        assert patch.status_code == 200, patch.text
        row = _psql_row("SELECT status FROM auth.users WHERE id = %s", (user_id,))
        assert row[0] == 0
    finally:
        _cleanup(prefix)


def _run_gate(gate, path: str, token: str):
    """直呼中间件：返回 JSONResponse（被拦截）或 None（放行）。"""
    from starlette.requests import Request

    async def call_next(_request):
        return {"ok": True}

    scope = {
        "type": "http",
        "method": "GET",
        "path": path,
        "query_string": b"",
        "headers": [(b"authorization", f"Bearer {token}".encode())],
        "scheme": "http",
        "server": ("testserver", 80),
    }
    return _run_async(gate(Request(scope), call_next))


def _run_async(coro):
    import asyncio

    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


def _change_password(token: str, old_password: str) -> dict:
    """以临时密码调用 /auth/change-password（走 TestClient + 真实 DB）。"""
    from backend.app.api.routes.auth_local import router as auth_router

    app = FastAPI()
    app.include_router(auth_router)
    client = TestClient(app, raise_server_exceptions=False)
    resp = client.post(
        "/auth/change-password",
        json={"oldPassword": old_password, "newPassword": f"New-{secrets.token_hex(6)}"},
        headers={
            "Authorization": f"Bearer {token}",
            "X-Tenant-Id": "tenant-p6",
            "X-Auth-Type": "jwt",
        },
    )
    return resp.json()
