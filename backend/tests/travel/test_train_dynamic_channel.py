# -*- coding: utf-8 -*-
"""12306 动态 capability 通道契约测试（T1，travel-proto-production）。

覆盖（TASK §4b 验收口径）：
  - 三态选择：动态在场优先 → 动态从未配置 + TRAIN_MCP_ENABLED=true 走静态
    → 两者皆无如实「未启用」；
  - fail-closed：已登记但未生效（撤销/未审批/漂移）→ 如实拒绝，**不回退
    静态直连绕过治理**；动态执行失败同样不回退；
  - 契约回放：上游响应按 train.py 2026-10-02 实测四种形态回放（成功/真无
    直达/坏站名/坏日期），动态与静态共用同一解析事实源；
  - 真 PG 门控集成（pg marker）：mock FastMCP 12306 server + admin API
    登记/审核/授权全链，live_search_service 端到端走动态通道。
"""
from __future__ import annotations

import threading
import time
from unittest.mock import patch

import pytest

from backend.travel.services import live_search_service
from backend.travel.services.live_search_service import LiveSearchError

# 与动态通道模块的判别基准一致（测试内 monkeypatch 为同值，保证确定性）
_BASE_URL = "http://mcp-12306:8000/mcp"
_SEARCH_CAP = "mcp.t12306__query_tickets"
_PRICE_CAP = "mcp.t12306__query_ticket_price"

# ── 上游契约回放样本（2026-10-02 实测形态，见 train.py 模块头）──

_SUCCESS = {
    "success": True,
    "count": 2,
    "from_station": "福州",
    "to_station": "厦门北",
    "train_date": "2026-11-16",
    "trains": [
        {"train_code": "G1655", "start_time": "08:05", "end_time": "09:40",
         "duration": "01:35", "seats": {"business": "3"}},
        {"train_code": "D3333", "start_time": "06:43", "end_time": "08:20",
         "duration": "01:37", "seats": {"second_class": "有"}},
    ],
}
_NO_DIRECT = {
    "success": False, "error": "未找到该线路的余票", "count": 0, "trains": [],
}
_BAD_STATION = {
    "success": False, "error": "车站名称无效",
    "suggestions": ["福州南", "福州东"], "hint": "请核对站名后重试",
}
_BAD_DATE = {"success": False, "errors": ["日期格式错误，应为 YYYY-MM-DD"]}
_PRICE_DATA_SHAPE = {
    "data": [{"train_code": "D3333",
              "prices": {"second_class": "58.5", "first_class": "93.5"}}],
}
_PRICE_FLAT = {"train_code": "D3333", "prices": {"second_class": "58.5"}}


def _runtime(capability: str, upstream_tool: str):
    from backend.infra.mcp_registry import specs as spec_lib
    from backend.infra.mcp_registry import store as mcp_store

    return mcp_store.McpToolRuntime(
        capability=capability, tool_id=1, connection_id="conn_train",
        upstream_tool_name=upstream_tool, tenant_id="default",
        spec=spec_lib.derive_tool_spec(
            capability, "12306 查询", {"type": "object"},
            {"operation": "read", "risk_level": "R1",
             "confirmation_policy": "none"}),
        network_scope="private")


def _snapshot(*, tools: dict | None = None, conn_base_url: str | None = None):
    """注入用快照：tools 传 {capability: runtime}；conn_base_url 决定是否
    有「同址连接」在场（fail-closed 判别）。"""
    from backend.infra.mcp_registry import store as mcp_store

    connections = {}
    if conn_base_url:
        connections["conn_train"] = mcp_store.McpConnectionRuntime(
            connection_id="conn_train", base_url=conn_base_url,
            auth_type="none", network_scope="private")
    return mcp_store.McpRegistrySnapshot(
        loaded=True, tools=dict(tools or {}), connections=connections)


@pytest.fixture(autouse=True)
def _train_env(monkeypatch):
    """隔离共享栈快照与 .env 开关（每个用例自选状态注入）。"""
    from backend.config import mcp as mcp_config
    from backend.infra.mcp_registry import store as mcp_store

    mcp_store.reset_for_tests()
    monkeypatch.setattr(mcp_config, "TRAIN_MCP_BASE_URL", _BASE_URL)
    monkeypatch.setattr(mcp_config, "TRAIN_MCP_ENABLED", False)
    yield
    mcp_store.reset_for_tests()


