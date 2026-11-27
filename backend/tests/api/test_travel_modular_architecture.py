"""test_travel_modular_architecture.py — Tier 8 架构守卫测试。

验证 backend/app/api/routes/travel.py 单体治理成效：
1. 门面代码量严格受限（< 250 行，削减率 > 90%）；
2. 子模块按单一职责物理隔离，结构完整；
3. 保持 100% 存量导出符号与 APIRouter 契约不变；
4. FacadeProxy 动态代理支持外部 monkeypatch 透明穿透。
"""
from __future__ import annotations

import inspect
from pathlib import Path
from fastapi import APIRouter
import pytest

from backend.app.api.routes import travel
from backend.app.api.routes.travel_subroutes.base import facade


TRAVEL_PY = Path(travel.__file__).resolve()
SUBROUTES_DIR = TRAVEL_PY.parent / "travel_subroutes"


def test_travel_facade_line_count_budget():
    """守卫：travel.py 门面文件必须保持轻量（< 250 行），严防重新膨胀。"""
    lines = TRAVEL_PY.read_text(encoding="utf-8").splitlines()
    total_lines = len(lines)
    assert total_lines < 250, f"travel.py 门面行数超标：{total_lines} >= 250"
    # 相比原 2423 行削减率必须超过 90%
    reduction = (2423 - total_lines) / 2423
    assert reduction >= 0.90, f"削减率不足 90%：实际 {reduction:.1%}"


def test_travel_submodules_exist_and_non_empty():
    """守卫：子模块结构必须按单一职责完整存在且具备实质内容。"""
    expected_submodules = [
        "__init__.py",
        "base.py",
        "schemas.py",
        "planning.py",
        "streaming.py",
        "ics.py",
        "conversation.py",
        "retired_v1.py",
        "routes.py",
    ]
    assert SUBROUTES_DIR.is_dir(), f"子模块目录不存在：{SUBROUTES_DIR}"
    for filename in expected_submodules:
        module_file = SUBROUTES_DIR / filename
        assert module_file.is_file(), f"缺少子模块：{filename}"
        content = module_file.read_text(encoding="utf-8").strip()
        assert len(content.splitlines()) >= 5, f"子模块 {filename} 内容过短"


def test_travel_facade_exports_parity():
    """守卫：门面导出符号必须与重构前 100% 对齐，保证无侵入向下兼容。"""
    required_symbols = [
        "router",
        "_retired_v1_router",
        "require_identity",
        "logger",
        "TravelBrief",
        "TravelUiContext",
        "TravelPlanRequest",
        "TravelResponseMetadata",
        "TravelConversationMessage",
        "TravelConversationMessagesRequest",
        "IcsExportRequest",
        "TravelFeedbackRequest",
        "TravelPreferencesRequest",
        "TravelPlanConfirmRequest",
        "TravelPlanDiscardRequest",
        "TravelPlanRestoreRequest",
        "TravelDecisionRequest",
        "_travel_tenant",
        "_travel_resume_lock",
        "_build_travel_graph_input",
        "_submit_travel_request",
        "_finish_travel_trace",
        "travel_city_guide",
        "travel_plan",
        "travel_plan_stream",
        "travel_v2_plan_stream",
        "travel_export_ics",
        "itinerary_to_ics",
        "travel_feedback",
        "travel_recommend",
        "travel_conversation_messages",
        "replace_travel_conversation_messages",
    ]
    for sym in required_symbols:
        assert hasattr(travel, sym), f"门面 travel.py 缺失导出符号：{sym}"


def test_travel_router_endpoints_intact():
    """守卫：活跃 router 与退役 _retired_v1_router 必须为有效 APIRouter 并挂载正确路由。"""
    assert isinstance(travel.router, APIRouter)
    assert isinstance(travel._retired_v1_router, APIRouter)

    active_paths = {route.path for route in travel.router.routes}
    assert "/travel/city-guide" in active_paths or "/city-guide" in active_paths
    assert "/travel/v2/plan/stream" in active_paths or "/v2/plan/stream" in active_paths
    assert "/travel/export/ics" in active_paths or "/export/ics" in active_paths
    assert "/travel/feedback" in active_paths or "/feedback" in active_paths
    assert "/travel/recommend" in active_paths or "/recommend" in active_paths
    assert any("messages" in p for p in active_paths)


def test_facade_proxy_monkeypatch_transparency(monkeypatch):
    """守卫：FacadeProxy 必须支持外部 monkeypatch 透明穿透到子模块中。"""
    orig_func = travel._finish_travel_trace
    called = []

    def mock_finish(*args, **kwargs):
        called.append(args)
        return "mocked_finish"

    monkeypatch.setattr(travel, "_finish_travel_trace", mock_finish)
    assert facade._finish_travel_trace is mock_finish
    res = facade._finish_travel_trace("test_trace", 0.0)
    assert res == "mocked_finish"
    assert len(called) == 1
