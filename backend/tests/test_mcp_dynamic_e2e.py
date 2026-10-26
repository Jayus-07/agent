"""端到端：MCP 能力中心全链路验收（ADR-0011）。

真实依赖：PostgreSQL（迁移 114 已落库，-m pg）+ 本地 mock MCP Server
（FastMCP Streamable HTTP，随机端口）。不使用真实外部服务，可重复执行。

覆盖验收场景：
  1  新增连接 → 发现工具 → 落库
  3  重复发现幂等；上游 Schema 变化 → 待重审
  4  未审核工具无法调用
  5  未授权产品无法调用已审核工具
  6  跨租户访问被拒（404）
  7  写操作工具不能裸测（只读测试门）
  8  连接异常 → 明确错误分类（error_class）
  9  停用连接/工具后新调用立即被拒
  10 配置变更无需重启（快照刷新生效）
  12 密钥不出现在任何 API 响应
  13 治理调用返回状态/耗时/错误码
  2  持久化由 DB 行断言（进程内快照每次从 DB 重读）
"""
from __future__ import annotations

import threading
import time
import uuid

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.app.api.routes import admin_mcp

pytestmark = pytest.mark.pg

TENANT = "e2e-mcp-tenant"
ADMIN_HEADERS = {
    "X-Auth-Type": "jwt",
    "X-User-Id": "u-e2e-admin",
    "X-User-Roles": "admin",
    "X-Tenant-Id": TENANT,
}
OTHER_TENANT_HEADERS = {
    "X-Auth-Type": "jwt",
    "X-User-Id": "u-e2e-admin-b",
    "X-User-Roles": "admin",
    "X-Tenant-Id": "e2e-mcp-tenant-b",
}
SECRET_VALUE = f"sk-e2e-{uuid.uuid4().hex}"


# ── mock MCP servers ─────────────────────────────────────────

def _build_app_v1():
    # mcp SDK 内置 FastMCP（宿主与容器都有 mcp 依赖，不引入第二依赖）
    from mcp.server.fastmcp import FastMCP

    mcp = FastMCP("e2e-mcp-server")

    @mcp.tool()
    def echo(query: str) -> dict:
        """回显查询（E2E 只读工具 v1）"""
        return {"status": "success", "data": {"echo": query}}

    @mcp.tool()
    def write_record(text: str) -> dict:
        """写入记录（E2E 写工具）"""
        return {"status": "success", "data": {"written": text}}

    return mcp.streamable_http_app()


def _build_app_v2():
    """echo 的 Schema 发生变化（新增必填参数 extra）→ 漂移检测用。"""
    from mcp.server.fastmcp import FastMCP

    mcp = FastMCP("e2e-mcp-server")

    @mcp.tool()
    def echo(query: str, extra: int) -> dict:
        """回显查询（E2E 只读工具 v2，Schema 已变化）"""
        return {"status": "success", "data": {"echo": query, "extra": extra}}

    return mcp.streamable_http_app()


def _start_uvicorn(app) -> tuple["uvicorn.Server", int]:
    import uvicorn

    config = uvicorn.Config(app, host="127.0.0.1", port=0, log_level="error")
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.time() + 15
    while time.time() < deadline and not server.started:
        time.sleep(0.05)
    if not server.started:
        raise RuntimeError("mock MCP server 启动超时")
    port = server.servers[0].sockets[0].getsockname()[1]
    return server, port


@pytest.fixture(scope="module")
def mock_ports():
    _, port_a = _start_uvicorn(_build_app_v1())
    _, port_b = _start_uvicorn(_build_app_v2())
    return {"a": port_a, "b": port_b}


# ── DB 前置 ──────────────────────────────────────────────────

def _db_exec(sql: str, params: dict | None = None):
    from backend.infra.db import get_memory_engine

    raw = get_memory_engine().raw_connection()
    try:
        with raw.cursor() as cur:
            cur.execute(sql, params or {})
        raw.commit()
    finally:
        raw.close()


def _db_rows(sql: str, params: dict | None = None):
    from psycopg2.extras import RealDictCursor

    from backend.infra.db import get_memory_engine

    raw = get_memory_engine().raw_connection()
    try:
        with raw.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(sql, params or {})
            return [dict(r) for r in cur.fetchall()]
    finally:
        raw.close()


