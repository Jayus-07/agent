"""云端 embedding 客户端构造约束。"""
from __future__ import annotations

import sys
from types import SimpleNamespace
from unittest.mock import patch

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

    assert captured["timeout"] == 20
    assert captured["api_key"] == "sk-test"


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

    binding = ProcessingBinding(
        run_id="run-embed", step_id="step-embed", role="embedding", stage="embedding"
    )
    with bind_processing(binding):
        tracked._record(12, 2, 8.0)

    assert recorded[0]["run_id"] == "run-embed"
    assert recorded[0]["step_id"] == "step-embed"
    assert recorded[0]["role"] == "embedding"
    assert recorded[0]["stage"] == "embedding"
