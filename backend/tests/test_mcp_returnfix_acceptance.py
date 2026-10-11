"""PR#27 返修验收（2026-10-11 ChatGPT+Codex 联合复审新增验收）

覆盖:
  1. REST 授权矩阵：viewer/editor/admin/super_admin × get_stats、
     权限声明缺失、受限文档（permission_scope）下推
  2. 连续请求身份隔离：permissions/roles/tenant 不跨请求串扰
  3. 8091 标准 MCP：错误 API Key 401；机器主体 svc:mcp-protocol
     （service_workflow，无 rag.admin——禁止伪造 admin）；get_stats 拒绝
  4. Java 内部网关：actor 委托 + 未携带维度显式置空；白名单拒绝
  5. RAG Tool 权限参数全程传递：roles→Principal→ask_result；
     admin 与普通用户 KB 可见性差异
  6. 失败语义分离：REST ok:false（业务失败）；协议通道异常透传
  7. 问题/回答原文不进日志
"""
import json
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import backend.rag.indexing.doc_registry as doc_registry_module
from backend.app.api.routes.internal_ai import router as internal_ai_router
from backend.app.api.routes.mcp import router as mcp_router
from backend.core.request_context import (
    get_tool_department,
    get_tool_permissions,
    get_tool_roles,
    get_tool_tenant_id,
    get_tool_user_id,
    set_tool_department,
    set_tool_permissions,
    set_tool_roles,
    set_tool_tenant_id,
    set_tool_user_id,
)

_KB_A, _KB_B = "policy_general", "policy_hr"


# ==================== 公共替身 ====================

class FakeRegistry:
    """PG registry 替身：行带 file_path 与受限 permission_scope。"""

    last_kwargs: dict = {}

    def __init__(self, *args, **kwargs):
        pass

    @staticmethod
    def _rows():
        return [
            {"doc_id": "d1", "file_name": "a.md", "kb_id": _KB_A,
             "chunk_count": 3, "file_size": 10, "status": "active",
             "department": "", "doc_type": "", "created_at": "", "updated_at": "",
             "permission_scope": "general", "file_path": "/srv/secret/a.md"},
            {"doc_id": "d2", "file_name": "b.md", "kb_id": _KB_B,
             "chunk_count": 5, "file_size": 20, "status": "active",
             "department": "hr", "doc_type": "", "created_at": "", "updated_at": "",
             "permission_scope": "restricted", "file_path": "/srv/secret/b.md"},
        ]

    def list_active(self):
        return self._rows()

    def search(self, **kwargs):
        FakeRegistry.last_kwargs = kwargs
        kb_scope = set(kwargs.get("kb_scope") or [])
        visible = set(kwargs.get("visible_scopes") or [])
        items = [d for d in self._rows()
                 if d["kb_id"] in kb_scope
                 and ((d.get("permission_scope") or "general") in visible)]
        return {"items": items, "total": len(items)}

    def distinct_permission_scopes(self):
        return ["general", "restricted"]


@pytest.fixture()
def fake_registry(monkeypatch):
    monkeypatch.setattr(doc_registry_module, "DocumentRegistry", FakeRegistry)
    FakeRegistry.last_kwargs = {}
    return FakeRegistry


def _unbind_all():
    set_tool_user_id("")
    set_tool_roles(())
    set_tool_department("")
    set_tool_tenant_id("")
    set_tool_permissions(None)


@pytest.fixture()
def clean_ctx():
    _unbind_all()
    yield
    _unbind_all()


@pytest.fixture()
def rest_client():
    """REST 通道真实授权矩阵：注册真 RAGMCPServer（模拟 app startup），
    registry 用 FakeRegistry 替身，manager.route 真实执行。"""
    from mcp_servers.manager import manager as global_manager
    from mcp_servers.servers.rag import RAGMCPServer

    global_manager.register(RAGMCPServer())
    app = FastAPI()
    app.include_router(mcp_router, prefix="/api")
    return TestClient(app)


def _headers(uid="u1", roles="viewer", permissions=None, tenant="t1", dept=""):
    h = {"X-User-Id": uid, "X-User-Roles": roles, "X-Tenant-Id": tenant,
         "X-User-Dept": dept}
    if permissions is not None:
        h["X-User-Permissions"] = permissions
    return h


# ==================== 1. REST 授权矩阵 ====================

