"""test_session_guard_and_actor_kind.py — 方案 A（JWT 单通道）新增逻辑

覆盖四块（2026-09-16）：
  1. local_jwt：issue_access_token 签发 jti；session_key() 构造会话键；
     无 jti/userId 的旧式 payload → session_key 返回 None
  2. auth_local._write_session / _revoke_session：fake redis 下写入/删除
     auth:session:{userId}:{jti}（logout 即时失效的写侧闭环）
  3. middleware/auth._session_guard：audit 放行 / enforce 拒绝（缺 jti、
     会话已删）；Redis 不可用放行（网关层 fail-closed 兜底）
  4. deps.require_user_actor / require_admin_user：kind 语义统一——
     service 凭据（X-Internal-Token）enforce 403 / audit 放行；JWT 用户放行

不连真实 Redis / PostgreSQL：会话客户端用 fake 对象替换 get_redis；
服务凭据走 monkeypatch backend.config.messaging.AI_INTERNAL_TOKEN
（进程内缓存常量，setenv 无效——见 test_internal_token_failclosed.py §0）。
"""
import json

import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient

from backend.security import local_jwt
from backend.security.local_jwt import issue_access_token, session_key, verify_access_token


# ── 1. jti / session_key ─────────────────────────────────────

def test_issue_contains_jti(monkeypatch):
    monkeypatch.setenv("JWT_SECRET", "k" * 32)
    issued = issue_access_token(user_id=7, username="alice", roles=["admin"])
    payload = verify_access_token(issued["token"])
    assert payload is not None
    assert payload["jti"] == issued["jti"]
    assert session_key(payload) == f"auth:session:7:{issued['jti']}"


def test_session_key_legacy_payload(monkeypatch):
    """旧令牌（无 jti）→ session_key 返回 None，会话闸走 legacy 分支。"""
    monkeypatch.setenv("JWT_SECRET", "k" * 32)
    assert session_key({"userId": 7}) is None
    assert session_key({"jti": "abc"}) is None
    assert session_key({}) is None


# ── fake redis 基建 ──────────────────────────────────────────

class FakeRedis:
    """记录 set/delete/exists 的最小 fake（decode_responses 语义）。"""

    def __init__(self):
        self.store: dict[str, str] = {}
        self.ttls: dict[str, int] = {}

    def set(self, key, value, ex=None):
        self.store[key] = value
        self.ttls[key] = ex

    def delete(self, key):
        return self.store.pop(key, None) and 1 or 0

    def exists(self, key):
        return 1 if key in self.store else 0


@pytest.fixture
def fake_redis(monkeypatch):
    fr = FakeRedis()
    # auth_local 是模块级 from-import（绑定在自身命名空间），middleware 是
    # 函数内惰性 import（每次读源模块）——两处都要 patch
    monkeypatch.setattr("backend.infra.redis.client.get_redis", lambda: fr)
    monkeypatch.setattr("backend.app.api.routes.auth_local.get_redis", lambda: fr)
    return fr


# ── 2. auth_local 会话写/删 ──────────────────────────────────

def test_write_and_revoke_session(monkeypatch, fake_redis):
    monkeypatch.setenv("JWT_SECRET", "k" * 32)
    from backend.app.api.routes import auth_local

    issued = issue_access_token(user_id=7, username="alice", roles=["viewer"])
    assert auth_local._write_session(issued) is True
    key = f"auth:session:7:{issued['jti']}"
    assert key in fake_redis.store
    assert 0 < fake_redis.ttls[key] <= 30 * 60

    auth_local._revoke_session(issued["token"])
    assert key not in fake_redis.store


def test_write_session_without_redis(monkeypatch):
    """Redis 不可用：写侧尽力而为返回 False，不抛（audit 灰度期无影响）。"""
    monkeypatch.setattr("backend.app.api.routes.auth_local.get_redis", lambda: None)
    monkeypatch.setenv("JWT_SECRET", "k" * 32)
    from backend.app.api.routes import auth_local

    issued = issue_access_token(user_id=7, username="alice")
    assert auth_local._write_session(issued) is False


def test_write_session_legacy_issued_dict(monkeypatch, fake_redis):
    """无 jti 的 issued（理论不出现，防御分支）→ 不写。"""
    monkeypatch.setenv("JWT_SECRET", "k" * 32)
    from backend.app.api.routes import auth_local

    assert auth_local._write_session({"token": "x", "exp": 0}) is False


# ── 3. 中间件会话闸 ──────────────────────────────────────────

def _legacy_token() -> str:
    """手工构造无 jti 的合法签名 token（模拟方案 A 前签发的旧令牌）。"""
    import hashlib
    import hmac as _hmac
    import time

    from backend.security.local_jwt import _b64url, _secret

    now = int(time.time())
    payload = {"userId": 7, "username": "alice", "type": "access",
               "iss": "agent-platform", "iat": now, "exp": now + 600}
    header = {"alg": "HS512", "typ": "JWT"}
    signing_input = (_b64url(json.dumps(header).encode()) + "." +
                     _b64url(json.dumps(payload).encode()))
    sig = _b64url(_hmac.new(_secret().encode(), signing_input.encode(), hashlib.sha512).digest())
    return f"{signing_input}.{sig}"


