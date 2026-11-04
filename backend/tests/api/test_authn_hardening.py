"""认证面加固回归（审计 P2 批次，2026-11-03）。

钉死四件事：
  1. 登录账号级防爆破：按 tenant+username 计数，窗口内达阈值锁定并落
     security_events；登录成功清零；Redis 不可用 fail-open（不阻断主链路）。
  2. 注册用户名查重收窄到租户内（跨租户不再互见/互占；DB 兜底见迁移 131）。
  3. 验证码签发用 CSPRNG（secrets.choice）——random 可预测。
  4. /demo/seed、/demo/run 挂 admin 角色闸（写库 + workflow/LLM 消耗面）。
"""
from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.routing import APIRoute

from backend.app.api.routes import auth_local


# ── FakeStore：认证 Redis 最小桩（get/incr/expire/delete）──

class FakeStore:
    def __init__(self, fail: bool = False):
        self.data: dict[str, str] = {}
        self.fail = fail

    def _boom(self):
        if self.fail:
            raise ConnectionError("redis down")

    def get(self, key):
        self._boom()
        return self.data.get(key)

    def incr(self, key):
        self._boom()
        self.data[key] = str(int(self.data.get(key) or 0) + 1)
        return int(self.data[key])

    def expire(self, key, ttl):
        self._boom()

    def delete(self, key):
        self._boom()
        self.data.pop(key, None)


@pytest.fixture
def fake_store(monkeypatch):
    store = FakeStore()
    monkeypatch.setattr(auth_local, "_get_auth_store", lambda: store)
    return store


@pytest.fixture
def security_events(monkeypatch):
    seen: list[tuple] = []
    monkeypatch.setattr(
        auth_local, "record_security_event",
        lambda event_type, **kw: seen.append((event_type, kw)) or True,
    )
    return seen


T, U = "tenant-1", "alice"


def test_lockout_not_triggered_below_threshold(fake_store):
    for _ in range(auth_local._LOGIN_LOCKOUT_THRESHOLD - 1):
        auth_local._login_lockout_record_failure(T, U)
    assert auth_local._login_lockout_check(T, U) is False


def test_lockout_triggers_at_threshold_and_records_event(
        fake_store, security_events):
    for _ in range(auth_local._LOGIN_LOCKOUT_THRESHOLD):
        auth_local._login_lockout_record_failure(T, U)
    assert auth_local._login_lockout_check(T, U) is True
    types = [e for e, _kw in security_events]
    assert types == ["LOGIN_BRUTE_FORCE_LOCKED"]
    kw = security_events[0][1]
    assert kw["tenant_id"] == T and kw["detail"]["username"] == U


def test_lockout_is_scoped_by_tenant_and_username(fake_store):
    """计数键按 tenant+username 分桶：不同租户/用户互不影响。"""
    for _ in range(auth_local._LOGIN_LOCKOUT_THRESHOLD):
        auth_local._login_lockout_record_failure(T, U)
    assert auth_local._login_lockout_check(T, U) is True
    assert auth_local._login_lockout_check(T, "bob") is False
    assert auth_local._login_lockout_check("tenant-2", U) is False


def test_lockout_reset_clears_counter(fake_store):
    for _ in range(auth_local._LOGIN_LOCKOUT_THRESHOLD):
        auth_local._login_lockout_record_failure(T, U)
    auth_local._login_lockout_reset(T, U)
    assert auth_local._login_lockout_check(T, U) is False


def test_lockout_fail_open_when_redis_down(monkeypatch):
    """Redis 不可用：check 返回 False（放行）、记失败不抛——登录主链路不受阻。"""
    broken = FakeStore(fail=True)
    monkeypatch.setattr(auth_local, "_get_auth_store", lambda: broken)
    assert auth_local._login_lockout_check(T, U) is False
    auth_local._login_lockout_record_failure(T, U)  # 不抛
    auth_local._login_lockout_reset(T, U)  # 不抛


# ── 结构断言：源码级防回退 ──

def _src(rel: str) -> str:
    # 本文件位于 backend/tests/api/ → parents[3] = 仓库根
    return (Path(__file__).resolve().parents[3] / rel).read_text(encoding="utf-8")


def test_login_wires_throttle_calls():
    src = _src("backend/app/api/routes/auth_local.py")
    assert "_login_lockout_check(tenant_id, username)" in src
    assert "_login_lockout_record_failure(tenant_id, username)" in src
    assert "_login_lockout_reset(tenant_id, username)" in src


def test_register_dedup_is_tenant_scoped():
    src = _src("backend/app/api/routes/auth_local.py")
    assert "WHERE username = :u AND tenant_id = :tenant_id" in src, (
        "注册查重必须带租户条件（跨租户互占/枚举面收口，审计 P2）")


def test_captcha_uses_csprng():
    src = _src("backend/app/api/routes/auth_local.py")
    assert "import secrets" in src
    assert 'secrets.choice("0123456789")' in src, (
        "验证码是安全凭证，必须用 CSPRNG（random 可预测）")


# ── demo 端点 admin 闸（结构断言）──

def test_demo_endpoints_require_admin():
    from backend.app.api.routes import demo as demo_route

    guards = {}
    for r in demo_route.router.routes:
        if isinstance(r, APIRoute) and r.path.startswith("/demo"):
            names = {d.call.__name__ for d in r.dependant.dependencies
                     if getattr(d.call, "__name__", "").startswith("require_")}
            for m in r.methods:
                if m == "POST":
                    guards[(r.path, m)] = names
    assert guards[("/demo/seed", "POST")] == {"require_admin_user"}
    assert guards[("/demo/run/{scenario_id}", "POST")] == {"require_admin_user"}


# ── 登录级行为：只对「存在的账号」计数（防第三方锁号 DoS）──

def test_login_counts_failures_only_for_existing_accounts(monkeypatch):
    """契约对齐 test_repeated_invalid_login_attempts_do_not_lock_account：
    不存在的用户名永不计数/锁定；存在但密码错的账号按阈值锁定。"""
    from contextlib import asynccontextmanager

    from fastapi.testclient import TestClient

    @asynccontextmanager
    async def fake_db():
        yield object()

    rows = {"alice": {"id": 7, "username": "alice", "status": 1,
                      "password_hash": "not-the-password", "tenant_id": "tenant-a"}}

    async def fetch_user(_session, username, _tenant_id):
        return rows.get(username)

    store = FakeStore()
    monkeypatch.setattr(auth_local, "_get_auth_store", lambda: store)
    monkeypatch.setattr(auth_local, "_db", fake_db)
    monkeypatch.setattr(auth_local, "_fetch_user", fetch_user)
    monkeypatch.setattr(auth_local, "verify_password", lambda _p, _h: False)

    app = FastAPI()
    app.include_router(auth_local.router)
    with TestClient(app) as client:
        # ghost（不存在）：连打 8 次永不锁定
        ghost = [client.post(
            "/auth/login", headers={"X-Tenant-Id": "tenant-a"},
            json={"username": "ghost", "password": "x"}) for _ in range(8)]
        assert [r.status_code for r in ghost] == [400] * 8
        # alice（存在）：第 5 次起锁定 429
        alice = [client.post(
            "/auth/login", headers={"X-Tenant-Id": "tenant-a"},
            json={"username": "alice", "password": "x"}) for _ in range(6)]
        assert [r.status_code for r in alice] == [400] * 5 + [429]
