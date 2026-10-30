"""test_travel_auth.py — 旅游域 REST 端点身份收口契约（2026-09-30）

契约：``backend/app/api/routes/travel.py`` **全部端点要求已认证身份**
（``require_identity`` → 未认证 401）。此前用宽容 ``resolve_identity``：
未登录请求按 ``guest`` 放行、``user_id=""`` 落库——反馈写进空账号、
推荐可被匿名调用。

为什么是纵深防御而非唯一闸门：网关 APISIX 的 ``/api/*`` 兜底路由已挂
``gateway-auth``（``GATEWAY_AUTH_MODE=enforce``），未认证请求在网关层即
401；本层拦的是**绕过网关直连后端**的通道。两层互补，故本用例刻意只挂
travel router 的最小 app（不带全局中间件），单独验证本层。

V1 规划/偏好端点已退役（``_retired_v1_router`` 不再挂载，见
tests/travel_v2/test_legacy_persistence_retired.py 守卫），其身份契约
随端点移除；V2 规划流（唯一现行规划入口）的 401 边界由本文件续守。

覆盖：
  1. 现行端点无身份头 → 401
  2. 网关 guest 模式注入的 ``anonymous`` 占位身份同样 401（不是真实用户）
  3. 已认证请求不落 401 分支（/recommend 真跑）

不覆盖：X-API-Key 门禁（``backend/app/api/middleware/auth.py`` 职责，
另有测试）。
"""
from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.app.api.routes import travel as travel_route

# 网关注入的已认证身份头（与 identity._from_headers 口径一致）
_AUTH = {"X-User-Id": "15", "X-User-Name": "Mint", "X-Auth-Type": "jwt"}
# 网关 guest 模式（GATEWAY_AUTH_MODE=guest）注入的占位身份
_GUEST = {"X-User-Id": "anonymous", "X-Auth-Type": "anonymous"}

# (method, path, json body) —— body=None 表示无请求体；只含现行 router 端点
_ENDPOINTS: list[tuple[str, str, dict | None]] = [
    ("POST", "/travel/export/ics", {"itinerary": {}}),
    ("POST", "/travel/feedback", {"vote": "positive"}),
    ("GET", "/travel/recommend", None),
]


def _client() -> TestClient:
    """只挂 travel router 的最小 app：隔离验证 router 层鉴权边界。"""
    app = FastAPI()
    app.include_router(travel_route.router)
    return TestClient(app, raise_server_exceptions=False)


@pytest.mark.parametrize("method,path,body", _ENDPOINTS)
def test_unauthenticated_is_401(method: str, path: str, body: dict | None) -> None:
    """无身份头 → 401（六个端点全覆盖）。"""
    r = _client().request(method, path, json=body)
    assert r.status_code == 401, (
        f"{method} {path} 未认证应为 401，实得 {r.status_code}")


@pytest.mark.parametrize("method,path,body", _ENDPOINTS)
def test_anonymous_placeholder_is_401(method: str, path: str,
                                      body: dict | None) -> None:
    """``anonymous`` 占位身份不是真实用户 → 同样 401。

    网关 guest 模式下未认证请求会被注入 ``X-User-Id: anonymous``；
    若把占位当用户放行，偏好/反馈会落进共享的 "anonymous" 账号。
    """
    r = _client().request(method, path, json=body, headers=_GUEST)
    assert r.status_code == 401, (
        f"{method} {path} 匿名占位应为 401，实得 {r.status_code}")


def test_authenticated_recommend_ok() -> None:
    """已认证 → /recommend 真跑（纯函数，无 IO）。"""
    r = _client().get("/travel/recommend", headers=_AUTH)
    assert r.status_code == 200
    assert "recommendations" in r.json()


def test_v2_plan_stream_unauthenticated_is_401() -> None:
    """V2 规划流（唯一现行规划入口）未认证 → 401。

    与 V1 同源 fail-closed：``travel_v2_plan_stream`` 先过
    ``require_identity`` 再解析/执行，SSE 建立前即拦截。
    """
    r = _client().post("/travel/v2/plan/stream", json={"message": "杭州2天"})
    assert r.status_code == 401


def test_v2_plan_stream_anonymous_placeholder_is_401() -> None:
    """网关 guest 注入的 ``anonymous`` 占位身份对 V2 规划流同样 401。"""
    r = _client().post("/travel/v2/plan/stream", json={"message": "杭州2天"},
                       headers=_GUEST)
    assert r.status_code == 401
