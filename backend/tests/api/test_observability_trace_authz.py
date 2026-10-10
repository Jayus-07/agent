"""Trace 访问授权与租户隔离的**行为**测试（管理端/API 主题）。

覆盖要求：未认证/viewer/editor/admin 的访问矩阵；后端直接请求与网关请求
的权限边界；跨用户、跨租户、无权子 Trace 的读取；列表与详情的越权过滤；
父子关系、循环、去重、递归上限与部分失败。

实现说明：授权由 FastAPI 依赖（`_require_trace_reader`）强制，**依赖只在
HTTP 链路生效**，直接调用 handler 会绕过它。因此访问矩阵与端点行为一律经
`TestClient` 走真实路由（与 test_gateway_access_logs_api.py 同法）；仅纯函数
级的资源判定与子 Trace 遍历用直接调用。
"""
from __future__ import annotations

import asyncio

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from backend.app.api.routes import observability
from backend.app.api.routes import _trace_authz


def _trace(trace_id, *, tenant="", user="", question="合成问题", answer="合成答案"):
    tags = {}
    if tenant:
        tags["tenant_id"] = tenant
    if user:
        tags["user_id"] = user
    return {
        "id": trace_id,
        "session_id": f"sess-{trace_id}",
        "question": question,
        "answer_preview": answer,
        "tags": tags,
        "spans": [],
        "children_ids": [],
    }


def _headers(*, user_id=None, tenant=None, roles=None):
    h = {}
    if user_id:
        h["X-Auth-Type"] = "jwt"
        h["X-User-Id"] = user_id
    if tenant:
        h["X-Tenant-Id"] = tenant
    if roles:
        h["X-User-Roles"] = roles
    return h


class _FakeStore:
    """内存 store：仅实现端点用到的接口。"""

    def __init__(self, children=None, by_id=None):
        self._children = children or {}
        self._by_id = by_id or {}
        self.calls: list[tuple] = []

    def get(self, trace_id):
        return self._by_id.get(trace_id)

    def list_children(self, parent_id, limit=50, *, tenant_id=None):
        self.calls.append((parent_id, tenant_id))
        out = []
        for c in self._children.get(parent_id, []):
            ct = str((c.get("tags") or {}).get("tenant_id") or "")
            if tenant_id and ct == tenant_id:
                out.append(c)
        return out


@pytest.fixture
def app():
    a = FastAPI()
    a.include_router(observability.router)
    return a


@pytest.fixture
def client(app):
    return TestClient(app, raise_server_exceptions=False)


# ── 1. HTTP 层访问矩阵（依赖真实生效）──────────────────────


def test_detail_unauthenticated_is_denied(client, monkeypatch):
    """无身份头（服务凭据通道）不得读取 Trace 详情。"""
    monkeypatch.setattr(observability.trace_collector, "get",
                        lambda _tid: _trace("t1", tenant="A"))
    monkeypatch.setattr(observability, "get_trace_store", lambda: _FakeStore())

    r = client.get("/observability/traces/t1")
    assert r.status_code in (401, 403, 503), r.text
    assert "合成问题" not in r.text, "被拒时不得回显数据"


def test_detail_cross_tenant_admin_is_hidden(client, monkeypatch):
    """admin 不得跨租户；无权按 404 处理，不泄露存在性。"""
    monkeypatch.setattr(observability.trace_collector, "get",
                        lambda _tid: _trace("t1", tenant="B"))
    monkeypatch.setattr(observability, "get_trace_store", lambda: _FakeStore())

    r = client.get("/observability/traces/t1",
                   headers=_headers(user_id="u1", tenant="A", roles="admin"))
    assert r.status_code == 404, r.text


def test_detail_super_admin_reads_cross_tenant(client, monkeypatch):
    """super_admin 是唯一可跨租户的角色。"""
    monkeypatch.setattr(observability.trace_collector, "get",
                        lambda _tid: _trace("t1", tenant="B"))
    monkeypatch.setattr(observability, "get_trace_store", lambda: _FakeStore())
    monkeypatch.setattr(observability, "_backfill_usage", lambda _d: None)

    r = client.get("/observability/traces/t1",
                   headers=_headers(user_id="su", tenant="A",
                                    roles="super_admin"))
    assert r.status_code == 200, r.text
    assert r.json()["id"] == "t1"


def test_detail_same_tenant_admin_allowed(client, monkeypatch):
    monkeypatch.setattr(observability.trace_collector, "get",
                        lambda _tid: _trace("t1", tenant="A"))
    monkeypatch.setattr(observability, "get_trace_store", lambda: _FakeStore())
    monkeypatch.setattr(observability, "_backfill_usage", lambda _d: None)

    r = client.get("/observability/traces/t1",
                   headers=_headers(user_id="u1", tenant="A", roles="admin"))
    assert r.status_code == 200, r.text


def test_detail_viewer_cannot_read_other_users_trace(client, monkeypatch):
    """viewer 读同租户他人 Trace 应被拒（按 404 隐藏）。"""
    monkeypatch.setattr(observability, "get_trace_store", lambda: _FakeStore())
    monkeypatch.setattr(observability, "_backfill_usage", lambda _d: None)

    monkeypatch.setattr(observability.trace_collector, "get",
                        lambda _t: _trace("other", tenant="A", user="u2"))
    r = client.get("/observability/traces/other",
                   headers=_headers(user_id="u1", tenant="A",
                                    roles="viewer"))
    assert r.status_code == 404, r.text


