"""云端 embedding 客户端构造约束。"""
from __future__ import annotations

import sys
from types import SimpleNamespace
from unittest.mock import patch


def test_cloud_embedding_sets_bounded_request_timeout():
    """客服/RAG 请求必须把 embedding I/O 约束在明确的超时内。"""
    from backend.rag import embedding_singleton

    captured: dict = {}

    class FakeOpenAIEmbeddings:
        def __init__(self, **kwargs):
            captured.update(kwargs)

    fake_module = SimpleNamespace(
        ChatOpenAI=object,
        OpenAIEmbeddings=FakeOpenAIEmbeddings,
    )
    with patch.object(embedding_singleton, "EMBEDDING_API_KEY", "test-key"):
        with patch.dict(sys.modules, {"langchain_openai": fake_module}):
            embedding_singleton._get_cloud_embedding()

    assert captured["timeout"] == 20
