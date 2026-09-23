"""Model Governance STOP C —— Fallback / Retry usage（C11/C12）。

每次真实 Provider attempt 都有独立留痕；retry（同模型）与
fallback（换模型）可区分；fallback 接管成功也有计数。
"""
from __future__ import annotations

from unittest.mock import patch

import pytest

from backend.infra.llm import proxy as proxy_mod


class _CaptureStore:
    """捕获 record() 事件的假 store（只 mock 外部 DB 边界）。"""

    def __init__(self):
        self.events = []

    def record(self, event):
        self.events.append(dict(event))
        return True


@pytest.fixture()
def capture_store(monkeypatch):
    store = _CaptureStore()
    import backend.observability.llm_usage_store as storeapi

    monkeypatch.setattr(storeapi, "get_llm_usage_store", lambda: store)
    yield store


def _err():
    return RuntimeError("boom provider")


def test_failed_attempt_recorded_as_primary(capture_store):
    """首次 attempt 失败 → decision=primary 留痕行（此前完全消失）。"""
    proxy_mod._record_failed_attempt("doubao-seed-2.0-mini", _err(),
                                     decision="primary", duration_ms=123.4)
    assert len(capture_store.events) == 1
    row = capture_store.events[0]
    assert row["model"] == "doubao-seed-2.0-mini"
    assert row["decision"] == "primary"
    assert row["finish_reason"].startswith("error:RuntimeError")
    assert row["binding_source"] == "failed_attempt"
    assert row["total_tokens"] == 0
    assert row["duration_ms"] == 123.4


def test_retry_attempt_decision_distinct(capture_store):
    """retry attempt 与 primary 决策可区分（C12）。"""
    proxy_mod._record_failed_attempt("doubao-seed-2.0-mini", _err(),
                                     decision="retry")
    assert capture_store.events[0]["decision"] == "retry"


def test_fallback_attempt_recorded(capture_store):
    """fallback 也失败的 attempt 有留痕（双 attempt 场景的第二次失败）。"""
    proxy_mod._record_failed_attempt("qwen3.8-flash", _err(),
                                     decision="fallback")
    row = capture_store.events[0]
    assert row["model"] == "qwen3.8-flash"
    assert row["decision"] == "fallback"


def test_failed_attempt_provider_resolved_from_registry(capture_store):
    """失败行 provider 按 canonical 双匹配直查（不是 ollama 兜底）。"""
    from backend.infra.llm import models
    from backend.tests.infra.test_model_registry import (
        GOVERNANCE_MODELS,
        GOVERNANCE_PROVIDERS,
    )

    models.reset_dynamic_models_for_tests()
    models.set_dynamic_models([dict(m) for m in GOVERNANCE_MODELS])
    models.set_dynamic_providers([dict(p) for p in GOVERNANCE_PROVIDERS])
    try:
        proxy_mod._record_failed_attempt("doubao-seed-2.0-mini", _err())
        assert capture_store.events[0]["provider"] == (
            "custom-doubao-seed-2-0-mini")
    finally:
        models.reset_dynamic_models_for_tests()


def test_failed_attempt_never_raises(capture_store, monkeypatch):
    """留痕失败（store 炸裂）不影响韧性链本身。"""
    def _boom(event):
        raise RuntimeError("store down")

    monkeypatch.setattr(capture_store, "record", _boom)
    proxy_mod._record_failed_attempt("some-model", _err())
    # 无异常抛出即通过


def test_resilience_records_each_failed_attempt(capture_store, monkeypatch):
    """端到端：重试耗尽 → 每次 attempt 各留一行 + 终态异常抛出。"""
    monkeypatch.setattr(proxy_mod, "LLM_MAX_RETRIES", 1)
    monkeypatch.setattr(proxy_mod, "_is_transient", lambda e: True)
    monkeypatch.setattr(proxy_mod, "_handle_terminal_failure",
                        lambda e, args, kwargs: (_ for _ in ()).throw(e))
    monkeypatch.setattr(proxy_mod, "_notify_degradation", lambda *a, **k: None)
    import backend.infra.circuit_breaker as cb

    monkeypatch.setattr(cb.llm_circuit_breaker, "call",
                        lambda fn, *a, **k: (_ for _ in ()).throw(_err()))

    def _sleep(_):
        return None

    monkeypatch.setattr(proxy_mod.time, "sleep", _sleep)
    with pytest.raises(RuntimeError):
        proxy_mod._call_with_resilience(lambda *a, **k: "ok")
    decisions = [e["decision"] for e in capture_store.events]
    assert decisions == ["primary", "retry"]
