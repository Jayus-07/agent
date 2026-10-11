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
                from mcp_servers.manager import MCPToolError

                raise MCPToolError("denied-for-test", "permission_denied")

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
        """REST 通道契约：非受控异常（PermissionError 带任意文本——类型
        不能保证消息安全）→ manager.route 捕获 → ok:false + 通用文案 +
        internal 类型码，原文不出边界。"""
        from mcp_servers.manager import manager as global_manager

        original = global_manager._servers
        global_manager._servers = {"fake": _BoomServer()}
        try:
            result = global_manager.route("boom_tool", {})
        finally:
            global_manager._servers = original
        assert result["ok"] is False
        assert "denied-for-rest" not in result["error"]
        assert result["error_code"] == "internal:PermissionError"


class _BoomServer:
    """call_tool 必抛异常的最小 server 替身。"""
    name = "fake"
    description = ""

    def list_tools(self):
        return [{"name": "boom_tool", "description": "", "parameters": {}}]

    def call_tool(self, tool_name, params):
        raise PermissionError("denied-for-rest")


# ==================== 7. 异常敏感信息泄漏围堵 ====================

_SENSITIVE = "/srv/secret/path SECRET_KEY=sk-ant-123 用户敏感内容user_content"


class _LeakServer:
    """call_tool 抛含敏感数据的意外异常的最小 server 替身。"""
    name = "leak"
    description = ""

    def __init__(self, exc: Exception):
        self._exc = exc

    def list_tools(self):
        return [{"name": "leak_tool", "description": "", "parameters": {}}]

    def call_tool(self, tool_name, params):
        raise self._exc


class TestExceptionLeakContainment:
    """收口验收：Tool 意外异常含敏感路径/密钥/用户内容时，
    REST 响应、标准 MCP 响应不泄漏原文；受控日志保留诊断。"""

    def _register(self, exc):
        from mcp_servers.manager import manager as global_manager

        original = global_manager._servers
        global_manager._servers = {"leak": _LeakServer(exc)}
        return original

    def test_rest_unexpected_exception_contained(self, clean_ctx):
        from mcp_servers.manager import manager as global_manager

        original = self._register(
            OSError(f"open failed: {_SENSITIVE}"))
        try:
            result = global_manager.route("leak_tool", {})
        finally:
            global_manager._servers = original
        assert result["ok"] is False
        assert _SENSITIVE not in result["error"]
        assert "内部错误" in result["error"]
        assert result["error_code"] == "internal:OSError"
        assert "trace_id" in result

    def test_controlled_business_exception_passes_through(self, clean_ctx):
        """受控业务异常（MCPToolError，safe_message 构造时声明安全）保留
        文案与业务错误码，供调用方区分拒绝原因。"""
        from mcp_servers.manager import MCPToolError, manager as global_manager

        original = self._register(
            MCPToolError("get_stats 仅限管理员", "permission_denied"))
        try:
            result = global_manager.route("leak_tool", {})
        finally:
            global_manager._servers = original
        assert result["ok"] is False
        assert "仅限管理员" in result["error"]
        assert result["error_code"] == "permission_denied"

    def test_protocol_unexpected_exception_contained(self, monkeypatch):
        """标准 MCP 通道：意外异常经 _fn 边界转受控文案，client 响应
        （isError content）不含敏感原文。"""
        from fastapi.testclient import TestClient
        from mcp_servers.manager import MCPManager
        from mcp_servers.protocol_app import build_protocol_app

        monkeypatch.setattr("backend.config.API_KEY", "k-leak")
        monkeypatch.setattr("backend.config.ALLOW_UNAUTHENTICATED", False)
        monkeypatch.setattr("backend.config.ENVIRONMENT", "dev")
        mgr = MCPManager()
        mgr.register(_LeakServer(OSError(f"db conn failed: {_SENSITIVE}")))
        app = build_protocol_app(mgr)
        with TestClient(app) as client:
            resp = client.post(
                "/mcp",
                json={"jsonrpc": "2.0", "method": "tools/call",
                      "params": {"name": "leak_tool", "arguments": {}}, "id": 1},
                headers={"X-API-Key": "k-leak",
                         "Accept": "application/json, text/event-stream"})
            assert resp.status_code == 200
            assert _SENSITIVE not in resp.text
            assert "internal:OSError" in resp.text

    def test_internal_log_retains_diagnostics(self, clean_ctx, caplog):
        """日志保留安全诊断（异常类型+位置），消息原文不落日志——
        对齐 AGENTS.md 敏感信息保护，诊断能力以类型+位置+trace 承载。"""
        from mcp_servers.manager import manager as global_manager

        original = self._register(OSError(f"open failed: {_SENSITIVE}"))
        try:
            with caplog.at_level("ERROR"):
                global_manager.route("leak_tool", {})
        finally:
            global_manager._servers = original
        assert "OSError" in caplog.text
        assert _SENSITIVE not in caplog.text

    def test_runtime_error_with_secrets_contained_everywhere(
            self, clean_ctx, caplog):
        """复核负向：RuntimeError 携带模拟密钥/内部路径/用户内容——
        REST 响应与日志均不得出现原文（类型不作为安全凭据）。"""
        from mcp_servers.manager import manager as global_manager

        original = self._register(
            RuntimeError("conn refused: /srv/keys SECRET_KEY=sk-live-9 用户内容"))
        try:
            with caplog.at_level("ERROR"):
                result = global_manager.route("leak_tool", {})
        finally:
            global_manager._servers = original
        assert result["ok"] is False
        assert "SECRET_KEY" not in result["error"]
        assert "/srv/keys" not in result["error"]
        assert "用户内容" not in result["error"]
        assert result["error_code"] == "internal:RuntimeError"
        assert "SECRET_KEY" not in caplog.text
        assert "/srv/keys" not in caplog.text

    def test_permission_error_with_sensitive_content_contained(
            self, clean_ctx, caplog):
        """复核负向：PermissionError 携带用户内容——REST 响应与日志均
        围堵，只回稳定通用文案。"""
        from mcp_servers.manager import manager as global_manager

        original = self._register(
            PermissionError("denied: 用户内容 secret/doc.md"))
        try:
            with caplog.at_level("ERROR"):
                result = global_manager.route("leak_tool", {})
        finally:
            global_manager._servers = original
        assert result["ok"] is False
        assert "用户内容" not in result["error"]
        assert result["error"] == "工具执行失败（内部错误），请稍后重试或联系管理员"
        assert "用户内容" not in caplog.text

    def test_java_actor_injection_rejected(self):
        """actor 字段禁控制字符/超长（CRLF 日志注入防护）。"""
        from pydantic import ValidationError

        from backend.app.api.routes.internal_ai import AiCallRequest

        with pytest.raises(ValidationError):
            AiCallRequest(tool="search_knowledge",
                          actor_user_id="u1\r\n[X-Injected-Header] evil")
        with pytest.raises(ValidationError):
            AiCallRequest(tool="search_knowledge", actor_user_id="u" * 129)
        ok = AiCallRequest(tool="search_knowledge", actor_user_id="svc:java-u1",
                           actor_department="sales")
        assert ok.actor_user_id == "svc:java-u1"
