"""tests/test_zhihu_search_tool.py — 知乎/全网搜索 Tool（外部 MCP 数据源）离线单测

**全部不联网**：外部 MCP 调用被替换为固定响应（2026-10-02 实测形态），
验证本层的「归一化 / 失败分类 / 封套语义 / 配额保护」。真实链路由实机
验收覆盖（真实官方 MCP + 真实 key）。

重点覆盖三类「静默错误」：
  1. 「查不到」与「查不了」混同：code=0 且 items 空是确定答案（成功封套
     + 空列表）；code!=0（配额尽/无权限）与基础设施失败（McpClientError）
     必须走失败封套——按键名分类，不做文本匹配
  2. 配额保护：count 超上游上限必须本地收敛（超限请求会被上游 400 拒绝
     且可能烧配额）；结果整包进 prompt，summary 必须截断
  3. 鉴权与分源参数：headers（Bearer）、min_interval/ttl 必须真的传给
     客户端——知乎官方 MCP 靠它们鉴权与省配额
"""
from __future__ import annotations

import json

import pytest

from backend.config import mcp as MCP_CFG
from backend.infra import mcp_client
from backend.tools.search import global_search_tool, zhihu_search_tool
from backend.tools.search import zhihu as ZHIHU
from backend.tools.tool_registry import tool_registry


@pytest.fixture
def enabled(monkeypatch):
    """临时开启外部数据源（config 默认关；本机 .env 开了也必须能测关闭态）。"""
    monkeypatch.setattr(MCP_CFG, "ZHIHU_MCP_ENABLED", True)
    monkeypatch.setattr(MCP_CFG, "ZHIHU_MCP_BASE_URL", "https://test/mcp")
    monkeypatch.setattr(MCP_CFG, "ZHIHU_MCP_API_KEY", "test-key")
    monkeypatch.setattr(MCP_CFG, "ZHIHU_MCP_MIN_INTERVAL", 0.0)
    monkeypatch.setattr(MCP_CFG, "ZHIHU_MCP_CACHE_TTL", 0)
    yield


@pytest.fixture
def upstream(monkeypatch):
    """拦截外部 MCP 调用：记录参数与调用选项，返回预置载荷。"""
    calls: list[dict] = []
    responses: list = []

    def _fake(base_url, tool_name, arguments, **kw):
        calls.append({"base_url": base_url, "tool": tool_name,
                      "arguments": dict(arguments), "kw": kw})
        if not responses:
            raise AssertionError("测试未预置上游响应")
        item = responses.pop(0)
        if isinstance(item, Exception):
            raise item
        return item

    monkeypatch.setattr(ZHIHU, "call_tool", _fake)
    return calls, responses


# 2026-10-02 实测响应裁剪（知乎搜索「泉州 旅游 美食 推荐」）
_REAL_SUCCESS = {
    "code": 0, "message": "success",
    "data": {"has_more": False, "item_count": 2, "items": [
        {"title": "泉州美食推荐!好吃就行!",
         "url": "https://www.zhihu.com/question/52530242/answer/1923118769062",
         "content_type": "Answer", "content_id": "-2887734363006859835",
         "author_name": "某位作者", "author_signature": "ambar",
         "author_avatar": "https://pica.zhimg.com/50/v2-x_l.jpg",
         "author_badge_text": "前线开发话题下的优秀答主",
         "summary": "很长的摘要" * 200},
        {"title": "泉州五天游,有什么吃喝玩乐指南?",
         "url": "https://www.zhihu.com/question/3292212907/answer/18932979789",
         "content_type": "Answer"},
    ]},
}
# 全网搜索形态：字段更宽（vote_up_count/edit_time），content_type 可为空串
_REAL_GLOBAL = {
    "code": 0, "message": "success",
    "data": {"has_more": True, "item_count": 20, "items": [
        {"title": "串联世遗景点与山海风味 泉州推出十大美食精品旅游线路",
         "url": "http://www.hxnews.com/news/fj/mn/qz/202609/15/2270289.shtml",
         "content_type": "", "author_name": "海峡网",
         "vote_up_count": 12, "edit_time": "2026-09-15",
         "content_image": "https://img.example/x.jpg",
         "ranking_score": 0.87, "summary": "泉州推出美食旅游线路"},
    ]},
}
_REAL_EMPTY = {"code": 0, "message": "success",
               "data": {"has_more": False, "item_count": 0, "items": []}}
