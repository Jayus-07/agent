"""云端 embedding 客户端构造约束。"""
from __future__ import annotations

import sys
from contextlib import contextmanager
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from backend.shared.processing_context import ProcessingBinding, bind_processing


def test_cloud_embedding_sets_bounded_request_timeout():
    """客服/RAG 请求必须把 embedding I/O 约束在明确的超时内。

    都用 DB（2026-09-21 拍板）：注入数据库专项绑定 + 供应商凭据构造客户端，
    不再依赖旧 EMBEDDING_API_KEY env 兜底。
    """
    from backend.rag import embedding_singleton

    captured: dict = {}

    class FakeOpenAIEmbeddings:
        def __init__(self, **kwargs):
            captured.update(kwargs)

    fake_module = SimpleNamespace(
        ChatOpenAI=object,
        OpenAIEmbeddings=FakeOpenAIEmbeddings,
    )
    fake_binding = SimpleNamespace(
        provider_id="dashscope-rag",
        adapter="dashscope_embedding",
        model_name="qwen3.7-text-embedding",
        upstream_name="text-embedding-v3",
        base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
        options={"dimensions": 1024},
    )
    with patch.object(
        embedding_singleton.specialized_mod,
        "resolve_binding",
        lambda _role: fake_binding,
    ):
        with patch.object(
            embedding_singleton.credentials_mod,
            "resolve_credentials",
            lambda provider_id, model_name=None: SimpleNamespace(api_key="sk-test"),
        ):
            with patch.dict(sys.modules, {"langchain_openai": fake_module}):
                embedding_singleton._get_cloud_embedding()

    # 运行时默认值为 8 秒（加 max_retries=1），这里只验证供应商请求
    # 被限制在客服总超时以内，避免把配置默认值硬编码进单元测试。
    assert 0 < captured["timeout"] <= 20
    assert captured["api_key"] == "sk-test"


def test_rebuild_timeout_override_does_not_change_online_default(monkeypatch):
    """离线请求可以等待长文本，后续在线客户端仍使用原有短超时。"""
    from backend.rag import embedding_singleton

    captured = []

    class FakeOpenAIEmbeddings:
        def __init__(self, **kwargs):
            keys = ("timeout", "chunk_size", "max_retries")
            captured.append({key: kwargs[key] for key in keys})

    monkeypatch.setitem(sys.modules, "langchain_openai", SimpleNamespace(
        ChatOpenAI=object, OpenAIEmbeddings=FakeOpenAIEmbeddings,
    ))
    config = {"model": "test", "api_key": "sk-test",
              "base_url": "https://example.com/v1", "dimensions": None}
    online_timeout = embedding_singleton.EMBEDDING_REQUEST_TIMEOUT
    embedding_singleton._get_cloud_embedding(
        config, request_timeout=60, batch_size=1, max_retries=0,
    )
    embedding_singleton._get_cloud_embedding(config)

    assert captured == [
        {"timeout": 60, "chunk_size": 1, "max_retries": 0},
        {"timeout": online_timeout,
         "chunk_size": embedding_singleton.EMBEDDING_BATCH_SIZE, "max_retries": 1},
    ]
    assert embedding_singleton.EMBEDDING_REQUEST_TIMEOUT == online_timeout


@pytest.mark.parametrize("timeout", [0, -1, float("nan"), float("inf")])
def test_embedding_timeout_rejects_unbounded_values(timeout):
    from backend.rag import embedding_singleton

    with pytest.raises(ValueError, match="有限正数"):
        embedding_singleton._get_cloud_embedding(
            {"api_key": "sk-test"}, request_timeout=timeout,
        )


@pytest.mark.parametrize("batch_size", [0, -1, True, 1.5])
def test_embedding_rebuild_batch_size_requires_positive_integer(batch_size):
    from backend.rag import embedding_singleton

    with pytest.raises(ValueError, match="正整数"):
        embedding_singleton._get_cloud_embedding(
            {"api_key": "sk-test"}, batch_size=batch_size,
        )


def test_embedding_usage_payload_contains_processing_binding(monkeypatch):
    from backend.rag import embedding_singleton

    recorded = []
    monkeypatch.setattr(
        "backend.observability.llm_usage_store.get_llm_usage_store",
        lambda: SimpleNamespace(record=recorded.append),
    )
    tracked = embedding_singleton._TrackedEmbedding.__new__(
        embedding_singleton._TrackedEmbedding
    )
    tracked._model_name = "text-embedding-test"
    tracked._provider = "test-provider"
    tracked._provider_id = "test-provider"

    binding = ProcessingBinding(
        run_id="run-embed", step_id="step-embed", role="embedding", stage="embedding"
    )
    with bind_processing(binding):
        tracked._record(12, 2, 8.0)

    assert recorded[0]["provider"] == "test-provider"
    assert recorded[0]["model"] == "text-embedding-test"
    assert recorded[0]["run_id"] == "run-embed"
    assert recorded[0]["step_id"] == "step-embed"
    assert recorded[0]["role"] == "embedding"
    assert recorded[0]["stage"] == "embedding"


