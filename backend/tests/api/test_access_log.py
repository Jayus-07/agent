"""test_access_log.py — 访问审计日志中间件契约（backend/app/api/middleware/access_log.py）

覆盖：
  - 请求完成后一行 [Access] 日志：ip / user / auth / method / path / status / 耗时 / trace
  - XFF 只取最后一跳（客户端自带前缀可伪造，APISIX 追加的真实 IP 恒在最右）
  - 无 XFF（直连 8000 调试）回退 peer 地址
  - OPTIONS 预检与 /health、/metrics 探测不打点
  - 认证/并发中间件短路返回时仍被打点（注册顺序必须在 api_key 外层，防回归）
  - call_next 抛异常仍打点且状态码兜底 500
"""
from __future__ import annotations

import logging

import pytest
from fastapi import FastAPI
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient

from backend.app.api.middleware.access_log import access_log_middleware, client_ip


@pytest.fixture()
def app():
    app = FastAPI()
    app.middleware("http")(access_log_middleware)

    @app.get("/ping")
    async def ping():
        return {"ok": True}

    @app.get("/health")
    async def health():
        return {"ok": True}

    @app.get("/metrics")
    async def metrics():
        return {"ok": True}

    @app.get("/boom")
    async def boom():
        raise RuntimeError("boom")

    return app


def _access_lines(caplog) -> list[str]:
    return [r.getMessage() for r in caplog.records if "[Access]" in r.getMessage()]


def test_records_identity_ip_path_status(caplog, app):
    caplog.set_level(logging.INFO, logger="rag_system")
    client = TestClient(app)
    r = client.get(
        "/ping",
        headers={
            "X-Forwarded-For": "9.9.9.9, 7.7.7.7",  # 前缀伪造 + 网关追加的真实 IP
            "X-User-Id": "1001",
            "X-Auth-Type": "jwt",
            "X-Trace-Id": "t-123",
        },
    )
    assert r.status_code == 200
    lines = _access_lines(caplog)
    assert len(lines) == 1
    line = lines[0]
    # 真实 IP 是 XFF 的最后一跳（网关追加），而非客户端可伪造的前缀
    assert "ip=7.7.7.7" in line
    assert "9.9.9.9" not in line
    assert "user=1001" in line
    assert "auth=jwt" in line
    assert "GET /ping -> 200" in line
    assert "trace=t-123" in line


def test_falls_back_to_peer_without_xff(caplog, app):
    caplog.set_level(logging.INFO, logger="rag_system")
    TestClient(app).get("/ping")
    assert "ip=testclient" in _access_lines(caplog)[0]


def test_skips_options_and_probe_paths(caplog, app):
    caplog.set_level(logging.INFO, logger="rag_system")
    client = TestClient(app)
    client.options("/ping")
    client.get("/health")
    client.get("/metrics")
    assert _access_lines(caplog) == []


def test_short_circuited_responses_still_logged(caplog, app):
    """认证短路（401）发生在 access_log 内层时不得漏记——对应 server.py 注册顺序约束。"""
    caplog.set_level(logging.INFO, logger="rag_system")

    async def deny_all(request, call_next):
        return JSONResponse({"error": "Unauthorized"}, status_code=401)

    app.middleware("http")(deny_all)      # 先注册 → 内层（模拟 api_key 短路）
    app.middleware("http")(access_log_middleware)  # 后注册 → 外层
    TestClient(app).get("/ping", headers={"X-User-Id": "1001"})
    assert "user=1001 GET /ping -> 401" in " ".join(_access_lines(caplog)).replace("auth=- ", "")


def test_exception_still_logged_with_fallback_status(caplog, app):
    caplog.set_level(logging.INFO, logger="rag_system")
    TestClient(app, raise_server_exceptions=False).get("/boom")
    assert "GET /boom -> 500" in _access_lines(caplog)[0]


def test_client_ip_xff_parsing():
    """最后一跳取值 + 空白容忍；纯 peer 回退逻辑由 TestClient 用例覆盖。"""
    class _Req:
        headers = {"X-Forwarded-For": "1.2.3.4 , 5.6.7.8 "}
        client = None

    assert client_ip(_Req()) == "5.6.7.8"
