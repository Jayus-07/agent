# -*- coding: utf-8 -*-
"""AI 语义契约 API（决策 #19）：生成 / 草稿编辑 / 审核并入。

真实依赖：PostgreSQL（迁移 120 已落库，-m pg）。LLM 经
monkeypatch backend.infra.llm.llm 打桩（合法 JSON / 垃圾输出 / 抛异常三态）。
覆盖：
  - suggest：合法 JSON → 草稿落库（status=suggested）+ prompt 仅含工具
    元数据且不含 URL/凭据/连接 base_url；
  - suggest：垃圾输出重试 1 次仍失败 → 502；LLM 抛异常 → 503；
    非 approved/pending 工具 → 409；
  - draft：编辑保存 status=edited + 长度非法 400；
  - approve：summary 并入 description（截断 500）+ semantic_approved 落库
    + 草稿置 applied + 再 approve 409 + 无草稿 409；
  - 详情端点透出 semantic_draft / semantic_approved。
工具行直插照 test_mcp_health_monitor.py 模式（连接 + 工具，租户隔离清理）。
"""
from __future__ import annotations

import json
import uuid
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.app.api.routes import admin_mcp

pytestmark = pytest.mark.pg

TENANT = "e2e-semantic-tenant"
ADMIN_HEADERS = {
    "X-Auth-Type": "jwt",
    "X-User-Id": "u-e2e-semantic-admin",
    "X-User-Roles": "admin",
    "X-Tenant-Id": TENANT,
}
VIEWER_HEADERS = {
    "X-Auth-Type": "jwt",
    "X-User-Id": "u-e2e-semantic-viewer",
    "X-User-Roles": "viewer",
    "X-Tenant-Id": TENANT,
}
CONN_BASE_URL = "http://127.0.0.1:1/secret-mcp?api_key=sk-should-never-leak"

VALID_JSON = ('{"summary": "按 ID 查询订单状态", '
              '"scenarios": ["用户询问订单进度", "核对发货状态"], '
              '"exclusions": ["退款操作"], '
              '"capability_hint": null}')


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


def _db_exec_returning(sql: str, params: dict | None = None):
    """执行写语句并提交，返回 RETURNING 的第一列（读辅助 _db_one 不提交）。"""
    from backend.infra.db import get_memory_engine

    raw = get_memory_engine().raw_connection()
    try:
        with raw.cursor() as cur:
            cur.execute(sql, params or {})
            row = cur.fetchone()
            value = row[0] if row is not None else None
        raw.commit()
        return value
    finally:
        raw.close()


@pytest.fixture(autouse=True)
def _clean():
    _db_exec("DELETE FROM ai.mcp_connections WHERE tenant_id = %(t)s",
             {"t": TENANT})
    yield
    _db_exec("DELETE FROM ai.mcp_connections WHERE tenant_id = %(t)s",
             {"t": TENANT})


@pytest.fixture()
def client(monkeypatch):
    monkeypatch.setattr(
        admin_mcp, "run_governance_mutation",
        lambda request, operator, operation, payload, callback: callback())
    app = FastAPI()
    app.include_router(admin_mcp.router)
    return TestClient(app, raise_server_exceptions=False)


def _insert_tool(*, status: str = "approved",
                 governance: dict | None = None) -> int:
    """直插一条连接 + 工具行（描述故意带 URL，验证 prompt 清洗）。"""
    conn_id = f"mcp_conn_{uuid.uuid4().hex[:12]}"
    slug = f"semantic-{uuid.uuid4().hex[:10]}"
    _db_exec(
        """
        INSERT INTO ai.mcp_connections
            (id, name, base_url, transport, auth_type, network_scope,
             enabled, status, tenant_id, created_by, updated_by)
        VALUES (%(id)s, %(name)s, %(url)s, 'http_api', 'none', 'private',
                true, 'connected', %(tenant)s, 'seed', 'seed')
        """,
        {"id": conn_id, "name": "语义测试连接", "url": CONN_BASE_URL,
         "tenant": TENANT})
    tool_id = _db_exec_returning(
        """
        INSERT INTO ai.mcp_tools
            (connection_id, tool_name, capability_slug, description, status,
             content_hash, governance, last_seen_version, tenant_id)
        VALUES (%(cid)s, 'get_orders', %(slug)s,
                '查询订单 https://api.example.com/orders?v=1 详情', %(status)s,
                'hash-' || %(slug)s, %(gov)s::jsonb, 1, %(tenant)s)
        RETURNING id
        """,
        {"cid": conn_id, "slug": slug, "status": status,
         "gov": json.dumps(governance or {"operation": "read"}),
         "tenant": TENANT})
    return int(tool_id)


