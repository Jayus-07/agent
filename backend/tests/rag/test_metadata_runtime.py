"""元数据并发、缓存与幂等运行时测试。"""

import asyncio

import pytest

from backend.rag.preprocessing import metadata_runtime as runtime
from backend.rag.preprocessing.metadata_schema import DecisionEnvelope


@pytest.mark.asyncio
async def test_llm_limiter_never_exceeds_configured_width(monkeypatch):
    monkeypatch.setattr("backend.config.rag.METADATA_LLM_CONCURRENCY", 2)
    active = 0
    peak = 0

    async def work():
        nonlocal active, peak
        active += 1
        peak = max(peak, active)
        await asyncio.sleep(0.01)
        active -= 1

    await asyncio.gather(*(runtime.run_limited("llm", work) for _ in range(8)))

    assert peak <= 2


def test_metadata_cache_key_changes_with_model_and_rule_versions():
    key1 = runtime.metadata_cache_key("text", "a.docx", "", "tax-v1", "model-a", "prompt-2")
    key2 = runtime.metadata_cache_key("text", "a.docx", "", "tax-v1", "model-b", "prompt-2")
    key3 = runtime.metadata_cache_key("text", "a.docx", "", "tax-v1", "model-a", "prompt-3")

    assert key1 != key2
    assert key1 != key3


def test_idempotency_key_changes_with_rule_version():
    key1 = runtime.idempotency_key("file-hash", "tax-v1", "rules-v1", "model-v1")
    key2 = runtime.idempotency_key("file-hash", "tax-v1", "rules-v2", "model-v1")

    assert key1 != key2


@pytest.mark.asyncio
async def test_resource_wait_timeout_is_typed(monkeypatch):
    monkeypatch.setattr("backend.config.rag.METADATA_DB_CONCURRENCY", 1)
    limiters = runtime.get_metadata_limiters()
    await limiters.db.acquire()
    try:
        with pytest.raises(runtime.MetadataResourceTimeout):
            await runtime.run_limited("db", lambda: asyncio.sleep(0), timeout=0.001)
    finally:
        limiters.db.release()


def test_cache_round_trip_validates_decision_envelope(monkeypatch):
    class _Cache:
        def __init__(self):
            self.values = {}

        def get_json(self, key):
            return self.values.get(key)

        def set_json(self, key, value, ttl=None):
            self.values[key] = value

    cache = _Cache()
    monkeypatch.setattr(runtime, "_metadata_cache", cache)
    envelope = DecisionEnvelope(
        decision="accepted", doc_type="legal", source="r0", confidence=0.99
    )
    runtime.put_cached_decision("k", envelope)
    loaded = runtime.get_cached_decision("k")

    assert loaded is not None
    assert loaded.doc_type == "legal"
    assert loaded.source == "r0"


def test_stale_version_is_a_cache_miss(monkeypatch):
    class _Cache:
        def __init__(self):
            self.values = {}

        def get_json(self, key):
            return self.values.get(key)

        def set_json(self, key, value, ttl=None):
            self.values[key] = value

    cache = _Cache()
    monkeypatch.setattr(runtime, "_metadata_cache", cache)
    envelope = DecisionEnvelope(
        decision="accepted", doc_type="legal", source="r0", confidence=0.99
    )
    key_v1 = runtime.metadata_cache_key(
        "text", "a.docx", "", "tax-v1", "model-v1", "prompt-v1", "rules-v1"
    )
    key_v2 = runtime.metadata_cache_key(
        "text", "a.docx", "", "tax-v1", "model-v1", "prompt-v1", "rules-v2"
    )
    runtime.put_cached_decision(key_v1, envelope)

    assert runtime.get_cached_decision(key_v1) is not None
    assert runtime.get_cached_decision(key_v2) is None
