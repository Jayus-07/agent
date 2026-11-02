"""能力包管理 API 端到端（Batch 2）。

真实依赖：PostgreSQL（迁移 116 已落库，-m pg）。覆盖：
  - 创建（名称冲突 / 非法 domain / 非法动态成员 fail-fast）
  - 更新（成员整体替换）
  - 授权 bundle 给产品 → bundle_registry 快照可见、静态成员进白名单
  - 撤销 → 快照与 mcp_tool_grants 同步失效（强一致撤权事实源）
  - bundle 内动态成员的 grants 自动同步（授权补建 / 撤销禁用）

本文件所有语句均为 psycopg2 参数化（%(name)s 占位符），无字符串拼接。
"""
from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.app.api.routes import admin_bundles

pytestmark = pytest.mark.pg

TENANT = "e2e-bundle-tenant"
ADMIN_HEADERS = {
    "X-Auth-Type": "jwt",
    "X-User-Id": "u-e2e-bundle-admin",
    "X-User-Roles": "admin",
    "X-Tenant-Id": TENANT,
}
DYN_SLUG = "bundle_rest__get_order"
DYN_CAP = "mcp.bundle_rest__get_order"


def _raw_conn():
    from backend.infra.db import get_memory_engine

    return get_memory_engine().raw_connection()


def _exec_delete_bundles_by_user():
    raw = _raw_conn()
    try:
        with raw.cursor() as cur:
            # created_by 实际落库为 "user:<id>"（_actor_of 前缀），裸 id
            # 永远匹配不上——残留行会让重名创建 409（2026-10-28 实证）
            cur.execute(
                "DELETE FROM ai.capability_bundles "
                "WHERE created_by IN (%(u)s, %(prefixed)s)",
                {"u": "u-e2e-bundle-admin",
                 "prefixed": "user:u-e2e-bundle-admin"})
        raw.commit()
    finally:
        raw.close()


def _exec_delete_orphan_grants():
    raw = _raw_conn()
    try:
        with raw.cursor() as cur:
            cur.execute(
                "DELETE FROM ai.capability_bundles WHERE id IN "
                "(SELECT bundle_id FROM ai.product_bundle_grants "
                "WHERE tenant_id = %(t)s)",
                {"t": TENANT})
        raw.commit()
    finally:
        raw.close()


def _tables_ready() -> bool:
    raw = _raw_conn()
    try:
        with raw.cursor() as cur:
            cur.execute(
                "SELECT EXISTS(SELECT 1 FROM information_schema.tables "
                "WHERE table_schema='ai' AND table_name='capability_bundles')")
            return bool(cur.fetchone()[0])
    finally:
        raw.close()


def _insert_fixture_tool():
    raw = _raw_conn()
    try:
        with raw.cursor() as cur:
            cur.execute(
                "INSERT INTO ai.mcp_connections "
                "(id, name, base_url, transport, network_scope, tenant_id, "
                "created_by) VALUES (%(cid)s, %(name)s, %(url)s, 'http_api', "
                "'private', %(t)s, 'e2e') ON CONFLICT (id) DO NOTHING",
                {"cid": "bundle_test_conn", "name": "bundle-rest",
                 "url": "http://127.0.0.1:1", "t": TENANT})
            cur.execute(
                "INSERT INTO ai.mcp_tools (connection_id, tool_name, "
                "capability_slug, description, status, content_hash, "
                "tenant_id) VALUES (%(cid)s, %(tool)s, %(slug)s, 'e2e', "
                "'approved', 'h1', %(t)s) "
                "ON CONFLICT (connection_id, tool_name) DO NOTHING",
                {"cid": "bundle_test_conn", "tool": "get_order",
                 "slug": DYN_SLUG, "t": TENANT})
        raw.commit()
    finally:
        raw.close()


def _fixture_tool_id() -> int:
    raw = _raw_conn()
    try:
        with raw.cursor() as cur:
            cur.execute(
                "SELECT id FROM ai.mcp_tools WHERE capability_slug = %(slug)s",
                {"slug": DYN_SLUG})
            return cur.fetchone()[0]
    finally:
        raw.close()


