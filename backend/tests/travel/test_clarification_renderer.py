"""tests/travel/test_clarification_renderer.py — 追问渲染层守卫（2026-10-08 STOP 4）

覆盖验收门禁：
  - P0-13 LLM Renderer 只能生成 question（schema 白名单）
  - P0-14 Renderer timeout 自动模板降级
  - P0-15 Renderer invalid output 自动模板降级
  - P0-17 同一轮 Clarification LLM 调用 ≤1（失败不重试）
  - 确定性背景行（context_notes）不交给 LLM，恒拼尾部
"""
from __future__ import annotations

import json

import pytest

from backend.travel.models.brief import TravelBrief
from backend.travel.services import clarification_renderer as renderer
from backend.travel.services.clarification_renderer import render_clarification
from backend.travel.services.clarification_service import (
    build_clarification_plan,
    render_template,
)


class _FakeLLM:
    def bind(self, **kwargs):
        return self

    def invoke(self, messages):
        raise AssertionError("测试未注入响应")


def _patch_llm(monkeypatch, content) -> list[int]:
    """mock 外部边界；返回调用次数表（验证不重试）。"""
    from backend.infra import async_utils
    from backend.infra.llm import proxy as proxy_mod

    calls = [0]

    class _Resp:
        pass

    _Resp.content = content if not isinstance(content, Exception) else ""

    def _fake_sync_call(fn, timeout, *args, **kwargs):
        calls[0] += 1
        if isinstance(content, Exception):
            raise content
        return _Resp()

    monkeypatch.setattr(async_utils, "sync_call_with_timeout", _fake_sync_call)
    monkeypatch.setattr(proxy_mod, "_build_llm_for", lambda name: _FakeLLM())
    monkeypatch.setattr(proxy_mod, "record_llm_result",
                        lambda *a, **k: None)
    return calls


@pytest.fixture()
def _flag_on(monkeypatch):
    from backend.config import travel as T

    monkeypatch.setattr(T, "TRAVEL_LLM_CLARIFICATION_ENABLED", True)


def _plan(message: str = "我想出去玩"):
    brief = TravelBrief()
    return build_clarification_plan(brief, message, unsupported_city="")


def test_disabled_flag_uses_template(monkeypatch):
    from backend.config import travel as T

    monkeypatch.setattr(T, "TRAVEL_LLM_CLARIFICATION_ENABLED", False)

    def _boom(*a, **k):
        raise AssertionError("flag 关闭不得触碰 LLM")

    from backend.infra import async_utils

    monkeypatch.setattr(async_utils, "sync_call_with_timeout", _boom)
    plan = _plan()
    text, meta = render_clarification(plan, "我想出去玩")
    assert meta["source"] == "template"
    assert meta["fallback_reason"] == "disabled"
    assert text == render_template(plan)


def test_llm_question_used_with_notes_appended(monkeypatch, _flag_on):
    question = "听起来你准备出去玩，我先确认一下：想去哪个城市呢？"
    _patch_llm(monkeypatch, json.dumps({"question": question},
                                       ensure_ascii=False))
    plan = _plan()
    plan.context_notes = ["（当前可规划的城市：福州、厦门、杭州）"]
    text, meta = render_clarification(plan, "我想出去玩")
    assert meta["source"] == "llm"
    assert meta["slot"] == "destination"
    assert text.startswith(question)
    # 确定性背景行恒在尾部，LLM 不能吃掉它
    assert "当前可规划的城市" in text


def test_non_question_keys_ignored(monkeypatch, _flag_on):
    """P0-13：输出里的其他键（options/next_node…）在结构上被丢弃。"""
    content = json.dumps({
        "question": "想去哪个城市呢？告诉我吧。",
        "options": [{"label": " hacker"}],
        "next_node": "poi",
    }, ensure_ascii=False)
    _patch_llm(monkeypatch, content)
    text, meta = render_clarification(_plan(), "我想出去玩")
    assert meta["source"] == "llm"
    assert "hacker" not in text
    assert "poi" not in text


def test_timeout_falls_back_to_template(monkeypatch, _flag_on):
    """P0-14：超时 → 模板立即接管，且不重试（调用次数=1）。"""
    from concurrent.futures import TimeoutError as FutureTimeout

    calls = _patch_llm(monkeypatch, FutureTimeout("boom"))
    text, meta = render_clarification(_plan(), "我想出去玩")
    assert meta["source"] == "template"
    assert meta["fallback_reason"] == "llm_error"
    assert text == render_template(_plan())
    assert calls == [1]


@pytest.mark.parametrize("content,reason", [
    ("这不是 JSON", "invalid_json"),
    (json.dumps({"question": ""}, ensure_ascii=False), "empty"),
    (json.dumps({"question": "x" * 300}, ensure_ascii=False), "too_long"),
    (json.dumps({"question": "看这个 http://evil.com"}, ensure_ascii=False),
     "forbidden_content"),
    (json.dumps({"question": "第一行\n第二行"}, ensure_ascii=False),
     "forbidden_content"),
    (json.dumps({"no_question": 1}, ensure_ascii=False), "invalid_json"),
])
def test_invalid_outputs_fall_back(monkeypatch, _flag_on, content, reason):
    """P0-15：非法输出全部落模板，reason 如实记录。"""
    _patch_llm(monkeypatch, content)
    text, meta = render_clarification(_plan(), "我想出去玩")
    assert meta["source"] == "template"
    assert meta["fallback_reason"] == reason
    assert "去哪个城市" in text


def test_meta_is_scalar_and_serializable(monkeypatch, _flag_on):
    _patch_llm(monkeypatch, json.dumps({"question": "想去哪个城市呢？"}))
    _, meta = render_clarification(_plan(), "我想出去玩")
    assert set(meta) == {"source", "prompt_version", "latency_ms",
                         "fallback_reason", "slot"}
    assert isinstance(json.dumps(meta, ensure_ascii=False), str)