_REAL_QUOTA = {"code": 429, "message": "配额已用尽", "data": None}


def _ok(out: str) -> dict:
    data = json.loads(out)
    assert data["status"] == "success", data
    return data["data"]


def _fail(out: str) -> dict:
    data = json.loads(out)
    assert data["status"] == "failed", data
    return data


class TestRegistration:
    def test_registered(self):
        assert "zhihu_search_tool" in tool_registry.tool_names
        assert "global_search_tool" in tool_registry.tool_names


class TestDisabledAndValidation:
    def test_disabled_reports_explicitly(self, monkeypatch, upstream):
        monkeypatch.setattr(MCP_CFG, "ZHIHU_MCP_ENABLED", False)
        out = _fail(zhihu_search_tool.func(query="泉州 美食"))
        assert "未启用" in out["error"]
        assert "ZHIHU_MCP_API_KEY" in out["hint"]
        assert upstream[0] == []

    def test_missing_api_key_disables_source(self, monkeypatch, upstream):
        monkeypatch.setattr(MCP_CFG, "ZHIHU_MCP_API_KEY", "")
        out = _fail(zhihu_search_tool.func(query="泉州 美食"))
        assert "未启用" in out["error"]

    def test_empty_query_rejected_locally(self, enabled, upstream):
        out = _fail(zhihu_search_tool.func(query="  "))
        assert "query" in out["error"]
        assert upstream[0] == []  # 未发起外部调用（省配额）

    @pytest.mark.parametrize("bad_count", ["bad", None, 0, 21, True])
    def test_invalid_count_rejected(self, enabled, upstream, bad_count):
        out = _fail(global_search_tool.func(query="泉州", count=bad_count))
        assert "count" in out["error"]
        assert upstream[0] == []


class TestUpstreamContract:
    def test_zhihu_search_passes_bearer_and_source_options(self, enabled, upstream):
        calls, responses = upstream
        responses.append(dict(_REAL_SUCCESS))
        _ok(zhihu_search_tool.func(query="泉州 美食", count=5))
        call = calls[0]
        assert call["tool"] == "zhihu_search"
        assert call["arguments"] == {"query": "泉州 美食", "count": 5}
        assert call["kw"]["headers"] == {"Authorization": "Bearer test-key"}
        assert call["kw"]["timeout"] == MCP_CFG.ZHIHU_MCP_TIMEOUT
        assert call["kw"]["ttl"] == MCP_CFG.ZHIHU_MCP_CACHE_TTL
        assert call["kw"]["min_interval"] == MCP_CFG.ZHIHU_MCP_MIN_INTERVAL

    def test_count_clamped_to_upstream_limit(self, enabled, upstream):
        """超该工具上限的请求本地收敛（zhihu≤10），global 上限 20 由校验放行。"""
        calls, responses = upstream
        responses.append(dict(_REAL_GLOBAL))
        responses.append(dict(_REAL_GLOBAL))
        _ok(zhihu_search_tool.func(query="泉州", count=15))
        _ok(global_search_tool.func(query="泉州", count=20))
        assert calls[0]["arguments"]["count"] == 10
        assert calls[1]["arguments"]["count"] == 20

    def test_zhihu_result_strips_noise_and_truncates_summary(self, enabled, upstream):
        """avatar/badge 等展示噪音必须裁掉；summary 整包进 prompt 必须截断。"""
        _, responses = upstream
        responses.append(dict(_REAL_SUCCESS))
        data = _ok(zhihu_search_tool.func(query="泉州 美食", count=5))
        first, second = data["results"]
        assert first["title"] == "泉州美食推荐!好吃就行!"
        assert "author_avatar" not in first and "author_badge_text" not in first
        assert len(first["summary"]) == 500
        # 字段缺失不补造
        assert "summary" not in second and "author_name" not in second
        assert data["source"] == "zhihu_mcp"
        assert data["total_matched"] == 2

    def test_global_result_keeps_interaction_fields(self, enabled, upstream):
        calls, responses = upstream
        responses.append(dict(_REAL_GLOBAL))
        data = _ok(global_search_tool.func(query="泉州 美食线路", count=5))
        assert calls[0]["tool"] == "global_search"
        first = data["results"][0]
        assert first["vote_up_count"] == 12
        assert first["edit_time"] == "2026-09-15"
        assert "content_image" not in first and "ranking_score" not in first
        # content_type 空串不保留
        assert "content_type" not in first
        assert data["source"] == "zhihu_mcp_global"


