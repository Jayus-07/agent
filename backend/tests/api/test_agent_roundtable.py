"""Agent 圆桌演示 SSE 端点测试（自建 app，不触全局应用与 PostgreSQL）。

打桩点：bind_llm_for_role（固定脚本 FakeLLM）、_execute_capability（不触真实
Skill）、_require_admin（403 契约用例）；require_rate_limit 经 dependency
override 放行。事件契约：meta → (status[/tool_result])* → message* → done/error
终帧互斥。
"""
from __future__ import annotations

import json
from types import SimpleNamespace

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from backend.app.api.routes import agent_roundtable as arm
from backend.infra.llm.rate_limiter import require_rate_limit


def _msg(content: str = "", tool_calls: list | None = None):
    return SimpleNamespace(content=content, tool_calls=tool_calls or [])


class _FakeLLM:
    """按脚本序消费响应；bind_tools 返回自身以复用同一脚本。"""

    def __init__(self, script: list):
        self.script = list(script)
        self.calls = 0

    def invoke(self, msgs):
        idx = min(self.calls, len(self.script) - 1)
        self.calls += 1
        item = self.script[idx]
        if isinstance(item, Exception):
            raise item
        return item

    def bind_tools(self, tools):
        return self


def _parse_frames(body: str) -> list[tuple[str, dict]]:
    events: list[tuple[str, dict]] = []
    for block in body.split("\n\n"):
        etype, data = None, None
        for line in block.splitlines():
            if line.startswith("event: "):
                etype = line[len("event: "):]
            elif line.startswith("data: "):
                data = json.loads(line[len("data: "):])
        if etype is not None:
            events.append((etype, data or {}))
    return events


def _types(events: list[tuple[str, dict]]) -> list[str]:
    return [e for e, _ in events]


@pytest.fixture()
def app(monkeypatch):
    monkeypatch.setattr(arm, "_require_admin", _async_ok)
    application = FastAPI()
    application.include_router(arm.router)
    application.dependency_overrides[require_rate_limit] = lambda: None
    return application


@pytest.fixture()
def client(app):
    return TestClient(app)


async def _async_ok(r) -> None:
    return None


def test_roundtable_basic_event_sequence(client, monkeypatch):
    """2 人 × 1 轮纯发言：meta → thinking/message 交替 → done，终帧不缺席。"""
    fake = _FakeLLM([_msg("规划师先说"), _msg("客服接着说")])
    monkeypatch.setattr(arm, "bind_llm_for_role", lambda *a, **k: fake)
    monkeypatch.setattr(arm, "_domain_tools", lambda mode: ([], {}))

    with client.stream(
        "POST", "/roundtable/stream",
        json={"topic": "测试话题", "participants": ["travel", "customer_service"],
              "max_rounds": 1},
    ) as resp:
        assert resp.status_code == 200
        events = _parse_frames(b"".join(resp.iter_bytes()).decode("utf-8"))

    types = _types(events)
    assert types[0] == "meta"
    assert types[-1] == "done"
    assert types.count("message") == 2
    assert types == ["meta", "status", "message", "status", "message", "done"]

    meta = events[0][1]
    assert meta["request_id"] and len(meta["participants"]) == 2
    assert meta["participants"][0]["route_mode"] == "travel"
    assert meta["topic"] == "测试话题"
    assert events[-1][1]["message_count"] == 2
    # 每条 message 前必有同角色的 thinking
    assert types.index("message") - 1 == types.index("status")


def test_roundtable_tool_call_path(client, monkeypatch):
    """FC 决策 → 真实执行（打桩）→ 结果回填二次生成发言。"""
    scripted = [
        _msg(tool_calls=[{"name": "map_lookup", "args": {"action": "weather"}}]),
        _msg("基于天气数据的发言"),
    ]
    fake = _FakeLLM(scripted)
    monkeypatch.setattr(arm, "bind_llm_for_role", lambda *a, **k: fake)
    tools = [{"type": "function", "function": {"name": "map_lookup", "parameters": {}}}]
    monkeypatch.setattr(arm, "_domain_tools", lambda mode: (tools, {"map_lookup": "map.lookup"}))
    monkeypatch.setattr(
        arm, "_execute_capability",
        lambda cap, args: (True, json.dumps({"weather": "晴"}), 0.1),
    )

    with client.stream(
        "POST", "/roundtable/stream",
        json={"topic": "天气", "participants": ["travel", "customer_service"],
              "max_rounds": 1},
    ) as resp:
        events = _parse_frames(b"".join(resp.iter_bytes()).decode("utf-8"))

    types = _types(events)
    assert "tool_result" in types
    tool_status_idx = types.index("tool_result") - 1
    assert types[tool_status_idx] == "status"
    assert events[tool_status_idx][1]["stage"] == "tool"
    tool_evt = dict(events[types.index("tool_result")][1])
    assert tool_evt["ok"] is True and tool_evt["capability"] == "map.lookup"
    # 第二次发言基于工具结果
    assert events[types.index("message")][1]["content"] == "基于天气数据的发言"
    assert types[-1] == "done"


