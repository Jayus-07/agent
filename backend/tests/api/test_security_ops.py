"""test_security_ops.py — 安全运营端点（方案 A 配套运营面）

覆盖：
  - GET  /sys/security/overview   灰度开关状态 + 敏感端点清单
  - 守卫：require_admin_user —— viewer 403（enforce 默认）、service 凭据 403

2026-09-19 会话实体改造：/security/sessions 列表已切 DB 口径（auth.sessions，
一行 = 一次设备登录）、强制下线改按 sessionId——相关行为用例迁移到
test_auth_session_family.py（真 PG + fake Redis）。

不连真实 Redis / PostgreSQL：get_redis 打 fake。
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
    app = FastAPI()
    app.include_router(auth_local.sys_router, prefix="/api")
    return TestClient(app)


@pytest.fixture
def fake_redis(monkeypatch):
    fr = FakeRedis()
    monkeypatch.setattr("backend.infra.redis.client.get_redis", lambda: fr)
    monkeypatch.setattr("backend.app.api.routes.auth_local.get_redis", lambda: fr)
    return fr


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