class _FakeLLM:
    """llm.invoke 打桩：记录收到的 messages，按 payload/异常脚本响应。"""

    def __init__(self, payloads: list):
        self.payloads = payloads
        self.calls: list = []

    def invoke(self, messages, **kwargs):
        self.calls.append(messages)
        item = self.payloads[min(len(self.calls) - 1, len(self.payloads) - 1)]
        if isinstance(item, Exception):
            raise item
        return SimpleNamespace(content=item)


class TestSuggest:
    def test_suggest_writes_draft_and_prompt_has_no_url_or_credential(
            self, client, monkeypatch):
        tool_id = _insert_tool()
        fake = _FakeLLM([VALID_JSON])
        monkeypatch.setattr("backend.infra.llm.llm", fake)

        r = client.post(f"/admin/mcp/tools/{tool_id}/semantic/suggest",
                        headers=ADMIN_HEADERS)
        assert r.status_code == 200, r.text
        draft = r.json()["draft"]
        assert draft["summary"] == "按 ID 查询订单状态"
        assert draft["status"] == "suggested"
        assert draft["scenarios"] == ["用户询问订单进度", "核对发货状态"]
        assert draft["exclusions"] == ["退款操作"]
        assert draft["capability_hint"] is None
        assert draft["generated_at"] and draft["generated_by"]

        # prompt 仅一次调用；不含上游描述中的 URL / 连接 base_url / 凭据参数
        assert len(fake.calls) == 1
        system_text = fake.calls[0][0][1]
        assert "get_orders" in system_text
        assert "read" in system_text  # governance.operation 进入 prompt
        for forbidden in ("https://", "http://", "api.example.com",
                          "127.0.0.1", "api_key", "sk-should-never-leak"):
            assert forbidden not in system_text, forbidden

        # 草稿落独立列（governance 不被污染）
        assert _db_one(
            "SELECT semantic_draft->>'status' FROM ai.mcp_tools "
            "WHERE id = %(t)s", {"t": tool_id}) == "suggested"
        assert _db_one(
            "SELECT governance->>'operation' FROM ai.mcp_tools "
            "WHERE id = %(t)s", {"t": tool_id}) == "read"

    def test_suggest_garbage_twice_returns_502(self, client, monkeypatch):
        tool_id = _insert_tool()
        fake = _FakeLLM(["这不是 JSON 输出", "完全无法解析的内容"])
        monkeypatch.setattr("backend.infra.llm.llm", fake)

        r = client.post(f"/admin/mcp/tools/{tool_id}/semantic/suggest",
                        headers=ADMIN_HEADERS)
        assert r.status_code == 502
        assert r.json()["code"] == "SEMANTIC_PARSE_FAILED"
        # 重试恰好 1 次：共 2 次 LLM 调用
        assert len(fake.calls) == 2
        assert _db_one("SELECT semantic_draft FROM ai.mcp_tools "
                       "WHERE id = %(t)s", {"t": tool_id}) is None

    def test_suggest_llm_unavailable_returns_503(self, client, monkeypatch):
        tool_id = _insert_tool()
        fake = _FakeLLM([RuntimeError("当前未配置可用模型")])
        monkeypatch.setattr("backend.infra.llm.llm", fake)

        r = client.post(f"/admin/mcp/tools/{tool_id}/semantic/suggest",
                        headers=ADMIN_HEADERS)
        assert r.status_code == 503
        assert r.json()["code"] == "LLM_UNAVAILABLE"
        assert "未配置可用模型" in r.json()["message"]

    def test_suggest_rejects_non_suggestable_status(self, client):
        tool_id = _insert_tool(status="retired")
        r = client.post(f"/admin/mcp/tools/{tool_id}/semantic/suggest",
                        headers=ADMIN_HEADERS)
        assert r.status_code == 409

    def test_suggest_rejects_viewer(self, client):
        r = client.post("/admin/mcp/tools/1/semantic/suggest",
                        headers=VIEWER_HEADERS)
        assert r.status_code == 403

    def test_suggest_missing_tool_404(self, client):
        r = client.post("/admin/mcp/tools/99999999/semantic/suggest",
                        headers=ADMIN_HEADERS)
        assert r.status_code == 404