def _tool_grants(tool_id: int) -> list[tuple]:
    raw = _raw_conn()
    try:
        with raw.cursor() as cur:
            cur.execute(
                "SELECT product_key, enabled FROM ai.mcp_tool_grants "
                "WHERE tool_id = %(tid)s AND tenant_id = %(tn)s",
                {"tid": tool_id, "tn": TENANT})
            return cur.fetchall()
    finally:
        raw.close()


def _delete_fixture_tool(tool_id: int) -> None:
    raw = _raw_conn()
    try:
        with raw.cursor() as cur:
            cur.execute("DELETE FROM ai.mcp_tools WHERE id = %(tid)s",
                        {"tid": tool_id})
            cur.execute("DELETE FROM ai.mcp_connections WHERE id = %(cid)s",
                        {"cid": "bundle_test_conn"})
        raw.commit()
    finally:
        raw.close()


@pytest.fixture(autouse=True)
def _clean():
    if not _tables_ready():
        pytest.skip("ai.capability_* 表不存在（迁移 116 未执行）")
    _exec_delete_orphan_grants()
    _exec_delete_bundles_by_user()
    from backend.infra.bundle_registry import store as bundle_store

    bundle_store.reset_for_tests()
    yield
    _exec_delete_bundles_by_user()
    bundle_store.reset_for_tests()


@pytest.fixture()
def client(monkeypatch):
    monkeypatch.setattr(
        admin_bundles, "run_governance_mutation",
        lambda request, operator, operation, payload, callback: callback())
    app = FastAPI()
    app.include_router(admin_bundles.router)
    return TestClient(app, raise_server_exceptions=False)


def _create(client, name, capabilities=None, domain="data", **overrides):
    body = {"name": name, "description": "e2e", "domain": domain,
            "capabilities": capabilities or []}
    body.update(overrides)
    r = client.post("/admin/capability-bundles", headers=ADMIN_HEADERS,
                    json=body)
    assert r.status_code == 200, r.text
    return r.json()["id"]