@pytest.mark.usefixtures("fake_registry", "clean_ctx")
class TestRestAuthzMatrix:
    def test_unauthenticated_401(self, rest_client):
        resp = rest_client.post("/api/mcp/call",
                                json={"tool_name": "get_stats", "params": {}})
        assert resp.status_code == 401

    def test_viewer_get_stats_business_failure_ok_false(self, rest_client):
        """viewer 调管理员工具：HTTP 200（协议成功）但 ok:false（业务失败）
        ——失败与成功在响应体显式分离，不再伪装。"""
        resp = rest_client.post("/api/mcp/call",
                                json={"tool_name": "get_stats", "params": {}},
                                headers=_headers(roles="viewer"))
        body = resp.json()
        assert resp.status_code == 200
        assert body["ok"] is False
        assert "仅限管理员" in body["error"]

    def test_editor_get_stats_denied(self, rest_client):
        resp = rest_client.post("/api/mcp/call",
                                json={"tool_name": "get_stats", "params": {}},
                                headers=_headers(roles="editor"))
        assert resp.json()["ok"] is False

    def test_admin_get_stats_ok_sanitized(self, rest_client):
        resp = rest_client.post("/api/mcp/call",
                                json={"tool_name": "get_stats", "params": {}},
                                headers=_headers(roles="admin"))
        body = resp.json()
        assert body["ok"] is True
        result = body["result"]
        assert result["doc_count"] == 2 and result["vector_db"] == "pgvector"
        assert "/srv" not in json.dumps(body)

    def test_super_admin_get_stats_ok(self, rest_client):
        resp = rest_client.post("/api/mcp/call",
                                json={"tool_name": "get_stats", "params": {}},
                                headers=_headers(roles="super_admin"))
        assert resp.json()["ok"] is True

    def test_viewer_without_permissions_cannot_see_restricted(self, rest_client):
        """权限声明缺失 = 未声明：受限 permission_scope 文档不可见。"""
        resp = rest_client.post(
            "/api/mcp/call",
            json={"tool_name": "list_documents", "params": {"page": 1}},
            headers=_headers(roles="viewer", permissions=None))
        body = resp.json()
        assert body["ok"] is True
        visible_scopes = FakeRegistry.last_kwargs.get("visible_scopes") or []
        assert "restricted" not in visible_scopes
        assert all(d["doc_id"] != "d2" for d in body["result"]["documents"])

    def test_admin_sees_restricted(self, rest_client):
        resp = rest_client.post(
            "/api/mcp/call",
            json={"tool_name": "list_documents", "params": {"page": 1}},
            headers=_headers(roles="admin"))
        docs = resp.json()["result"]["documents"]
        assert {d["doc_id"] for d in docs} == {"d1", "d2"}
        assert all("file_path" not in d for d in docs)


# ==================== 2. 连续请求身份隔离 ====================

@pytest.mark.usefixtures("fake_registry", "clean_ctx")
class TestNoIdentityLeakBetweenRequests:
    def test_consecutive_requests_isolated(self, rest_client, monkeypatch):
        """登录用户连续请求：permissions/roles/tenant 各自正确，不串。"""
        from mcp_servers.manager import manager as global_manager

        captured = []

        def spy_route(tool_name, params):
            captured.append({
                "user": get_tool_user_id(),
                "roles": get_tool_roles(),
                "permissions": get_tool_permissions(),
                "tenant": get_tool_tenant_id(),
                "dept": get_tool_department(),
            })
            return {"ok": True, "result": {}}

        monkeypatch.setattr(global_manager, "route", spy_route)
        rest_client.post("/api/mcp/call",
                         json={"tool_name": "search_knowledge",
                               "params": {"question": "q1"}},
                         headers=_headers(uid="viewer_u", roles="viewer",
                                          permissions="doc.a", tenant="t1"))
        rest_client.post("/api/mcp/call",
                         json={"tool_name": "search_knowledge",
                               "params": {"question": "q2"}},
                         headers=_headers(uid="admin_u", roles="admin",
                                          permissions=None, tenant="t2",
                                          dept="hr"))
        first, second = captured
        assert first["user"] == "viewer_u" and second["user"] == "admin_u"
        assert first["roles"] == ("viewer",) and second["roles"] == ("admin",)
        assert first["permissions"] == ("doc.a",)
        assert second["permissions"] is None  # 未声明显式置空，不继承上一请求
        assert first["tenant"] == "t1" and second["tenant"] == "t2"
        assert second["dept"] == "hr" and first["dept"] == ""


# ==================== 3. 8091 标准协议：机器主体 ====================

