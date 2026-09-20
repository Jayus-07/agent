"""test_auth_session_family.py — 会话实体改造回归（2026-09-19）

会话实体 = auth.sessions 一行（一次设备登录 = refresh token family），
access token 携带 sid claim，refresh 轮换归入同一 session_id。

覆盖成功标准 S1-S8（docs/superpowers/plans/2026-09-19-会话实体改造-设备登录session.md）：
  S1 同浏览器登录一次 → 活跃会话 1 条
  S2 连续 refresh 5 次 → 行数不变、sid 不变、last_active_at 单调
  S3 两个不同 deviceId → 活跃会话 2 条
  S4 同 deviceId 二次登录 → 旧会话 revoked（reason=replaced），活跃仍 1
  S5 超宽限期重放已吊销 refresh → 401 + 整会话吊销
  S6 宽限期内并发同 hash 刷新 → 均成功、同一 sid、不整吊销
  S7 按 session 强制下线 → DB 吊销 + refresh 全吊销 + Redis 闸键删除
  S8 Redis 不可用 → 列表仍返回 DB 数据

基础设施：真 PostgreSQL（agent_memory，MEMORY_DB_CONFIG，测试用户随机用户名
并级联清理）；Redis 全部打 fake（auth_local.get_redis）。
"""
import uuid

import psycopg2
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.app.api.routes import auth_local, rbac
from backend.config.database import MEMORY_DB_CONFIG
from backend.security.local_jwt import hash_password, verify_access_token
from backend.security.session_service import access_session_key

ADMIN_HEADERS = {
    "X-Auth-Type": "jwt",
    "X-User-Id": "1",
    "X-User-Roles": "admin",
    "X-Tenant-Id": "default",
}


class FakeRedis:
    """set/delete/ttl/scan + 会话索引集合（sadd/smembers/srem/expire）。"""

    def __init__(self):
        self.store: dict[str, str] = {}
        self.sets: dict[str, set[str]] = {}
        self.ttls: dict[str, int] = {}

    def set(self, key, value, ex=None):
        self.store[key] = value
        self.ttls[key] = ex

    def delete(self, key):
        n = 1 if self.store.pop(key, None) is not None else 0
        return n + (1 if self.sets.pop(key, None) is not None else 0)

    def ttl(self, key):
        return self.ttls.get(key, -2)

    def sadd(self, key, *members):
        self.sets.setdefault(key, set()).update(members)

    def smembers(self, key):
        return set(self.sets.get(key, set()))

    def srem(self, key, *members):
        s = self.sets.get(key)
        if s:
            s.difference_update(members)

    def expire(self, key, ttl):
        if key in self.store or key in self.sets:
            self.ttls[key] = ttl

    def scan_iter(self, match=None, count=None):
        for k in list(self.store):
            if match and k.startswith(match.rstrip("*")):
                yield k


def _pg(sql, params=()):
    """执行 SQL 并提交；SELECT 返回全部行，其余语句返回 None。"""
    with psycopg2.connect(**MEMORY_DB_CONFIG) as conn:
        with conn.cursor() as cur:
            cur.execute(sql, params)
            rows = cur.fetchall() if cur.description else None
            return rows


@pytest.fixture()
def env(monkeypatch):
    monkeypatch.setenv("JWT_SECRET", "k" * 32)
    monkeypatch.setenv("SENSITIVE_API_GUARD_MODE", "enforce")
    fr = FakeRedis()
    monkeypatch.setattr("backend.infra.redis.client.get_redis", lambda: fr)
    monkeypatch.setattr("backend.app.api.routes.auth_local.get_redis", lambda: fr)
    monkeypatch.setattr(
        rbac,
        "_session_service",
        auth_local.SessionService(redis_getter=lambda: fr),
    )
    app = FastAPI()
    app.include_router(auth_local.router, prefix="/api")
    app.include_router(auth_local.sys_router, prefix="/api")
    app.include_router(rbac.router, prefix="/api")
    yield TestClient(app), fr


