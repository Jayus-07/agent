"""连接健康巡检端到端（Batch 5-3，决策 #16）。

真实依赖：PostgreSQL（迁移 118 已落库，-m pg）+ mock server。覆盖：
  - 退避曲线（基础周期 × 2^min(N,4)，上限 ×16）；
  - 手工探活（/test）落健康事件 + 失败计数，恢复清零；
  - 巡检器 probe_once（直连闭端口 → error 事件 + 计数递增；恢复可达 →
    connected + 清零）；
  - 健康总览（租户隔离 + 事件时间线 + 退避字段）。
"""
from __future__ import annotations

import uuid

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.app.api.routes import admin_mcp

pytestmark = pytest.mark.pg

TENANT = "e2e-health-tenant"
ADMIN_HEADERS = {
    "X-Auth-Type": "jwt",
    "X-User-Id": "u-e2e-health-admin",
    "X-User-Roles": "admin",
    "X-Tenant-Id": TENANT,
}


def _db_exec(sql: str, params: dict | None = None) -> None:
    from backend.infra.db import get_memory_engine

    raw = get_memory_engine().raw_connection()
    try:
        with raw.cursor() as cur:
            cur.execute(sql, params or {})
        raw.commit()
    finally:
        raw.close()


def _db_one(sql: str, params: dict | None = None):
    from backend.infra.db import get_memory_engine

    raw = get_memory_engine().raw_connection()
    try:
        with raw.cursor() as cur:
            cur.execute(sql, params or {})
            row = cur.fetchone()
            return row[0] if row is not None else None
    finally:
        raw.close()


@pytest.fixture(autouse=True)
def _clean():
    _db_exec("DELETE FROM ai.mcp_connections WHERE tenant_id = %(t)s",
             {"t": TENANT})
    yield
    _db_exec("DELETE FROM ai.mcp_connections WHERE tenant_id = %(t)s",
             {"t": TENANT})


@pytest.fixture(scope="module")
def mock_ports():
    """可开关的 HTTP mock：closed 端口表示断线。"""
    import http.server
    import socket
    import threading

    class _Handler(http.server.BaseHTTPRequestHandler):
        def do_HEAD(self):  # noqa: N802
            self.send_response(200)
            self.end_headers()

        def do_GET(self):  # noqa: N802
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"{}")

        def log_message(self, *args):  # noqa: N803
            return

    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    dead_port = sock.getsockname()[1]
    sock.close()
    yield {"live": server.server_address[1], "dead": dead_port}
    server.shutdown()


@pytest.fixture()
def client():
    app = FastAPI()
    app.include_router(admin_mcp.router)
    return TestClient(app, raise_server_exceptions=False)


def _insert_http_connection(name: str, port: int) -> str:
    conn_id = f"mcp_conn_{uuid.uuid4().hex[:12]}"
    _db_exec(
        """
        INSERT INTO ai.mcp_connections
            (id, name, base_url, transport, auth_type, network_scope,
             enabled, status, tenant_id, created_by, updated_by)
        VALUES (%(id)s, %(name)s, %(url)s, 'http_api', 'none', 'private',
                true, 'never_connected', %(tenant)s, 'seed', 'seed')
        """,
        {"id": conn_id, "name": name,
         "url": f"http://127.0.0.1:{port}/mcp", "tenant": TENANT})
    return conn_id


class TestBackoffCurve:
    def test_backoff_doubles_and_caps(self):
        from backend.infra.mcp_registry.health import backoff_seconds

        base = 300.0
        assert backoff_seconds(0) == base
        assert backoff_seconds(1) == base * 2
        assert backoff_seconds(2) == base * 4
        assert backoff_seconds(4) == base * 16
        assert backoff_seconds(9) == base * 16  # 上限 ×16


