"""MCP P0 安全收口定向测试（task/mcp-p0-security-20261011）

覆盖:
  1. REST /api/mcp/* 三端点未认证 401（P0-1 入口鉴权，匿名不再放行任意 tool）
  2. 认证后 /mcp/call 正常路由执行，日志不含 params 原文（敏感日志收口）
  3. RAG MCP server：get_stats 匿名拒绝 / 非管理员拒绝 / 管理员脱敏统计
     （服务器绝对路径不再出边界）
  4. RAG MCP server：list_documents 授权范围下推 + file_path 不出边界
  5. RAG MCP server：search_knowledge 经 tool 本体执行（P1-3 检索旁路收口）
"""
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.app.api.routes.mcp import router as mcp_router
from backend.core.request_context import (
    set_tool_department,
    set_tool_roles,
    set_tool_tenant_id,
    set_tool_user_id,
)
from mcp_servers.manager import manager as global_manager
from mcp_servers.servers.rag import RAGMCPServer

_AUTH = {"X-User-Id": "sec_user", "X-User-Roles": "viewer"}


# ==================== 1/2：REST 路由入口 ====================

@pytest.fixture()
def api_client():
    app = FastAPI()
    app.include_router(mcp_router, prefix="/api")
    return TestClient(app)


def test_mcp_tools_requires_identity(api_client):
    assert api_client.get("/api/mcp/tools").status_code == 401


def test_mcp_servers_requires_identity(api_client):
    assert api_client.get("/api/mcp/servers").status_code == 401


def test_mcp_call_requires_identity(api_client):
    resp = api_client.post("/api/mcp/call",
                           json={"tool_name": "get_stats", "params": {}})
    assert resp.status_code == 401


def test_mcp_tools_authenticated_ok(api_client):
    resp = api_client.get("/api/mcp/tools", headers=_AUTH)
    assert resp.status_code == 200


def test_mcp_call_authenticated_routes(api_client, monkeypatch):
    seen = {}

    def fake_route(tool_name, params):
        seen["tool"] = tool_name
        return {"ok": True, "tool": tool_name, "result": {}}

    monkeypatch.setattr(global_manager, "route", fake_route)
    resp = api_client.post("/api/mcp/call",
                           json={"tool_name": "search_knowledge",
                                 "params": {"question": "SECRET_MARK"}},
                           headers=_AUTH)
    assert resp.status_code == 200
    assert seen["tool"] == "search_knowledge"


def test_mcp_call_log_omits_params_raw(api_client, monkeypatch, caplog):
    """params 原文（用户内容/潜在 PII）不落日志，只留键名+长度指纹。"""
    monkeypatch.setattr(global_manager, "route",
                        lambda t, p: {"ok": True, "result": {}})
    with caplog.at_level("INFO", logger="backend.shared.logger"):
        api_client.post("/api/mcp/call",
                        json={"tool_name": "search_knowledge",
                              "params": {"question": "SECRET_MARK"}},
                        headers=_AUTH)
    assert "SECRET_MARK" not in caplog.text
    assert "question<str:11>" in caplog.text


# ==================== 3/4/5：RAG MCP server 资源授权 ====================

_KB_A, _KB_B = "policy_general", "policy_hr"


class FakeRegistry:
    """PG registry 替身：行带 file_path（内部路径），记录 search 下推参数。"""

    last_kwargs: dict = {}

    def __init__(self, *args, **kwargs):
        pass

    @staticmethod
    def _rows():
        return [
            {"doc_id": "d1", "file_name": "a.md", "kb_id": _KB_A,
             "chunk_count": 3, "file_size": 10, "status": "active",
             "department": "", "doc_type": "", "created_at": "", "updated_at": "",
             "file_path": "/srv/secret/a.md"},
            {"doc_id": "d2", "file_name": "b.md", "kb_id": _KB_B,
             "chunk_count": 5, "file_size": 20, "status": "active",
             "department": "hr", "doc_type": "", "created_at": "", "updated_at": "",
             "file_path": "/srv/secret/b.md"},
        ]

    def list_active(self):
        return self._rows()

    def search(self, **kwargs):
        FakeRegistry.last_kwargs = kwargs
        kb_scope = kwargs.get("kb_scope") or []
        items = [d for d in self._rows() if d["kb_id"] in kb_scope]
        return {"items": items, "total": len(items)}

    def distinct_permission_scopes(self):
        return ["general"]