@pytest.fixture(autouse=True)
def _clean_tables():
    try:
        _db_exec("SELECT 1 FROM ai.mcp_connections LIMIT 1")
    except Exception as exc:  # noqa: BLE001
        pytest.skip(f"ai.mcp_* 表不存在（迁移 114 未执行）: {exc}")
    _db_exec("DELETE FROM ai.mcp_connections WHERE tenant_id = %(t)s",
             {"t": TENANT})
    _db_exec("DELETE FROM ai.mcp_connections WHERE tenant_id = %(t)s",
             {"t": "e2e-mcp-tenant-b"})
    from backend.infra.mcp_registry import store as mcp_store

    mcp_store.reset_for_tests()
    yield
    _db_exec("DELETE FROM ai.mcp_connections WHERE tenant_id = %(t)s",
             {"t": TENANT})
    _db_exec("DELETE FROM ai.mcp_connections WHERE tenant_id = %(t)s",
             {"t": "e2e-mcp-tenant-b"})
    mcp_store.reset_for_tests()


@pytest.fixture()
def client(monkeypatch):
    """最小 app + 幂等旁路（幂等机制由共享设施保障，本测试聚焦 MCP 链路）。"""
    monkeypatch.setattr(
        admin_mcp, "run_governance_mutation",
        lambda request, operator, operation, payload, callback: callback())
    app = FastAPI()
    app.include_router(admin_mcp.router)
    return TestClient(app, raise_server_exceptions=False)


def _create_connection(client, mock_ports, **overrides):
    body = {
        "name": "e2e-srv",
        "base_url": f"http://127.0.0.1:{mock_ports['a']}/mcp",
        "auth_type": "static_bearer",
        "secret": SECRET_VALUE,
        "network_scope": "private",  # 本地 mock server = 内网目标
        "enabled": True,
    }
    body.update(overrides)
    r = client.post("/admin/mcp/connections", headers=ADMIN_HEADERS, json=body)
    assert r.status_code == 200, r.text
    return r.json()["id"]


def _discover(client, conn_id):
    r = client.post(f"/admin/mcp/connections/{conn_id}/discover",
                    headers=ADMIN_HEADERS)
    assert r.status_code == 200, r.text
    return r.json()


def _tools_of(client, conn_id):
    r = client.get(f"/admin/mcp/connections/{conn_id}/tools",
                   headers=ADMIN_HEADERS)
    assert r.status_code == 200, r.text
    return {t["tool_name"]: t for t in r.json()["tools"]}


def _approve(client, tool_id, **gov_overrides):
    gov = {"operation": "read", "risk_level": "R1",
           "confirmation_policy": "none", "timeout_ms": 15000,
           "planner_example": {"query": "回显"}}
    gov.update(gov_overrides)
    r = client.post(f"/admin/mcp/tools/{tool_id}/review",
                    headers=ADMIN_HEADERS,
                    json={"action": "approve", "governance": gov,
                          "examples": ["帮我回显一段文字", "把这句话原样返回"],
                          "notes": "E2E 审核"})
    assert r.status_code == 200, r.text
    return r.json()


def _invoke(client, tool_id, arguments, product="internal"):
    return client.post(f"/admin/mcp/tools/{tool_id}/test-invoke",
                       headers=ADMIN_HEADERS,
                       json={"arguments": arguments, "product_key": product})