@pytest.fixture()
def user():
    """随机用户名建号（role=admin 便于联表展示），级联清理 sessions/refresh_tokens。"""
    username = f"sesfam-{uuid.uuid4().hex[:10]}"
    with psycopg2.connect(**MEMORY_DB_CONFIG) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO auth.users "
                "(username, password_hash, real_name, role, tenant_id) "
                "VALUES (%s, %s, '会话测试', 'admin', 'default') RETURNING id",
                (username, hash_password("pw-123456")))
            uid = cur.fetchone()[0]
        conn.commit()
    yield {"id": uid, "username": username, "password": "pw-123456"}
    with psycopg2.connect(**MEMORY_DB_CONFIG) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "DELETE FROM customer_service.cs_agents WHERE auth_user_id = %s",
                (str(uid),),
            )
            cur.execute("DELETE FROM auth.users WHERE id = %s", (uid,))
        conn.commit()


def _login(
    client,
    user,
    device_id="dev-A",
    user_agent="TestUA/1.0",
    tenant_id="default",
):
    r = client.post("/api/auth/login", json={
        "username": user["username"], "password": user["password"],
        "deviceId": device_id}, headers={
            "User-Agent": user_agent,
            "X-Tenant-Id": tenant_id,
    })
    assert r.status_code == 200, r.text
    client.headers.update({"X-Tenant-Id": tenant_id})
    return r


def _active_sessions(uid):
    return _pg("SELECT id, revoked_at, revoke_reason FROM auth.sessions "
               "WHERE user_id = %s AND revoked_at IS NULL "
               "AND refresh_expires_at > now()", (uid,))


def _cookie_of(client):
    return client.cookies.get("refresh_token")


def _use_cookie(client, raw):
    """用指定 refresh cookie 发起刷新（模拟另一标签页的独立 cookie 状态）。"""
    client.cookies.set("refresh_token", raw)
    return client.post("/api/auth/refresh")


# ── S1：同浏览器登录一次 → 1 条 ──────────────────────────────

def test_s1_single_login_single_session(env, user):
    client, _ = env
    _login(client, user, device_id="dev-A")
    r = client.get("/api/sys/security/sessions", headers=ADMIN_HEADERS)
    assert r.status_code == 200
    sessions = r.json()["data"]["sessions"]
    mine = [s for s in sessions if s["userId"] == user["id"]]
    assert len(mine) == 1
    s = mine[0]
    assert s["device"] == "dev-A"
    assert s["userAgent"] == "TestUA/1.0"
    assert s["username"] == user["username"]
    assert s["createdAt"] and s["lastActiveAt"] and s["expiresAt"]


# ── S2：连续 refresh 5 次 → 会话实体不变 ─────────────────────

def test_s2_refresh_keeps_same_session(env, user):
    client, _ = env
    resp = _login(client, user)
    sid0 = verify_access_token(resp.json()["data"]["token"])["sid"]

    last_active = None
    for i in range(5):
        r = client.post("/api/auth/refresh")
        assert r.status_code == 200, r.text
        sid = verify_access_token(r.json()["data"]["token"])["sid"]
        assert sid == sid0 and sid != ""
        rows = _pg("SELECT count(*), max(last_active_at) FROM auth.sessions "
                   "WHERE user_id = %s", (user["id"],))
        count, last_active = rows[0]
        assert count == 1
    # 行数不变 + last_active_at 已被 refresh 推进（≥ 创建时刻）
    created, = _pg("SELECT created_at FROM auth.sessions WHERE user_id = %s",
                   (user["id"],))[0]
    assert last_active >= created


# ── S3：不同设备 → 2 条 ─────────────────────────────────────

def test_s3_two_devices_two_sessions(env, user):
    client, _ = env
    _login(client, user, device_id="dev-A")
    _login(client, user, device_id="dev-B")
    assert len(_active_sessions(user["id"])) == 2
    r = client.get("/api/sys/security/sessions", headers=ADMIN_HEADERS)
    mine = [s for s in r.json()["data"]["sessions"] if s["userId"] == user["id"]]
    assert {s["device"] for s in mine} == {"dev-A", "dev-B"}


# ── S4：同设备二次登录 → 替换旧会话 ──────────────────────────

def test_s4_same_device_relogin_replaces(env, user):
    client, _ = env
    _login(client, user, device_id="dev-A")
    _login(client, user, device_id="dev-A")

    rows = _pg("SELECT id, revoked_at, revoke_reason FROM auth.sessions "
               "WHERE user_id = %s", (user["id"],))
    revoked = [r for r in rows if r[1] is not None]
    assert len(revoked) == 1 and revoked[0][2] == "replaced"
    assert len(_active_sessions(user["id"])) == 1


# ── S5：超宽限期重放 → 整会话吊销 + 401 ──────────────────────

