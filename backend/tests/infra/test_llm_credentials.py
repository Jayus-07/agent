"""infra/llm/credentials.py + registry_store.py —— 出站凭据通道（P1a-1）

验收重点：**凭据解析必须与改造前 providers/* 实际使用的量逐项一致**，
以及 DB 覆盖层为空时回落 env（零行为变化）。
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


def test_env_binding_matches_pre_refactor_values():
    """逐项对齐改造前 providers/*.py 引用的量（含 minimax 的字面量端点）。"""
    expect = {
        "deepseek": (cfg.DEEPSEEK_API_KEY or None, cfg.DEEPSEEK_API_BASE),
        "minimax": (cfg.MINIMAX_API_KEY or None, cred.MINIMAX_ANTHROPIC_URL),
        "qwen": (cfg.QWEN_API_KEY or None, cfg.QWEN_API_BASE),
        "qwen_tp": (cfg.QWEN_TP_API_KEY or None, cfg.QWEN_TP_API_BASE),
        "vllm": (cfg.VLLM_API_KEY or None, cfg.VLLM_API_BASE),
        "siliconflow": (cfg.SILICONFLOW_API_KEY or None, cfg.SILICONFLOW_API_BASE),
        "ollama": (None, cfg.OLLAMA_BASE_URL),
    }
    assert set(expect) == set(PROVIDERS)
    for prov, (want_key, want_url) in expect.items():
        c = cred.resolve_credentials(prov)
        assert c.api_key == want_key, prov
        assert c.base_url == want_url, prov
        assert c.source == "env", prov
        assert c.version == 0, prov


def test_minimax_uses_anthropic_literal_not_config():
    """minimax 走 Anthropic 兼容端点字面量；config 的 MINIMAX_API_BASE 是另一套协议。"""
    assert cred.MINIMAX_ANTHROPIC_URL == "https://api.minimaxi.com/anthropic"
    assert cred.resolve_credentials("minimax").base_url == cred.MINIMAX_ANTHROPIC_URL


def test_unknown_provider_raises():
    with pytest.raises(cred.UnknownProviderError):
        cred.resolve_credentials("glm")


def test_db_override_wins_over_env():
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
    assert cred.resolve_credentials("qwen").source == "env"


def test_missing_key_message_keeps_original_wording(monkeypatch):
    monkeypatch.setattr("backend.config.llm.SILICONFLOW_API_KEY", "")
    assert cred.missing_key_message("siliconflow") == (
        "SILICONFLOW_API_KEY 未配置，请在 .env 中设置"
    )
    monkeypatch.setattr("backend.config.llm.VLLM_API_KEY", "")
    assert cred.missing_key_message("vllm") == (
        "VLLM_API_KEY 未配置，请在 .env 中设置（deploy.sh 会生成）"
    )
    monkeypatch.setattr("backend.config.llm.QWEN_TP_API_KEY", "")
    assert cred.missing_key_message("qwen_tp") == (
        "QWEN_TP_API_KEY 未配置，请在 .env 中设置（sk-sp- 模型包 Key）"
    )


def test_configured_key_produces_no_message():
    assert cred.missing_key_message("ollama") is None   # 本地无需 Key
    if cfg.DEEPSEEK_API_KEY:
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