def test_roundtable_rejects_invalid_params(client):
    """未知角色 / 参与者不足 / 空话题 → 422，不启动编排。"""
    for payload in (
        {"topic": "t", "participants": ["travel", "nope"]},
        {"topic": "t", "participants": ["travel"]},
        {"topic": "  ", "participants": ["travel", "customer_service"]},
    ):
        resp = client.post("/roundtable/stream", json=payload)
        assert resp.status_code == 422, payload


def test_roundtable_requires_admin(monkeypatch):
    """端点契约：非管理员被管理员闸挡下（403），不会触达编排。"""
    async def _deny(r):
        raise HTTPException(status_code=403, detail={"message": "仅限管理员"})

    monkeypatch.setattr(arm, "_require_admin", _deny)
    application = FastAPI()
    application.include_router(arm.router)
    application.dependency_overrides[require_rate_limit] = lambda: None
    client = TestClient(application)

    resp = client.post(
        "/roundtable/stream",
        json={"topic": "t", "participants": ["travel", "customer_service"]},
    )
    assert resp.status_code == 403


def test_roundtable_llm_failure_emits_error_frame(client, monkeypatch):
    """LLM 链路异常：如实发 error 终帧，不伪造发言、不挂死。"""
    fake = _FakeLLM([RuntimeError("provider down")])
    monkeypatch.setattr(arm, "bind_llm_for_role", lambda *a, **k: fake)
    monkeypatch.setattr(arm, "_domain_tools", lambda mode: ([], {}))

    with client.stream(
        "POST", "/roundtable/stream",
        json={"topic": "t", "participants": ["travel", "customer_service"],
              "max_rounds": 1},
    ) as resp:
        events = _parse_frames(b"".join(resp.iter_bytes()).decode("utf-8"))

    types = _types(events)
    assert types[-1] == "error"
    assert "message" not in types
    assert "done" not in types


def test_roundtable_participants_catalog(client):
    """GET /participants：清单与限额结构完整，是配置面板的唯一数据源。"""
    resp = client.get("/roundtable/participants")
    assert resp.status_code == 200
    data = resp.json()
    modes = [p["route_mode"] for p in data["participants"]]
    assert set(modes) == set(arm._PERSONAS)
    for p in data["participants"]:
        assert p["display_name"] and p["label"]
        # 工具箱必须只含只读白名单能力（写类能力不得进圆桌）
        assert set(p["tools"]) <= arm._READONLY_CAPABILITIES
    assert data["limits"]["max_rounds"] == arm.MAX_ROUNDS


def test_roundtable_trace_recorded(client, monkeypatch):
    """治理要求：圆桌开 trace session，发言 span 收口，用量可按 skill_id 归因。"""
    fake = _FakeLLM([_msg("规划师说"), _msg("客服说")])
    monkeypatch.setattr(arm, "bind_llm_for_role", lambda *a, **k: fake)
    monkeypatch.setattr(arm, "_domain_tools", lambda mode: ([], {}))

    from backend.observability import tracer as tracer_mod

    started: dict = {}
    real_start = tracer_mod.trace_collector.start

    def spy_start(*a, **k):
        record = real_start(*a, **k)
        started["record"] = record
        return record

    monkeypatch.setattr(tracer_mod.trace_collector, "start", spy_start)

    with client.stream(
        "POST", "/roundtable/stream",
        json={"topic": "trace 测试", "participants": ["travel", "customer_service"],
              "max_rounds": 1},
    ) as resp:
        events = _parse_frames(b"".join(resp.iter_bytes()).decode("utf-8"))

    assert _types(events)[-1] == "done"
    record = started.get("record")
    assert record is not None, "圆桌应开启 trace session"
    assert record.workflow_name == "agent_roundtable"
    assert record.session_id.startswith("roundtable:")
    span_names = [s.name for s in record.spans]
    assert "行程规划师 发言" in span_names
    assert "客服专员 发言" in span_names
    # finish 已收尾：无泄漏（end_time 为空的 span）且总耗时已填充
    assert record.total_ms >= 0
    assert all(s.end_time for s in record.spans)