class TestBundleLifecycle:
    def test_create_update_grant_revoke(self, client, monkeypatch):
        from backend.infra.bundle_registry import store as bundle_store
        from backend.core import product_context

        # ── 1. 创建（静态成员）──
        bundle_id = _create(client, "数据分析包",
                            ["sql.query", "report.generate"])
        detail = client.get(f"/admin/capability-bundles/{bundle_id}",
                            headers=ADMIN_HEADERS).json()
        assert detail["capabilities"] == ["report.generate", "sql.query"]

        # ── 2. 名称冲突 / 非法 domain 拒绝 ──
        r = client.post("/admin/capability-bundles", headers=ADMIN_HEADERS,
                        json={"name": "数据分析包", "domain": "data"})
        assert r.status_code == 409
        r = client.post("/admin/capability-bundles", headers=ADMIN_HEADERS,
                        json={"name": "X", "domain": "nope"})
        assert r.status_code == 400

        # ── 3. 授权给 agent ──
        r = client.post(f"/admin/capability-bundles/{bundle_id}/grants",
                        headers=ADMIN_HEADERS,
                        json={"product_key": "agent"})
        assert r.status_code == 200, r.text

        # ── 4. 刷新快照后：成员进白名单、非成员被拒 ──
        assert __import__('asyncio').run(bundle_store.refresh())
        monkeypatch.setattr(
            "backend.core.request_context.get_tool_tenant_id",
            lambda: TENANT)
        assert product_context.product_allows_capability("agent", "sql.query")
        assert product_context.product_allows_capability("agent", "report.generate")
        # 白名单语义：授权表非空后，未授权成员一律拒绝（即使黑名单不拦）
        assert not product_context.product_allows_capability("agent", "rag.search")

        # ── 5. 成员整体替换 ──
        r = client.put(f"/admin/capability-bundles/{bundle_id}",
                       headers=ADMIN_HEADERS,
                       json={"capabilities": ["rag.search"]})
        assert r.status_code == 200, r.text
        assert __import__('asyncio').run(bundle_store.refresh())
        assert product_context.product_allows_capability("agent", "rag.search")
        assert not product_context.product_allows_capability("agent", "sql.query")

        # ── 6. 撤销 → 快照失效，回退黑名单 ──
        r = client.post(f"/admin/capability-bundles/{bundle_id}/grants",
                        headers=ADMIN_HEADERS,
                        json={"product_key": "agent", "revoke": True})
        assert r.status_code == 200, r.text
        assert __import__('asyncio').run(bundle_store.refresh())
        # 无有效授权行 → 回退黑名单 → rag.search 重新对 agent 放行
        assert product_context.product_allows_capability("agent", "rag.search")

    def test_bundle_grant_syncs_dynamic_member_grants(self, client):
        """bundle 授权 → 动态 mcp.* 成员的 ai.mcp_tool_grants 同步补建；
        撤销 → 同步禁用（写操作强一致撤权的事实源）。"""
        from backend.infra.bundle_registry import store as bundle_store
        from backend.infra.mcp_registry import store as mcp_store
        from backend.infra.mcp_registry import specs as spec_lib

        _insert_fixture_tool()
        tool_id = _fixture_tool_id()
        # 注入运行时快照：成员校验要求动态工具已生效（approved+未漂移+连接启用）
        spec = spec_lib.derive_tool_spec(
            DYN_CAP, "e2e",
            {"type": "object", "properties": {}},
            {"operation": "read", "risk_level": "R1"})
        mcp_store.inject_snapshot_for_tests(mcp_store.McpRegistrySnapshot(
            loaded=True,
            tools={DYN_CAP: mcp_store.McpToolRuntime(
                capability=DYN_CAP, tool_id=tool_id,
                connection_id="bundle_test_conn", upstream_tool_name="get_order",
                tenant_id=TENANT, spec=spec, network_scope="private")},
            connections={"bundle_test_conn": mcp_store.McpConnectionRuntime(
                connection_id="bundle_test_conn",
                base_url="http://127.0.0.1:1", auth_type="none",
                network_scope="private")},
        ))
        try:
            bundle_id = _create(client, "订单查询包", [DYN_CAP], domain="mcp")
            r = client.post(f"/admin/capability-bundles/{bundle_id}/grants",
                            headers=ADMIN_HEADERS,
                            json={"product_key": "agent"})
            assert r.status_code == 200, r.text
            grants = _tool_grants(tool_id)
            assert grants and grants[0][1] is True

            # bundle 快照可见动态成员
            assert __import__('asyncio').run(bundle_store.refresh())
            assert DYN_CAP in bundle_store.snapshot().product_capabilities(
                "agent", TENANT)

            # 撤销 → grants 禁用 + 快照移除
            r = client.post(f"/admin/capability-bundles/{bundle_id}/grants",
                            headers=ADMIN_HEADERS,
                            json={"product_key": "agent", "revoke": True})
            assert r.status_code == 200, r.text
            grants = _tool_grants(tool_id)
            assert grants and grants[0][1] is False
            assert __import__('asyncio').run(bundle_store.refresh())
            assert DYN_CAP not in bundle_store.snapshot().product_capabilities(
                "agent", TENANT)
        finally:
            _delete_fixture_tool(tool_id)
            mcp_store.reset_for_tests()

    def test_unknown_dynamic_member_rejected(self, client):
        """动态成员未在运行时生效 → 登记拒绝（防悬空授权）。"""
        r = client.post("/admin/capability-bundles", headers=ADMIN_HEADERS,
                        json={"name": "悬空包", "domain": "mcp",
                              "capabilities": ["mcp.nope__missing"]})
        assert r.status_code == 400
        assert "未在运行时生效" in r.json()["message"]


class TestGrantDomainBoundary:
    """决策 #15 业务域硬隔离：跨域专属 bundle 不可授权给该产品入口，
    授权端点服务端拒绝（PR #115 审计修复）。"""

    def test_travel_bundle_rejected_for_agent_and_cs(self, client):
        bundle_id = _create(client, "旅游专属包", ["travel.poi_search"],
                            domain="travel")
        r = client.post(f"/admin/capability-bundles/{bundle_id}/grants",
                        headers=ADMIN_HEADERS, json={"product_key": "agent"})
        assert r.status_code == 400, r.text
        assert "跨域专属能力" in r.json()["message"]
        assert "travel.poi_search" in r.json()["message"]
        r = client.post(f"/admin/capability-bundles/{bundle_id}/grants",
                        headers=ADMIN_HEADERS,
                        json={"product_key": "customer_service"})
        assert r.status_code == 400, r.text

    def test_cs_bundle_rejected_for_agent_but_allowed_for_cs(self, client):
        bundle_id = _create(client, "客服专属包", ["cs.ticket.manage"],
                            domain="cs")
        r = client.post(f"/admin/capability-bundles/{bundle_id}/grants",
                        headers=ADMIN_HEADERS, json={"product_key": "agent"})
        assert r.status_code == 400, r.text
        r = client.post(f"/admin/capability-bundles/{bundle_id}/grants",
                        headers=ADMIN_HEADERS,
                        json={"product_key": "customer_service"})
        assert r.status_code == 200, r.text

    def test_member_root_rejected_even_when_domain_label_general(self, client):
        """domain 标注为 general 但静态成员是跨域专属 → 按成员根拒绝。"""
        bundle_id = _create(client, "伪通用包", ["travel.poi_search"],
                            domain="general")
        r = client.post(f"/admin/capability-bundles/{bundle_id}/grants",
                        headers=ADMIN_HEADERS, json={"product_key": "agent"})
        assert r.status_code == 400, r.text
        assert "travel.poi_search" in r.json()["message"]