def test_s5_replay_beyond_grace_revokes_family(env, user):
    client, _ = env
    _login(client, user)
    old_cookie = _cookie_of(client)
    # 正常轮换一次：old_cookie 吊销（revoked_at≈now，宽限期内）
    r = client.post("/api/auth/refresh")
    assert r.status_code == 200
    sid = verify_access_token(r.json()["data"]["token"])["sid"]

    # 把旧行的 revoked_at 回拨 120s（模拟超出 60s 宽限期的重放，免真实等待）
    _pg("UPDATE auth.refresh_tokens SET revoked_at = now() - interval '120 seconds' "
        "WHERE session_id = %s AND revoked = TRUE", (sid,))

    r = _use_cookie(client, old_cookie)
    assert r.status_code == 401
    # 整会话吊销：sessions.revoked_at 非空 + 家族全部 refresh revoked
    session_row = _pg("SELECT revoked_at, revoke_reason FROM auth.sessions "
                      "WHERE id = %s", (sid,))[0]
    assert session_row[0] is not None and session_row[1] == "replay_detected"
    active_tokens, = _pg("SELECT count(*) FROM auth.refresh_tokens "
                         "WHERE session_id = %s AND revoked = FALSE", (sid,))[0]
    assert active_tokens == 0


# ── S6：宽限期内同 hash 并发刷新 → 均成功、同一 sid ───────────

def test_s6_concurrent_refresh_within_grace(env, user):
    client, _ = env
    _login(client, user)
    old_cookie = _cookie_of(client)
    r1 = client.post("/api/auth/refresh")   # 标签页 A：轮换成功
    assert r1.status_code == 200
    sid_a = verify_access_token(r1.json()["data"]["token"])["sid"]
    r2 = _use_cookie(client, old_cookie)    # 标签页 B：还在用旧 cookie（宽限期内）
    assert r2.status_code == 200, r2.text
    sid_b = verify_access_token(r2.json()["data"]["token"])["sid"]
    assert sid_b == sid_a and sid_b != ""
    # 不触发整吊销：会话仍活跃
    assert len(_active_sessions(user["id"])) == 1


# ── S7：按 session 强制下线 ─────────────────────────────────

def test_s7_force_logout_by_session(env, user):
    client, fr = env
    resp = _login(client, user)
    sid = verify_access_token(resp.json()["data"]["token"])["sid"]
    jti = verify_access_token(resp.json()["data"]["token"])["jti"]
    assert fr.store.get(f"auth:session:{user['id']}:{jti}") == "1"

    r = client.delete(f"/api/sys/security/sessions/{sid}", headers=ADMIN_HEADERS)
    assert r.status_code == 200 and r.json()["data"]["revoked"] is True
    # DB：会话 + 家族 refresh 全吊销
    row = _pg("SELECT revoked_at, revoke_reason FROM auth.sessions WHERE id = %s",
              (sid,))[0]
    assert row[0] is not None and row[1] == "admin_force_logout"
    active_tokens, = _pg("SELECT count(*) FROM auth.refresh_tokens "
                         "WHERE session_id = %s AND revoked = FALSE", (sid,))[0]
    assert active_tokens == 0
    # Redis：jti 闸键 + 索引集合均删除
    assert f"auth:session:{user['id']}:{jti}" not in fr.store
    assert fr.smembers(f"auth:session_idx:{user['id']}:{sid}") == set()
    # 旧凭据刷新 → 401
    r = client.post("/api/auth/refresh")
    assert r.status_code == 401


def test_force_logout_invalid_session_id(env):
    client, _ = env
    r = client.delete("/api/sys/security/sessions/not-a-uuid", headers=ADMIN_HEADERS)
    assert r.status_code == 400


def test_force_logout_missing_session_404(env):
    client, _ = env
    r = client.delete(f"/api/sys/security/sessions/{uuid.uuid4()}",
                      headers=ADMIN_HEADERS)
    assert r.status_code == 404


# ── S8：Redis 不可用 → 列表仍返回 DB 数据 ────────────────────

def test_s8_list_works_without_redis(env, user, monkeypatch):
    client, _ = env
    _login(client, user)
    monkeypatch.setattr("backend.app.api.routes.auth_local.get_redis", lambda: None)
    r = client.get("/api/sys/security/sessions", headers=ADMIN_HEADERS)
    assert r.status_code == 200
    mine = [s for s in r.json()["data"]["sessions"] if s["userId"] == user["id"]]
    assert len(mine) == 1


