"""tests/test_train_mcp_tool.py — 12306 车票查询 Tool（外部 MCP 数据源）离线单测

**全部不联网**：外部 MCP 调用被替换为固定响应（2026-10-02 实测形态），
验证的是本层的「解析 / 失败分类 / 封套语义」。真实链路由实机验收覆盖
（真实容器 + 真实 12306 数据）。

重点覆盖三类「静默错误」——它们不抛异常，只会让结果悄悄错掉：
  1. 「查不到」与「查不了」混同：无直达（count/trains 键）是确定的业务
     答案（成功封套+空列表），参数错（errors/suggestions 键）与基础设施
     失败（McpClientError）必须走失败封套——上游三种业务失败结构可区分，
     分类按键名做，不做脆弱的文本匹配
  2. 协议层结构假设：上游可能回 structuredContent、JSON 文本或纯文本，
     客户端必须三种都吃得下
  3. 大结果集不截断会把 236 条车次整包塞进 prompt：limit 截断 + 按出发
     时刻排序必须在工具层生效
"""
from __future__ import annotations

import asyncio
import json

import pytest

from backend.config import mcp as MCP_CFG
from backend.infra import mcp_client
from backend.tools.tool_registry import tool_registry
from backend.tools.travel import train as TRAIN
from backend.tools.travel.train import travel_train_search_tool


@pytest.fixture
def enabled(monkeypatch):
    """临时开启外部数据源（config 默认关；本机 .env 开了也必须能测关闭态）。"""
    monkeypatch.setattr(MCP_CFG, "TRAIN_MCP_ENABLED", True)
    monkeypatch.setattr(MCP_CFG, "TRAIN_MCP_BASE_URL", "http://test/mcp")
    monkeypatch.setattr(MCP_CFG, "TRAIN_MCP_MIN_INTERVAL", 0.0)
    monkeypatch.setattr(MCP_CFG, "TRAIN_MCP_CACHE_TTL", 0)
    yield


@pytest.fixture
def upstream(monkeypatch):
    """拦截外部 MCP 调用：记录参数并返回预置载荷。"""
    calls: list[tuple[str, str, dict]] = []
    responses: list = []

    def _fake(base_url, tool_name, arguments, **kw):
        calls.append((base_url, tool_name, dict(arguments)))
        if not responses:
            raise AssertionError("测试未预置上游响应")
        item = responses.pop(0)
        if isinstance(item, Exception):
            raise item
        return item

    monkeypatch.setattr(TRAIN, "call_tool", _fake)
    return calls, responses


# 2026-10-02 实测响应裁剪（福州→厦门）
_REAL_SUCCESS = {
    "success": True,
    "from_station": "福州",
    "to_station": "厦门",
    "train_date": "2026-10-03",
    "count": 236,
    "trains": [
        {"train_no": "D6409", "from_station": "福州", "from_station_code": "FZS",
         "to_station": "厦门北", "to_station_code": "XKS",
         "start_time": "07:07", "arrive_time": "09:09", "duration": "02:02",
         "seats": {"first_class": "2", "second_class": "无", "no_seat": "有"}},
        {"train_no": "D3333", "from_station": "福州南", "from_station_code": "FYS",
         "to_station": "厦门北", "to_station_code": "XKS",
         "start_time": "06:43", "arrive_time": "08:20", "duration": "01:37",
         "seats": {"first_class": "有", "second_class": "1", "no_seat": "有"}},
    ],
}
_REAL_NO_DIRECT = {
    "success": False, "error": "未找到该线路的余票",
    "from_station": "福州", "to_station": "乌鲁木齐",
    "train_date": "2026-10-03", "count": 0, "trains": [],
}
_REAL_BAD_STATION = {
    "success": False, "error": "车站名称无效", "suggestions": [],
    "hint": "可尝试拼音、简拼、三字码或用 search_stations 工具辅助查询",
}
_REAL_BAD_DATE = {"success": False, "errors": ["日期格式错误，请使用 YYYY-MM-DD 格式"]}
_REAL_PRICE = {
    "train_code": "D6409",
    "prices": {"二等座": "80.0", "一等座": "128.0", "商务座": "240.0"},
}
_REAL_PRICE_DATA = {
    "success": True,
    "from_station": "九江",
    "to_station": "永修",
    "train_date": "2025-06-01",
    "count": 1,
    "data": [{
        "train_code": "G1556",
        "prices": {"二等座": "23.0", "一等座": "37.0"},
    }],
}