# =============================================
# 三态选择（service 层端到端，离线桩）
# =============================================


def test_dynamic_present_wins_over_static(monkeypatch):
    """动态在场 → 走动态（TRAIN_MCP_ENABLED 即使为 true 也不走静态）。"""
    from backend.config import mcp as mcp_config
    from backend.infra.mcp_registry import store as mcp_store

    mcp_store.inject_snapshot_for_tests(_snapshot(
        tools={_SEARCH_CAP: _runtime(_SEARCH_CAP, "query-tickets"),
               _PRICE_CAP: _runtime(_PRICE_CAP, "query-ticket-price")},
        conn_base_url=_BASE_URL))
    monkeypatch.setattr(mcp_config, "TRAIN_MCP_ENABLED", True)

    def _no_static(*args, **kwargs):  # 静态通道被命中即失败
        raise AssertionError("动态在场时不得触达静态直连通道")

    with patch("backend.tools.travel.train.call_tool", _no_static), \
            patch("backend.tools.travel.train_dynamic._invoke_dynamic",
                  lambda capability, params: _SUCCESS):
        data = live_search_service.search_trains(
            from_station="福州", to_station="厦门北", travel_date="2026-11-16")
    assert data["channel"] == "mcp_dynamic"
    assert data["count"] == 2
    # 排序 + 席别中文化与静态通道一致（共享解析事实源）
    assert [t["train_code"] for t in data["trains"]] == ["D3333", "G1655"]
    assert data["trains"][0]["seats"] == {"二等座": "有"}


def test_absent_with_static_enabled_falls_back(monkeypatch):
    """动态从未配置（无同址连接）+ TRAIN_MCP_ENABLED=true → 静态直连。"""
    from backend.config import mcp as mcp_config
    from backend.infra.mcp_registry import store as mcp_store

    mcp_store.inject_snapshot_for_tests(_snapshot())  # 空快照 = 从未配置
    monkeypatch.setattr(mcp_config, "TRAIN_MCP_ENABLED", True)

    def _no_dynamic(capability, params):
        raise AssertionError("未配置动态时不得触达动态执行核心")

    with patch("backend.tools.travel.train_dynamic._invoke_dynamic",
               _no_dynamic), \
            patch("backend.tools.travel.train.call_tool",
                  lambda *a, **k: _SUCCESS):
        data = live_search_service.search_trains(
            from_station="福州", to_station="厦门北", travel_date="2026-11-16")
    assert data["channel"] == "static_direct"


def test_absent_and_switch_off_is_honest_disabled():
    """动态从未配置 + TRAIN_MCP_ENABLED=false → 如实「未启用」。"""
    with pytest.raises(LiveSearchError) as exc_info:
        live_search_service.search_trains(
            from_station="福州", to_station="厦门北", travel_date="2026-11-16")
    assert "未启用" in str(exc_info.value)
    assert exc_info.value.category == "disabled"


# =============================================
# fail-closed（撤销≠回退）
# =============================================


def test_configured_inactive_fail_closed(monkeypatch):
    """连接已登记（同址在场）但工具 runtime 不在场 + 静态开关开着 →
    如实拒绝，绝不回退静态直连绕过治理。"""
    from backend.config import mcp as mcp_config
    from backend.infra.mcp_registry import store as mcp_store

    # 只有连接、没有工具 runtime（模拟撤销授权/未审批/内容漂移）
    mcp_store.inject_snapshot_for_tests(_snapshot(conn_base_url=_BASE_URL))
    monkeypatch.setattr(mcp_config, "TRAIN_MCP_ENABLED", True)

    def _no_static(*args, **kwargs):
        raise AssertionError("已登记未生效时不得回退静态直连")

    with patch("backend.tools.travel.train.call_tool", _no_static):
        with pytest.raises(LiveSearchError) as exc_info:
            live_search_service.search_trains(
                from_station="福州", to_station="厦门北",
                travel_date="2026-11-16")
    assert "未生效" in str(exc_info.value)
    assert "不回退静态直连" in str(exc_info.value)
    assert exc_info.value.category == "disabled"


