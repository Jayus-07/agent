"""test_refresh_claims_latest.py — login/refresh 必须携带 DB 最新身份

任务书 §23/§55（授权收口 2026-09-23）：管理员修改 department/roles 后，
旧 access token 内是旧 claim；正确语义：
  - refresh → 重新读取 DB（user/tenant/department/roles）→ 新 access token
    携带最新身份（绝不从旧 JWT claim 复制）；
  - 重新登录 → 同样读 DB 最新值。

用 legacy refresh token（session_id=NULL，023 上线前形态）验证：该路径
不做会话吊销检查，refresh 纯凭 DB 状态轮换 → 可隔离验证「refresh 重读
DB」这一条（新版 session token 在管理员变更时会被整会话吊销，走重新
登录路径，属另一语义）。
"""
import uuid

import psycopg2
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.app.api.routes import auth_local
from backend.config.database import MEMORY_DB_CONFIG
from backend.security.local_jwt import (
    hash_password,
    hash_refresh_token,
    new_refresh_token,
    verify_access_token,
)

TENANT = "tenant-refresh-e2e"


def _require_pg() -> None:
    try:
        with psycopg2.connect(**MEMORY_DB_CONFIG, connect_timeout=2) as conn:
            with conn.cursor() as cursor:
                cursor.execute(
                    "SELECT COUNT(*) = 1 FROM information_schema.tables "
                    "WHERE table_schema='auth' AND table_name='departments'")
                ready = cursor.fetchone()[0]
    except Exception as exc:
        pytest.skip(f"agent_memory PostgreSQL 不可达: {exc}")
    if not ready:
        pytest.skip("auth.departments 缺少 041 表（迁移未应用），跳过")


@pytest.fixture()
def client():
    app = FastAPI()
    app.include_router(auth_local.router, prefix="/api")
    app.include_router(auth_local.sys_router, prefix="/api")
    c = TestClient(app, raise_server_exceptions=False)
    c.headers["X-Tenant-Id"] = TENANT
    return c


def _db_exec(sql: str, params=()) -> None:
    with psycopg2.connect(**MEMORY_DB_CONFIG) as conn:
        with conn.cursor() as cursor:
            cursor.execute(sql, params)
        conn.commit()


def _db_one(sql: str, params=()):
    with psycopg2.connect(**MEMORY_DB_CONFIG) as conn:
        with conn.cursor() as cursor:
            cursor.execute(sql, params)
            return cursor.fetchone()


def _make_legacy_refresh(user_id: int) -> str:
    raw, token_hash = new_refresh_token()
    _db_exec(
        "INSERT INTO auth.refresh_tokens (user_id, token_hash, device_id, "
        "expires_at) VALUES (%s, %s, '', now() + interval '7 days')",
        (user_id, token_hash))
    return raw


@pytest.mark.parametrize("endpoint", ["/api/auth/refresh"])
def test_refresh_and_login_carry_latest_db_identity(client, endpoint):
    _require_pg()
    prefix = f"rfr{uuid.uuid4().hex[:8]}"
    username = f"{prefix}a"
    _db_exec(
        "INSERT INTO auth.departments (tenant_id, code, name) "
        "VALUES (%s, 'general', '通用') ON CONFLICT (tenant_id, code) DO NOTHING",
        (TENANT,))
    _db_exec(
        "INSERT INTO auth.users (username, password_hash, real_name, dept, "
        "role, status, tenant_id, version) "
        "VALUES (%s, %s, '刷新测试', 'general', 'viewer', 1, %s, 0)",
        (username, hash_password("TestPass#123"), TENANT))
    user_id = _db_one("SELECT id FROM auth.users WHERE username = %s",
                      (username,))[0]
    try:
        # ── 初始 refresh：JWT 携带 DB 当前部门 general ──
        raw1 = _make_legacy_refresh(user_id)
        r1 = client.post(endpoint, cookies={"refresh_token": raw1})
        assert r1.status_code == 200, r1.text
        claims1 = verify_access_token(r1.json()["data"]["token"])
        assert claims1 is not None and claims1["dept"] == "general"
        assert claims1["roles"] == ["viewer"]

        # ── 管理员改部门 + 角色（直接落库模拟；legacy token 无会话不受吊销影响）──
        _db_exec("UPDATE auth.users SET dept = 'hr', role = 'editor' WHERE id = %s",
                 (user_id,))

        # ── 再次 refresh：新 token 必须携带 hr/editor（重读 DB，不是复制旧 claim）──
        raw2 = _make_legacy_refresh(user_id)
        r2 = client.post(endpoint, cookies={"refresh_token": raw2})
        assert r2.status_code == 200, r2.text
        claims2 = verify_access_token(r2.json()["data"]["token"])
        assert claims2 is not None
        assert claims2["dept"] == "hr", "refresh 未重读 DB：dept 仍是旧 claim"
        assert claims2["roles"] == ["editor"], "refresh 未重读 DB：roles 仍是旧 claim"
        # 响应体 userInfo 同步最新（P9：dept 下发供前端只读展示）
        assert r2.json()["data"]["userInfo"]["dept"] == "hr"

        # ── 重新登录：同样读 DB 最新值 ──
        login = client.post("/api/auth/login", json={
            "username": username, "password": "TestPass#123"})
        assert login.status_code == 200, login.text
        claims3 = verify_access_token(login.json()["data"]["token"])
        assert claims3 is not None
        assert claims3["dept"] == "hr"
        assert claims3["roles"] == ["editor"]
        assert login.json()["data"]["userInfo"]["dept"] == "hr"

        # ── 禁用用户后 refresh 拒绝（status!=1 → 401）──
        _db_exec("UPDATE auth.users SET status = 0 WHERE id = %s", (user_id,))
        raw3 = _make_legacy_refresh(user_id)
        r3 = client.post(endpoint, cookies={"refresh_token": raw3})
        assert r3.status_code == 401
    finally:
        _db_exec(
            "DELETE FROM auth.refresh_tokens WHERE user_id = %s", (user_id,))
        _db_exec("DELETE FROM auth.sessions WHERE user_id = %s", (user_id,))
        _db_exec("DELETE FROM auth.rbac_audits WHERE target_user_id = %s",
                 (user_id,))
        _db_exec("DELETE FROM auth.users WHERE id = %s", (user_id,))
        _db_exec("DELETE FROM auth.departments WHERE tenant_id = %s", (TENANT,))
