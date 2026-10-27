"""治理配置发布/回滚/导入端到端（Batch 5-2，决策 #08/#14）。

真实依赖：PostgreSQL（迁移 117 已落库，-m pg）。覆盖：
  - 快照草稿（payload 与导出同构、不含凭据；含 URL 凭据净化红线）
  - 差异预览（modified/added/removed 三类，方向=回滚将发生什么）
  - 发布（draft→published；重复发布 409；draft 回滚 409）
  - 回滚（声明/授权精确对齐快照 + 动态成员 grants 同步 + 生成新版本）
  - 导入（纯增量：disabled 无凭据连接 + 不建授权 + 冲突跳过 + 凭据 URL 拒绝）

本文件所有语句均为 psycopg2 参数化（%(name)s 占位符），无字符串拼接。
"""
from __future__ import annotations

import uuid

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.app.api.routes import admin_governance_releases

pytestmark = pytest.mark.pg

TENANT = "e2e-rel-tenant"
OTHER_TENANT = "e2e-rel-tenant-b"
USER_ID = "u-e2e-rel-admin"
ADMIN_HEADERS = {
    "X-Auth-Type": "jwt",
    "X-User-Id": USER_ID,
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
    _db_exec("DELETE FROM ai.governance_releases WHERE tenant_id = ANY(%(t)s)",
             {"t": [TENANT, OTHER_TENANT]})
    _db_exec("DELETE FROM ai.mcp_connections WHERE tenant_id = ANY(%(t)s)",
             {"t": [TENANT, OTHER_TENANT]})
    _db_exec("DELETE FROM ai.capability_bundles WHERE name LIKE %(p)s",
             {"p": "rel-%"})
    yield
    _db_exec("DELETE FROM ai.governance_releases WHERE tenant_id = ANY(%(t)s)",
             {"t": [TENANT, OTHER_TENANT]})
    _db_exec("DELETE FROM ai.mcp_connections WHERE tenant_id = ANY(%(t)s)",
             {"t": [TENANT, OTHER_TENANT]})
    _db_exec("DELETE FROM ai.capability_bundles WHERE name LIKE %(p)s",
             {"p": "rel-%"})


@pytest.fixture()
def client(monkeypatch):
    monkeypatch.setattr(
        admin_governance_releases, "run_governance_mutation",
        lambda request, operator, operation, payload, callback: callback())
    app = FastAPI()
    app.include_router(admin_governance_releases.router)
    return TestClient(app, raise_server_exceptions=False)


def _insert_connection(name: str, base_url: str, tenant: str = TENANT) -> None:
    _db_exec(
        """
        INSERT INTO ai.mcp_connections
            (id, name, base_url, transport, auth_type, network_scope,
             enabled, status, tenant_id, created_by, updated_by)
        VALUES (%(id)s, %(name)s, %(url)s, 'http_api', 'none', 'private',
                false, 'never_connected', %(tenant)s, 'seed', 'seed')
        """,
        {"id": f"mcp_conn_{uuid.uuid4().hex[:12]}", "name": name,
         "url": base_url, "tenant": tenant})


def _insert_bundle(name: str, capabilities: list[str],
                   granted: list[str]) -> str:
    bundle_id = f"cap_bundle_{uuid.uuid4().hex[:12]}"
    _db_exec(
        "INSERT INTO ai.capability_bundles "
        "(id, name, description, domain, enabled, created_by, updated_by) "
        "VALUES (%(id)s, %(name)s, 'rel-e2e', 'data', true, 'seed', 'seed')",
        {"id": bundle_id, "name": name})
    for cap in capabilities:
        _db_exec(
            "INSERT INTO ai.capability_bundle_items (bundle_id, capability) "
            "VALUES (%(bid)s, %(cap)s) ON CONFLICT DO NOTHING",
            {"bid": bundle_id, "cap": cap})
    for product in granted:
        _db_exec(
            "INSERT INTO ai.product_bundle_grants "
            "(bundle_id, product_key, granted_by, tenant_id) "
            "VALUES (%(bid)s, %(pk)s, 'seed', %(tenant)s)",
            {"bid": bundle_id, "pk": product, "tenant": TENANT})
    return bundle_id


class TestGovernanceReleases:
    def test_snapshot_publish_rollback_lifecycle(self, client):
        _insert_connection("rel-conn-a", "http://127.0.0.1:9/mcp")
        _insert_bundle("rel-bundle-a", ["sql.query", "mcp.rel__get_order"],
                       ["agent"])
        # ── 1. 快照草稿 ──
        r = client.post("/admin/mcp/releases", headers=ADMIN_HEADERS,
                        json={"description": "基线版本"})
        assert r.status_code == 200, r.text
        v1 = r.json()
        # 快照之后新增的额外授权行：回滚后应被禁用（快照外漂移）
        bundle_id = _db_one(
            "SELECT id FROM ai.capability_bundles WHERE name = 'rel-bundle-a'")
        _db_exec(
            "INSERT INTO ai.product_bundle_grants "
            "(bundle_id, product_key, granted_by, tenant_id) "
            "VALUES (%(bid)s, 'customer_service', 'seed', %(tenant)s)",
            {"bid": bundle_id, "tenant": TENANT})
        assert v1["status"] == "draft" and v1["version"] == 1
        assert v1["connection_count"] == 1 and v1["bundle_count"] == 1
        raw = str(_db_one(
            "SELECT payload FROM ai.governance_releases WHERE id = %(id)s",
            {"id": v1["id"]}))
        assert "secret_cipher" not in raw and "fingerprint" not in raw

        # ── 2. 发布后制造漂移：改连接地址 + 撤销快照内的 agent 授权 ──
        r = client.post(f"/admin/mcp/releases/{v1['id']}/publish",
                        headers=ADMIN_HEADERS)
        assert r.status_code == 200 and r.json()["status"] == "published"
        r = client.post(f"/admin/mcp/releases/{v1['id']}/publish",
                        headers=ADMIN_HEADERS)
        assert r.status_code == 409
        _db_exec(
            "UPDATE ai.mcp_connections SET base_url = 'http://127.0.0.1:10/mcp' "
            "WHERE name = 'rel-conn-a' AND tenant_id = %(t)s", {"t": TENANT})
        _db_exec(
            "UPDATE ai.product_bundle_grants SET enabled = false, "
            "revoked_at = now() WHERE bundle_id = %(bid)s "
            "AND product_key = 'agent'", {"bid": bundle_id})

        # ── 3. 差异预览：modified（连接地址）+ modified（授权）──
        r = client.get(f"/admin/mcp/releases/{v1['id']}/diff",
                       headers=ADMIN_HEADERS)
        assert r.status_code == 200, r.text
        diff = r.json()
        conn_changes = [c for c in diff["changes"]
                        if c["kind"] == "connection"]
        assert conn_changes and conn_changes[0]["change"] == "modified"
        assert any(f["field"] == "base_url"
                   and f["release"] == "http://127.0.0.1:9/mcp"
                   for f in conn_changes[0]["fields"])
        bundle_changes = [c for c in diff["changes"] if c["kind"] == "bundle"
                          and c["name"] == "rel-bundle-a"]
        assert any(f["field"] == "granted_products"
                   for f in bundle_changes[0]["fields"])

        # ── 4. 回滚：声明恢复 + 授权对齐快照 + 新版本生成 ──
        r = client.post(f"/admin/mcp/releases/{v1['id']}/rollback",
                        headers=ADMIN_HEADERS)
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["rolled_back_to"] == 1
        assert body["restored_connections"] == 1
        assert body["restored_bundles"] == 1
        assert _db_one(
            "SELECT base_url FROM ai.mcp_connections "
            "WHERE name = 'rel-conn-a' AND tenant_id = %(t)s",
            {"t": TENANT}) == "http://127.0.0.1:9/mcp"
        # 快照内的 agent 授权恢复启用；快照外的 cs 授权被禁用
        active = _db_one(
            "SELECT count(*) FROM ai.product_bundle_grants "
            "WHERE bundle_id = %(bid)s AND enabled AND revoked_at IS NULL "
            "AND product_key = 'agent'", {"bid": bundle_id})
        assert active == 1
        cs_active = _db_one(
            "SELECT count(*) FROM ai.product_bundle_grants "
            "WHERE bundle_id = %(bid)s AND enabled AND revoked_at IS NULL "
            "AND product_key = 'customer_service'", {"bid": bundle_id})
        assert cs_active == 0
        # 回滚生成新版本（published，描述含回滚来源）
        new_rel = _db_one(
            "SELECT description FROM ai.governance_releases "
            "WHERE version = 2 AND tenant_id = %(t)s", {"t": TENANT})
        assert new_rel and "回滚自 v1" in new_rel

    def test_import_is_additive_disabled_and_credential_free(self, client):
        _insert_connection("rel-existing", "http://127.0.0.1:9/mcp")
        _db_exec(
            "INSERT INTO ai.capability_bundles "
            "(id, name, description, domain, enabled, created_by, updated_by) "
            "VALUES ('cap_bundle_rel_dup', 'rel-dup-bundle', '', 'data', "
            "true, 'seed', 'seed')")
        payload = {
            "version": 1,
            "connections": [
                {"name": "rel-import-new",
                 "base_url": "http://import.example.com/mcp",
                 "transport": "http_api", "auth_type": "static_bearer",
                 "network_scope": "public"},
                {"name": "rel-existing",
                 "base_url": "http://127.0.0.1:9/mcp",
                 "transport": "http_api", "auth_type": "none",
                 "network_scope": "private"},
                {"name": "rel-cred-bad",
                 "base_url": "http://user:pass@127.0.0.1:9/mcp?api_key=sk-x",
                 "transport": "http_api", "auth_type": "none",
                 "network_scope": "private"},
            ],
            "capability_bundles": [
                {"name": "rel-import-bundle", "description": "导入包",
                 "domain": "data", "capabilities": ["sql.query"],
                 "granted_products": ["agent"]},
                {"name": "rel-dup-bundle", "description": "", "domain": "data",
                 "capabilities": [], "granted_products": []},
            ],
        }
        r = client.post("/admin/mcp/import", headers=ADMIN_HEADERS,
                        json={"payload": payload})
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["created_connections"] == ["rel-import-new", "rel-cred-bad"]
        assert body["created_bundles"] == ["rel-import-bundle"]
        assert {s["name"] for s in body["skipped"]} == {
            "rel-existing", "rel-dup-bundle"}
        # 凭据 URL 净化后创建并透明标注（与导出语义一致，凭据不落库）
        assert {x["name"] for x in body["redacted"]} == {"rel-cred-bad"}
        assert _db_one(
            "SELECT base_url FROM ai.mcp_connections "
            "WHERE name = 'rel-cred-bad' AND tenant_id = %(t)s",
            {"t": TENANT}) == "http://127.0.0.1:9/mcp"

        # 导入的连接：disabled、无凭据、不进运行时
        assert _db_one(
            "SELECT enabled FROM ai.mcp_connections "
            "WHERE name = 'rel-import-new' AND tenant_id = %(t)s",
            {"t": TENANT}) is False
        assert _db_one(
            "SELECT count(*) FROM ai.mcp_connection_credentials k "
            "JOIN ai.mcp_connections c ON c.id = k.connection_id "
            "WHERE c.name = 'rel-import-new'") == 0
        # 导入的能力包：无授权行
        assert _db_one(
            "SELECT count(*) FROM ai.product_bundle_grants g "
            "JOIN ai.capability_bundles b ON b.id = g.bundle_id "
            "WHERE b.name = 'rel-import-bundle'") == 0

    def test_release_tenant_isolation(self, client):
        _insert_connection("rel-other-conn", "http://127.0.0.1:9/mcp",
                           tenant=OTHER_TENANT)
        _insert_connection("rel-mine", "http://127.0.0.1:9/mcp")
        r = client.post("/admin/mcp/releases", headers=ADMIN_HEADERS,
                        json={"description": "mine"})
        assert r.status_code == 200, r.text
        mine = r.json()
        # 快照只含本租户连接，其他租户不可见
        assert mine["connection_count"] == 1
        r = client.get(f"/admin/mcp/releases/{mine['id']}/diff",
                       headers=ADMIN_HEADERS)
        assert r.status_code == 200
