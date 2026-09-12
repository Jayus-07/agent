"""标准 MCP 协议端点单测（mcp_servers/protocol_app.py）

覆盖:
  1. 协议握手：initialize / notifications/initialized（stateless + JSON 响应）
  2. tools/list：工具清单与 manager 注册一致，inputSchema 参数展开正确
  3. tools/call：参数透传到 MCPServer.call_tool，返回内容可解析
  4. 鉴权：fail-closed 语义（无 key 503 / 错 key 401 / 对 key 200）
  5. 工具名冲突检测
"""
import json

import pytest
from fastapi.testclient import TestClient

from mcp_servers.manager import MCPManager, MCPServer
from mcp_servers.protocol_app import build_protocol_app, _make_tool_fn

_JSON_HEADERS = {"Accept": "application/json, text/event-stream"}


# ==================== 测试用假 MCPServer ====================

class FakeRAGServer(MCPServer):
    name = "rag"
    description = "假 RAG server"

    def list_tools(self) -> list:
        return [
            {
                "name": "search_knowledge",
                "description": "知识库检索",
                "parameters": {
                    "question": {"type": "string", "required": True, "description": "问题"},
                    "kb_id": {"type": "string", "required": False, "default": "default"},
                },
            },
        ]

    def call_tool(self, tool_name: str, params: dict):
        assert tool_name == "search_knowledge"
        return {"answer": f"mock:{params['question']}", "kb_id": params.get("kb_id")}


@pytest.fixture()
def client(monkeypatch):
    """构建注入 FakeRAGServer 的协议应用（鉴权豁免，专注协议行为）。"""
    monkeypatch.setattr("backend.config.API_KEY", "")
    monkeypatch.setattr("backend.config.ALLOW_UNAUTHENTICATED", True)
    mgr = MCPManager()
    mgr.register(FakeRAGServer())
    app = build_protocol_app(mgr)
    with TestClient(app) as c:
        yield c


def _rpc(method: str, params: dict | None = None, rpc_id: int | None = 1) -> dict:
    body = {"jsonrpc": "2.0", "method": method}
    if params is not None:
        body["params"] = params
    if rpc_id is not None:
        body["id"] = rpc_id
    return body


# ==================== 1-3. 协议行为 ====================

class TestProtocol:
    def test_initialize_handshake(self, client):
        resp = client.post(
            "/mcp", json=_rpc(
                "initialize",
                {"protocolVersion": "2025-06-18", "capabilities": {},
                 "clientInfo": {"name": "test", "version": "0"}},
            ), headers=_JSON_HEADERS,
        )
        assert resp.status_code == 200
        result = resp.json()["result"]
        assert result["serverInfo"]["name"] == "agent-platform"

    def test_tools_list_parity_and_schema(self, client):
        client.post("/mcp", json=_rpc("initialize", {
            "protocolVersion": "2025-06-18", "capabilities": {},
            "clientInfo": {"name": "t", "version": "0"},
        }), headers=_JSON_HEADERS)
        resp = client.post("/mcp", json=_rpc("tools/list"), headers=_JSON_HEADERS)
        assert resp.status_code == 200
        tools = resp.json()["result"]["tools"]
        assert [t["name"] for t in tools] == ["search_knowledge"]

        schema = tools[0]["inputSchema"]["properties"]
        assert "question" in schema and "kb_id" in schema

    def test_tools_call_executes_server(self, client):
        resp = client.post("/mcp", json=_rpc("tools/call", {
            "name": "search_knowledge",
            "arguments": {"question": "退货流程", "kb_id": "cs_faq"},
        }), headers=_JSON_HEADERS)
        assert resp.status_code == 200
        payload = resp.json()["result"]
        assert payload.get("isError") in (False, None)
        text = payload["content"][0]["text"]
        data = json.loads(text)
        assert data["answer"] == "mock:退货流程"
        assert data["kb_id"] == "cs_faq"


# ==================== 4. 鉴权（fail-closed） ====================

class TestAuth:
    def test_no_config_rejected(self, monkeypatch):
        monkeypatch.setattr("backend.config.API_KEY", "")
        monkeypatch.setattr("backend.config.ALLOW_UNAUTHENTICATED", False)
        mgr = MCPManager()
        mgr.register(FakeRAGServer())
        app = build_protocol_app(mgr)
        with TestClient(app) as c:
            resp = c.post("/mcp", json=_rpc("tools/list"), headers=_JSON_HEADERS)
            assert resp.status_code == 503
            assert resp.json()["error"] == "AuthNotConfigured"

    def test_wrong_key_401(self, monkeypatch):
        monkeypatch.setattr("backend.config.API_KEY", "secret-key")
        mgr = MCPManager()
        mgr.register(FakeRAGServer())
        app = build_protocol_app(mgr)
        with TestClient(app) as c:
            resp = c.post("/mcp", json=_rpc("tools/list"),
                          headers={**_JSON_HEADERS, "X-API-Key": "wrong"})
            assert resp.status_code == 401
            resp = c.post("/mcp", json=_rpc("tools/list"),
                          headers={**_JSON_HEADERS, "X-API-Key": "secret-key"})
            assert resp.status_code == 200

    def test_health_skips_auth(self, monkeypatch):
        monkeypatch.setattr("backend.config.API_KEY", "secret-key")
        mgr = MCPManager()
        mgr.register(FakeRAGServer())
        app = build_protocol_app(mgr)
        with TestClient(app) as c:
            assert c.get("/health").status_code == 200


# ==================== 5. 冲突检测 ====================

class TestConflicts:
    def test_duplicate_tool_name_raises(self):
        class AnotherServerWithSameTool(MCPServer):
            """server 名不同（不会被 manager 覆盖）但工具名与 rag 冲突。"""
            name = "other"
            description = "同名工具的另一 server"

            def list_tools(self):
                return [{"name": "search_knowledge", "description": "x", "parameters": {}}]

            def call_tool(self, tool_name, params):
                return {}

        mgr = MCPManager()
        mgr.register(FakeRAGServer())
        mgr.register(AnotherServerWithSameTool())
        with pytest.raises(ValueError, match="工具名冲突"):
            build_protocol_app(mgr)


# ==================== 签名构造 ====================

class TestMakeToolFn:
    def test_signature_from_parameters(self):
        fn = _make_tool_fn(FakeRAGServer(), "search_knowledge", FakeRAGServer.list_tools(self)[0]["parameters"])
        import inspect

        sig = inspect.signature(fn)
        assert list(sig.parameters) == ["question", "kb_id"]
        # 必填参数无默认值，可选参数有默认值
        assert sig.parameters["question"].default is inspect.Parameter.empty
        assert sig.parameters["kb_id"].default == "default"

    def test_kwargs_forwarded_to_call_tool(self):
        server = FakeRAGServer()
        fn = _make_tool_fn(server, "search_knowledge", FakeRAGServer.list_tools(server)[0]["parameters"])
        assert fn(question="hi")["answer"] == "mock:hi"