def test_dynamic_execution_failure_no_static_fallback(monkeypatch):
    """动态在场但执行失败（上游/治理不可用）→ 如实报错，不回退静态。"""
    from backend.config import mcp as mcp_config
    from backend.infra.mcp_client import McpClientError
    from backend.infra.mcp_registry import store as mcp_store

    mcp_store.inject_snapshot_for_tests(_snapshot(
        tools={_SEARCH_CAP: _runtime(_SEARCH_CAP, "query-tickets")},
        conn_base_url=_BASE_URL))
    monkeypatch.setattr(mcp_config, "TRAIN_MCP_ENABLED", True)

    def _boom(capability, params):
        raise McpClientError("MCP 工具未生效或已停用: x")

    def _no_static(*args, **kwargs):
        raise AssertionError("动态执行失败时不得回退静态直连")

    with patch("backend.tools.travel.train.call_tool", _no_static), \
            patch("backend.tools.travel.train_dynamic._invoke_dynamic", _boom):
        with pytest.raises(LiveSearchError) as exc_info:
            live_search_service.search_trains(
                from_station="福州", to_station="厦门北",
                travel_date="2026-11-16")
    assert "动态通道不可用" in str(exc_info.value)


def test_price_channel_resolved_independently(monkeypatch):
    """车次/票价 capability 独立判定：search 走动态时 price 未生效 →
    票价如实拒绝，不影响车次主数据。"""
    from backend.infra.mcp_registry import store as mcp_store

    mcp_store.inject_snapshot_for_tests(_snapshot(
        tools={_SEARCH_CAP: _runtime(_SEARCH_CAP, "query-tickets")},
        conn_base_url=_BASE_URL))
    with patch("backend.tools.travel.train_dynamic._invoke_dynamic",
               lambda capability, params: _SUCCESS):
        data = live_search_service.search_trains(
            from_station="福州", to_station="厦门北", travel_date="2026-11-16")
    assert data["channel"] == "mcp_dynamic"
    with pytest.raises(LiveSearchError) as exc_info:
        live_search_service.search_train_price(
            from_station="福州", to_station="厦门北",
            travel_date="2026-11-16", train_code="D3333")
    assert "未生效" in str(exc_info.value)


# =============================================
# 契约回放（动态通道 × train.py 实测四种形态）
# =============================================


def test_replay_no_direct_is_business_answer_not_error(monkeypatch):
    """真无直达 = 成功封套 + 空列表 + 建议（≠错误）。"""
    from backend.infra.mcp_registry import store as mcp_store

    mcp_store.inject_snapshot_for_tests(_snapshot(
        tools={_SEARCH_CAP: _runtime(_SEARCH_CAP, "query-tickets")},
        conn_base_url=_BASE_URL))
    with patch("backend.tools.travel.train_dynamic._invoke_dynamic",
               lambda capability, params: _NO_DIRECT):
        data = live_search_service.search_trains(
            from_station="福州", to_station="拉萨", travel_date="2026-11-16")
    assert data["count"] == 0 and data["trains"] == []
    assert data["note"] == "未找到该线路的余票"
    assert "中转" in data["suggestion"]
    assert data["channel"] == "mcp_dynamic"


def test_replay_bad_station_carries_upstream_clues(monkeypatch):
    """坏站名 = 失败封套 + 上游纠错线索（suggestions/hint 不丢）。"""
    from backend.infra.mcp_registry import store as mcp_store

    mcp_store.inject_snapshot_for_tests(_snapshot(
        tools={_SEARCH_CAP: _runtime(_SEARCH_CAP, "query-tickets")},
        conn_base_url=_BASE_URL))
    with patch("backend.tools.travel.train_dynamic._invoke_dynamic",
               lambda capability, params: _BAD_STATION):
        with pytest.raises(LiveSearchError) as exc_info:
            live_search_service.search_trains(
                from_station="福洲", to_station="厦门北",
                travel_date="2026-11-16")
    assert "车站名称无效" in str(exc_info.value)


