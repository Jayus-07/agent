"""Reporter LLM 的事实边界、Prompt 归因与模板降级契约。"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from types import SimpleNamespace
from pathlib import Path

import pytest


@pytest.mark.parametrize(
    ("configured", "expected"),
    [(None, "true"), ("false", "false")],
)
def test_reporter_default_is_enabled_but_explicit_false_is_respected(
        tmp_path: Path, configured: str | None, expected: str) -> None:
    repo_root = Path(__file__).resolve().parents[3]
    child_env = os.environ.copy()
    child_env.pop("TRAVEL_LLM_REPORTER_ENABLED", None)
    if configured is not None:
        child_env["TRAVEL_LLM_REPORTER_ENABLED"] = configured
    child_env["PYTHONPATH"] = str(repo_root)

    proc = subprocess.run(
        [sys.executable, "-c", (
            "from backend.config.travel import TRAVEL_LLM_REPORTER_ENABLED; "
            "print(str(TRAVEL_LLM_REPORTER_ENABLED).lower())"
        )],
        cwd=tmp_path, env=child_env, capture_output=True, text=True, timeout=30,
    )

    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.strip() == expected


def _patch_llm(monkeypatch: pytest.MonkeyPatch, content: str | Exception):
    from backend.config import model_roles
    from backend.infra import async_utils
    from backend.infra.llm import proxy as proxy_mod
    from backend.travel.services import reporter_renderer

    calls = []

    class _FakeLLM:
        def bind(self, **_kwargs):
            return self

        def invoke(self, *_args, **_kwargs):
            raise AssertionError("模型调用应由超时包装器接管")

    response = SimpleNamespace(
        content="" if isinstance(content, Exception) else content,
        usage_metadata={"input_tokens": 31, "output_tokens": 12},
        response_metadata={},
    )

    def _sync_call(fn, timeout, *args, **kwargs):
        calls.append((fn, timeout, args, kwargs))
        if isinstance(content, Exception):
            raise content
        return response

    monkeypatch.setattr(async_utils, "sync_call_with_timeout", _sync_call)
    monkeypatch.setattr(proxy_mod, "_build_llm_for", lambda _name: _FakeLLM())
    monkeypatch.setattr(proxy_mod, "record_llm_result", lambda *a, **k: None)
    monkeypatch.setattr(
        model_roles, "resolve_effective", lambda _role: {"value": "main-test"})
    monkeypatch.setattr(
        reporter_renderer, "_render_prompt",
        lambda *_args: ("system prompt", "travel.reporter@3", "snapshot"),
    )
    return calls


def test_disabled_reporter_returns_template_without_touching_llm(
        monkeypatch: pytest.MonkeyPatch) -> None:
    from backend.config import travel
    from backend.travel.services.reporter_renderer import render_travel_reply

    monkeypatch.setattr(travel, "TRAVEL_LLM_REPORTER_ENABLED", False)

    def _unexpected(*_args, **_kwargs):
        raise AssertionError("开关关闭时不得调用 LLM")

    from backend.infra import async_utils
    monkeypatch.setattr(async_utils, "sync_call_with_timeout", _unexpected)
    answer, meta = render_travel_reply({}, "确定性模板回复")

    assert answer == "确定性模板回复"
    assert meta["source"] == "template"
    assert meta["fallback_reason"] == "disabled"


def test_valid_reply_records_model_prompt_and_tokens(
        monkeypatch: pytest.MonkeyPatch) -> None:
    from backend.config import travel
    from backend.travel.services.reporter_renderer import render_travel_reply

    monkeypatch.setattr(travel, "TRAVEL_LLM_REPORTER_ENABLED", True)
    calls = _patch_llm(
        monkeypatch,
        json.dumps({"reply": "我按你的需求整理了福州行程，具体安排请看行程卡。"},
                   ensure_ascii=False),
    )
    answer, meta = render_travel_reply(
        {
            "user_message": "帮我规划福州行程",
            "intent": "plan",
            "brief": {"destination": "福州", "days": 2},
            "turn_decision": {"primary_action": "create_plan"},
        },
        "福州行程草案已生成。",
    )

    assert answer == "我按你的需求整理了福州行程，具体安排请看行程卡。"
    assert meta == {
        "source": "llm", "model": "main-test",
        "prompt_version": "travel.reporter@3", "prompt_source": "snapshot",
        "latency_ms": meta["latency_ms"], "input_tokens": 31,
        "output_tokens": 12, "fallback_reason": "",
    }
    assert len(calls) == 1


@pytest.mark.parametrize("reply", [
    "查到 G9999 次车，酒店 399 元一晚。",
    "行程已应用到正式版。",
    "当前行程已生效。",
])
def test_unsupported_facts_or_apply_claim_fall_back(
        monkeypatch: pytest.MonkeyPatch, reply: str) -> None:
    from backend.config import travel
    from backend.travel.services.reporter_renderer import render_travel_reply

    monkeypatch.setattr(travel, "TRAVEL_LLM_REPORTER_ENABLED", True)
    _patch_llm(monkeypatch, json.dumps({"reply": reply}, ensure_ascii=False))
    fallback = "福州行程草案已整理，尚未应用。"
    answer, meta = render_travel_reply(
        {"user_message": "调整福州行程"}, fallback)

    assert answer == fallback
    assert meta["source"] == "template"
    assert meta["fallback_reason"] in {
        "unsupported_numeric_claim", "forbidden_claim",
    }


def test_numeric_evidence_requires_an_exact_number_token() -> None:
    from backend.travel.services.reporter_renderer import _validate_reply

    assert _validate_reply("酒店 39 元", "酒店 399 元") == (
        "", "unsupported_numeric_claim",
    )


def test_reporter_cannot_surface_new_hotel_price_from_structured_snapshot(
        monkeypatch: pytest.MonkeyPatch) -> None:
    from backend.config import travel
    from backend.travel.services.reporter_renderer import render_travel_reply

    monkeypatch.setattr(travel, "TRAVEL_LLM_REPORTER_ENABLED", True)
    _patch_llm(monkeypatch, json.dumps({"reply": "这家酒店每晚 399 元。"}, ensure_ascii=False))
    answer, meta = render_travel_reply({
        "task_results": [{
            "task_id": "hotel-1", "type": "query_hotel", "status": "degraded",
            "data_status": "unavailable", "data": {"preview": [{"price": 399}]},
        }],
    }, "酒店查询已完成，详情见结果卡。")

    assert answer == "酒店查询已完成，详情见结果卡。"
    assert meta["fallback_reason"] == "unsupported_numeric_claim"


def test_reporter_can_summarize_exact_facts_from_successful_tool_results(
        monkeypatch: pytest.MonkeyPatch) -> None:
    from backend.config import travel
    from backend.travel.services.reporter_renderer import render_travel_reply

    monkeypatch.setattr(travel, "TRAVEL_LLM_REPORTER_ENABLED", True)
    reply = "工具结果中，星海酒店报价 399 元。"
    calls = _patch_llm(monkeypatch, json.dumps({"reply": reply}, ensure_ascii=False))
    answer, meta = render_travel_reply({
        "task_results": [{
            "task_id": "hotel-1", "type": "query_hotel", "status": "success",
            "data_status": "available", "result_count": 1,
            "data": {"preview": [{"name": "星海酒店", "price": 399}]},
        }],
        "degraded_tools": [{
            "tool": "weather.query", "note": "天气源暂不可用",
        }],
    }, "酒店查询已完成，详情见结果卡。")

    assert answer == reply
    assert meta["source"] == "llm"
    human_message = calls[0][2][0][1].content
    assert '"type": "query_hotel"' in human_message
    assert '"status": "success"' in human_message
    assert '"name": "星海酒店"' in human_message
    assert '"price": 399' in human_message
    assert '"tool": "weather.query"' in human_message
    assert '"note": "天气源暂不可用"' in human_message


def test_model_error_falls_back_without_retry(monkeypatch: pytest.MonkeyPatch) -> None:
    from backend.config import travel
    from backend.travel.services.reporter_renderer import render_travel_reply

    monkeypatch.setattr(travel, "TRAVEL_LLM_REPORTER_ENABLED", True)
    calls = _patch_llm(monkeypatch, TimeoutError("model timed out"))
    answer, meta = render_travel_reply({"user_message": "福州"}, "模板兜底")

    assert answer == "模板兜底"
    assert meta["source"] == "template"
    assert meta["fallback_reason"] == "llm_error"
    assert len(calls) == 1
