"""test_sys_config_admin.py — 灰度开关动态配置（2026-09-16 Lite 版）

覆盖：
  - GET  /sys/config        生效状态（来源 env-default / db）
  - PUT  /sys/config/{key}  覆盖写入 + 历史审计（old→new）
  - 白名单：非法值 400、未登记键 404、DB 非法值被忽略并告警
  - fail-closed：DB 失败保留上次已知值；缓存为空回落 env 默认（含非法 env 值）
  - 守卫联动：PUT 后 get_mode 立即生效；/sys/security/overview 反映 db 来源
  - 权限：viewer / service 凭据 403

不连真实 DB：sys_config.get_session 打桩为 FakeSession（内存 store 模拟
sys_config 表）。refresh_loop 不在单测中启动（由 server startup 挂载）。
"""
import asyncio

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.app.api.routes import auth_local, sys_config_admin
from backend.services import sys_config


# ── 假 DB 会话（模拟 sys_config / sys_config_history 两张表）──

class FakeResult:
    def __init__(self, rows):
        self._rows = rows

    def first(self):
        return self._rows[0] if self._rows else None

    def mappings(self):
        return self

    def all(self):
        return self._rows


class FakeSession:
    """按语句类型路由到内存 store；commit 计数供断言。"""

    def __init__(self, store: dict[str, str], store_meta: dict[str, dict]):
        self.store = store
        self.store_meta = store_meta
        self.history: list[tuple] = []
        self.commits = 0

    async def execute(self, stmt, params=None):
        sql = str(stmt)
        params = params or {}
        if "SELECT value FROM sys_config" in sql:
            v = self.store.get(params["k"])
            return FakeResult([(v,)] if v is not None else [])
        if "SELECT key, value, updated_by, updated_at" in sql:
            rows = [{"key": k, "value": v,
                     "updated_by": self.store_meta.get(k, {}).get("updatedBy"),
                     "updated_at": None}
                    for k, v in self.store.items() if k in sys_config._SWITCHES]
            return FakeResult(rows)
        if "INSERT INTO sys_config " in sql:
            self.store[params["k"]] = params["v"]
            self.store_meta[params["k"]] = {"updatedBy": params["op"]}
            return FakeResult([])
        if "INSERT INTO sys_config_history" in sql:
            self.history.append((params["k"], params["o"], params["n"], params["op"]))
            return FakeResult([])
        return FakeResult([])   # CREATE TABLE / CREATE INDEX 等 DDL

    async def commit(self):
        self.commits += 1


ADMIN_HEADERS = {"X-User-Id": "1", "X-User-Roles": "admin"}
VIEWER_HEADERS = {"X-User-Id": "2", "X-User-Roles": "viewer"}


@pytest.fixture(autouse=True)
def _reset_cache_around_test():
    """_values 是模块级缓存，set_value 的写入 monkeypatch 不会回滚——
    前后各清一次，防止污染其他测试文件（本文件 test 顺序不定）。"""
    sys_config.reset_cache_for_tests()
    yield
    sys_config.reset_cache_for_tests()


@pytest.fixture
def fresh(monkeypatch):
    """清空进程内缓存 + 打桩 DB 会话。"""
    sys_config.reset_cache_for_tests()
    fake = FakeSession(store={}, store_meta={})

    async def _fake_get_session():
        yield fake

    monkeypatch.setattr(sys_config, "get_session", _fake_get_session)
    return fake


@pytest.fixture
def client(monkeypatch, fresh):
    monkeypatch.setenv("JWT_SECRET", "k" * 32)
    monkeypatch.setenv("SENSITIVE_API_GUARD_MODE", "enforce")
    app = FastAPI()
    app.include_router(sys_config_admin.router, prefix="/api")
    app.include_router(auth_local.sys_router, prefix="/api")
    return TestClient(app)


# ── get_mode 回退链 ──────────────────────────────────────────

def test_get_mode_env_default(fresh, monkeypatch):
    monkeypatch.setenv("JWT_SESSION_GUARD_MODE", "enforce")
    assert sys_config.get_mode("JWT_SESSION_GUARD_MODE") == "enforce"


def test_get_mode_invalid_env_falls_to_registry_default(fresh, monkeypatch):
    monkeypatch.setenv("JWT_SESSION_GUARD_MODE", "yolo")
    assert sys_config.get_mode("JWT_SESSION_GUARD_MODE") == "audit"


def test_db_failure_keeps_last_known_value(fresh, monkeypatch):
    sys_config._values["JWT_SESSION_GUARD_MODE"] = "enforce"
    async def _boom():
        raise RuntimeError("db down")
        yield  # pragma: no cover
    monkeypatch.setattr(sys_config, "get_session", _boom)
    assert asyncio.run(sys_config.refresh_once()) is False
    assert sys_config.get_mode("JWT_SESSION_GUARD_MODE") == "enforce"  # 不回落、不放空


