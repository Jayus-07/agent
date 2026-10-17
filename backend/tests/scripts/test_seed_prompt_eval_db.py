"""Prompt 评测 CI 模型绑定配置的回归测试。"""
from __future__ import annotations

import pytest

from backend.scripts import seed_prompt_eval_db as seed_script


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

    configs = seed_script.build_seed_configs()

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
    # 模拟 pricing 在 seed 前导入并缓存了另一份 MEMORY_DB_CONFIG。
    monkeypatch.setenv("MEMORY_PGDATABASE", "agent_memory")
    import backend.infra.llm.pricing  # noqa: F401
    monkeypatch.delenv("MEMORY_PGDATABASE")

    values = {
        "PGHOST": "localhost",
        "PGPORT": "5432",
        "PGUSER": "postgres",
        "PGPASSWORD": "test-password",
        "PROMPT_EVAL_TEST_PGDATABASE": "agent_memory_test",
        "PROMPT_EVAL_TEST_PROVIDER_ID": "eval-chat",
        "PROMPT_EVAL_TEST_MODEL": "chat-model",
        "PROMPT_EVAL_TEST_BASE_URL": "https://chat.example/v1",
        "PROMPT_EVAL_TEST_API_KEY": "chat-key",
        "PROMPT_EVAL_TEST_EMBEDDING_PROVIDER_ID": "eval-embedding",
        "PROMPT_EVAL_TEST_EMBEDDING_MODEL": "embedding-model",
        "PROMPT_EVAL_TEST_EMBEDDING_BASE_URL": "https://embedding.example/v1",
        "PROMPT_EVAL_TEST_EMBEDDING_API_KEY": "embedding-key",
        "PROMPT_EVAL_TEST_RERANK_PROVIDER_ID": "eval-rerank",
        "PROMPT_EVAL_TEST_RERANK_MODEL": "rerank-model",
        "PROMPT_EVAL_TEST_RERANK_BASE_URL": "https://rerank.example/v1",
        "PROMPT_EVAL_TEST_RERANK_API_KEY": "rerank-key",
    }
    for name, value in values.items():
        monkeypatch.setenv(name, value)

    class Result:
        def mappings(self):
            return self

        def one(self):
            return {
                "database": "agent_memory_test",
                "username": "postgres",
                "server_address": "127.0.0.1",
                "server_port": 5432,
                "read_only": "off",
                "in_recovery": False,
            }

        def first(self):
            return None

    class Session:
        def __init__(self):
            self.commit_count = 0
            self.statements = []

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def execute(self, statement, params=None):
            self.statements.append((str(statement), params or {}))
            return Result()

        async def commit(self):
            self.commit_count += 1

    class Engine:
        async def dispose(self):
            return None

    session = Session()
    engine = Engine()
    urls = []

    def create_engine(url, **_kwargs):
        urls.append(url)
        return engine

    monkeypatch.setattr(seed_script, "create_async_engine", create_engine)
    monkeypatch.setattr(seed_script, "AsyncSession", lambda *_args, **_kwargs: session)
    monkeypatch.setattr("backend.shared.crypto.encrypt_secret", lambda value: f"enc:{value}")
    monkeypatch.setattr("backend.shared.crypto.fingerprint", lambda value: "fingerprint")
    monkeypatch.setattr("backend.shared.crypto.last4", lambda value: "last4")

    await seed_script.seed()

    assert urls[0].database == "agent_memory_test"
    assert urls[0].username == "postgres"
    assert session.commit_count == 1
    role_bindings = [
        params
        for statement, params in session.statements
        if "INSERT INTO llm_model_role_bindings" in statement
    ]
    assert role_bindings == [
        {"role": "eval_gen", "model_name": "chat-model"},
        {"role": "embedding", "model_name": "embedding-model"},
        {"role": "rerank", "model_name": "rerank-model"},
    ]


@pytest.mark.asyncio
async def test_conflicting_memory_database_is_rejected_before_engine_creation(monkeypatch):
    monkeypatch.setenv("PROMPT_EVAL_TEST_PGDATABASE", "agent_memory_test")
    monkeypatch.setenv("MEMORY_PGDATABASE", "agent_memory")
    monkeypatch.setenv("PGUSER", "postgres")
    monkeypatch.setenv("PGPASSWORD", "test-password")
    engine_created = False

    def create_engine(*_args, **_kwargs):
        nonlocal engine_created
        engine_created = True
        raise AssertionError("冲突配置下不应创建数据库连接")

    monkeypatch.setattr(seed_script, "create_async_engine", create_engine)

    with pytest.raises(RuntimeError, match="冲突；拒绝写入"):
        await seed_script.seed()

    assert engine_created is False


@pytest.mark.parametrize(
    "database", ["agent_memory", "production", "agent_memory_test"]
)
def test_test_database_target_rejects_non_test_database_or_missing_role(
    monkeypatch, database
):
    monkeypatch.delenv("MEMORY_PGDATABASE", raising=False)
    monkeypatch.setenv("PROMPT_EVAL_TEST_PGDATABASE", database)
    monkeypatch.setenv("PGUSER", "postgres")
    monkeypatch.setenv("PGPASSWORD", "test-password")
    monkeypatch.setenv("PGHOST", "localhost")
    if database == "agent_memory_test":
        monkeypatch.delenv("PGUSER")
        monkeypatch.delenv("PROMPT_EVAL_TEST_PGUSER", raising=False)
        with pytest.raises(RuntimeError, match="连接角色和密码"):
            seed_script._require_test_database()
    else:
        with pytest.raises(RuntimeError, match="测试库"):
            seed_script._require_test_database()


def test_test_database_target_rejects_remote_host_even_when_named_test(monkeypatch):
    monkeypatch.delenv("MEMORY_PGDATABASE", raising=False)
    monkeypatch.setenv("PROMPT_EVAL_TEST_PGDATABASE", "agent_memory_test")
    monkeypatch.setenv("PGUSER", "postgres")
    monkeypatch.setenv("PGPASSWORD", "test-password")
    monkeypatch.setenv("PGHOST", "10.0.0.8")

    with pytest.raises(RuntimeError, match="只允许连接本机隔离 PostgreSQL"):
        seed_script._require_test_database()


def test_database_identity_accepts_explicit_readonly_verification_session():
    target = {
        "host": "127.0.0.1", "port": 5432,
        "database": "agent_memory_test", "username": "test-role",
        "password": "test-password",
    }
    identity = {
        "database": "agent_memory_test",
        "username": "test-role",
        "server_address": "127.0.0.1/32",
        "server_port": 5432,
        "read_only": "on",
        "in_recovery": False,
    }

    seed_script._assert_database_identity(identity, target, read_only=True)
    with pytest.raises(RuntimeError, match="身份与显式目标不一致"):
        seed_script._assert_database_identity(identity, target)
