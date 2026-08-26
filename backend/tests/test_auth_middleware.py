"""test_auth_middleware.py — API Key 认证中间件测试（2026-08-21 P0 加固）

锁定 fail-closed 行为：
1. 未配置 API_KEY 且未豁免 → 业务端点 503（不再静默放行）
2. 未配置 API_KEY 但 ALLOW_UNAUTHENTICATED=true → 放行
3. 配置了 API_KEY → 无/错 Key 返回 401，正确 Key 放行
4. /health、/metrics 等系统端点不受认证影响
5. 比较使用常量时间（secrets.compare_digest）
"""
from __future__ import annotations

import importlib

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient


def _build_app() -> FastAPI:
    """构造挂载认证中间件的独立 app（不依赖 app.server 的重依赖）。"""
    from backend.app.api.middleware.auth import api_key_middleware

    app = FastAPI()

    @app.get("/health")
    async def health():
        return {"status": "ok"}

    @app.get("/metrics")
    async def metrics():
        return {}

    @app.get("/chat")
    async def chat():
        return {"ok": True}

    app.middleware("http")(api_key_middleware)
    return app


@pytest.fixture
def auth_env(monkeypatch):
    """按用例设置认证相关环境变量并重新加载模块。"""

    def _set(api_key: str, allow_unauth: str = "false"):
        monkeypatch.setenv("API_KEY", api_key)
        monkeypatch.setenv("ALLOW_UNAUTHENTICATED", allow_unauth)
        import backend.config as config_mod
        importlib.reload(config_mod)
        import backend.app.api.middleware.auth as auth_mod
        importlib.reload(auth_mod)
        return auth_mod

    yield _set

    # 恢复现场：重载为原始环境（monkeypatch 会在 teardown 时还原 env）
    monkeypatch.delenv("API_KEY", raising=False)
    monkeypatch.setenv("API_KEY", "")
    import backend.config as config_mod
    importlib.reload(config_mod)
    import backend.app.api.middleware.auth as auth_mod
    importlib.reload(auth_mod)


class TestFailClosed:
    """未配置 API_KEY 时必须拒绝业务请求"""

    def test_missing_api_key_returns_503(self, auth_env):
        auth_mod = auth_env(api_key="", allow_unauth="false")
        app = _build_app()
        client = TestClient(app)

        resp = client.get("/chat")
        assert resp.status_code == 503
        assert resp.json()["error"] == "AuthNotConfigured"

    def test_system_endpoints_bypass_auth(self, auth_env):
        """健康检查与 metrics 不受认证影响（供探活/抓取）"""
        auth_env(api_key="", allow_unauth="false")
        client = TestClient(_build_app())

        assert client.get("/health").status_code == 200
        assert client.get("/metrics").status_code == 200

    def test_explicit_allow_unauthenticated_passes(self, auth_env):
        auth_env(api_key="", allow_unauth="true")
        client = TestClient(_build_app())

        resp = client.get("/chat")
        assert resp.status_code == 200
        assert resp.json() == {"ok": True}


class TestApiKeyValidation:
    """配置 API_KEY 后的标准校验路径"""

    def test_missing_header_returns_401(self, auth_env):
        auth_env(api_key="secret-key-123", allow_unauth="false")
        client = TestClient(_build_app())

        resp = client.get("/chat")
        assert resp.status_code == 401
        assert resp.json()["error"] == "Unauthorized"

    def test_wrong_key_returns_401(self, auth_env):
        auth_env(api_key="secret-key-123", allow_unauth="false")
        client = TestClient(_build_app())

        resp = client.get("/chat", headers={"X-API-Key": "wrong"})
        assert resp.status_code == 401

    def test_correct_key_passes(self, auth_env):
        auth_env(api_key="secret-key-123", allow_unauth="false")
        client = TestClient(_build_app())

        resp = client.get("/chat", headers={"X-API-Key": "secret-key-123"})
        assert resp.status_code == 200
        assert resp.json() == {"ok": True}

    def test_non_ascii_configured_key_handled(self, auth_env):
        """服务端配置非 ASCII Key 时不抛异常（utf-8 编码比较路径）；
        HTTP 头本身不允许非 ASCII 值，客户端只能送 ASCII 错误 Key → 401"""
        auth_env(api_key="密钥-测试-123", allow_unauth="false")
        client = TestClient(_build_app())

        assert client.get("/chat").status_code == 401
        resp = client.get("/chat", headers={"X-API-Key": "wrong"})
        assert resp.status_code == 401
