"""backend/config/rag.py 中 RAG_INDEX_REQUEST_MAX_TOKENS 配置校验测试。"""

import importlib
import pytest


def test_rag_index_request_max_tokens_default(monkeypatch):
    monkeypatch.delenv("RAG_INDEX_REQUEST_MAX_TOKENS", raising=False)
    import backend.config.rag as rag_config
    importlib.reload(rag_config)
    assert rag_config.RAG_INDEX_REQUEST_MAX_TOKENS == 5_000_000


def test_rag_index_request_max_tokens_valid_custom(monkeypatch):
    monkeypatch.setenv("RAG_INDEX_REQUEST_MAX_TOKENS", "8000000")
    import backend.config.rag as rag_config
    importlib.reload(rag_config)
    assert rag_config.RAG_INDEX_REQUEST_MAX_TOKENS == 8_000_000


def test_rag_index_request_max_tokens_rejects_negative_or_zero(monkeypatch):
    monkeypatch.setenv("RAG_INDEX_REQUEST_MAX_TOKENS", "0")
    import backend.config.rag as rag_config
    with pytest.raises(ValueError, match="必须为正整数"):
        importlib.reload(rag_config)


def test_rag_index_request_max_tokens_rejects_excessive(monkeypatch):
    monkeypatch.setenv("RAG_INDEX_REQUEST_MAX_TOKENS", "10000001")
    import backend.config.rag as rag_config
    with pytest.raises(ValueError, match="超出合理上限 10000000"):
        importlib.reload(rag_config)