# =============================================
# 1. 客户端：payload 归一（纯函数）
# =============================================
class TestExtractPayload:
    class _Result:
        def __init__(self, structured=None, texts=(), is_error=False):
            self.structuredContent = structured
            self.content = texts
            self.isError = is_error

    class _Text:
        def __init__(self, text):
            self.text = text

    def test_structured_content_preferred(self):
        r = self._Result(structured={"a": 1}, texts=[self._Text('{"b": 2}')])
        assert mcp_client._extract_payload(r) == {"a": 1}

    def test_json_text_parsed(self):
        r = self._Result(texts=[self._Text('{"success": true, "count": 3}')])
        assert mcp_client._extract_payload(r) == {"success": True, "count": 3}

    def test_plain_text_passthrough(self):
        r = self._Result(texts=[self._Text("车站名称无效"), self._Text("第二段")])
        assert mcp_client._extract_payload(r) == "车站名称无效\n第二段"

    def test_empty_content_is_none(self):
        assert mcp_client._extract_payload(self._Result()) is None


# =============================================
# 2. 客户端：失败收口与缓存
# =============================================
class TestClientFailures:
    def test_underlying_exception_wrapped(self, monkeypatch):
        async def _boom(*a, **kw):
            raise ConnectionError("refused")

        monkeypatch.setattr(mcp_client, "_call_async", _boom)
        monkeypatch.setattr(MCP_CFG, "TRAIN_MCP_MIN_INTERVAL", 0.0)
        with pytest.raises(mcp_client.McpClientError, match="refused"):
            mcp_client.call_tool("http://x/mcp", "query-tickets", {}, ttl=0)

    def test_timeout_wrapped(self, monkeypatch):
        async def _slow(*a, **kw):
            raise asyncio.TimeoutError("t")

        monkeypatch.setattr(mcp_client, "_call_async", _slow)
        monkeypatch.setattr(MCP_CFG, "TRAIN_MCP_MIN_INTERVAL", 0.0)
        with pytest.raises(mcp_client.McpClientError, match="超时"):
            mcp_client.call_tool("http://x/mcp", "query-tickets", {}, ttl=0)

    def test_cache_reuses_same_arguments(self, monkeypatch, enabled):
        n = {"count": 0}

        async def _fake(*a, **kw):
            n["count"] += 1
            return {"ok": True}

        monkeypatch.setattr(mcp_client, "_call_async", _fake)
        mcp_client.clear_cache()
        args = {"from_station": "福州", "to_station": "厦门", "train_date": "2026-10-03"}
        mcp_client.call_tool("http://x/mcp", "query-tickets", args, ttl=120)
        mcp_client.call_tool("http://x/mcp", "query-tickets", dict(args), ttl=120)
        assert n["count"] == 1
        mcp_client.clear_cache()


# =============================================
# 3. Tool 层
# =============================================
def _ok(out: str) -> dict:
    data = json.loads(out)
    assert data["status"] == "success", data
    return data["data"]


def _fail(out: str) -> dict:
    data = json.loads(out)
    assert data["status"] == "failed", data
    return data