def test_replay_bad_date_param_guard(monkeypatch):
    """坏日期在适配器参数校验即拦截（不触网，形态与静态工具一致）。"""
    from backend.infra.mcp_registry import store as mcp_store

    mcp_store.inject_snapshot_for_tests(_snapshot(
        tools={_SEARCH_CAP: _runtime(_SEARCH_CAP, "query-tickets")},
        conn_base_url=_BASE_URL))

    def _no_network(capability, params):
        raise AssertionError("参数非法不得出网")

    with patch("backend.tools.travel.train_dynamic._invoke_dynamic",
               _no_network):
        with pytest.raises(LiveSearchError) as exc_info:
            live_search_service.search_trains(
                from_station="福州", to_station="厦门北", travel_date="11-16")
    assert "date 格式无法解析" in str(exc_info.value)


def test_price_replay_selects_same_train_from_data_list(monkeypatch):
    """票价 data[] 形态：按请求车次选取（不误展示别的车次）。"""
    from backend.infra.mcp_registry import store as mcp_store

    mcp_store.inject_snapshot_for_tests(_snapshot(
        tools={_PRICE_CAP: _runtime(_PRICE_CAP, "query-ticket-price")},
        conn_base_url=_BASE_URL))
    with patch("backend.tools.travel.train_dynamic._invoke_dynamic",
               lambda capability, params: _PRICE_DATA_SHAPE):
        data = live_search_service.search_train_price(
            from_station="福州", to_station="厦门北",
            travel_date="2026-11-16", train_code="D3333")
    assert data["train_code"] == "D3333"
    assert data["prices"]["second_class"] == "58.5"
    assert data["channel"] == "mcp_dynamic"
    # 扁平形态兼容（与静态适配层同口径）
    with patch("backend.tools.travel.train_dynamic._invoke_dynamic",
               lambda capability, params: _PRICE_FLAT):
        flat = live_search_service.search_train_price(
            from_station="福州", to_station="厦门北",
            travel_date="2026-11-16", train_code="D3333")
    assert flat["prices"] == {"second_class": "58.5"}


# =============================================
# 静态通道回归（重构后行为不变 + channel 标注）
# =============================================


def test_static_channel_success_keeps_shape(monkeypatch):
    from backend.config import mcp as mcp_config

    monkeypatch.setattr(mcp_config, "TRAIN_MCP_ENABLED", True)
    with patch("backend.tools.travel.train.call_tool",
               lambda *a, **k: _SUCCESS):
        data = live_search_service.search_trains(
            from_station="福州", to_station="厦门北", travel_date="2026-11-16")
    assert data["channel"] == "static_direct"
    assert [t["train_code"] for t in data["trains"]] == ["D3333", "G1655"]
    assert data["trains"][0]["seats"] == {"二等座": "有"}
    assert data["total_matched"] == 2


def test_static_channel_no_direct_keeps_business_answer(monkeypatch):
    """静态通道「真无直达」语义不变（重构回归）。"""
    from backend.config import mcp as mcp_config

    monkeypatch.setattr(mcp_config, "TRAIN_MCP_ENABLED", True)
    with patch("backend.tools.travel.train.call_tool",
               lambda *a, **k: _NO_DIRECT):
        data = live_search_service.search_trains(
            from_station="福州", to_station="拉萨", travel_date="2026-11-16")
    assert data["count"] == 0
    assert "中转" in data["suggestion"]
    assert data["channel"] == "static_direct"


# =============================================
# 真 PG 门控集成（pg marker；-m "not pg" 跳过）
# =============================================

_TENANT = "t1-train-dynamic-it"
_ADMIN_HEADERS = {
    "X-Auth-Type": "jwt",
    "X-User-Id": "u-t1-admin",
    "X-User-Roles": "admin",
    "X-Tenant-Id": _TENANT,
}
# 集成专用连接名（不与生产 12306 连接的 capability slug 冲突）；
# slug 派生：12306-it 数字开头 → t12306_it（specs.slugify）
_CONN_NAME = "12306-it"
_CONN_SEARCH_CAP = f"mcp.t{_CONN_NAME.replace('-', '_')}__query_tickets"
_CONN_PRICE_CAP = f"mcp.t{_CONN_NAME.replace('-', '_')}__query_ticket_price"


