"""STOP CS-A P0-4 回归：RAG 答案缓存 v2（结构化记录 + 旧格式 miss）。"""
from __future__ import annotations

import pytest

from backend.rag.answer_cache import (
    CACHE_SCHEMA_VERSION,
    AnswerCache,
    _coerce_record,
    get_answer_cache,
)


class _FakeCache:
    """get_json/set_json 内存桩。"""

    def __init__(self):
        self.store: dict = {}
        self.versions: dict = {}

    def get_json(self, key):
        return self.store.get(key)

    def set_json(self, key, value, ttl=None):
        self.store[key] = value

    def incr_version(self, name):
        self.versions[name] = self.versions.get(name, 0) + 1
        return self.versions[name]


@pytest.fixture
def cache(monkeypatch):
    c = AnswerCache(ttl=60)
    fake = _FakeCache()
    monkeypatch.setattr(c, "_get_cache", lambda: fake)
    c._fake = fake
    return c


class TestSchemaV2:

    def test_put_stores_structured_record(self, cache):
        meta = {"confidence": 0.9, "can_answer": True}
        cache.put("退货政策是什么", "cs_faq", {}, "m1", "答案", meta=meta)
        ((key, value),) = cache._fake.store.items()
        assert value["schema_version"] == CACHE_SCHEMA_VERSION == 2
        assert value["answer"] == "答案"
        assert value["meta"]["confidence"] == 0.9
        assert value["created_at"]

    def test_get_returns_record_with_meta(self, cache):
        meta = {"confidence": 0.8, "can_answer": True, "sources": ["d1"]}
        cache.put("q", "kb", {}, "m1", "答", meta=meta)
        record = cache.get("q", "kb", {}, "m1")
        assert record is not None
        assert record["answer"] == "答"
        assert record["meta"] == meta

    def test_v1_string_value_treated_as_miss(self, cache):
        """v1 裸字符串缓存 = miss（绝不把无 meta 的旧答案升级成可信结果）。"""
        key = cache._build_key("legacy", "kb", {}, "m1")
        cache._fake.store[key] = "旧版裸答案"
        assert cache.get("legacy", "kb", {}, "m1") is None

    def test_wrong_schema_version_treated_as_miss(self, cache):
        key = cache._build_key("future", "kb", {}, "m1")
        cache._fake.store[key] = {
            "schema_version": 99, "answer": "x", "meta": {}, "created_at": "",
        }
        assert cache.get("future", "kb", {}, "m1") is None

    def test_key_namespace_v2_differs_from_v1_layout(self, cache):
        """v2 key 与旧布局物理隔离（raw 前缀带 v2 namespace）。"""
        import hashlib

        normalized = "q".strip().lower()
        raw_v1 = f"{normalized}|kb|0|empty|m1|"
        legacy_key = hashlib.sha256(raw_v1.encode()).hexdigest()
        v2_key = cache._build_key("q", "kb", {}, "m1")
        assert v2_key != legacy_key

    def test_coerce_record_rejects_malformed(self):
        assert _coerce_record(None) is None
        assert _coerce_record("str") is None
        assert _coerce_record({"schema_version": 2}) is None  # 缺 answer
        assert _coerce_record(
            {"schema_version": 2, "answer": "a", "meta": "not-dict"},
        )["meta"] == {}


class TestGateSemanticsPreserved:
    """缓存命中不绕过 Evidence Gate：meta 原样回放，低置信仍走 CAUTIOUS/REFUSE。"""

    def test_low_confidence_meta_roundtrip(self, cache):
        meta = {"confidence": 0.5, "can_answer": False}
        cache.put("q", "kb", {}, "m1", "答", meta=meta)
        record = cache.get("q", "kb", {}, "m1")
        # CS Knowledge Gate 读取 meta.confidence/can_answer —— 缓存命中
        # 后门禁仍按 0.5/can_answer=False 判 REFUSE（语义与首算一致）
        assert record["meta"]["confidence"] == 0.5
        assert record["meta"]["can_answer"] is False


class TestSingleton:
    def test_get_answer_cache_singleton(self):
        assert get_answer_cache() is get_answer_cache()
