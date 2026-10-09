"""统一旅行轮次决策 LLM 的 schema、计量与失败回退测试。"""
from __future__ import annotations

import json
from types import SimpleNamespace

from backend.travel.services import turn_decision_service as service


def _enable_llm(monkeypatch, content: str):
    from backend.config import model_roles, travel as travel_config

    monkeypatch.setattr(travel_config, "TRAVEL_TURN_DECISION_LLM_ENABLED", True)
    monkeypatch.setattr(model_roles, "resolve_effective", lambda _role: {
        "role": "main", "value": "test-main-model",
    })

    calls = []
    fake_response = SimpleNamespace(
        content=content,
        usage_metadata={"input_tokens": 11, "output_tokens": 7},
        response_metadata={"model_name": "test-main-model"},
    )
    fake_llm = SimpleNamespace(invoke=lambda messages: calls.append(messages) or fake_response)
    monkeypatch.setattr(
        "backend.infra.llm.proxy._build_llm_for",
        lambda _name: SimpleNamespace(bind=lambda **_kwargs: fake_llm),
    )
    monkeypatch.setattr(
        "backend.prompts.service.prompt_service.render_sync",
        lambda _key: SimpleNamespace(text="受约束的测试 Prompt", version="v-test"),
    )
    recorded = []
    monkeypatch.setattr(
        "backend.infra.llm.proxy.record_llm_result",
        lambda *args, **kwargs: recorded.append((args, kwargs)),
    )
    return calls, recorded


def test_valid_decision_is_parsed_and_accounted_once(monkeypatch):
    payload = {
        "primary_action": "modify_plan",
        "changes": [{
            "op": "set_pace", "scope": {"day_index": 2},
            "value": "relaxed", "evidence_text": "第二天别太累",
        }],
        "additional_tasks": [{
            "task_id": "weather-1", "type": "query_weather",
            "params": {"city": "厦门"},
        }],
        "needs_clarification": False,
        "missing_fields": [],
        "confidence": 0.91,
    }
    calls, recorded = _enable_llm(monkeypatch, json.dumps(payload, ensure_ascii=False))

    outcome = service.interpret_turn_with_llm(
        "第二天别太累，顺便查天气", context={"has_itinerary": True},
    )

    assert outcome.status == "ok"
    assert outcome.decision.primary_action == "modify_plan"
    assert outcome.decision.additional_tasks[0].params.city == "厦门"
    assert outcome.prompt_version == "travel.turn_decision@v-test"
    assert outcome.meta()["input_tokens"] == 11
    assert outcome.meta()["output_tokens"] == 7
    assert len(calls) == 1
    assert len(recorded) == 1


def test_invalid_or_privileged_output_returns_no_decision(monkeypatch):
    calls, _ = _enable_llm(
        monkeypatch,
        '{"primary_action":"execute_tool","tool":"dangerous"}',
    )

    outcome = service.interpret_turn_with_llm("改一下行程", context={})

    assert outcome.decision is None
    assert outcome.status == "schema_invalid"
    assert len(calls) == 1