def test_login_and_refresh_user_info_contains_current_rbac_fields(env, user):
    client, _ = env
    login_response = _login(client, user)
    login_info = login_response.json()["data"]["userInfo"]
    assert login_info["roles"] == ["admin"]
    assert login_info["platformRole"] == "admin"
    assert login_info["tenantId"] == "default"
    assert login_info["csRole"] is None

    refresh_response = client.post("/api/auth/refresh")
    assert refresh_response.status_code == 200, refresh_response.text
    refresh_info = refresh_response.json()["data"]["userInfo"]
    assert refresh_info["roles"] == ["admin"]
    assert refresh_info["platformRole"] == "admin"
    assert refresh_info["tenantId"] == "default"
    assert refresh_info["csRole"] is None


@pytest.fixture()
def tenant_cs_user():
    """创建非 default 租户的客服用户，验证登录与刷新均取可信租户。"""
    username = f"tenant-cs-{uuid.uuid4().hex[:10]}"
    with psycopg2.connect(**MEMORY_DB_CONFIG) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO auth.users "
                "(username, password_hash, real_name, role, tenant_id) "
                "VALUES (%s, %s, '租户客服', 'viewer', 'tenant-b') RETURNING id",
                (username, hash_password("pw-tenant")),
            )
            uid = cur.fetchone()[0]
            cur.execute(
                "INSERT INTO customer_service.cs_agents "
                "(agent_id, tenant_id, display_name, auth_user_id, role) "
                "VALUES (%s, 'tenant-b', '租户客服', %s, 'supervisor')",
                (f"agent-{uuid.uuid4().hex[:12]}", str(uid)),
            )
        conn.commit()
    yield {"id": uid, "username": username, "password": "pw-tenant"}
    with psycopg2.connect(**MEMORY_DB_CONFIG) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "DELETE FROM customer_service.cs_agents WHERE auth_user_id = %s",
                (str(uid),),
            )
            cur.execute("DELETE FROM auth.users WHERE id = %s", (uid,))
        conn.commit()


def test_login_and_refresh_user_info_uses_trusted_non_default_tenant(
    env,
    tenant_cs_user,
):
    client, _ = env

    login_response = _login(client, tenant_cs_user, tenant_id="tenant-b")
    login_info = login_response.json()["data"]["userInfo"]
    assert login_info["tenantId"] == "tenant-b"
    assert login_info["csRole"] == "supervisor"

    refresh_response = client.post(
        "/api/auth/refresh",
        headers={"X-Tenant-Id": "tenant-b"},
    )
    assert refresh_response.status_code == 200, refresh_response.text
    refresh_info = refresh_response.json()["data"]["userInfo"]
    assert refresh_info["tenantId"] == "tenant-b"
    assert refresh_info["csRole"] == "supervisor"


@pytest.fixture()
def tenant_session_user():
    """创建同租户 admin 与客服用户，供角色变更真实会话失效测试使用。"""
    suffix = uuid.uuid4().hex[:10]
    actor_name = f"actor-{suffix}"
    target_name = f"target-{suffix}"
    with psycopg2.connect(**MEMORY_DB_CONFIG) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO auth.users "
                "(username, password_hash, real_name, role, tenant_id) "
                "VALUES (%s, %s, '租户管理员', 'admin', 'tenant-b') RETURNING id",
                (actor_name, hash_password("pw-actor")),
            )
            actor_id = cur.fetchone()[0]
            cur.execute(
                "INSERT INTO auth.users "
                "(username, password_hash, real_name, role, tenant_id) "
                "VALUES (%s, %s, '目标客服', 'viewer', 'tenant-b') RETURNING id",
                (target_name, hash_password("pw-target")),
            )
            target_id = cur.fetchone()[0]
            cur.execute(
                "INSERT INTO customer_service.cs_agents "
                "(agent_id, tenant_id, display_name, auth_user_id, role) "
                "VALUES (%s, 'tenant-b', '目标客服', %s, 'agent')",
                (f"agent-{suffix}", str(target_id)),
            )
        conn.commit()
    yield {
        "actor": {"id": actor_id, "username": actor_name, "password": "pw-actor"},
        "target": {"id": target_id, "username": target_name, "password": "pw-target"},
    }
    with psycopg2.connect(**MEMORY_DB_CONFIG) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "DELETE FROM customer_service.cs_agents WHERE auth_user_id IN (%s, %s)",
                (str(actor_id), str(target_id)),
            )
            cur.execute(
                "DELETE FROM auth.users WHERE id IN (%s, %s)",
                (actor_id, target_id),
            )
        conn.commit()