class TestProbeLifecycle:
    def test_manual_probe_records_events_and_recovers(
            self, client, mock_ports):
        conn_id = _insert_http_connection("health-live", mock_ports["live"])

        # 1) 探活成功 → connected + fail_count=0 + manual 事件
        r = client.post(f"/admin/mcp/connections/{conn_id}/test",
                        headers=ADMIN_HEADERS)
        assert r.status_code == 200 and r.json()["ok"] is True, r.text
        assert _db_one(
            "SELECT status FROM ai.mcp_connections WHERE id = %(c)s",
            {"c": conn_id}) == "connected"
        assert _db_one(
            "SELECT probe_fail_count FROM ai.mcp_connections "
            "WHERE id = %(c)s", {"c": conn_id}) == 0
        assert _db_one(
            "SELECT count(*) FROM ai.mcp_health_events "
            "WHERE connection_id = %(c)s AND ok AND source = 'manual'",
            {"c": conn_id}) == 1

        # 2) 指向死端口（模拟断线）→ error + 计数递增
        _db_exec(
            "UPDATE ai.mcp_connections SET base_url = %(url)s WHERE id = %(c)s",
            {"url": f"http://127.0.0.1:{mock_ports['dead']}/mcp",
             "c": conn_id})
        r = client.post(f"/admin/mcp/connections/{conn_id}/test",
                        headers=ADMIN_HEADERS)
        assert r.status_code == 200 and r.json()["ok"] is False
        assert _db_one(
            "SELECT status FROM ai.mcp_connections WHERE id = %(c)s",
            {"c": conn_id}) == "error"
        assert _db_one(
            "SELECT probe_fail_count FROM ai.mcp_connections "
            "WHERE id = %(c)s", {"c": conn_id}) == 1

        # 3) 恢复可达 → connected + 计数清零（退避重连语义）
        _db_exec(
            "UPDATE ai.mcp_connections SET base_url = %(url)s WHERE id = %(c)s",
            {"url": f"http://127.0.0.1:{mock_ports['live']}/mcp",
             "c": conn_id})
        r = client.post(f"/admin/mcp/connections/{conn_id}/test",
                        headers=ADMIN_HEADERS)
        assert r.json()["ok"] is True
        assert _db_one(
            "SELECT probe_fail_count FROM ai.mcp_connections "
            "WHERE id = %(c)s", {"c": conn_id}) == 0

        # 4) 健康总览：事件时间线 + 退避字段 + 租户隔离
        r = client.get("/admin/mcp/health", headers=ADMIN_HEADERS)
        assert r.status_code == 200, r.text
        conns = {c["name"]: c for c in r.json()["connections"]}
        assert "health-live" in conns
        view = conns["health-live"]
        assert view["status"] == "connected"
        assert view["probe_fail_count"] == 0
        assert view["probe_interval_seconds"] == 300
        assert len(view["recent_events"]) == 3
        assert {e["source"] for e in view["recent_events"]} == {"manual"}

    def test_monitor_probe_once_marks_failure_and_recovery(
            self, client, mock_ports):
        from backend.infra.mcp_registry import health as mcp_health

        conn_id = _insert_http_connection("health-monitor", mock_ports["dead"])
        row = admin_mcp._query_one(
            "SELECT * FROM ai.mcp_connections WHERE id = %(c)s",
            {"c": conn_id})
        result = mcp_health.probe_once(dict(row))
        assert result["ok"] is False
        assert _db_one(
            "SELECT probe_fail_count FROM ai.mcp_connections "
            "WHERE id = %(c)s", {"c": conn_id}) == 1
        assert _db_one(
            "SELECT source FROM ai.mcp_health_events WHERE connection_id = %(c)s",
            {"c": conn_id}) == "monitor"

        # 恢复可达 → 巡检器探活成功即恢复 connected（不动 enabled）
        _db_exec(
            "UPDATE ai.mcp_connections SET base_url = %(url)s WHERE id = %(c)s",
            {"url": f"http://127.0.0.1:{mock_ports['live']}/mcp",
             "c": conn_id})
        row = admin_mcp._query_one(
            "SELECT * FROM ai.mcp_connections WHERE id = %(c)s",
            {"c": conn_id})
        result = mcp_health.probe_once(dict(row))
        assert result["ok"] is True
        assert _db_one(
            "SELECT status FROM ai.mcp_connections WHERE id = %(c)s",
            {"c": conn_id}) == "connected"
        assert _db_one(
            "SELECT enabled FROM ai.mcp_connections WHERE id = %(c)s",
            {"c": conn_id}) is True

    def test_health_overview_tenant_isolation(self, client, mock_ports):
        other = f"mcp_conn_{uuid.uuid4().hex[:12]}"
        _db_exec(
            """
            INSERT INTO ai.mcp_connections
                (id, name, base_url, transport, auth_type, network_scope,
                 enabled, status, tenant_id, created_by, updated_by)
            VALUES (%(id)s, 'health-other', %(url)s, 'http_api', 'none',
                    'private', true, 'never_connected', 'e2e-health-tenant-b',
                    'seed', 'seed')
            """,
            {"id": other, "url": f"http://127.0.0.1:{mock_ports['live']}/mcp"})
        try:
            r = client.get("/admin/mcp/health", headers=ADMIN_HEADERS)
            names = {c["name"] for c in r.json()["connections"]}
            assert "health-other" not in names
        finally:
            _db_exec("DELETE FROM ai.mcp_connections WHERE id = %(c)s",
                     {"c": other})
