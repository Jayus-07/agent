"""Prompt 评测 CI 模型绑定配置的回归测试。"""
from __future__ import annotations

import pytest

from backend.scripts.seed_prompt_eval_db import build_seed_configs, seed


def test_build_seed_configs_requires_and_returns_chat_and_embedding_bindings(monkeypatch):
    values = {
        "PROMPT_EVAL_TEST_PROVIDER_ID": "eval-chat",
        "PROMPT_EVAL_TEST_MODEL": "chat-model",
        "PROMPT_EVAL_TEST_BASE_URL": "https://chat.example/v1",
        "PROMPT_EVAL_TEST_API_KEY": "chat-key",
        "PROMPT_EVAL_TEST_EMBEDDING_PROVIDER_ID": "eval-embedding",
        "PROMPT_EVAL_TEST_EMBEDDING_MODEL": "embedding-model",
        "PROMPT_EVAL_TEST_EMBEDDING_BASE_URL": "https://embedding.example/v1",
        "PROMPT_EVAL_TEST_EMBEDDING_API_KEY": "embedding-key",
    }
    for name, value in values.items():
        monkeypatch.setenv(name, value)

    configs = build_seed_configs()

    assert configs["eval_gen"] == {
        "provider_id": "eval-chat",
        "model_name": "chat-model",
        "base_url": "https://chat.example/v1",
        "api_key": "chat-key",
    }
    assert configs["embedding"] == {
        "provider_id": "eval-embedding",
        "model_name": "embedding-model",
        "base_url": "https://embedding.example/v1",
        "api_key": "embedding-key",
        "adapter": "openai_embedding",
        "dimensions": 1024,
    }


@pytest.mark.asyncio
async def test_seed_commits_model_bindings_before_closing_session(monkeypatch):
    values = {
        "PGHOST": "localhost",
        "PROMPT_EVAL_TEST_PGDATABASE": "agent_memory_test",
        "PROMPT_EVAL_TEST_PROVIDER_ID": "eval-chat",
        "PROMPT_EVAL_TEST_MODEL": "chat-model",
        "PROMPT_EVAL_TEST_BASE_URL": "https://chat.example/v1",
        "PROMPT_EVAL_TEST_API_KEY": "chat-key",
        "PROMPT_EVAL_TEST_EMBEDDING_PROVIDER_ID": "eval-embedding",
        "PROMPT_EVAL_TEST_EMBEDDING_MODEL": "embedding-model",
        "PROMPT_EVAL_TEST_EMBEDDING_BASE_URL": "https://embedding.example/v1",
        "PROMPT_EVAL_TEST_EMBEDDING_API_KEY": "embedding-key",
    }
    for name, value in values.items():
        monkeypatch.setenv(name, value)

    class Session:
        def __init__(self):
            self.commit_count = 0

        async def execute(self, _statement, _params=None):
            return None

        async def commit(self):
            self.commit_count += 1

    session = Session()

    async def session_stream():
        yield session

    import backend.memory.database as database

    monkeypatch.setattr(database, "get_session", lambda: session_stream())
    monkeypatch.setattr("backend.shared.crypto.encrypt_secret", lambda value: f"enc:{value}")
    monkeypatch.setattr("backend.shared.crypto.fingerprint", lambda value: "fingerprint")
    monkeypatch.setattr("backend.shared.crypto.last4", lambda value: "last4")

    await seed()

    assert session.commit_count == 1
