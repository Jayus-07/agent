"""tests/tools/test_services.py — Month 1 微服务框架冒烟测试

覆盖：
1. ToolServer 三种调用方式（Standard / Batch / Streaming）
2. WebSearch Service（web_crawl 走 Mock，避免真实网络）
3. DataCollection Service（本地数据集，离线可跑）
"""
import json

import pytest

pytest.importorskip("fastapi")

from fastapi.testclient import TestClient


@pytest.fixture(scope="module")
def web_search_client():
    from backend.services.web_search_server import app
    with TestClient(app) as client:
        yield client


@pytest.fixture(scope="module")
def data_collection_client():
    from backend.services.data_collection_server import app
    with TestClient(app) as client:
        yield client


class TestToolServerFramework:
    """通用框架行为（以 DataCollection Service 为载体验证）"""

    def test_health_endpoint(self, data_collection_client):
        resp = data_collection_client.get("/health")
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "ok"
        assert "data_collection_tool" in body["tools"]

    def test_tools_discovery(self, data_collection_client):
        resp = data_collection_client.get("/tools")
        assert resp.status_code == 200
        tools = resp.json()
        names = [t["name"] for t in tools]
        assert "data_collection_tool" in names

    def test_unknown_tool_returns_404(self, data_collection_client):
        resp = data_collection_client.post("/call/nonexistent_tool", json={})
        assert resp.status_code == 404

    def test_batch_with_unknown_tool_yields_failure_item(self, data_collection_client):
        resp = data_collection_client.post("/batch/calls", json={
            "calls": [{"tool": "nonexistent_tool", "params": {}}],
        })
        # 单项失败不中断批量，但会映射为 404 异常 → FastAPI 返回 404
        assert resp.status_code == 404


class TestDataCollectionService:
    """DataCollection Service 端到端（本地数据集，离线可跑）"""

    def test_standard_call_success(self, data_collection_client):
        resp = data_collection_client.post("/call/data_collection_tool", json={
            "source": "products",
            "enable_write": False,
            "enable_analysis": True,
        })
        assert resp.status_code == 200
        body = resp.json()
        assert body["success"] is True
        assert body["tool"] == "data_collection_tool"
        assert "数据采集报告" in body["data"]
        assert body["latency_ms"] > 0

    def test_standard_call_missing_source(self, data_collection_client):
        resp = data_collection_client.post("/call/data_collection_tool", json={
            "source": "no_such_dataset_file_xyz",
            "enable_write": False,
        })
        assert resp.status_code == 200
        body = resp.json()
        # Tool 内部返回友好错误报告（success=True，data 含 failed）
        assert "**failed**" in body["data"]

    def test_datasets_listing(self, data_collection_client):
        resp = data_collection_client.get("/datasets")
        assert resp.status_code == 200
        body = resp.json()
        assert body["count"] >= 1
        assert "products.json" in body["files"]

    def test_stream_call_sse(self, data_collection_client):
        with data_collection_client.stream(
            "GET", "/stream/data_collection_tool",
            params={"source": "products", "enable_write": "false"},
        ) as resp:
            assert resp.status_code == 200
            assert "text/event-stream" in resp.headers["content-type"]
            events = []
            for line in resp.iter_lines():
                if line.startswith("data: "):
                    events.append(line[len("data: "):])
        # start → complete/error → [DONE]
        assert events[-1] == "[DONE]"
        first = json.loads(events[0])
        assert first["event"] == "start"
        # enable_write 查询参数为字符串，应由 args_schema 强转
        assert any(json.loads(e).get("event") in ("complete", "error")
                   for e in events[1:-1])


class TestWebSearchService:
    """WebSearch Service（爬取走 Mock，避免真实网络）"""

    def test_health_and_tools(self, web_search_client):
        resp = web_search_client.get("/health")
        assert resp.status_code == 200
        assert set(resp.json()["tools"]) == {"web_search_tool", "web_crawl_tool"}

        resp = web_search_client.get("/tools")
        assert resp.status_code == 200
        assert len(resp.json()) == 2

    def test_crawl_call_mocked(self, web_search_client):
        from unittest.mock import MagicMock
        from backend.services.web_search_server import server

        # StructuredTool 是 Pydantic 模型，禁止 setattr，改为临时替换注册表条目
        mock_tool = MagicMock()
        mock_tool.name = "web_crawl_tool"
        mock_tool.args_schema = None
        mock_tool.invoke.return_value = "# 页面正文"
        original = server._tools["web_crawl_tool"]
        server._tools["web_crawl_tool"] = mock_tool
        try:
            resp = server._invoke_sync("web_crawl_tool", {"url": "https://example.com"})
        finally:
            server._tools["web_crawl_tool"] = original

        assert resp.success is True
        assert resp.data == "# 页面正文"
        assert resp.latency_ms >= 0

    def test_batch_calls_mocked(self, web_search_client):
        from unittest.mock import patch
        from backend.services.web_search_server import server

        # 批量调用：两个搜索任务并行（临时替换注册表条目为 Mock）
        from unittest.mock import MagicMock

        mock_tool = MagicMock()
        mock_tool.name = "web_search_tool"
        mock_tool.args_schema = None
        mock_tool.invoke.return_value = "结果摘要"
        original = server._tools["web_search_tool"]
        server._tools["web_search_tool"] = mock_tool
        try:
            resp = web_search_client.post("/batch/calls", json={
                "calls": [
                    {"tool": "web_search_tool", "params": {"query": "python"}},
                    {"tool": "web_search_tool", "params": {"query": "fastapi"}},
                ],
                "max_concurrency": 2,
            })
        finally:
            server._tools["web_search_tool"] = original
        assert resp.status_code == 200
        body = resp.json()
        assert len(body["results"]) == 2
        assert all(r["success"] for r in body["results"])
        assert body["total_latency_ms"] >= 0