class TestEndToEnd:
    def test_full_lifecycle(self, client, mock_ports):
        """连接 → 测试 → 发现 → 审核 → 授权 → 调用 → 漂移 → 停用。"""
        # ── 1. 新增连接（凭据密文入库，响应无明文）──
        conn_id = _create_connection(client, mock_ports)
        assert conn_id.startswith("mcp_conn_")
        detail = client.get(f"/admin/mcp/connections/{conn_id}",
                            headers=ADMIN_HEADERS).json()
        assert detail["credential"]["configured"] is True
        assert detail["credential"]["last4"] == SECRET_VALUE[-4:]
        assert SECRET_VALUE not in str(detail)  # 密钥绝不回显

        # ── 2. 连接测试（真实握手 + 耗时）──
        test = client.post(f"/admin/mcp/connections/{conn_id}/test",
                           headers=ADMIN_HEADERS).json()
        assert test["ok"] is True, test
        assert test["tool_count"] == 2
        assert test["elapsed_ms"] >= 0

        # ── 3. 首次发现：两个工具，pending（不可执行）──
        d1 = _discover(client, conn_id)
        assert d1["ok"] is True
        assert {n["tool"] for n in d1["new"]} == {"echo", "write_record"}
        tools = _tools_of(client, conn_id)
        assert tools["echo"]["status"] == "pending"
        assert tools["echo"]["capability"] == "mcp.e2e_srv__echo"

        # ── 4. 重复发现：幂等（unchanged）──
        d2 = _discover(client, conn_id)
        assert d2["ok"] is True
        assert len(d2["unchanged"]) == 2
        assert not d2["new"] and not d2["changed"]

        # ── 5. 未审核 → 无法测试调用（运行时未生效）──
        echo_id = tools["echo"]["id"]
        r = _invoke(client, echo_id, {"query": "hi"})
        assert r.status_code == 409
        assert "未在运行时生效" in r.json()["message"]

        # ── 6. 审核 echo（只读）→ 授权前产品身份调用被拒 ──
        _approve(client, echo_id)
        overview = client.get("/admin/mcp/overview",
                              headers=ADMIN_HEADERS).json()
        assert "mcp.e2e_srv__echo" in overview["active_tools"]
        r = _invoke(client, echo_id, {"query": "hi"}, product="agent")
        assert r.status_code == 200
        body = r.json()
        assert body["tool_status"] != "success"
        assert body["error_code"] == "product_capability_denied"

        # ── 7. internal 身份可调用（服务运维语义）──
        r = _invoke(client, echo_id, {"query": "hello-e2e"})
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["tool_status"] == "success", body
        assert body["result"]["data"]["echo"] == "hello-e2e"
        assert body["elapsed_ms"] >= 0

        # ── 8. 授权 agent 产品后，agent 身份可调用（B3 核心）──
        grant = client.post(f"/admin/mcp/tools/{echo_id}/grants",
                            headers=ADMIN_HEADERS,
                            json={"product_key": "agent"})
        assert grant.status_code == 200
        r = _invoke(client, echo_id, {"query": "agent-call"},
                    product="agent")
        body = r.json()
        assert body["tool_status"] == "success", body
        assert body["result"]["data"]["echo"] == "agent-call"
        # 未授权的 customer_service 仍被拒
        r = _invoke(client, echo_id, {"query": "x"},
                    product="customer_service")
        assert r.json()["error_code"] == "product_capability_denied"

        # ── 9. 非法参数被治理 Schema 拒绝（unknown 参数/类型错误）──
        r = _invoke(client, echo_id, {"query": 123})
        assert r.json()["tool_status"] != "success"
        assert r.json()["error_code"] == "invalid_param"

        # ── 10. 写工具：审核为写操作后禁止裸测 ──
        write_id = tools["write_record"]["id"]
        _approve(client, write_id, operation="update", risk_level="R2",
                 confirmation_policy="user_confirmation")
        r = _invoke(client, write_id, {"text": "x"})
        assert r.status_code == 409
        assert "只读" in r.json()["message"]

        # ── 11. Schema 漂移：换上游（v2 Schema）再发现 → 待重审 + 失效 ──
        upd = client.put(f"/admin/mcp/connections/{conn_id}",
                         headers=ADMIN_HEADERS,
                         json={"base_url": f"http://127.0.0.1:{mock_ports['b']}/mcp"})
        assert upd.status_code == 200, upd.text
        d3 = _discover(client, conn_id)
        assert d3["ok"] is True
        changed = {c["tool"]: c["reason"] for c in d3["changed"]}
        assert changed.get("echo") == "schema_changed_rereview_required"
        tools_after = _tools_of(client, conn_id)
        assert tools_after["echo"]["schema_changed"] is True
        # 漂移的 approved 工具立即退出运行时
        r = _invoke(client, echo_id, {"query": "hi"})
        assert r.status_code == 409
        # 重新审核后恢复（approved_hash 更新为新内容）
        _approve(client, echo_id)
        r = _invoke(client, echo_id, {"query": "v2-call", "extra": 1})
        assert r.json()["tool_status"] == "success"
        assert r.json()["result"]["data"]["extra"] == 1

        # ── 12. 停用工具 → 新调用立即被拒 ──
        dis = client.post(f"/admin/mcp/tools/{echo_id}/disable",
                          headers=ADMIN_HEADERS)
        assert dis.status_code == 200
        r = _invoke(client, echo_id, {"query": "hi"})
        assert r.status_code == 409

        # ── 13. 停用连接 → 其余工具也退出运行时 ──
        r = client.post(f"/admin/mcp/connections/{conn_id}/disable",
                        headers=ADMIN_HEADERS)
        assert r.status_code == 200
        overview = client.get("/admin/mcp/overview",
                              headers=ADMIN_HEADERS).json()
        assert "mcp.e2e_srv__write_record" not in overview["active_tools"]

        # ── 14. 持久化：DB 行存在（重启语义 = 快照从 DB 重读）──
        rows = _db_rows(
            "SELECT capability_slug, status FROM ai.mcp_tools "
            "WHERE connection_id = %(c)s", {"c": conn_id})
        assert {r["capability_slug"]: r["status"] for r in rows} == {
            "e2e_srv__echo": "disabled",
            # write_record 在换到 v2 server 后上游消失 → retired（步骤 11）
            "e2e_srv__write_record": "retired",
        }
        grants = _db_rows(
            "SELECT product_key, enabled FROM ai.mcp_tool_grants "
            "WHERE tool_id = %(t)s", {"t": echo_id})
        assert grants and grants[0]["product_key"] == "agent"

        # ── 15. 凭据密文落库（无明文）──
        cred = _db_rows(
            "SELECT secret_cipher, secret_fingerprint FROM "
            "ai.mcp_connection_credentials WHERE connection_id = %(c)s",
            {"c": conn_id})
        assert cred and SECRET_VALUE not in cred[0]["secret_cipher"]
        assert cred[0]["secret_cipher"].startswith("enc:") or cred[0]["secret_cipher"]

    def test_cross_tenant_isolation(self, client, mock_ports):
        """跨租户：连接与工具详情对他租户管理员 404。"""
        conn_id = _create_connection(client, mock_ports)
        _discover(client, conn_id)
        r = client.get(f"/admin/mcp/connections/{conn_id}",
                       headers=OTHER_TENANT_HEADERS)
        assert r.status_code == 404
        r = client.get(f"/admin/mcp/connections/{conn_id}/tools",
                       headers=OTHER_TENANT_HEADERS)
        assert r.status_code == 404
        # 他租户也不能删
        r = client.delete(f"/admin/mcp/connections/{conn_id}",
                          headers=OTHER_TENANT_HEADERS)
        assert r.status_code == 404

    def test_connection_failure_is_explicit(self, client, mock_ports):
        """连接异常 → 明确错误分类，不伪装成功（场景 8）。"""
        conn_id = _create_connection(
            client, mock_ports,
            base_url="http://127.0.0.1:1/mcp",  # 不可达端口
            name="e2e-dead")
        r = client.post(f"/admin/mcp/connections/{conn_id}/test",
                        headers=ADMIN_HEADERS)
        assert r.status_code == 200
        body = r.json()
        assert body["ok"] is False
        assert body["error_class"] in ("connection_failed", "timeout",
                                       "handshake_failed")
        assert body["error"]
        # 连接状态落库为 error
        detail = client.get(f"/admin/mcp/connections/{conn_id}",
                            headers=ADMIN_HEADERS).json()
        assert detail["status"] == "error"
        assert detail["last_error"]

    def test_delete_blocked_by_references(self, client, mock_ports):
        """存在工具记录/授权时删除被拒（409），避免悬空引用。"""
        conn_id = _create_connection(client, mock_ports)
        _discover(client, conn_id)
        r = client.delete(f"/admin/mcp/connections/{conn_id}",
                          headers=ADMIN_HEADERS)
        assert r.status_code == 409
        assert "工具记录" in r.json()["message"]
        # 清理工具（停用不删行——用直接 DB 清理模拟管理员清理）后可删
        _db_exec("DELETE FROM ai.mcp_tools WHERE connection_id = %(c)s",
                 {"c": conn_id})
        r = client.delete(f"/admin/mcp/connections/{conn_id}",
                          headers=ADMIN_HEADERS)
        assert r.status_code == 200

    def test_refresh_from_db_survives_process_restart_semantics(
            self, client, mock_ports):
        """快照从 DB 全量重读：reset（模拟新进程）后 refresh 恢复一致视图。"""
        from backend.infra.mcp_registry import store as mcp_store

        conn_id = _create_connection(client, mock_ports)
        _discover(client, conn_id)
        tools = _tools_of(client, conn_id)
        _approve(client, tools["echo"]["id"])
        client.post(f"/admin/mcp/tools/{tools['echo']['id']}/grants",
                    headers=ADMIN_HEADERS, json={"product_key": "agent"})
        # 模拟新进程：清空内存快照 → 从 DB 重建
        mcp_store.reset_for_tests()
        assert not mcp_store.snapshot().tools
        import asyncio

        assert asyncio.run(mcp_store.refresh())
        snap = mcp_store.snapshot()
        assert "mcp.e2e_srv__echo" in snap.tools
        assert ("agent", "mcp.e2e_srv__echo", TENANT) in snap.grants