def test_tracked_embedding_uses_specialized_provider_id(monkeypatch):
    from backend.rag import embedding_singleton
    from backend.infra.llm import pricing

    class FakeEmbedding:
        pass

    recorded = []
    priced = []
    monkeypatch.setattr(
        "backend.observability.llm_usage_store.get_llm_usage_store",
        lambda: SimpleNamespace(record=recorded.append),
    )
    monkeypatch.setattr(
        pricing,
        "price_usage",
        lambda model, role, usage, **kwargs: priced.append((model, role)) or None,
    )

    monkeypatch.setattr(
        embedding_singleton,
        "_embedding_runtime_signature",
        lambda: ("specialized", "embedding-provider"),
    )
    monkeypatch.setattr(embedding_singleton, "_embedding_is_cloud", lambda: True)
    monkeypatch.setattr(
        embedding_singleton.specialized_mod,
        "resolve_binding",
        lambda role: embedding_singleton.specialized_mod.SpecializedBinding(
            role="embedding",
            provider_id="embedding-provider",
            adapter="openai",
            model_name="registered-embedding",
            base_url="https://example.com/v1",
        )
        if role == "embedding"
        else None,
    )
    monkeypatch.setattr(
        embedding_singleton,
        "_get_cloud_embedding",
        lambda: FakeEmbedding(),
    )

    tracked = embedding_singleton._TrackedEmbedding(FakeEmbedding(), tracker=None)

    assert tracked._provider == "cloud"
    assert tracked._provider_id == "embedding-provider"
    assert tracked._model_name == "registered-embedding"
    assert tracked._index_fingerprint
    tracked._record(12, 1, 2.0)

    assert priced == [("registered-embedding", "embedding")]
    assert recorded[0]["provider"] == "embedding-provider"
    assert recorded[0]["model"] == "registered-embedding"


def test_cloud_documents_reserve_and_record_each_provider_subbatch(monkeypatch):
    """按 inner.chunk_size 的真实出站批次分别预占和结算，并保持输入顺序。"""
    from backend.rag import embedding_singleton

    events = []

    class FakeEmbedding:
        chunk_size = 2

        def embed_documents(self, texts):
            events.append(("call", list(texts)))
            return [[float(len(text))] for text in texts]

    tracked = embedding_singleton._TrackedEmbedding.__new__(
        embedding_singleton._TrackedEmbedding,
    )
    tracked._inner = FakeEmbedding()
    tracked._provider = "cloud"
    tracked._model_name = "registered-embedding"
    tracked.embed_batch_size = 10
    monkeypatch.setattr(tracked, "_refresh_if_changed", lambda: None)

    @contextmanager
    def model_call_scope():
        events.append(("reserve", tracked._model_name))
        yield
        events.append(("scope-exit", tracked._model_name))

    monkeypatch.setattr(tracked, "_model_call_scope", model_call_scope)
    monkeypatch.setattr(
        tracked,
        "_record",
        lambda tokens, count, _duration, status="success": events.append(
            ("settle", tokens, count, status)
        ),
    )
    monkeypatch.setattr(
        tracked,
        "_emit_call",
        lambda kind, _duration, status="success": events.append(
            ("emit", kind, status)
        ),
    )
    monkeypatch.setattr(
        tracked,
        "_account_documents",
        lambda count, _duration: events.append(("account", count)),
    )

    result = tracked.embed_documents(["a", "bb", "ccc", "dddd", "eeeee"])

    assert result == [[1.0], [2.0], [3.0], [4.0], [5.0]]
    assert [event[1] for event in events if event[0] == "call"] == [
        ["a", "bb"], ["ccc", "dddd"], ["eeeee"],
    ]
    assert sum(event[0] == "reserve" for event in events) == 3
    assert [event[1:] for event in events if event[0] == "settle"] == [
        (1, 2, "success"), (2, 2, "success"), (1, 1, "success"),
    ]
    assert sum(event[0] == "scope-exit" for event in events) == 3
    assert [event[1] for event in events if event[0] == "account"] == [2, 2, 1]