class TestGrantTenantBoundary:
    """S1 审计修复：grant/runtime/tool/connection 租户必须一致。

    capability slug 全局唯一，历史实现下 B 租户管理员可用 A 租户已批准
    工具的 slug 创建 bundle，把执行授权绑到 A 的 runtime/connection/凭据
    （读调用经 product_granted 放行；写调用被 verify_write_authorization
    拦截）。修复后四层防线：登记端成员校验拒绝跨租户成员、授权端写入
    租户过滤 + 0 行显式冲突、读快照对存量错配行默认拒绝、迁移 130 的
    DB 组合外键兜底。
    """

    B_TENANT = "e2e-bundle-tenant-b"
    B_HEADERS = {
        "X-Auth-Type": "jwt",
        "X-User-Id": "u-e2e-bundle-admin-b",
        "X-User-Roles": "admin",
        "X-Tenant-Id": "e2e-bundle-tenant-b",
    }

    def _inject_runtime(self, tool_id: int) -> None:
        from backend.infra.mcp_registry import store as mcp_store
        from backend.infra.mcp_registry import specs as spec_lib

        spec = spec_lib.derive_tool_spec(
            DYN_CAP, "e2e",
            {"type": "object", "properties": {}},
            {"operation": "read", "risk_level": "R1"})
        mcp_store.inject_snapshot_for_tests(mcp_store.McpRegistrySnapshot(
            loaded=True,
            tools={DYN_CAP: mcp_store.McpToolRuntime(
                capability=DYN_CAP, tool_id=tool_id,
                connection_id="bundle_test_conn", upstream_tool_name="get_order",
                tenant_id=TENANT, spec=spec, network_scope="private")},
            connections={"bundle_test_conn": mcp_store.McpConnectionRuntime(
                connection_id="bundle_test_conn",
                base_url="http://127.0.0.1:1", auth_type="none",
                network_scope="private")},
        ))

    def _delete_grants_for_tenants(self) -> None:
        raw = _raw_conn()
        try:
            with raw.cursor() as cur:
                cur.execute(
                    "DELETE FROM ai.mcp_tool_grants WHERE tenant_id = ANY(%(ts)s)",
                    {"ts": [TENANT, self.B_TENANT]})
            raw.commit()
        finally:
            raw.close()

    def test_cross_tenant_member_rejected_at_bundle_create(self, client):
        """B 租户创建含 A 租户工具 slug 的 bundle → 400（登记端拒绝）。"""
        _insert_fixture_tool()
        tool_id = _fixture_tool_id()
        self._inject_runtime(tool_id)
        try:
            r = client.post(
                "/admin/capability-bundles", headers=self.B_HEADERS,
                json={"name": "跨租户包", "domain": "mcp",
                      "capabilities": [DYN_CAP]})
            assert r.status_code == 400, r.text
            assert "不属于当前租户" in r.json()["message"]
        finally:
            _delete_fixture_tool(tool_id)
            from backend.infra.mcp_registry import store as mcp_store

            mcp_store.reset_for_tests()

    def test_cross_tenant_bundle_grant_rejected_at_write(self, client):
        """A 创建自有工具 bundle 合法；B 授权该 bundle → 409，且不产生 B 的 grant 行。"""
        _insert_fixture_tool()
        tool_id = _fixture_tool_id()
        self._inject_runtime(tool_id)
        try:
            bundle_id = _create(client, "A自有工具包", [DYN_CAP], domain="mcp")
            r = client.post(
                f"/admin/capability-bundles/{bundle_id}/grants",
                headers=self.B_HEADERS, json={"product_key": "agent"})
            assert r.status_code == 409, r.text
            assert "不属于当前租户" in r.json()["message"]
            raw = _raw_conn()
            try:
                with raw.cursor() as cur:
                    cur.execute(
                        "SELECT count(*) FROM ai.mcp_tool_grants "
                        "WHERE tool_id = %(tid)s AND tenant_id = %(tn)s",
                        {"tid": tool_id, "tn": self.B_TENANT})
                    assert cur.fetchone()[0] == 0
            finally:
                raw.close()
        finally:
            _delete_fixture_tool(tool_id)
            from backend.infra.mcp_registry import store as mcp_store

            mcp_store.reset_for_tests()

    def test_poisoned_grant_row_excluded_from_snapshot(self):
        """存量错配行（tool 属 A、grant 记 B）不进读快照：product_granted
        与 verify_write_authorization 双双拒绝——迁移 130 之前 DB 可能存在
        此类行，读路径默认拒绝是执行链的最后防线。"""
        import asyncio

        import psycopg2

        from backend.infra.mcp_registry import store as mcp_store

        _insert_fixture_tool()
        tool_id = _fixture_tool_id()
        raw = _raw_conn()
        try:
            with raw.cursor() as cur:
                # A 租户正常授权行（回归正例的载体）
                cur.execute(
                    "INSERT INTO ai.mcp_tool_grants "
                    "(tool_id, product_key, granted_by, tenant_id) "
                    "VALUES (%(tid)s, 'agent', 'e2e', %(t)s) "
                    "ON CONFLICT (tool_id, product_key, tenant_id) DO NOTHING",
                    {"tid": tool_id, "t": TENANT})
                # 错配行：模拟迁移 130 之前的历史脏数据。组合外键生效的库
                # 上直插会被拒，用 session_replication_role 绕过（仅测试库
                # postgres 超用户可执行），专测读快照的防线本身。
                cur.execute("SET session_replication_role = replica")
                cur.execute(
                    "INSERT INTO ai.mcp_tool_grants "
                    "(tool_id, product_key, granted_by, tenant_id) "
                    "VALUES (%(tid)s, 'agent', 'e2e-poison', %(t)s)",
                    {"tid": tool_id, "t": self.B_TENANT})
                cur.execute("RESET session_replication_role")
            raw.commit()
        finally:
            raw.close()
        try:
            mcp_store.reset_for_tests()
            assert asyncio.run(mcp_store.refresh())
            assert mcp_store.product_granted("agent", DYN_CAP, self.B_TENANT) is False
            assert mcp_store.product_granted("agent", DYN_CAP, TENANT) is True
            ok, _reason = mcp_store.verify_write_authorization(
                DYN_CAP, "agent", self.B_TENANT)
            assert ok is False
        finally:
            self._delete_grants_for_tenants()
            _delete_fixture_tool(tool_id)
            mcp_store.reset_for_tests()

    def test_db_fk_rejects_cross_tenant_grant_insert(self):
        """迁移 130 组合外键：错配 grant 直插必须被 DB 拒绝（NOT VALID
        只豁免存量行，新写入立即生效）。"""
        import psycopg2

        raw = _raw_conn()
        try:
            with raw.cursor() as cur:
                cur.execute(
                    "SELECT 1 FROM pg_constraint "
                    "WHERE conname = 'fk_mcp_grants_tool_tenant'")
                if cur.fetchone() is None:
                    pytest.skip("迁移 130 未执行（组合外键不存在）")
        finally:
            raw.close()
        _insert_fixture_tool()
        tool_id = _fixture_tool_id()
        raw = _raw_conn()
        try:
            with raw.cursor() as cur:
                with pytest.raises(psycopg2.errors.ForeignKeyViolation):
                    cur.execute(
                        "INSERT INTO ai.mcp_tool_grants "
                        "(tool_id, product_key, granted_by, tenant_id) "
                        "VALUES (%(tid)s, 'agent', 'e2e', %(t)s)",
                        {"tid": tool_id, "t": self.B_TENANT})
                raw.rollback()
        finally:
            raw.close()
            _delete_fixture_tool(tool_id)