def test_invalid_db_value_ignored_and_warned(fresh, monkeypatch, caplog):
    fresh.store["JWT_SESSION_GUARD_MODE"] = "hacked"
    monkeypatch.setenv("JWT_SESSION_GUARD_MODE", "audit")
    assert asyncio.run(sys_config.refresh_once()) is True
    assert sys_config.get_mode("JWT_SESSION_GUARD_MODE") == "audit"   # 非法值不进缓存
    assert any("不在白名单" in r.getMessage() for r in caplog.records)


def test_override_removed_falls_back_to_env(fresh, monkeypatch):
    sys_config._values["JWT_SESSION_GUARD_MODE"] = "enforce"
    fresh.store.pop("JWT_SESSION_GUARD_MODE", None)
    assert asyncio.run(sys_config.refresh_once()) is True
    monkeypatch.setenv("JWT_SESSION_GUARD_MODE", "audit")
    assert sys_config.get_mode("JWT_SESSION_GUARD_MODE") == "audit"


# ── GET /sys/config ─────────────────────────────────────────

def test_get_config_lists_registered_switches(client):
    r = client.get("/api/sys/config", headers=ADMIN_HEADERS)
    assert r.status_code == 200
    items = {i["key"]: i for i in r.json()["items"]}
    assert set(items) == {"JWT_SESSION_GUARD_MODE", "SENSITIVE_API_GUARD_MODE"}
    assert items["JWT_SESSION_GUARD_MODE"]["allowed"] == ["off", "audit", "enforce"]
    assert items["SENSITIVE_API_GUARD_MODE"]["source"] == "env-default"


def test_get_config_viewer_403(client):
    assert client.get("/api/sys/config", headers=VIEWER_HEADERS).status_code == 403


def test_get_config_service_credential_403(client, monkeypatch):
    monkeypatch.setattr("backend.config.messaging.AI_INTERNAL_TOKEN", "svc-token",
                        raising=False)
    r = client.get("/api/sys/config", headers={"X-Internal-Token": "svc-token"})
    assert r.status_code == 403


# ── PUT /sys/config/{key} ───────────────────────────────────

def test_put_valid_value_takes_effect_immediately(client, fresh):
    r = client.put("/api/sys/config/SENSITIVE_API_GUARD_MODE", json={"value": "audit"},
                   headers=ADMIN_HEADERS)
    assert r.status_code == 200
    body = r.json()
    assert body["old"] is None and body["new"] == "audit"
    assert sys_config.get_mode("SENSITIVE_API_GUARD_MODE") == "audit"  # 免重启即时生效
    assert fresh.commits == 1
    # 再改一次：旧值来自上一次覆盖（回滚基准）
    r2 = client.put("/api/sys/config/SENSITIVE_API_GUARD_MODE", json={"value": "enforce"},
                    headers=ADMIN_HEADERS)
    assert r2.json()["old"] == "audit"
    assert fresh.history[-1] == ("SENSITIVE_API_GUARD_MODE", "audit", "enforce", "user:1")


def test_put_invalid_value_400(client, fresh):
    r = client.put("/api/sys/config/JWT_SESSION_GUARD_MODE", json={"value": "yolo"},
                   headers=ADMIN_HEADERS)
    assert r.status_code == 400
    assert "JWT_SESSION_GUARD_MODE" not in fresh.store   # 未落库


def test_put_unknown_key_404(client):
    r = client.put("/api/sys/config/GATEWAY_SESSION_CHECK", json={"value": "enforce"},
                   headers=ADMIN_HEADERS)
    assert r.status_code == 404


def test_put_viewer_403(client):
    r = client.put("/api/sys/config/JWT_SESSION_GUARD_MODE", json={"value": "off"},
                   headers=VIEWER_HEADERS)
    assert r.status_code == 403


# ── overview 联动 ───────────────────────────────────────────

def test_overview_reflects_db_override(client, fresh, monkeypatch):
    monkeypatch.setenv("JWT_SECRET", "k" * 32)
    client.put("/api/sys/config/JWT_SESSION_GUARD_MODE", json={"value": "enforce"},
               headers=ADMIN_HEADERS)
    r = client.get("/api/sys/security/overview", headers=ADMIN_HEADERS)
    assert r.status_code == 200
    m = r.json()["data"]["modes"]["jwtSessionGuard"]
    assert m["mode"] == "enforce" and m["source"] == "db"
    assert m["allowed"] == ["off", "audit", "enforce"]
    gw = r.json()["data"]["modes"]["gatewaySessionCheck"]
    assert gw["mode"] is None and "allowed" not in gw   # 部署层不猜值、不可切
