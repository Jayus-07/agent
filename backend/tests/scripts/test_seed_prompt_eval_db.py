"""Prompt 评测 CI 模型绑定配置的回归测试。"""
from __future__ import annotations

import secrets

import pytest

from backend.scripts import seed_prompt_eval_db as seed_script


def _generated_secret(name: str) -> str:
    """运行时生成测试假密钥：测试只要求进程内一致，不依赖具体值。

    硬编码凭据治理（2026-10-25）：源码不落凭据字面量（安全扫描按字面量
    拦截），运行时生成即无泄漏面。
    """
    return f"test-{name}-{secrets.token_urlsafe(9)}"


PG_TEST_PASSWORD = _generated_secret("pg")
CHAT_API_KEY = _generated_secret("chat")
EMBEDDING_API_KEY = _generated_secret("embedding")
RERANK_API_KEY = _generated_secret("rerank")


def test_build_seed_configs_requires_and_returns_chat_and_embedding_bindings(monkeypatch):
    values = {
        "PROMPT_EVAL_TEST_PROVIDER_ID": "eval-chat",
        "PROMPT_EVAL_TEST_MODEL": "chat-model",
        "PROMPT_EVAL_TEST_BASE_URL": "https://chat.example/v1",
        "PROMPT_EVAL_TEST_API_KEY": CHAT_API_KEY,
        "PROMPT_EVAL_TEST_EMBEDDING_PROVIDER_ID": "eval-embedding",
        "PROMPT_EVAL_TEST_EMBEDDING_MODEL": "embedding-model",
        "PROMPT_EVAL_TEST_EMBEDDING_BASE_URL": "https://embedding.example/v1",
        "PROMPT_EVAL_TEST_EMBEDDING_API_KEY": EMBEDDING_API_KEY,
    }
    for name, value in values.items():
        monkeypatch.setenv(name, value)

    configs = seed_script.build_seed_configs()

    assert configs["eval_gen"] == {
        "provider_id": "eval-chat",
        "model_name": "chat-model",
        "base_url": "https://chat.example/v1",
        "api_key": CHAT_API_KEY,
    }
    assert configs["embedding"] == {
        "provider_id": "eval-embedding",
        "model_name": "embedding-model",
        "base_url": "https://embedding.example/v1",
        "api_key": EMBEDDING_API_KEY,
        "adapter": "openai_embedding",
        "dimensions": 1024,
    }


@pytest.mark.asyncio
@pytest.mark.parametrize("multiquery", [False, True])
async def test_seed_commits_model_bindings_before_closing_session(monkeypatch, multiquery):
    # 模拟 pricing 在 seed 前导入并缓存了另一份 MEMORY_DB_CONFIG。
    monkeypatch.setenv("MEMORY_PGDATABASE", "agent_memory")
    import backend.infra.llm.pricing  # noqa: F401
    monkeypatch.delenv("MEMORY_PGDATABASE")

    values = {
        "PGHOST": "localhost",
        "PGPORT": "5432",
        "PGUSER": "postgres",
        "PGPASSWORD": PG_TEST_PASSWORD,
        "PROMPT_EVAL_TEST_PGDATABASE": "agent_memory_test",
        "PROMPT_EVAL_TEST_PROVIDER_ID": "eval-chat",
        "PROMPT_EVAL_TEST_MODEL": "chat-model",
        "PROMPT_EVAL_TEST_BASE_URL": "https://chat.example/v1",
        "PROMPT_EVAL_TEST_API_KEY": CHAT_API_KEY,
        "PROMPT_EVAL_TEST_EMBEDDING_PROVIDER_ID": "eval-embedding",
        "PROMPT_EVAL_TEST_EMBEDDING_MODEL": "embedding-model",
        "PROMPT_EVAL_TEST_EMBEDDING_BASE_URL": "https://embedding.example/v1",
        "PROMPT_EVAL_TEST_EMBEDDING_API_KEY": EMBEDDING_API_KEY,
        "PROMPT_EVAL_TEST_RERANK_PROVIDER_ID": "eval-rerank",
        "PROMPT_EVAL_TEST_RERANK_MODEL": "rerank-model",
        "PROMPT_EVAL_TEST_RERANK_BASE_URL": "https://rerank.example/v1",
        "PROMPT_EVAL_TEST_RERANK_API_KEY": RERANK_API_KEY,
    }
    for name, value in values.items():
        monkeypatch.setenv(name, value)
    monkeypatch.setenv("EVAL_MULTIQUERY", str(multiquery).lower())

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
    expected_bindings = [
        {"role": "eval_gen", "model_name": "chat-model"},
        {"role": "embedding", "model_name": "embedding-model"},
    ]
    if multiquery:
        expected_bindings.append({"role": "multi_query", "model_name": "chat-model"})
    expected_bindings.append({"role": "rerank", "model_name": "rerank-model"})
    assert role_bindings == expected_bindings