class TestTrainTool:
    def test_registered(self):
        assert "travel_train_search_tool" in tool_registry.tool_names

    def test_disabled_reports_explicitly(self, monkeypatch, upstream):
        monkeypatch.setattr(MCP_CFG, "TRAIN_MCP_ENABLED", False)
        out = _fail(travel_train_search_tool.func(
            from_station="福州", to_station="厦门", date="2026-10-03"))
        assert "未启用" in out["error"]
        assert "TRAIN_MCP_ENABLED" in out["hint"]

    def test_missing_stations_rejected(self, enabled, upstream):
        out = _fail(travel_train_search_tool.func(
            from_station=" ", to_station="厦门", date="2026-10-03"))
        assert "from_station" in out["error"]

    @pytest.mark.parametrize("bad_limit", ["bad", None, 0])
    def test_invalid_limit_is_a_failed_tool_result(self, enabled, upstream, bad_limit):
        out = _fail(travel_train_search_tool.func(
            from_station="福州", to_station="厦门", date="2026-10-03",
            limit=bad_limit))
        assert "limit" in out["error"]
        assert upstream[0] == []

    def test_bad_date_rejected_locally(self, enabled, upstream):
        """日期在本地先校验，不打上游。"""
        out = _fail(travel_train_search_tool.func(
            from_station="福州", to_station="厦门", date="明天"))
        assert "YYYY-MM-DD" in out["error"]
        assert upstream[0] == []  # 未发起外部调用

    def test_success_sorted_localized_and_capped(self, enabled, upstream):
        calls, responses = upstream
        responses.append(dict(_REAL_SUCCESS))
        data = _ok(travel_train_search_tool.func(
            from_station="福州", to_station="厦门", date="2026-10-03"))
        # 参数按上游 schema 透传
        assert calls[0][1] == "query-tickets"
        assert calls[0][2] == {"from_station": "福州", "to_station": "厦门",
                               "train_date": "2026-10-03"}
        # 按出发时刻升序
        assert [t["train_no"] for t in data["trains"]] == ["D3333", "D6409"]
        # 席别键中文化，数值原样
        assert data["trains"][0]["seats"] == {"一等座": "有", "二等座": "1",
                                              "无座": "有"}
        # 溯源口径
        assert data["source"] == "12306"
        assert data["total_matched"] == 236
        from datetime import datetime
        assert datetime.fromisoformat(data["trains"][0]["updated_at"]).tzinfo is not None

    def test_price_tool_calls_query_ticket_price(self, enabled, upstream):
        """票价查询必须调用独立 MCP 工具，并保留车次与来源。"""
        calls, responses = upstream
        responses.append(dict(_REAL_PRICE))
        price_tool = getattr(TRAIN, "travel_train_price_tool", None)
        assert price_tool is not None
        data = _ok(price_tool.func(
            from_station="福州", to_station="厦门北",
            train_date="2026-10-03", train_code="D6409",
        ))
        assert calls[0][1] == "query-ticket-price"
        assert calls[0][2] == {
            "from_station": "福州", "to_station": "厦门北",
            "train_date": "2026-10-03", "train_code": "D6409",
        }
        assert data["train_code"] == "D6409"
        assert data["prices"]["二等座"] == "80.0"
        assert data["source"] == "12306"

    def test_price_tool_accepts_documented_data_list(self, enabled, upstream):
        """兼容 12306 MCP 文档中的 data[] 返回形态。"""
        calls, responses = upstream
        responses.append(dict(_REAL_PRICE_DATA))
        price_tool = getattr(TRAIN, "travel_train_price_tool")
        data = _ok(price_tool.func(
            from_station="九江", to_station="永修",
            train_date="2025-06-01", train_code="G1556",
        ))
        assert calls[0][1] == "query-ticket-price"
        assert data["train_code"] == "G1556"
        assert data["prices"]["二等座"] == "23.0"

    def test_no_direct_is_success_with_note(self, enabled, upstream):
        """「查不到」：无直达是确定答案，成功封套 + 建议中转。"""
        _, responses = upstream
        responses.append(dict(_REAL_NO_DIRECT))
        data = _ok(travel_train_search_tool.func(
            from_station="福州", to_station="乌鲁木齐", date="2026-10-03"))
        assert data["count"] == 0 and data["trains"] == []
        assert "未找到" in data["note"]
        assert "中转" in data["suggestion"]

    def test_bad_station_is_failed_with_clues(self, enabled, upstream):
        """「查不了」（参数错）：失败封套 + 上游纠错线索。"""
        _, responses = upstream
        responses.append(dict(_REAL_BAD_STATION))
        out = _fail(travel_train_search_tool.func(
            from_station="不存在的站", to_station="厦门", date="2026-10-03"))
        assert "车站名称无效" in out["error"]
        assert out["suggestions"] == []

    def test_upstream_date_error_is_failed(self, enabled, upstream):
        _, responses = upstream
        responses.append(dict(_REAL_BAD_DATE))
        out = _fail(travel_train_search_tool.func(
            from_station="福州", to_station="厦门", date="2026-10-03"))
        assert "日期格式错误" in out["error"]

    def test_infra_failure_is_failed_with_fallback_hint(self, enabled, upstream):
        """「查不了」（基础设施）：明确说上游不可用，给降级提示。"""
        _, responses = upstream
        responses.append(mcp_client.McpClientError("连接被拒"))
        out = _fail(travel_train_search_tool.func(
            from_station="福州", to_station="厦门", date="2026-10-03"))
        assert "上游服务不可用" in out["error"]

    def test_unknown_payload_shape_is_failed(self, enabled, upstream):
        _, responses = upstream
        responses.append("不是字典的返回")
        out = _fail(travel_train_search_tool.func(
            from_station="福州", to_station="厦门", date="2026-10-03"))
        assert "无法识别" in out["error"]
