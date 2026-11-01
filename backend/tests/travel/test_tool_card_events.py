# -*- coding: utf-8 -*-
"""F18 tool.card 事件契约测试（travel-v2-production P2）。

覆盖：卡片载荷契约（类目白名单/条目上限/JSON 安全）、run_checked 成功
路径的发射条件（有 preview 白名单类目才发、失败/空不发）、事件载荷经
emit_travel_event 出口形状完整（source/event/agent/tool/card/tool_call_id）。
"""
from __future__ import annotations

import pytest

from backend.travel.core.cards import (
    MAX_CARD_ITEMS,
    ToolCardPayload,
    card_from_summary,
)
from backend.travel.core.events import travel_event_scope
from backend.travel.services.tool_failure_policy import (
    ToolCriticality,
    run_checked,
)

_PREVIEW = [
    {"train_no": "D3333", "start_time": "06:43", "arrive_time": "08:20"},
    {"train_no": "D3210", "start_time": "07:15", "arrive_time": "09:02"},
]


def _run_train_query(summary: dict | None = None) -> str:
    """模拟一次 auxiliary 车次查询（result_summary 含 category+preview）。"""
    data, _outcome = run_checked(
        "travel_train_search_tool", "planning",
        lambda: {"trains": _PREVIEW},
        provider="12306", dependency=ToolCriticality.OPTIONAL,
        task_id="task-1",
        result_summary=lambda _data: summary if summary is not None else {
            "category": "train", "result_count": len(_PREVIEW),
            "data_status": "available", "preview": _PREVIEW,
            "provider": "12306",
        },
    )
    assert data is not None
    return data["trains"][0]["train_no"]


@pytest.fixture
def captured_events():
    """捕获当前上下文全部 travel 事件（SSE sink 同构）。"""
    events: list[dict] = []
    with travel_event_scope(events.append):
        yield events


# =============================================
# 卡片载荷契约
# =============================================


def test_card_payload_validates_kind_whitelist():
    with pytest.raises(Exception):
        ToolCardPayload(kind="weather", title="", items=[{"a": 1}])
    card = ToolCardPayload(kind="train", title="", items=_PREVIEW,
                           provider="12306")
    dumped = card.model_dump()
    assert dumped["kind"] == "train"
    assert dumped["disclosure"] == ""          # 缺失不补造
    assert dumped["items"][0]["train_no"] == "D3333"


def test_card_payload_rejects_over_limit_items():
    with pytest.raises(Exception):
        ToolCardPayload(kind="food", title="", items=[{"n": i} for i in range(MAX_CARD_ITEMS + 1)])


def test_card_from_summary_rules():
    assert card_from_summary(None, title="") is None
    assert card_from_summary({"category": "weather", "preview": [{"d": 1}]},
                             title="") is None          # 白名单外不发
    assert card_from_summary({"category": "train", "preview": []},
                             title="") is None          # 空 preview 不发
    assert card_from_summary(
        {"category": "hotel", "preview": ["脏数据", {"name": "x"}]},
        title="") is not None                            # 非对象条目被剔除
    assert card_from_summary({"category": "food", "preview": ["非对象"]},
                             title="") is None            # 全非对象不发


# =============================================
# run_checked 发射条件（F18 接线语义）
# =============================================


def test_successful_query_emits_tool_card(captured_events):
    _run_train_query()
    cards = [e for e in captured_events if e["event"] == "tool.card"]
    assert len(cards) == 1
    card_event = cards[0]
    assert card_event["source"] == "travel"
    assert card_event["tool"] == "travel_train_search_tool"
    assert card_event["task_id"] == "task-1"
    payload = card_event["card"]
    assert payload["kind"] == "train"
    assert payload["items"] == _PREVIEW
    assert payload["provider"] == "12306"
    # 与同次调用的 tool.result 用同一 tool_call_id（可关联）
    results = [e for e in captured_events if e["event"] == "tool.result"]
    assert results and results[0]["tool_call_id"] == card_event["tool_call_id"]


def test_failed_query_emits_no_card(captured_events):
    from backend.travel.services.live_search_service import LiveSearchError

    data, outcome = run_checked(
        "map_merchant_search_tool", "research",
        lambda: (_ for _ in ()).throw(LiveSearchError("高德不可用")),
        provider="amap", dependency=ToolCriticality.OPTIONAL,
        result_summary=lambda _d: {"category": "food", "preview": []},
    )
    assert data is None
    assert not [e for e in captured_events if e["event"] == "tool.card"]


def test_empty_preview_emits_no_card(captured_events):
    _run_train_query(summary={"category": "train", "result_count": 0,
                              "data_status": "empty", "preview": [],
                              "provider": "12306"})
    assert not [e for e in captured_events if e["event"] == "tool.card"]


def test_out_of_whitelist_category_emits_no_card(captured_events):
    _run_train_query(summary={"category": "weather",
                              "preview": [{"date": "12-01", "weather": "晴"}],
                              "provider": "qweather"})
    assert not [e for e in captured_events if e["event"] == "tool.card"]


def test_card_without_sink_does_not_break_query(captured_events=None):
    """无 SSE sink（非流式路径）时卡片事件照常落日志、查询照常返回。"""
    assert _run_train_query() == "D3333"