class TestFailureSemantics:
    def test_empty_items_is_success_not_error(self, enabled, upstream):
        """「查不到」是确定答案：成功封套 + 空列表，绝不冒充失败。"""
        _, responses = upstream
        responses.append(dict(_REAL_EMPTY))
        data = _ok(zhihu_search_tool.func(query="不存在的 xyzzy 话题"))
        assert data["count"] == 0 and data["results"] == []

    def test_upstream_error_code_is_failed_with_message(self, enabled, upstream):
        _, responses = upstream
        responses.append(dict(_REAL_QUOTA))
        out = _fail(global_search_tool.func(query="泉州"))
        assert "配额已用尽" in out["error"]

    def test_infra_failure_is_failed_with_fallback_hint(self, enabled, upstream):
        _, responses = upstream
        responses.append(mcp_client.McpClientError("连接被拒"))
        out = _fail(zhihu_search_tool.func(query="泉州"))
        assert "不可用" in out["error"]
        assert "web_search_tool" in out.get("hint", "")

    @pytest.mark.parametrize("bad_payload", ["纯文本", {"no_code": 1}, None])
    def test_unknown_payload_shape_is_failed(self, enabled, upstream, bad_payload):
        _, responses = upstream
        responses.append(bad_payload)
        out = _fail(zhihu_search_tool.func(query="泉州"))
        assert "无法识别" in out["error"]

    def test_items_not_a_list_is_failed(self, enabled, upstream):
        _, responses = upstream
        responses.append({"code": 0, "message": "success", "data": {"items": "x"}})
        out = _fail(zhihu_search_tool.func(query="泉州"))
        assert "无法识别" in out["error"]


class TestClientExtension:
    """call_tool 新增 headers/min_interval 的向后兼容与分源节流。"""

    def test_headers_forwarded_to_transport(self, monkeypatch):
        captured: dict = {}

        async def _fake_call_async(base_url, tool_name, arguments, timeout_s,
                                   headers=None):
            captured["headers"] = headers
            return {"code": 0}

        monkeypatch.setattr(mcp_client, "_call_async", _fake_call_async)
        monkeypatch.setattr(MCP_CFG, "TRAIN_MCP_MIN_INTERVAL", 0.0)
        mcp_client.clear_cache()
        mcp_client.call_tool("http://x/mcp", "t", {}, ttl=0,
                             headers={"Authorization": "Bearer k"})
        assert captured["headers"] == {"Authorization": "Bearer k"}

    def test_train_defaults_unchanged(self, monkeypatch):
        """不传新参时行为与扩展前一致（12306 链路零影响）。"""
        captured: dict = {}

        async def _fake_call_async(base_url, tool_name, arguments, timeout_s,
                                   headers=None):
            captured["headers"] = headers
            captured["timeout_s"] = timeout_s
            return {"ok": 1}

        monkeypatch.setattr(mcp_client, "_call_async", _fake_call_async)
        monkeypatch.setattr(MCP_CFG, "TRAIN_MCP_MIN_INTERVAL", 0.0)
        mcp_client.clear_cache()
        mcp_client.call_tool("http://y/mcp", "query-tickets", {})
        assert captured["headers"] is None
        assert captured["timeout_s"] == MCP_CFG.TRAIN_MCP_TIMEOUT