@pytest.fixture
def mw_client(monkeypatch):
    from backend.app.api.middleware import auth as auth_mw

    # middleware 模块 import 时已绑定 config 的 API_KEY —— 测试内直接改
    # 模块属性（from-import 的名字绑定在 auth 命名空间）
    monkeypatch.setattr(auth_mw, "API_KEY", "test-key", raising=False)
    monkeypatch.setattr(auth_mw, "ALLOW_UNAUTHENTICATED", False, raising=False)

    app = FastAPI()
    app.middleware("http")(auth_mw.api_key_middleware)

    @app.get("/ping")
    async def ping():
        return {"ok": True}

    return TestClient(app, raise_server_exceptions=False)


def test_mw_session_audit_allows_revoked(monkeypatch, mw_client, fake_redis):
    """audit（默认）：会话已删仍放行（灰度期行为不变）。"""
    monkeypatch.setenv("JWT_SECRET", "k" * 32)
    monkeypatch.setenv("JWT_SESSION_GUARD_MODE", "audit")
    issued = issue_access_token(user_id=7, username="alice")
    # 不写会话键 = 已吊销
    r = mw_client.get("/ping", headers={"X-API-Key": "test-key",
                                        "Authorization": f"Bearer {issued['token']}"})
    assert r.status_code == 200


def test_mw_session_enforce_rejects_revoked(monkeypatch, mw_client, fake_redis):
    monkeypatch.setenv("JWT_SECRET", "k" * 32)
    monkeypatch.setenv("JWT_SESSION_GUARD_MODE", "enforce")
    issued = issue_access_token(user_id=7, username="alice")
    r = mw_client.get("/ping", headers={"X-API-Key": "test-key",
                                        "Authorization": f"Bearer {issued['token']}"})
    assert r.status_code == 401
    assert "会话已失效" in r.json()["detail"]

    # 写入会话键后同 token 放行
    fake_redis.set(f"auth:session:7:{issued['jti']}", "1", ex=600)
    r2 = mw_client.get("/ping", headers={"X-API-Key": "test-key",
                                         "Authorization": f"Bearer {issued['token']}"})
    assert r2.status_code == 200


def test_mw_session_enforce_rejects_legacy_token(monkeypatch, mw_client, fake_redis):
    monkeypatch.setenv("JWT_SECRET", "k" * 32)
    monkeypatch.setenv("JWT_SESSION_GUARD_MODE", "enforce")
    r = mw_client.get("/ping", headers={"X-API-Key": "test-key",
                                        "Authorization": f"Bearer {_legacy_token()}"})
    assert r.status_code == 401
    assert "jti" in r.json()["detail"]


def test_mw_session_no_redis_allows(monkeypatch, mw_client):
    """Redis 不可用：本层放行（网关层同键检查 fail-closed 兜底）。"""
    monkeypatch.setenv("JWT_SECRET", "k" * 32)
    monkeypatch.setenv("JWT_SESSION_GUARD_MODE", "enforce")
    monkeypatch.setattr("backend.infra.redis.client.get_redis", lambda: None)
    issued = issue_access_token(user_id=7, username="alice")
    r = mw_client.get("/ping", headers={"X-API-Key": "test-key",
                                        "Authorization": f"Bearer {issued['token']}"})
    assert r.status_code == 200


# ── 4. 统一守卫 kind 语义 ────────────────────────────────────

ADMIN_HEADERS = {"X-Auth-Type": "jwt", "X-User-Id": "u-admin-1", "X-User-Roles": "admin"}
VIEWER_HEADERS = {"X-Auth-Type": "jwt", "X-User-Id": "u-viewer-1", "X-User-Roles": "viewer"}
SERVICE_HEADERS = {"X-Internal-Token": "tok-1"}


@pytest.fixture
def guard_client():
    from backend.app.api.deps import require_admin_user, require_user_actor

    app = FastAPI()

    @app.get("/user-only")
    async def user_only(ident=Depends(require_user_actor)):
        return {"kind": ident.kind}

    @app.get("/admin-only")
    async def admin_only(ident=Depends(require_admin_user)):
        return {"kind": ident.kind, "role": ident.role}

    return TestClient(app, raise_server_exceptions=False)


@pytest.fixture
def internal_token(monkeypatch):
    monkeypatch.setattr("backend.config.messaging.AI_INTERNAL_TOKEN", "tok-1", raising=False)


def test_guard_jwt_user_passes(guard_client):
    assert guard_client.get("/user-only", headers=VIEWER_HEADERS).json()["kind"] == "user"
    r = guard_client.get("/admin-only", headers=ADMIN_HEADERS)
    assert r.status_code == 200 and r.json()["role"] == "admin"


def test_guard_service_rejected_enforce(guard_client, internal_token):
    """service 凭据：enforce（默认）→ 403，JWT 用户通道是唯一身份来源。"""
    assert guard_client.get("/user-only", headers=SERVICE_HEADERS).status_code == 403
    assert guard_client.get("/admin-only", headers=SERVICE_HEADERS).status_code == 403


def test_guard_viewer_rejected_admin_endpoint(guard_client):
    """viewer 是 user 身份但角色不足 → admin 档 403（user-only 档放行）。"""
    assert guard_client.get("/admin-only", headers=VIEWER_HEADERS).status_code == 403


def test_guard_audit_mode_allows_service(guard_client, internal_token, monkeypatch):
    monkeypatch.setenv("SENSITIVE_API_GUARD_MODE", "audit")
    r = guard_client.get("/user-only", headers=SERVICE_HEADERS)
    assert r.status_code == 200 and r.json()["kind"] == "service"