def test_cs_role_change_revokes_real_access_gate_and_refresh_family(
    env,
    tenant_session_user,
):
    client, redis = env
    target = tenant_session_user["target"]
    actor = tenant_session_user["actor"]

    login_response = _login(client, target, tenant_id="tenant-b")
    access_token = login_response.json()["data"]["token"]
    payload = verify_access_token(access_token)
    sid = payload["sid"]
    jti = payload["jti"]
    old_refresh = _cookie_of(client)
    assert redis.store.get(access_session_key(target["id"], jti)) == "1"

    update_response = client.patch(
        f"/api/sys/rbac/users/{target['id']}",
        json={"version": 0, "csRole": "supervisor"},
        headers={
            "X-Auth-Type": "jwt",
            "X-User-Id": str(actor["id"]),
            "X-User-Roles": "admin",
            "X-Tenant-Id": "tenant-b",
        },
    )
    assert update_response.status_code == 200, update_response.text

    session_row = _pg(
        "SELECT revoked_at FROM auth.sessions WHERE id = %s",
        (sid,),
    )[0]
    assert session_row[0] is not None
    active_tokens, = _pg(
        "SELECT count(*) FROM auth.refresh_tokens "
        "WHERE session_id = %s AND revoked = FALSE",
        (sid,),
    )[0]
    assert active_tokens == 0
    assert access_session_key(target["id"], jti) not in redis.store

    client.cookies.set("refresh_token", old_refresh)
    refresh_response = client.post(
        "/api/auth/refresh",
        headers={"X-Tenant-Id": "tenant-b"},
    )
    assert refresh_response.status_code == 401


# ── 语义补充：logout 撤整个会话 / 索引一致性 ─────────────────

def test_logout_revokes_whole_session(env, user):
    client, fr = env
    resp = _login(client, user)
    sid = verify_access_token(resp.json()["data"]["token"])["sid"]
    token = resp.json()["data"]["token"]
    r = client.post("/api/auth/logout", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 200
    row = _pg("SELECT revoked_at, revoke_reason FROM auth.sessions WHERE id = %s",
              (sid,))[0]
    assert row[0] is not None and row[1] == "logout"
    assert len(_active_sessions(user["id"])) == 0


def test_login_writes_session_idx_index(env, user):
    """Redis 索引集合与 jti 闸键同写（R5 一致性前提）。"""
    client, fr = env
    resp = _login(client, user)
    payload = verify_access_token(resp.json()["data"]["token"])
    idx = f"auth:session_idx:{user['id']}:{payload['sid']}"
    assert payload["jti"] in fr.smembers(idx)
    assert fr.store.get(f"auth:session:{user['id']}:{payload['jti']}") == "1"


# ── 客户端 IP 偏好链（X-Client-IP → X-Real-IP → XFF → client.host）────

def test_client_ip_prefers_bff_injected_header(env, user):
    """BFF（frontend */api/[...path]）注入的 X-Client-IP 优先于网关头。"""
    client, _ = env
    r = client.post("/api/auth/login",
                    json={"username": user["username"], "password": user["password"],
                          "deviceId": "dev-ip"},
                    headers={"X-Client-IP": "203.0.113.9",
                             "X-Real-IP": "172.21.0.1",
                             "X-Tenant-Id": "default",
                             "User-Agent": "TestUA/2.0"})
    assert r.status_code == 200
    ip, ua = _pg("SELECT ip, user_agent FROM auth.sessions "
                 "WHERE user_id = %s AND device_id = 'dev-ip'", (user["id"],))[0]
    assert ip == "203.0.113.9"
    assert ua == "TestUA/2.0"


def test_client_ip_fallback_to_real_ip(env, user):
    """无 X-Client-IP 时回退 X-Real-IP（APISIX 注入值），无头时落 client.host。"""
    client, _ = env
    r = client.post("/api/auth/login",
                    json={"username": user["username"], "password": user["password"],
                          "deviceId": "dev-ip2"},
                    headers={"X-Real-IP": "172.21.0.1",
                             "X-Tenant-Id": "default"})
    assert r.status_code == 200
    ip, = _pg("SELECT ip FROM auth.sessions "
              "WHERE user_id = %s AND device_id = 'dev-ip2'", (user["id"],))[0]
    assert ip == "172.21.0.1"
