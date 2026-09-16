"""test_gateway_auth_metrics.py — 网关安全只读代理（GET /observability/gateway-auth）

覆盖：
- Prometheus 可用：聚合 denied_by_reason / status_codes / 趋势序列，按 count 降序；
- Prometheus 不可达 / 查询失败：available=false 显式降级（200 + 标记，不抛 500）；
- 零值原因被过滤（只有实际发生的拒绝进入分布）；
- 管理员闸（A3 收口）：非 admin / 服务 Key 通道 → 403，audit 模式放行；
- 单点容错：某一路查询失败不再拖垮整个接口（available 仍为 True）。

Prometheus 客户端函数打桩（不发真实 HTTP），聚合/排序/过滤逻辑真实执行。
"""
from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.app.api.routes import observability as obs

# 网关注入的管理员身份（X-User-Roles 由 gateway-auth 从 JWT roles claim 注入，
# 客户端伪造会被剥离）。审计类端点自 2026-09-16 起仅对 admin 开放。
ADMIN_HEADERS = {
    "X-Auth-Type": "jwt",
    "X-User-Id": "u-admin-1",
    "X-User-Roles": "admin",
}
VIEWER_HEADERS = {
    "X-Auth-Type": "jwt",
    "X-User-Id": "u-viewer-1",
    "X-User-Roles": "viewer",
}


@pytest.fixture
def client():
    app = FastAPI()
    app.include_router(obs.router)
    return TestClient(app, raise_server_exceptions=False)


def _stub(monkeypatch, instant=None, rng=None, error=None):
    if error is not None:
        async def _fail(promql):
            raise RuntimeError(error)

        async def _fail_range(promql, hours, step):
            raise RuntimeError(error)

        monkeypatch.setattr(obs, "_prom_instant", _fail)
        monkeypatch.setattr(obs, "_prom_range", _fail_range)
        return

    instant = instant or []
    rng = rng or []

    async def _instant(promql):
        return instant

    async def _range(promql, hours, step):
        return rng

    monkeypatch.setattr(obs, "_prom_instant", _instant)
    monkeypatch.setattr(obs, "_prom_range", _range)


def test_gateway_auth_aggregates_and_sorts(client, monkeypatch):
    # 四个并发查询按调用顺序返回同一份即时数据；reason/code 由 labels 决定
    payloads = [
        [{"labels": {"reason": "expired"}, "value": 3.0},
         {"labels": {"reason": "no-credential"}, "value": 10.0},
         {"labels": {"reason": "blacklist"}, "value": 0.0}],  # 零值应被过滤
        [],                                    # would_deny：enforce 模式恒空
        [{"labels": {"code": "401"}, "value": 13.0},
         {"labels": {"code": "429"}, "value": 5.0}],
    ]
    calls = {"n": 0}

    async def _instant(promql):
        p = payloads[min(calls["n"], len(payloads) - 1)]
        calls["n"] += 1
        return p

    async def _range(promql, hours, step):
        return [{"ts": 1000, "value": 2.0}, {"ts": 1300, "value": 3.5}]

    monkeypatch.setattr(obs, "_prom_instant", _instant)
    monkeypatch.setattr(obs, "_prom_range", _range)

    r = client.get("/observability/gateway-auth?hours=6", headers=ADMIN_HEADERS)
    assert r.status_code == 200
    body = r.json()
    assert body["available"] is True
    assert body["window_hours"] == 6
    # 按 count 降序 + 零值过滤
    reasons = [row["reason"] for row in body["denied_by_reason"]]
    assert reasons == ["no-credential", "expired"]
    assert body["total_denied"] == 13.0
    assert body["status_codes"] == [
        {"code": "401", "count": 13.0}, {"code": "429", "count": 5.0},
    ]
    assert body["denied_series"] == [{"ts": 1000, "value": 2.0}, {"ts": 1300, "value": 3.5}]


def test_gateway_auth_degrades_when_prometheus_down(client, monkeypatch):
    _stub(monkeypatch, error="connection refused")
    r = client.get("/observability/gateway-auth", headers=ADMIN_HEADERS)
    assert r.status_code == 200
    body = r.json()
    assert body["available"] is False
    assert "connection refused" in body["error"]


def test_gateway_auth_window_bounds(client, monkeypatch):
    _stub(monkeypatch)
    r = client.get("/observability/gateway-auth?hours=0", headers=ADMIN_HEADERS)
    assert r.status_code == 422  # gt=0 校验
    r = client.get("/observability/gateway-auth?hours=99999", headers=ADMIN_HEADERS)
    assert r.status_code == 422  # le=24*30 校验


# ── 管理员闸（A3 收口）──────────────────────────────────────────

def test_gateway_auth_rejects_non_admin(client, monkeypatch):
    """viewer 用户不得读取网关审计数据（此前任何登录用户都可读）。"""
    _stub(monkeypatch)
    r = client.get("/observability/gateway-auth", headers=VIEWER_HEADERS)
    assert r.status_code == 403


def test_gateway_auth_rejects_service_key_channel(client, monkeypatch):
    """无身份头 = 服务级 API Key 通道：该 Key 会下发到浏览器，不得读审计。"""
    _stub(monkeypatch)
    r = client.get("/observability/gateway-auth")
    assert r.status_code == 403


def test_gateway_auth_audit_mode_allows_non_admin(client, monkeypatch):
    """SENSITIVE_API_GUARD_MODE=audit：仅记日志、行为与收口前一致（灰度回退）。"""
    _stub(monkeypatch)
    monkeypatch.setenv("SENSITIVE_API_GUARD_MODE", "audit")
    r = client.get("/observability/gateway-auth", headers=VIEWER_HEADERS)
    assert r.status_code == 200


# ── 单点容错 ────────────────────────────────────────────────────

def test_gateway_auth_partial_failure_keeps_available(client, monkeypatch):
    """一路查询失败不应拖垮整体：其余分项仍返回，partial_errors 记录失败。"""
    calls = {"n": 0}

    async def _instant(promql):
        calls["n"] += 1
        if calls["n"] == 3:  # status_codes 那一路失败
            raise RuntimeError("bad promql")
        return [{"labels": {"reason": "expired"}, "value": 4.0}]

    async def _range(promql, hours, step):
        return [{"ts": 1000, "value": 1.0}]

    monkeypatch.setattr(obs, "_prom_instant", _instant)
    monkeypatch.setattr(obs, "_prom_range", _range)

    r = client.get("/observability/gateway-auth?hours=6", headers=ADMIN_HEADERS)
    assert r.status_code == 200
    body = r.json()
    assert body["available"] is True           # 不再整体降级
    assert body["total_denied"] == 4.0         # 有数据的分项照常返回
    assert body["status_codes"] == []          # 失败分项置空
    assert len(body["partial_errors"]) == 1
    assert "bad promql" in body["partial_errors"][0]