class TestDraftEdit:
    def test_draft_edit_roundtrip(self, client, monkeypatch):
        tool_id = _insert_tool()
        monkeypatch.setattr("backend.infra.llm.llm",
                            _FakeLLM([VALID_JSON]))
        client.post(f"/admin/mcp/tools/{tool_id}/semantic/suggest",
                    headers=ADMIN_HEADERS)

        r = client.put(f"/admin/mcp/tools/{tool_id}/semantic/draft",
                       headers=ADMIN_HEADERS,
                       json={"summary": "人工修正后的描述",
                             "scenarios": ["场景A"],
                             "exclusions": [],
                             "capability_hint": "order.lookup"})
        assert r.status_code == 200, r.text
        record = r.json()
        assert record["status"] == "edited"
        assert record["summary"] == "人工修正后的描述"
        # 生成留痕保留（AI 建议 → 人工编辑审计链）；actor 带 subject 前缀
        assert str(record["generated_by"]).endswith("u-e2e-semantic-admin")
        assert str(record["edited_by"]).endswith("u-e2e-semantic-admin")

    def test_draft_edit_length_validation_400(self, client):
        tool_id = _insert_tool()
        r = client.put(f"/admin/mcp/tools/{tool_id}/semantic/draft",
                       headers=ADMIN_HEADERS,
                       json={"summary": "x" * 301})
        assert r.status_code == 400
        assert "summary" in r.json()["message"]
        r = client.put(f"/admin/mcp/tools/{tool_id}/semantic/draft",
                       headers=ADMIN_HEADERS,
                       json={"summary": "ok", "scenarios": ["x" * 201]})
        assert r.status_code == 400
        r = client.put(f"/admin/mcp/tools/{tool_id}/semantic/draft",
                       headers=ADMIN_HEADERS,
                       json={"summary": "ok",
                             "scenarios": [f"s{i}" for i in range(11)]})
        assert r.status_code == 400
        r = client.put(f"/admin/mcp/tools/{tool_id}/semantic/draft",
                       headers=ADMIN_HEADERS, json={"summary": "   "})
        assert r.status_code == 400


class TestApprove:
    def test_approve_merges_description_and_stores_contract(
            self, client, monkeypatch):
        tool_id = _insert_tool()
        monkeypatch.setattr("backend.infra.llm.llm", _FakeLLM([VALID_JSON]))
        client.post(f"/admin/mcp/tools/{tool_id}/semantic/suggest",
                    headers=ADMIN_HEADERS)

        r = client.post(f"/admin/mcp/tools/{tool_id}/semantic/approve",
                        headers=ADMIN_HEADERS)
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["approved"] is True
        assert body["description"] == "按 ID 查询订单状态"

        row = _db_one(
            "SELECT description || '|' || "
            "(semantic_approved->>'summary') || '|' || "
            "(semantic_approved->>'approved_by') || '|' || "
            "(semantic_draft->>'status') FROM ai.mcp_tools "
            "WHERE id = %(t)s", {"t": tool_id})
        description, approved_summary, approved_by, draft_status = \
            row.split("|")
        assert description == "按 ID 查询订单状态"
        assert approved_summary == "按 ID 查询订单状态"
        assert approved_by.endswith("u-e2e-semantic-admin")
        assert draft_status == "applied"

        # 详情端点透出两字段
        detail = client.get(f"/admin/mcp/tools/{tool_id}",
                            headers=ADMIN_HEADERS).json()
        assert detail["semantic_approved"]["summary"] == "按 ID 查询订单状态"
        assert detail["semantic_draft"]["status"] == "applied"

        # 再 approve → 409（无待审草稿）
        r = client.post(f"/admin/mcp/tools/{tool_id}/semantic/approve",
                        headers=ADMIN_HEADERS)
        assert r.status_code == 409

        # 已 applied 后仍可再次生成（覆盖草稿）并重新并入
        monkeypatch.setattr(
            "backend.infra.llm.llm",
            _FakeLLM([('{"summary": "第二版描述", "scenarios": [], '
                       '"exclusions": [], "capability_hint": "cap.x"}')]))
        r = client.post(f"/admin/mcp/tools/{tool_id}/semantic/suggest",
                        headers=ADMIN_HEADERS)
        assert r.status_code == 200
        r = client.post(f"/admin/mcp/tools/{tool_id}/semantic/approve",
                        headers=ADMIN_HEADERS)
        assert r.status_code == 200
        assert r.json()["description"] == "第二版描述"

    def test_approve_truncates_long_summary_to_spec_limit(
            self, client, monkeypatch):
        from backend.infra.mcp_registry import specs as spec_lib

        tool_id = _insert_tool()
        long_summary = "长" * 800
        monkeypatch.setattr(
            "backend.infra.llm.llm",
            _FakeLLM([('{"summary": "%s", "scenarios": [], "exclusions": [], '
                       '"capability_hint": null}' % long_summary)]))
        client.post(f"/admin/mcp/tools/{tool_id}/semantic/suggest",
                    headers=ADMIN_HEADERS)
        r = client.post(f"/admin/mcp/tools/{tool_id}/semantic/approve",
                        headers=ADMIN_HEADERS)
        assert r.status_code == 200
        # 草稿层 300 字上限先生效（300 < spec 500）；approve 的
        # MAX_DESCRIPTION_CHARS 截断为防御性兜底
        assert len(r.json()["description"]) == 300
        assert len(r.json()["description"]) <= spec_lib.MAX_DESCRIPTION_CHARS

    def test_approve_without_draft_conflicts_409(self, client):
        tool_id = _insert_tool()
        r = client.post(f"/admin/mcp/tools/{tool_id}/semantic/approve",
                        headers=ADMIN_HEADERS)
        assert r.status_code == 409
        assert "无待审语义草稿" in r.json()["message"]