@pytest.fixture()
def fake_registry(monkeypatch):
    monkeypatch.setattr("backend.rag.indexing.doc_registry.DocumentRegistry",
                        FakeRegistry)
    FakeRegistry.last_kwargs = {}
    return FakeRegistry


def _bind(uid: str = "", roles: tuple = (), department: str = ""):
    """模拟 /api/mcp/call 的 ContextVar 身份绑定（空参即匿名）。"""
    set_tool_user_id(uid)
    set_tool_roles(roles)
    set_tool_department(department)
    set_tool_tenant_id("t1")


@pytest.fixture()
def unbind():
    yield
    _bind()


def _server() -> RAGMCPServer:
    return RAGMCPServer()


def test_get_stats_anonymous_denied(fake_registry, unbind):
    _bind()
    result = _server().call_tool("get_stats", {})
    assert result["reason"] == "unauthorized"
    assert "error" in result


def test_get_stats_non_admin_denied(fake_registry, unbind):
    _bind(uid="sec_user", roles=("viewer",))
    result = _server().call_tool("get_stats", {})
    assert result["reason"] == "forbidden"


def test_get_stats_admin_sanitized(fake_registry, unbind):
    _bind(uid="sec_admin", roles=("admin",))
    result = _server().call_tool("get_stats", {})
    assert "error" not in result
    assert result["doc_count"] == 2
    assert result["kb_count"] == 2
    assert result["chunk_count"] == 8
    assert result["vector_db"] == "pgvector"
    # 脱敏：服务器绝对路径/内部路径键不得出现在返回里
    assert "/srv" not in str(result)
    assert not any("path" in k for k in result)


def test_get_stats_admin_scope_excludes_other_kb(fake_registry, unbind):
    """admin 统计仍受 kb_scope 约束（audience=test 等库不进计数）。"""
    _bind(uid="sec_admin", roles=("admin",))
    rows = FakeRegistry._rows() + [
        {"doc_id": "d3", "file_name": "t.md", "kb_id": "kb_not_in_config",
         "chunk_count": 99, "file_size": 1, "status": "active",
         "department": "", "doc_type": "", "created_at": "", "updated_at": "",
         "file_path": "/srv/secret/t.md"},
    ]
    fake_registry.list_active = lambda self: rows
    result = _server().call_tool("get_stats", {})
    assert result["doc_count"] == 2  # 未登记 kb 不进 admin 统计


def test_list_documents_unauthorized_denied(fake_registry, unbind):
    _bind()
    result = _server().call_tool("list_documents", {})
    assert result["reason"] == "unauthorized"
    assert result["documents"] == []


def test_list_documents_scope_pushdown_and_path_stripped(fake_registry, unbind):
    _bind(uid="sec_user", roles=("viewer",))
    result = _server().call_tool("list_documents", {"keyword": "", "page": 1})
    # 授权下推：kb_scope 进了 registry.search（员工未带部门只见 "all" 库）
    assert fake_registry.last_kwargs.get("kb_scope")
    assert all(d["doc_id"] != "d2" for d in result["documents"])
    # 行白名单：file_path 等内部路径不出 MCP 边界
    for row in result["documents"]:
        assert "file_path" not in row
    assert result["documents"][0]["doc_id"] == "d1"


def test_search_knowledge_executes_via_tool_body(fake_registry, unbind, monkeypatch):
    """P1-3 收口：search_knowledge 必须经 tool 本体执行，不再直调 pipeline。"""
    calls = {}

    def fake_invoke(args):
        calls.update(args)
        return "tool-body-answer"

    monkeypatch.setattr("mcp_servers.servers.rag.search_knowledge_tool",
                        type("FakeTool", (), {"invoke": staticmethod(fake_invoke)}))
    _bind(uid="sec_user", roles=("viewer",))
    result = _server().call_tool(
        "search_knowledge", {"question": "退换货政策", "kb_id": _KB_A})
    assert result == {"answer": "tool-body-answer"}
    assert calls == {"question": "退换货政策", "kb_id": _KB_A}
