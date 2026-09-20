"""infra/llm/credentials.py + registry_store.py —— 出站凭据通道（P1a-1）

验收重点：数据库凭据是唯一云端来源；DB 覆盖层为空时必须明确返回未配置，
不能回落宿主机 env。
"""
from __future__ import annotations

import pytest

from backend.config import llm as cfg
from backend.infra.llm import credentials as cred
from backend.infra.llm.models import PROVIDERS


@pytest.fixture(autouse=True)
def _clean():
    cred.reset_credentials_for_tests()
    yield
    cred.reset_credentials_for_tests()


def test_unconfigured_cloud_provider_does_not_read_env(monkeypatch):
    """旧模型 Key/Base URL 即使存在于进程环境，也不能成为运行时来源。"""
    monkeypatch.setenv("QWEN_API_KEY", "env-only-key")
    monkeypatch.setenv("QWEN_API_BASE", "https://env.example/v1")

    for prov in ("deepseek", "minimax", "qwen", "qwen_tp", "vllm", "siliconflow"):
        c = cred.resolve_credentials(prov)
        assert c.api_key is None, prov
        assert c.base_url is None, prov
        assert c.source == "unconfigured", prov
        assert c.version == 0, prov

    ollama = cred.resolve_credentials("ollama")
    assert ollama.api_key is None
    assert ollama.base_url == "http://localhost:11434"
    assert ollama.source == "unconfigured"


def test_minimax_uses_anthropic_literal_not_config():
    """保留协议常量，但未登记 DB 地址时不能把它当作运行时配置。"""
    assert cred.MINIMAX_ANTHROPIC_URL == "https://api.minimaxi.com/anthropic"
    assert cred.resolve_credentials("minimax").base_url is None


def test_unknown_provider_raises():
    with pytest.raises(cred.UnknownProviderError):
        cred.resolve_credentials("glm")


def test_db_override_is_the_only_cloud_source(monkeypatch):
    monkeypatch.setenv("QWEN_API_KEY", "env-key-must-be-ignored")
    cred.set_db_credentials({
        "qwen": cred.ProviderCredentials(
            provider="qwen", api_key="db-key", base_url="https://db.example/v1",
            source="db", version=7),
    })
    c = cred.resolve_credentials("qwen")
    assert c.api_key == "db-key"
    assert c.source == "db"
    assert cred.credentials_version("qwen") == 7
    assert cred.credentials_version("deepseek") == 0

    cred.set_db_credentials(None)
    assert cred.resolve_credentials("qwen").source == "unconfigured"
    assert cred.resolve_credentials("qwen").api_key is None


def test_db_base_url_without_key_is_unconfigured(monkeypatch):
    """数据库只保存地址时，不能从旧 env 补 Key。"""
    monkeypatch.setenv("QWEN_TP_API_KEY", "env-token-plan-key")
    cred.set_db_credentials({
        "qwen_tp": cred.ProviderCredentials(
            provider="qwen_tp",
            base_url="https://db.example/v1",
            source="db",
            version=3,
        ),
    })

    resolved = cred.resolve_credentials("qwen_tp")

    assert resolved.api_key is None
    assert resolved.base_url == "https://db.example/v1"
    assert resolved.source == "db"
    assert resolved.version == 3


def test_missing_key_message_points_to_database(monkeypatch):
    monkeypatch.setenv("SILICONFLOW_API_KEY", "env-only-key")
    assert cred.missing_key_message("siliconflow") == (
        "供应商 siliconflow 未在数据库配置 API Key"
    )
    monkeypatch.setenv("VLLM_API_KEY", "env-only-key")
    assert cred.missing_key_message("vllm") == (
        "供应商 vllm 未在数据库配置 API Key（deploy.sh 会生成）"
    )
    monkeypatch.setenv("QWEN_TP_API_KEY", "env-only-key")
    assert cred.missing_key_message("qwen_tp") == (
        "供应商 qwen_tp 未在数据库配置 API Key（sk-sp- 模型包 Key）"
    )


def test_configured_key_produces_no_message():
    assert cred.missing_key_message("ollama") is None   # 本地无需 Key
    cred.set_db_credentials({
        "deepseek": cred.ProviderCredentials(
            provider="deepseek", api_key="db-key", base_url="https://db.example/v1",
            source="db", version=1,
        ),
    })
    assert cred.missing_key_message("deepseek") is None


def test_check_provider_usable_follows_env_mode(monkeypatch):
    monkeypatch.setattr("backend.config.llm.OLLAMA_ENABLED", False)
    reason = cred.check_provider_usable("ollama")
    assert reason and "ENV_MODE=cloud" in reason

    monkeypatch.setattr("backend.config.llm.OLLAMA_ENABLED", True)
    assert cred.check_provider_usable("ollama") is None


def test_check_provider_usable_covers_custom_provider(monkeypatch):
    """自建 provider 不再像旧 if 链那样被静默跳过（B.5#5）。"""
    assert cred.check_provider_usable("glm-coding") == "未知 provider: glm-coding"


def test_snapshot_has_no_plaintext_key():
    snap = cred.snapshot()
    assert set(snap) == set(PROVIDERS)
    secrets = [v for v in (cfg.QWEN_API_KEY, cfg.DEEPSEEK_API_KEY, cfg.MINIMAX_API_KEY,
                           cfg.SILICONFLOW_API_KEY, cfg.VLLM_API_KEY,
                           cfg.QWEN_TP_API_KEY) if v]
    for prov, info in snap.items():
        assert "apiKey" not in info and "key" not in info
        for value in info.values():
            for s in secrets:
                assert not (isinstance(value, str) and s in value), prov
        assert isinstance(info["hasApiKey"], bool)