def test_list_unauthenticated_is_denied(client, monkeypatch):
    """列表端点同样受依赖保护，未认证不得读取。"""
    monkeypatch.setattr(observability.trace_collector, "list",
                        lambda *_a, **_kw: [_trace("mine", tenant="A")])
    r = client.get("/observability/traces")
    assert r.status_code in (401, 403, 503), r.text


def test_list_filters_other_tenant_entries(client, monkeypatch):
    """列表只返回调用者有权读取的条目。"""
    monkeypatch.setattr(observability.trace_collector, "list",
                        lambda *_a, **_kw: [_trace("mine", tenant="A"),
                                            _trace("foreign", tenant="B")])
    r = client.get("/observability/traces",
                   headers=_headers(user_id="u1", tenant="A", roles="admin"))
    assert r.status_code == 200, r.text
    ids = [t["id"] for t in r.json()["traces"]]
    assert ids == ["mine"], f"越权条目不得下发: {ids}"


# ── 2. 资源级判定（纯函数） ────────────────────────────────


def test_undeclared_tenant_is_not_treated_as_shared():
    """Trace 未声明 tenant_id 时不得对所有登录用户可见。"""
    from starlette.requests import Request

    def req(uid="", tenant="", roles=""):
        headers = []
        if uid:
            headers += [(b"x-user-id", uid.encode()), (b"x-auth-type", b"jwt")]
        if tenant:
            headers.append((b"x-tenant-id", tenant.encode()))
        if roles:
            headers.append((b"x-user-roles", roles.encode()))
        return Request({"type": "http", "method": "GET", "path": "/x",
                        "headers": headers, "query_string": b""})

    assert _trace_authz.trace_visible_to(
        req("u1", "A", "admin"), _trace("x", tenant="", user="u1"),
        role="admin") is False
    assert _trace_authz.trace_visible_to(
        req("su", "A", "super_admin"), _trace("x", tenant=""),
        role="super_admin") is True


def test_request_without_tenant_cannot_see_tenant_scoped_trace():
    from starlette.requests import Request

    r = Request({"type": "http", "method": "GET", "path": "/x",
                 "headers": [(b"x-user-id", b"u1"),
                             (b"x-auth-type", b"jwt"),
                             (b"x-user-roles", b"admin")],
                 "query_string": b""})
    assert _trace_authz.trace_visible_to(r, _trace("x", tenant="A"),
                                        role="admin") is False


# ── 3. 子 Trace：授权 / 去重 / 循环 / 深度 / 失败 ──────────


def _req(user_id="u1", tenant="A", roles="admin"):
    from starlette.requests import Request

    headers = []
    if user_id:
        headers += [(b"x-user-id", user_id.encode()), (b"x-auth-type", b"jwt")]
    if tenant:
        headers.append((b"x-tenant-id", tenant.encode()))
    if roles:
        headers.append((b"x-user-roles", roles.encode()))
    return Request({"type": "http", "method": "GET", "path": "/x",
                    "headers": headers, "query_string": b""})


def test_children_require_per_child_authorization():
    """父可读不等于子可读：无权子 Trace 不得进入结果。"""
    ok_child = _trace("c-ok", tenant="A", user="u1")
    cross = _trace("c-foreign", tenant="B", user="u9")
    store = _FakeStore(children={"p": [ok_child, cross]})

    out = _trace_authz.collect_authorized_children(
        _req(), store, "p", role="admin")
    ids = [c["id"] for c in out]
    assert "c-ok" in ids
    assert "c-foreign" not in ids, "跨租户子 Trace 不得暴露"


def test_children_cycle_does_not_loop_forever():
    a = _trace("a", tenant="A", user="u1")
    b = _trace("b", tenant="A", user="u1")
    store = _FakeStore(children={"a": [b], "b": [a]})

    out = _trace_authz.collect_authorized_children(
        _req(), store, "a", role="admin")
    ids = [c["id"] for c in out]
    assert ids.count("b") == 1
    assert "a" not in ids


def test_children_depth_is_capped():
    chain = {}
    ids = [f"n{i}" for i in range(_trace_authz.MAX_CHILD_DEPTH + 5)]
    for i, nid in enumerate(ids[:-1]):
        chain[nid] = [_trace(ids[i + 1], tenant="A", user="u1")]
    store = _FakeStore(children=chain)

    out = _trace_authz.collect_authorized_children(
        _req(), store, ids[0], role="admin")
    assert len(out) == _trace_authz.MAX_CHILD_DEPTH


def test_children_store_failure_is_soft():
    class Boom:
        def list_children(self, *_a, **_kw):
            raise RuntimeError("store down")

    out = _trace_authz.collect_authorized_children(
        _req(), Boom(), "p", role="admin")
    assert out == []


def test_children_query_is_tenant_scoped():
    store = _FakeStore(children={"p": []})
    _trace_authz.collect_authorized_children(_req(), store, "p", role="admin")
    assert store.calls and store.calls[0][1] == "A"

    store2 = _FakeStore(children={"p": []})
    out = _trace_authz.collect_authorized_children(
        _req(tenant=""), store2, "p", role="admin")
    assert out == []
    assert store2.calls == [], "未声明租户不得发起无作用域子查询"
