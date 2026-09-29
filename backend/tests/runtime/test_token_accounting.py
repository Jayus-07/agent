# -*- coding: utf-8 -*-
"""STOP D：LLM、Embedding、Rerank 用量与成本归因的可验证契约。"""

import json
from types import SimpleNamespace

import pytest

from backend.infra.llm import proxy
from backend.infra.token_tracker import TokenTracker, TokenUsageEvent
from backend.observability.usage_parse import parse_provider_usage
from backend.orchestration.graph.events import summarize_turn_usage


@pytest.fixture(autouse=True)
def _usage_isolation():
    proxy.reset_turn_usage()
    yield
    proxy.reset_turn_usage()


@pytest.mark.parametrize(
    ("result", "expected"),
    [
        (SimpleNamespace(response_metadata={"token_usage": {
            "prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15,
        }}), {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15,
              "cached_tokens": 0, "reasoning_tokens": 0}),
        (SimpleNamespace(usage_metadata={
            "input_tokens": 8, "output_tokens": 4, "total_tokens": 12,
        }), {"prompt_tokens": 8, "completion_tokens": 4, "total_tokens": 12,
              "cached_tokens": 0, "reasoning_tokens": 0}),
        (SimpleNamespace(usage_metadata={
            "prompt_tokens": 20, "completion_tokens": 7, "total_tokens": 27,
            "input_token_details": {"cache_read": 6},
            "output_token_details": {"reasoning": 3},
        }), {"prompt_tokens": 20, "completion_tokens": 7, "total_tokens": 27,
              "cached_tokens": 6, "reasoning_tokens": 3}),
        (SimpleNamespace(response_metadata={}), {}),
    ],
)
def test_provider_usage_normalization(result, expected):
    assert parse_provider_usage(result) == expected


def test_proxy_record_tokens_updates_last_call_and_turn(monkeypatch):
    monkeypatch.setattr(proxy, "_get_provider_for", lambda _model: "test-provider")
    result = SimpleNamespace(
        usage_metadata={
            "input_tokens": 20, "output_tokens": 7, "total_tokens": 27,
            "input_token_details": {"cache_read": 6},
            "output_token_details": {"reasoning": 3},
        },
        response_metadata={"model_name": "test-model", "finish_reason": "stop"},
    )
    proxy._record_tokens(result, duration_ms=4, model_name="test-model")
    assert proxy._last_tokens_var.get()["total_tokens"] == 27
    usage = proxy.get_turn_usage()["test-model"]
    assert usage["prompt_tokens"] == 20
    assert usage["cached_tokens"] == 6
    assert usage["reasoning_tokens"] == 3
    assert usage["calls"] == 1


def test_proxy_missing_usage_is_explicitly_empty():
    proxy._record_tokens(SimpleNamespace(response_metadata={}), model_name="test-model")
    assert proxy._last_tokens_var.get() == {}
    assert proxy.get_turn_usage() == {}


def test_turn_summary_aggregates_multiple_model_calls(monkeypatch):
    monkeypatch.setattr(proxy, "_get_provider_for", lambda _model: "test-provider")
    for model, prompt, completion in (("model-a", 3, 2), ("model-a", 4, 1), ("model-b", 5, 5)):
        proxy._record_tokens(
            SimpleNamespace(usage_metadata={
                "input_tokens": prompt, "output_tokens": completion,
                "total_tokens": prompt + completion,
            }),
            model_name=model,
        )
    summary = summarize_turn_usage()
    assert summary["calls"] == 3
    assert summary["prompt_tokens"] == 12
    assert summary["completion_tokens"] == 8
    assert summary["models"]["model-a"]["calls"] == 2


def test_token_usage_event_is_json_serializable_with_identity_fields():
    event = TokenUsageEvent(
        component="llm", model_name="model-a", backend="cloud",
        prompt_tokens=3, completion_tokens=2, total_tokens=5,
        token_usage_available=True, duration_ms=12.5, status="success",
        trace_id="trace-1", request_id="request-1", user_id="user-1",
        tenant_id="tenant-1", role="planner", stage="main",
    )
    payload = json.loads(event.to_json())
    assert payload["total_tokens"] == 5
    assert payload["request_id"] == "request-1"
    assert payload["role"] == "planner"


def _tracker(tmp_path, component="embedding"):
    tracker = TokenTracker(
        component=component, log_path=str(tmp_path / f"{component}.jsonl"),
        model_name="embed-test", backend="cloud", trace_id="trace-1",
    )
    tracker._write_sqlite = lambda _event: None
    tracker._record_prometheus = lambda _event: None
    return tracker


def test_embedding_tracker_records_usage(monkeypatch, tmp_path):
    monkeypatch.setattr("backend.infra.llm.budget.reserve_model_call", lambda *a, **k: None)
    monkeypatch.setattr("backend.infra.llm.budget.record_model_usage", lambda *a, **k: None)
    tracker = _tracker(tmp_path, "embedding")

    class UsageTracker(TokenTracker):
        def _extract_usage(self, _result):
            return {"prompt_tokens": 11, "completion_tokens": 0, "total_tokens": 11}

    tracker.__class__ = UsageTracker
    assert tracker.track(lambda: "vectors")() == "vectors"
    row = json.loads((tmp_path / "embedding.jsonl").read_text(encoding="utf-8"))
    assert row["component"] == "embedding"
    assert row["total_tokens"] == 11
    assert row["token_usage_available"] is True


def test_rerank_tracker_records_local_missing_usage(monkeypatch, tmp_path):
    monkeypatch.setattr("backend.infra.llm.budget.reserve_model_call", lambda *a, **k: None)
    monkeypatch.setattr("backend.infra.llm.budget.record_model_usage", lambda *a, **k: None)
    tracker = _tracker(tmp_path, "rerank")
    tracker.backend = "local"
    tracker.track(lambda: ["doc-1"])()
    row = json.loads((tmp_path / "rerank.jsonl").read_text(encoding="utf-8"))
    assert row["component"] == "rerank"
    assert row["total_tokens"] is None
    assert row["token_usage_available"] is False


def test_tracker_error_is_recorded_and_exception_propagates(monkeypatch, tmp_path):
    monkeypatch.setattr("backend.infra.llm.budget.reserve_model_call", lambda *a, **k: None)
    monkeypatch.setattr("backend.infra.llm.budget.record_model_usage", lambda *a, **k: None)
    tracker = _tracker(tmp_path, "embedding")

    def fail():
        raise RuntimeError("provider down")

    with pytest.raises(RuntimeError, match="provider down"):
        tracker.track(fail)()
    row = json.loads((tmp_path / "embedding.jsonl").read_text(encoding="utf-8"))
    assert row["status"] == "error"
    assert row["error"] == "provider down"


def test_tracker_rejects_unsupported_component(tmp_path):
    with pytest.raises(ValueError, match="embedding"):
        TokenTracker("summary", str(tmp_path / "summary.jsonl"))