def _build_train_app():
    """mock 12306 上游（FastMCP Streamable HTTP，契约形态与实测一致）。"""
    from mcp.server.fastmcp import FastMCP

    mcp = FastMCP("12306-mock")

    @mcp.tool(name="query-tickets")
    def query_tickets(from_station: str, to_station: str, train_date: str) -> dict:
        """查询两站间余票（mock：契约回放形态）"""
        return _SUCCESS

    @mcp.tool(name="query-ticket-price")
    def query_ticket_price(from_station: str, to_station: str,
                           train_date: str, train_code: str) -> dict:
        """查询指定车次票价（mock：data[] 形态）"""
        return _PRICE_DATA_SHAPE

    return mcp.streamable_http_app()


def _start_uvicorn(app) -> int:
    import uvicorn

    config = uvicorn.Config(app, host="127.0.0.1", port=0, log_level="error")
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.time() + 15
    while time.time() < deadline and not server.started:
        time.sleep(0.05)
    if not server.started:
        raise RuntimeError("mock 12306 server 启动超时")
    return server.servers[0].sockets[0].getsockname()[1]


@pytest.mark.pg
def test_pg_registered_channel_end_to_end(monkeypatch):
    """真 PG + mock 上游全链：登记 → 发现 → 审核 → 授权 → 服务层走动态。

    治理面同步验证撤销语义：revoke 授权后 agent 身份 test-invoke 被拒
    （product_capability_denied），regrant 恢复。
    """
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from backend.app.api.routes import admin_mcp as admin_mcp_route
    from backend.infra.async_utils import run_async
    from backend.infra.mcp_registry import store as mcp_store

    # ── DB 前置：ai.mcp_* 表存在才跑（迁移未执行 → 跳过）──
    from backend.infra.db import get_memory_engine

    raw = get_memory_engine().raw_connection()
    try:
        with raw.cursor() as cur:
            cur.execute("SELECT 1 FROM ai.mcp_connections LIMIT 1")
        raw.commit()
    except Exception as exc:  # noqa: BLE001
        raw.close()
        pytest.skip(f"ai.mcp_* 表不存在（迁移未执行）: {exc}")
    finally:
        try:
            raw.close()
        except Exception:  # noqa: BLE001
            pass

    def _db_exec(sql: str, params: dict | None = None) -> None:
        conn = get_memory_engine().raw_connection()
        try:
            with conn.cursor() as cur:
                cur.execute(sql, params or {})
            conn.commit()
        finally:
            conn.close()

    _db_exec("DELETE FROM ai.mcp_connections WHERE tenant_id = %(t)s",
             {"t": _TENANT})
    mcp_store.reset_for_tests()

    port = _start_uvicorn(_build_train_app())
    mock_base = f"http://127.0.0.1:{port}/mcp"

    app = FastAPI()
    app.include_router(admin_mcp_route.router)
    client = TestClient(app, raise_server_exceptions=False)
    # 审计旁路（治理变更审计由共享设施保障，与 e2e 基座同口径）
    monkeypatch.setattr(
        admin_mcp_route, "run_governance_mutation",
        lambda request, operator, operation, payload, callback: callback())

    try:
        # ── 登记连接（private 内网口径，与生产登记参数同形）──
        r = client.post("/admin/mcp/connections", headers=_ADMIN_HEADERS,
                        json={"name": _CONN_NAME, "base_url": mock_base,
                              "auth_type": "none",
                              "network_scope": "private", "enabled": True})
        assert r.status_code == 200, r.text
        conn_id = r.json()["id"]

        # ── 发现 → 两个上游工具 pending ──
        r = client.post(f"/admin/mcp/connections/{conn_id}/discover",
                        headers=_ADMIN_HEADERS)
        assert r.status_code == 200, r.text
        tools = {
            t["tool_name"]: t
            for t in client.get(f"/admin/mcp/connections/{conn_id}/tools",
                                headers=_ADMIN_HEADERS).json()["tools"]
        }
        assert set(tools) == {"query-tickets", "query-ticket-price"}
        assert tools["query-tickets"]["capability"] == _CONN_SEARCH_CAP

        # ── 审核两工具（read / R1 / 无确认）──
        gov = {"operation": "read", "risk_level": "R1",
               "confirmation_policy": "none", "timeout_ms": 15000}
        for tool_id in (tools["query-tickets"]["id"],
                        tools["query-ticket-price"]["id"]):
            r = client.post(f"/admin/mcp/tools/{tool_id}/review",
                            headers=_ADMIN_HEADERS,
                            json={"action": "approve", "governance": gov,
                                  "examples": ["福州到厦门有哪些车",
                                               "查一下明天的车票"],
                                  "notes": "T1 集成审核"})
            assert r.status_code == 200, r.text

        # ── 授权 agent 产品（两工具）──
        for tool_id in (tools["query-tickets"]["id"],
                        tools["query-ticket-price"]["id"]):
            r = client.post(f"/admin/mcp/tools/{tool_id}/grants",
                            headers=_ADMIN_HEADERS,
                            json={"product_key": "agent"})
            assert r.status_code == 200, r.text

        # ── 快照刷新（进程内立即生效）──
        assert run_async(mcp_store.refresh()), "快照刷新失败"

        # ── 服务层端到端：走动态通道（channel=mcp_dynamic）──
        monkeypatch.setattr(
            "backend.config.mcp.TRAIN_MCP_BASE_URL", mock_base)
        monkeypatch.setattr(
            "backend.tools.travel.train_dynamic._TRAIN_SEARCH_CAPABILITY",
            _CONN_SEARCH_CAP)
        monkeypatch.setattr(
            "backend.tools.travel.train_dynamic._TRAIN_PRICE_CAPABILITY",
            _CONN_PRICE_CAP)
        data = live_search_service.search_trains(
            from_station="福州", to_station="厦门北", travel_date="2026-11-16")
        assert data["channel"] == "mcp_dynamic"
        assert data["count"] == 2
        assert data["trains"][0]["seats"] == {"二等座": "有"}

        price = live_search_service.search_train_price(
            from_station="福州", to_station="厦门北",
            travel_date="2026-11-16", train_code="D3333")
        assert price["channel"] == "mcp_dynamic"
        assert price["prices"]["second_class"] == "58.5"

        # ── 治理面撤销语义：revoke 授权 → agent 身份被拒 → regrant 恢复 ──
        search_tool_id = tools["query-tickets"]["id"]
        r = client.post(f"/admin/mcp/tools/{search_tool_id}/grants",
                        headers=_ADMIN_HEADERS,
                        json={"product_key": "agent", "revoke": True})
        assert r.status_code == 200, r.text
        assert run_async(mcp_store.refresh())
        r = client.post(f"/admin/mcp/tools/{search_tool_id}/test-invoke",
                        headers=_ADMIN_HEADERS,
                        json={"arguments": {"from_station": "福州",
                                            "to_station": "厦门北",
                                            "train_date": "2026-11-16"},
                              "product_key": "agent"})
        assert r.status_code == 200
        assert r.json()["error_code"] == "product_capability_denied"

        r = client.post(f"/admin/mcp/tools/{search_tool_id}/grants",
                        headers=_ADMIN_HEADERS,
                        json={"product_key": "agent"})
        assert r.status_code == 200, r.text
        assert run_async(mcp_store.refresh())
        r = client.post(f"/admin/mcp/tools/{search_tool_id}/test-invoke",
                        headers=_ADMIN_HEADERS,
                        json={"arguments": {"from_station": "福州",
                                            "to_station": "厦门北",
                                            "train_date": "2026-11-16"},
                              "product_key": "agent"})
        assert r.status_code == 200, r.text
        assert r.json()["tool_status"] == "success"
    finally:
        _db_exec("DELETE FROM ai.mcp_connections WHERE tenant_id = %(t)s",
                 {"t": _TENANT})
        mcp_store.reset_for_tests()