@pytest.mark.asyncio
async def test_conflicting_memory_database_is_rejected_before_engine_creation(monkeypatch):
    monkeypatch.setenv("PROMPT_EVAL_TEST_PGDATABASE", "agent_memory_test")
    monkeypatch.setenv("MEMORY_PGDATABASE", "agent_memory")
    monkeypatch.setenv("PGUSER", "postgres")
    monkeypatch.setenv("PGPASSWORD", PG_TEST_PASSWORD)
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
    monkeypatch.setenv("PGPASSWORD", PG_TEST_PASSWORD)
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
    monkeypatch.setenv("PGPASSWORD", PG_TEST_PASSWORD)
    monkeypatch.setenv("PGHOST", "10.0.0.8")

    with pytest.raises(RuntimeError, match="只允许连接本机隔离 PostgreSQL"):
        seed_script._require_test_database()


def test_database_identity_accepts_explicit_readonly_verification_session():
    target = {
        "host": "127.0.0.1", "port": 5432,
        "database": "agent_memory_test", "username": "test-role",
        "password": PG_TEST_PASSWORD,
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


def test_database_identity_accepts_docker_host_port_mapping():
    """宿主映射 5433 → 容器 5432：端口不同不得误判为身份不符。

    Docker Desktop 的 loopback 端口映射下，inet_server_port() 返回容器内部
    监听端口，与客户端目标端口必然不同；身份可信度由库名/角色/read-only/
    主库状态与真实服务地址决定。
    """
    target = {
        "host": "127.0.0.1", "port": 5433,
        "database": "agent_memory_eval", "username": "eval-role",
        "password": PG_TEST_PASSWORD,
    }
    identity = {
        "database": "agent_memory_eval",
        "username": "eval-role",
        "server_address": "172.18.0.2/32",
        "server_port": 5432,
        "read_only": "off",
        "in_recovery": False,
    }

    seed_script._assert_database_identity(identity, target)
    seed_script._assert_database_identity(
        {**identity, "read_only": "on"}, target, read_only=True,
    )


@pytest.mark.parametrize(
    ("overrides", "read_only"),
    [
        ({"database": "agent_memory"}, False),
        ({"username": "postgres"}, False),
        ({"read_only": "on"}, False),
        ({"in_recovery": True}, False),
        # Docker Desktop 场景下服务器可能返回容器私网地址，故用公网地址验证拒绝；
        # 私网地址与 loopback 目标的组合是既定兼容规则（见 _server_address_matches_target）。
        ({"server_address": "8.8.8.8/32"}, False),
        ({"server_address": None}, False),
    ],
)
def test_database_identity_rejects_non_test_targets(overrides, read_only):
    """库名/角色/只读/主库/真实地址任一不符必须拒绝，端口一致也不行。"""
    target = {
        "host": "127.0.0.1", "port": 5432,
        "database": "agent_memory_eval", "username": "eval-role",
        "password": PG_TEST_PASSWORD,
    }
    identity = {
        "database": "agent_memory_eval",
        "username": "eval-role",
        "server_address": "127.0.0.1/32",
        "server_port": 5432,
        "read_only": "off",
        "in_recovery": False,
        **overrides,
    }

    with pytest.raises(RuntimeError, match="身份与显式目标不一致"):
        seed_script._assert_database_identity(identity, target, read_only=read_only)
