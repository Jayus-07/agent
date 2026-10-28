# -*- coding: utf-8 -*-
"""管理端 Tool Discovery 策略与模拟器 API（决策 #18，B1）。

真实依赖：PostgreSQL（迁移 119 已落库，-m pg）。覆盖：
  - PUT 后 GET 回显（upsert 幂等 + 单 active 不变量）
  - simulate 在 agent 产品下排除 travel.*（域禁行根语义 + 权限说明）
  - 非法 mode / 越界数值 400
门禁模式照 admin 治理路由既有测试：run_governance_mutation 打桩直通
（幂等层由 shared.idempotency 自测覆盖），ADMIN_HEADERS 注入网关身份头。
"""
from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.app.api.routes import admin_tools

pytestmark = pytest.mark.pg

TENANT = "e2e-discovery-tenant"
USER_ID = "u-e2e-discovery-admin"
ADMIN_HEADERS = {
    "X-Auth-Type": "jwt",
    "X-User-Id": USER_ID,
    "X-User-Roles": "admin",
    "X-Tenant-Id": TENANT,
}
EDITOR_HEADERS = {
    "X-Auth-Type": "jwt",
    "X-User-Id": "u-e2e-discovery-editor",
    "X-User-Roles": "editor",
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


@pytest.fixture(autouse=True)
def _clean():
    _db_exec("DELETE FROM ai.tool_discovery_policy WHERE tenant_id = %(t)s",
             {"t": TENANT})
    yield
    _db_exec("DELETE FROM ai.tool_discovery_policy WHERE tenant_id = %(t)s",
             {"t": TENANT})


@pytest.fixture()
def client(monkeypatch):
    monkeypatch.setattr(
        admin_tools, "run_governance_mutation",
        lambda request, operator, operation, payload, callback: callback())
    app = FastAPI()
    app.include_router(admin_tools.router)
    return TestClient(app, raise_server_exceptions=False)


class TestDiscoveryPolicy:
    def test_put_then_get_roundtrip_and_single_active(self, client):
        # 无 active 行 → 默认值
        r = client.get("/admin/tools/discovery/policy", headers=ADMIN_HEADERS)
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["policy"]["mode"] == "adaptive"
        assert body["policy"]["active"] is False
        assert body["policies"] == []

        # PUT 后 GET 回显
        r = client.put("/admin/tools/discovery/policy", headers=ADMIN_HEADERS,
                       json={"name": "基线策略", "mode": "reasoning",
                             "top_k": 5, "max_rounds": 3,
                             "cost_budget_milliunits": 80,
                             "timeout_ms": 12000,
                             "prompt_key": "router.tool_selector"})
        assert r.status_code == 200, r.text
        upserted = r.json()
        assert upserted["active"] is True
        assert upserted["id"].startswith("disc_pol_")
        assert upserted["mode"] == "reasoning" and upserted["top_k"] == 5
        assert upserted["tenant_id"] == TENANT

        r = client.get("/admin/tools/discovery/policy", headers=ADMIN_HEADERS)
        body = r.json()
        assert body["policy"]["id"] == upserted["id"]
        assert body["policy"]["mode"] == "reasoning"
        assert [p["id"] for p in body["policies"]] == [upserted["id"]]

        # 同名再 PUT = upsert 同一行；再建另一策略 → 旧行失去 active
        r = client.put("/admin/tools/discovery/policy", headers=ADMIN_HEADERS,
                       json={"name": "基线策略", "mode": "fast", "top_k": 2})
        assert r.status_code == 200, r.text
        assert r.json()["id"] == upserted["id"]
        r = client.put("/admin/tools/discovery/policy", headers=ADMIN_HEADERS,
                       json={"name": "激进策略", "mode": "adaptive", "top_k": 8})
        assert r.status_code == 200, r.text
        second_id = r.json()["id"]
        rows = client.get("/admin/tools/discovery/policy",
                          headers=ADMIN_HEADERS).json()
        actives = [p for p in rows["policies"] if p["active"]]
        assert [p["id"] for p in actives] == [second_id]
        assert rows["policy"]["top_k"] == 8

    def test_invalid_mode_and_bounds_rejected_with_400(self, client):
        r = client.put("/admin/tools/discovery/policy", headers=ADMIN_HEADERS,
                       json={"name": "x", "mode": "turbo"})
        assert r.status_code == 400
        assert "不支持的发现模式" in r.json()["detail"]
        for field, value in (("top_k", 0), ("top_k", 21),
                             ("max_rounds", 6), ("timeout_ms", 500)):
            payload = {"name": "x", "mode": "adaptive"}
            payload[field] = value
            r = client.put("/admin/tools/discovery/policy",
                           headers=ADMIN_HEADERS, json=payload)
            assert r.status_code == 400, (field, value, r.text)

    def test_put_requires_admin_and_get_allows_editor(self, client):
        r = client.put("/admin/tools/discovery/policy", headers=EDITOR_HEADERS,
                       json={"name": "x"})
        assert r.status_code in (401, 403)
        r = client.get("/admin/tools/discovery/policy", headers=EDITOR_HEADERS)
        assert r.status_code == 200


class TestDiscoverySimulate:
    def test_simulate_excludes_travel_candidates_for_agent_product(self, client):
        r = client.post("/admin/tools/discovery/simulate", headers=ADMIN_HEADERS,
                        json={"query": "规划福州 2 天的行程", "domain": "travel",
                              "top_k": 5})
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["source"] == "domain_registry"
        assert body["candidate_count"] > 0
        # agent 产品禁行根含 travel → 全部候选被过滤，且逐候选说明只给保留项
        assert body["candidates"] == []
        assert body["excluded_count"] == body["candidate_count"]
        assert any("剔除" in n for n in body["notes"])

    def test_simulate_returns_candidates_with_permission_notes(self, client):
        r = client.post("/admin/tools/discovery/simulate", headers=ADMIN_HEADERS,
                        json={"query": "公司年假规定是什么", "domain": "knowledge",
                              "top_k": 2})
        assert r.status_code == 200, r.text
        body = r.json()
        assert len(body["candidates"]) == 2
        for cand in body["candidates"]:
            assert cand["name"]
            assert cand["risk"]
            assert cand["source"] == "domain_registry"
            assert "产品范围允许" in cand["permission_note"]
        assert body["policy"]["mode"] == "adaptive"

    def test_simulate_rejects_unknown_product_and_bad_top_k(self, client):
        r = client.post("/admin/tools/discovery/simulate", headers=ADMIN_HEADERS,
                        json={"query": "q", "product_id": "travel_agent"})
        assert r.status_code == 400
        r = client.post("/admin/tools/discovery/simulate", headers=ADMIN_HEADERS,
                        json={"query": "q", "top_k": 99})
        assert r.status_code == 400