class TestProtocol8091ServicePrincipal:
    @pytest.fixture()
    def proto_client(self, monkeypatch, fake_registry):
        from mcp_servers.manager import MCPManager
        from mcp_servers.protocol_app import build_protocol_app
        from mcp_servers.servers.rag import RAGMCPServer

        monkeypatch.setattr("backend.config.API_KEY", "test-key-123")
        monkeypatch.setattr("backend.config.ALLOW_UNAUTHENTICATED", False)
        monkeypatch.setattr("backend.config.ENVIRONMENT", "dev")
        mgr = MCPManager()
        mgr.register(RAGMCPServer())
        app = build_protocol_app(mgr)
        with TestClient(app) as c:
            yield c

    @staticmethod
    def _rpc_call(tool, arguments=None):
        return {"jsonrpc": "2.0", "method": "tools/call",
                "params": {"name": tool, "arguments": arguments or {}}, "id": 1}

    def test_wrong_api_key_401(self, proto_client):
        resp = proto_client.post("/mcp", json=self._rpc_call("get_stats"),
                                 headers={"X-API-Key": "wrong",
                                          "Accept": "application/json, text/event-stream"})
        assert resp.status_code == 401

    def test_get_stats_denied_never_leaks_stats(self, proto_client):
        """机器主体（无 rag.admin）调私有诊断工具 → 失败透传，
        响应绝不包含任何统计数字。"""
        resp = proto_client.post("/mcp", json=self._rpc_call("get_stats"),
                                 headers={"X-API-Key": "test-key-123",
                                          "Accept": "application/json, text/event-stream"})
        assert resp.status_code == 200
        text = resp.text
        assert "doc_count" not in text and "chunk_count" not in text
        assert "仅限管理员" in text or "isError" in text or "error" in text.lower()

    def test_search_runs_as_service_principal(self, proto_client, monkeypatch):
        """业务工具以显式受限机器主体执行：svc:mcp-protocol /
        service_workflow——不是任何真人身份，更不是 admin。"""
        seen = {}

        def fake_invoke(args):
            seen["user"] = get_tool_user_id()
            seen["roles"] = get_tool_roles()
            seen["tenant"] = get_tool_tenant_id()
            seen["dept"] = get_tool_department()
            return "proto-answer"

        monkeypatch.setattr(
            "mcp_servers.servers.rag.search_knowledge_tool",
            type("FakeTool", (), {
                # args_schema=None → list_tools 派生空参数表（本用例只验证执行主体）
                "args_schema": None, "name": "search_knowledge_tool",
                "description": "", "invoke": staticmethod(fake_invoke)}))
        resp = proto_client.post(
            "/mcp", json=self._rpc_call("search_knowledge",
                                        {"question": "q", "kb_id": _KB_A}),
            headers={"X-API-Key": "test-key-123",
                     "Accept": "application/json, text/event-stream"})
        assert resp.status_code == 200
        assert seen["user"] == "svc:mcp-protocol"
        assert seen["roles"] == ("service_workflow",)
        assert seen["dept"] == "system"

    def test_service_principal_has_no_rag_admin(self):
        from backend.rag.authz import is_admin_roles

        assert is_admin_roles(("service_workflow",)) is False


# ==================== 4. Java 内部网关 ====================

@pytest.mark.usefixtures("clean_ctx")
class TestJavaInternalGateway:
    @pytest.fixture()
    def java_client(self, monkeypatch):
        monkeypatch.setattr("backend.config.messaging.AI_INTERNAL_TOKEN", "itok")
        monkeypatch.setattr("backend.config.messaging.AI_TOOLS_ENABLED", True)
        app = FastAPI()
        app.include_router(internal_ai_router)
        return TestClient(app)

    def test_actor_delegation_with_explicit_blanks(self, java_client, monkeypatch):
        """actor 委托传递 user/dept；roles/tenant/permissions 显式置空。"""
        from mcp_servers.manager import manager as global_manager

        captured = {}

        def spy_route(tool_name, params):
            captured.update({
                "user": get_tool_user_id(),
                "dept": get_tool_department(),
                "roles": get_tool_roles(),
                "tenant": get_tool_tenant_id(),
                "permissions": get_tool_permissions(),
            })
            return {"ok": True, "result": {}}

        monkeypatch.setattr(global_manager, "route", spy_route)
        resp = java_client.post("/internal/ai/call",
                                json={"tool": "search_knowledge",
                                      "params": {"question": "q"},
                                      "actor_user_id": "java_user",
                                      "actor_department": "sales"},
                                headers={"X-Internal-Token": "itok"})
        assert resp.status_code == 200
        assert captured["user"] == "java_user"
        assert captured["dept"] == "sales"
        assert captured["roles"] == ()
        assert captured["tenant"] == ""
        assert captured["permissions"] is None

    def test_tool_whitelist_denied(self, java_client):
        resp = java_client.post("/internal/ai/call",
                                json={"tool": "get_stats", "params": {}},
                                headers={"X-Internal-Token": "itok"})
        assert resp.status_code == 403


# ==================== 5. RAG Tool 权限参数全程传递 ====================

