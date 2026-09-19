"""infra/llm/models.py —— 可用模型统一入口 / provider 解析 / 计费口径（P1a-1）

`resolve_provider` 的宽松模式**必须与改造前 `LLMFactory._get_provider` 逐位一致**
（回归锁：backend/tests/infra/test_llm_siliconflow_provider.py 另有断言）。
"""
from __future__ import annotations

import pytest

from backend.infra.llm import models


@pytest.fixture(autouse=True)
def _clean():
    models.reset_dynamic_models_for_tests()
    yield
    models.reset_dynamic_models_for_tests()


# ── 统一读取入口 ──────────────────────────────────────────────────────


def test_no_dynamic_layer_returns_code_layer_identity():
    """DB 空表（覆盖层为空）时必须零拷贝返回代码层 —— 保证零行为变化。"""
    assert models.get_available_models() is models.AVAILABLE_MODELS


def test_dynamic_overrides_same_name_and_keeps_code_only_entries():
    models.set_dynamic_models([
        {"name": "qwen3.7-plus", "provider": "qwen", "display": "DB 覆盖", "source": "db"},
        {"name": "glm-4.6", "provider": "glm-coding", "display": "GLM", "source": "db"},
    ])
    out = models.get_available_models()
    names = [m["name"] for m in out]

    assert names.count("qwen3.7-plus") == 1
    assert next(m for m in out if m["name"] == "qwen3.7-plus")["display"] == "DB 覆盖"
    assert "glm-4.6" in names
    # DB 抖动/条目被删不应让内置模型消失
    assert "MiniMax-M3" in names


def test_get_model_entry_prefers_dynamic():
    models.set_dynamic_models([{"name": "MiniMax-M3", "provider": "custom"}])
    assert models.get_model_entry("MiniMax-M3")["provider"] == "custom"
    models.reset_dynamic_models_for_tests()
    assert models.get_model_entry("MiniMax-M3")["provider"] == "minimax"
    assert models.get_model_entry("nope") is None
    assert not models.is_known_model("nope")


# ── provider 解析（历史行为回归锁）────────────────────────────────────


@pytest.mark.parametrize("name,want", [
    ("qwen3.7-plus", "qwen"),
    ("qwen3.7-plus@tp", "qwen_tp"),
    ("qwen2.5:3b", "ollama"),          # 带冒号 = 本地 Ollama
    ("deepseek-v4-flash", "deepseek"),
    ("deepseek-chat", "deepseek"),     # 名称启发式
    ("MiniMax-M3", "minimax"),
    ("Qwen/Qwen3-32B-AWQ", "vllm"),    # 注册表命中，不得被 qwen 启发式抢走
    ("Qwen/Qwen3-32B", "siliconflow"),
    ("Qwen/Qwen3-8B", "siliconflow"),
    ("qwen3-max", "qwen"),
    ("llama3", "ollama"),              # 未注册 + 判不出 -> 本地兜底（历史行为）
    ("glm-4.6", "ollama"),
])
def test_resolve_provider_matches_historical_behavior(name, want):
    assert models.resolve_provider(name) == want


def test_dynamic_model_resolves_to_its_declared_provider():
    models.set_dynamic_models([{"name": "glm-4.6", "provider": "glm-coding"}])
    assert models.resolve_provider("glm-4.6") == "glm-coding"
    # 严格模式下也不再抛错（自建模型已有明确归属）
    assert models.resolve_provider("glm-4.6", strict=True) == "glm-coding"


def test_strict_mode_raises_only_when_undecidable():
    assert models.resolve_provider("MiniMax-M3", strict=True) == "minimax"
    with pytest.raises(models.ProviderResolutionError):
        models.resolve_provider("glm-4.6", strict=True)
    with pytest.raises(models.ProviderResolutionError):
        models.resolve_provider("llama3", strict=True)


def test_loose_mode_is_silent_after_first_warning():
    """未注册模型只告警一次，避免热路径刷屏（但返回值仍稳定）。"""
    models.reset_dynamic_models_for_tests()
    for _ in range(3):
        assert models.resolve_provider("unknown-model-xyz") == "ollama"


# ── 协议驱动 / 计费口径 ───────────────────────────────────────────────


@pytest.mark.parametrize("provider,driver", [
    ("minimax", "anthropic"), ("ollama", "ollama"), ("qwen", "openai"),
    ("deepseek", "openai"), ("qwen_tp", "openai"), ("vllm", "openai"),
    ("siliconflow", "openai"),
])
def test_driver_matrix(provider, driver):
    assert models.get_provider_driver(provider) == driver


def test_unknown_provider_driver_is_none():
    assert models.get_provider_driver("glm-coding") is None


@pytest.mark.parametrize("provider,billing", [
    ("qwen", "metered"), ("deepseek", "metered"), ("minimax", "metered"),
    ("siliconflow", "metered"),
    ("qwen_tp", "subscription"),   # 模型包 = 订阅制，不是「metered 且单价 0」
    ("ollama", "local"), ("vllm", "local"),
])
def test_billing_matrix(provider, billing):
    assert models.get_provider_billing(provider) == billing


def test_unknown_billing_defaults_to_metered():
    """保守：未知 provider 宁可照常计价，不可静默免费用量。"""
    assert models.get_provider_billing("glm-coding") == "metered"


def test_model_billing_by_model_name():
    assert models.get_model_billing("qwen3.7-plus@tp") == "subscription"
    assert models.get_model_billing("qwen3.7-plus") == "metered"
    assert models.get_model_billing("llama3") == "local"
