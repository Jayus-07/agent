"""test_security_ops.py — 安全运营端点（方案 A 配套运营面）

覆盖（2026-09-16）：
  - GET  /sys/security/sessions   扫 Redis auth:session:* 出列表（联表补用户名）
  - DELETE /sys/security/sessions/{uid}/{jti}  强制下线（删键 + jti 格式校验）
  - GET  /sys/security/overview   灰度开关状态 + 敏感端点清单（动态扫描 + 人工清单合并）
  - 守卫：require_admin_user —— viewer 403（enforce 默认）、service 凭据 403

不连真实 Redis / PostgreSQL：get_redis 打 fake；_attach_usernames（唯一 DB
触点）打桩为 no-op——用户名联表属普通 CRUD，由集成环境覆盖。
身份头语义与网关注入对齐：X-User-Id / X-User-Roles（identity.resolve_identity）。
"""
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.app.api.routes import auth_local


class FakeRedis:
    """scan_iter / ttl / delete 最小 fake（decode_responses 语义）。"""

    def __init__(self):
        self.store: dict[str, str] = {}
        self.ttls: dict[str, int] = {}

    def set(self, key, value, ex=None):
        self.store[key] = value
        self.ttls[key] = ex

    def delete(self, key):
        return 1 if self.store.pop(key, None) is not None else 0

    def ttl(self, key):
        return self.ttls.get(key, -2)

    def scan_iter(self, match=None, count=None):
        # 简化：不实现 glob，直接全量返回（测试键都匹配 auth:session:*）
        for k in list(self.store):
            if match and k.startswith(match.rstrip("*")):
                yield k


ADMIN_HEADERS = {"X-User-Id": "1", "X-User-Roles": "admin"}
VIEWER_HEADERS = {"X-User-Id": "2", "X-User-Roles": "viewer"}


@pytest.fixture
def client(monkeypatch, fake_redis):
    """fresh app + sys_router（不挂 API Key 中间件，身份走网关注入头语义）。"""
    monkeypatch.setenv("JWT_SECRET", "k" * 32)
    monkeypatch.setenv("SENSITIVE_API_GUARD_MODE", "enforce")
    # 列表联表触点打桩（DB 不进单测）
    async def _no_attach(parsed):
        for p in parsed:
            p["username"] = f"u{p['userId']}"
            p["realName"] = p["username"]
            p["role"] = None
    monkeypatch.setattr(auth_local, "_attach_usernames", _no_attach)

    app = FastAPI()
    app.include_router(auth_local.sys_router, prefix="/api")
    return TestClient(app)


@pytest.fixture
def fake_redis(monkeypatch):
    fr = FakeRedis()
    monkeypatch.setattr("backend.infra.redis.client.get_redis", lambda: fr)
    monkeypatch.setattr("backend.app.api.routes.auth_local.get_redis", lambda: fr)
    return fr


# ── 会话列表 ─────────────────────────────────────────────────

def test_sessions_lists_sorted(client, fake_redis):
    fake_redis.set("auth:session:9:aaaabbbb", "1", ex=100)
    fake_redis.set("auth:session:2:ccccdddd", "1", ex=200)
    r = client.get("/api/sys/security/sessions", headers=ADMIN_HEADERS)
    assert r.status_code == 200
    body = r.json()["data"]
    assert body["redisAvailable"] is True
    uids = [s["userId"] for s in body["sessions"]]
    assert uids == [2, 9]
    assert body["sessions"][0]["username"] == "u2"
    assert body["sessions"][0]["ttlSeconds"] == 200


def test_sessions_redis_down(client, monkeypatch):
    monkeypatch.setattr("backend.app.api.routes.auth_local.get_redis", lambda: None)
    r = client.get("/api/sys/security/sessions", headers=ADMIN_HEADERS)
    assert r.status_code == 200
    body = r.json()["data"]
    assert body == {"sessions": [], "redisAvailable": False}


def test_sessions_skips_malformed_keys(client, fake_redis):
    fake_redis.set("auth:session:notanumber:jti", "1", ex=10)
    fake_redis.set("auth:session:3", "1", ex=10)          # 无 jti 段
    fake_redis.set("auth:session:4:", "1", ex=10)         # 空 jti
    r = client.get("/api/sys/security/sessions", headers=ADMIN_HEADERS)
    assert r.json()["data"]["sessions"] == []


# ── 强制下线 ─────────────────────────────────────────────────

def test_force_logout_deletes_key(client, fake_redis):
    fake_redis.set("auth:session:7:aaaabbbbccccdddd", "1", ex=100)
    r = client.delete("/api/sys/security/sessions/7/aaaabbbbccccdddd",
                      headers=ADMIN_HEADERS)
    assert r.status_code == 200
    assert r.json()["data"]["revoked"] is True
    assert "auth:session:7:aaaabbbbccccdddd" not in fake_redis.store


def test_force_logout_missing_key_returns_false(client, fake_redis):
    r = client.delete("/api/sys/security/sessions/7/zzzzyyyyxxxxwwww",
                      headers=ADMIN_HEADERS)
    assert r.status_code == 200
    assert r.json()["data"]["revoked"] is False


def test_force_logout_invalid_jti(client, fake_redis):
    r = client.delete("/api/sys/security/sessions/7/short", headers=ADMIN_HEADERS)
    assert r.status_code == 400


def test_force_logout_redis_down_503(client, monkeypatch):
    monkeypatch.setattr("backend.app.api.routes.auth_local.get_redis", lambda: None)
    r = client.delete("/api/sys/security/sessions/7/aaaabbbbccccdddd",
                      headers=ADMIN_HEADERS)
    assert r.status_code == 503


# ── overview：灰度开关 + 敏感端点清单 ────────────────────────

def test_overview_modes_and_runtime_scan(client, fake_redis, monkeypatch):
    monkeypatch.setenv("JWT_SESSION_GUARD_MODE", "audit")
    monkeypatch.setenv("SENSITIVE_API_GUARD_MODE", "enforce")
    r = client.get("/api/sys/security/overview", headers=ADMIN_HEADERS)
    assert r.status_code == 200
    body = r.json()["data"]
    assert body["modes"]["jwtSessionGuard"]["mode"] == "audit"
    assert body["modes"]["sensitiveApiGuard"]["mode"] == "enforce"
    assert body["modes"]["gatewaySessionCheck"]["mode"] is None  # 部署层，不猜值
    # 动态扫描应识别出本路由组（require_admin_user 挂 Depends）
    paths = {e["path"] for e in body["endpoints"]}
    assert "/api/sys/security/sessions" in paths
    assert "/api/sys/security/overview" in paths
    # 人工清单合并进来了（内联守卫扫不到）
    curated = [e for e in body["endpoints"] if e["source"] == "curated"]
    assert {e["path"] for e in curated} >= {
        "/api/observability/gateway-auth",
        "/api/observability/system-health",
    }


# ── 守卫：require_admin_user ─────────────────────────────────

def test_viewer_rejected_403(client, fake_redis):
    r = client.get("/api/sys/security/sessions", headers=VIEWER_HEADERS)
    assert r.status_code == 403


def test_service_credential_rejected_403(client, fake_redis, monkeypatch):
    """service 通道（X-Internal-Token）不开放安全运营端点（enforce）。"""
    monkeypatch.setattr("backend.config.messaging.AI_INTERNAL_TOKEN", "svc-token",
                        raising=False)
    r = client.get("/api/sys/security/sessions",
                   headers={"X-Internal-Token": "svc-token"})
    assert r.status_code == 403