@pytest.mark.usefixtures("clean_ctx")
class TestRagToolIdentityPlumbing:
    @pytest.fixture()
    def pipeline_spy(self, monkeypatch):
        captured = {}

        def fake_ask_result(question, **kwargs):
            captured["question"] = question
            captured.update(kwargs)
            return SimpleNamespace(answer="ANSWER_TEXT", answer_meta={})

        monkeypatch.setattr("backend.tools.rag._get_rag_pipeline",
                            lambda: SimpleNamespace(ask_result=fake_ask_result))
        return captured

    def test_ask_result_receives_roles_and_principal(self, pipeline_spy):
        from backend.tools.rag import search_knowledge_tool

        set_tool_user_id("adm1")
        set_tool_roles(("admin",))
        set_tool_department("")
        set_tool_tenant_id("t9")
        set_tool_permissions(("doc.x",))
        search_knowledge_tool.invoke({"question": "SECRET_QUESTION_TEXT",
                                      "kb_id": _KB_A})
        assert pipeline_spy["roles"] == ("admin",)
        assert pipeline_spy["subject_type"] == "employee"
        assert pipeline_spy["permissions"] == ("doc.x",)
        assert pipeline_spy["tenant_id"] == "t9"
        assert pipeline_spy["user_id"] == "adm1"

    def test_admin_vs_plain_user_kb_visibility(self):
        """admin 跨部门可见全部非 test 库；普通员工（无部门）只见 "all" 库。"""
        from backend.config.knowledge_base import KNOWLEDGE_BASES
        from backend.rag.authz import retrieval_authorized_kbs

        non_test = {k for k, v in KNOWLEDGE_BASES.items()
                    if v.get("audience") != "test"}
        admin_kbs = set(retrieval_authorized_kbs("employee", "", roles=("admin",)))
        plain_kbs = set(retrieval_authorized_kbs("employee", "", roles=()))
        assert admin_kbs == non_test
        assert plain_kbs < admin_kbs
        assert "policy_general" in plain_kbs

    def test_no_question_or_answer_text_in_logs(self, pipeline_spy, caplog):
        from backend.tools.rag import search_knowledge_tool

        set_tool_user_id("u1")
        set_tool_roles(("viewer",))
        with caplog.at_level("INFO"):
            search_knowledge_tool.invoke({"question": "SECRET_QUESTION_TEXT",
                                          "kb_id": _KB_A})
        assert "SECRET_QUESTION_TEXT" not in caplog.text
        assert "ANSWER_TEXT" not in caplog.text
        assert "len=" in caplog.text  # 长度指纹仍在（可观测性保留）


# ==================== 6. 失败语义分离（协议通道） ====================

class TestFailureSemantics:
    def test_protocol_channel_tool_exception_not_silent_success(self, monkeypatch):
        """标准 MCP 通道：工具 raise → FastMCP 异常透传（isError/错误响应），
        绝不包装成空成功结果。"""
        from mcp_servers.manager import MCPManager
        from mcp_servers.protocol_app import build_protocol_app
        from mcp_servers.servers.rag import RAGMCPServer

        monkeypatch.setattr("backend.config.API_KEY", "k-semantic")
        monkeypatch.setattr("backend.config.ALLOW_UNAUTHENTICATED", False)
        monkeypatch.setattr("backend.config.ENVIRONMENT", "dev")

        class DenyServer(RAGMCPServer):
            def list_tools(self):
                return [{"name": "get_stats", "description": "", "parameters": {}}]

            def call_tool(self, tool_name, params):
                raise PermissionError("denied-for-test")

        mgr = MCPManager()
        mgr.register(DenyServer())
        app = build_protocol_app(mgr)
        with TestClient(app) as client:
            resp = client.post(
                "/mcp",
                json={"jsonrpc": "2.0", "method": "tools/call",
                      "params": {"name": "get_stats", "arguments": {}}, "id": 1},
                headers={"X-API-Key": "k-semantic",
                         "Accept": "application/json, text/event-stream"})
            assert resp.status_code == 200
            body = resp.json()
            result = body.get("result") or {}
            assert result.get("isError") is True or "error" in body
            assert "denied-for-test" in resp.text

    def test_rest_manager_wraps_exception_as_ok_false(self):
        """REST 通道契约：call_tool 异常 → manager.route 捕获 → ok:false。"""
        from mcp_servers.manager import manager as global_manager

        original = global_manager._servers
        global_manager._servers = {"fake": _BoomServer()}
        try:
            result = global_manager.route("boom_tool", {})
        finally:
            global_manager._servers = original
        assert result["ok"] is False
        assert "denied-for-rest" in result["error"]


class _BoomServer:
    """call_tool 必抛异常的最小 server 替身。"""
    name = "fake"
    description = ""

    def list_tools(self):
        return [{"name": "boom_tool", "description": "", "parameters": {}}]

    def call_tool(self, tool_name, params):
        raise PermissionError("denied-for-rest")
