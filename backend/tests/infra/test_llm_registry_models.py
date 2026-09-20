"""infra/llm/models.py —— 可用模型统一入口 / provider 解析 / 计费口径（P1a-1）

§B.15（迁移 0023）起代码层种子退役：`get_available_models()` 为 **DB-only**。
本文件的注入 fixture 模拟 `registry_store.refresh_registry()` 的行为 ——
生产环境中这些条目来自 `llm_models` 表（含迁移 0023 种入的 8 条 builtin）。

`resolve_provider` 的宽松模式**必须与改造前 `LLMFactory._get_provider` 逐位一致**
（回归锁：backend/tests/infra/test_llm_siliconflow_provider.py 另有断言）。
"""
from __future__ import annotations

import pytest

from backend.infra.llm import models

# 迁移 0023 种入 llm_models 的 8 条内置模型（registry_store._model_entry 的输出形状）
SEED_MODELS = [
    {"name": "qwen3.7-plus", "provider": "qwen", "display": "Qwen 3.7 Plus - 在线",
     "input_price_per_1m": 0.4, "output_price_per_1m": 1.2, "source": "db"},
    {"name": "qwen3.7-plus@tp", "provider": "qwen_tp", "display": "Qwen 3.7 Plus - Token Plan",
     "input_price_per_1m": 0.0, "output_price_per_1m": 0.0, "source": "db"},
    {"name": "qwen2.5:3b", "provider": "ollama", "display": "Qwen 2.5 (3B) - 本地",
     "input_price_per_1m": 0.0, "output_price_per_1m": 0.0, "source": "db"},
    {"name": "deepseek-v4-flash", "provider": "deepseek", "display": "DeepSeek V4-Flash - 云端",
     "input_price_per_1m": 0.14, "output_price_per_1m": 0.28, "source": "db"},
    {"name": "MiniMax-M3", "provider": "minimax", "display": "MiniMax M3 - 云端",
     "input_price_per_1m": 3.0, "output_price_per_1m": 15.0, "source": "db"},
    {"name": "Qwen/Qwen3-32B-AWQ", "provider": "vllm", "display": "Qwen3 32B (AWQ) - 自托管",
     "input_price_per_1m": 0.0, "output_price_per_1m": 0.0, "source": "db"},
    {"name": "Qwen/Qwen3-32B", "provider": "siliconflow", "display": "Qwen3 32B - 硅基流动",
     "input_price_per_1m": 0.0, "output_price_per_1m": 0.0, "source": "db"},
    {"name": "Qwen/Qwen3-8B", "provider": "siliconflow", "display": "Qwen3 8B - 硅基流动",
     "input_price_per_1m": 0.0, "output_price_per_1m": 0.0, "source": "db"},
]


@pytest.fixture(autouse=True)
def _seeded():
    """默认注入 DB 种子清单（模拟注册表已加载）；用例内可 reset 得到空注册表。"""
    models.reset_dynamic_models_for_tests()
    models.set_dynamic_models([dict(m) for m in SEED_MODELS])
    yield
    models.reset_dynamic_models_for_tests()


# ── 统一读取入口（DB-only）────────────────────────────────────────────


def test_empty_registry_returns_empty_list():
    """动态层为空（未加载/单测态）→ 空清单；代码层种子已退役，不再兜底。"""
    models.reset_dynamic_models_for_tests()
    assert models.get_available_models() == []
    assert models.get_model_entry("qwen3.7-plus@tp") is None
    assert not models.is_known_model("qwen3.7-plus@tp")


def test_registry_is_exactly_what_was_injected():
    """清单 = 注入内容本身：不再有「代码层 ∪ DB」合并语义。"""
    out = models.get_available_models()
    assert len(out) == len(SEED_MODELS)
    assert {m["name"] for m in out} == {m["name"] for m in SEED_MODELS}


def test_db_removal_means_model_disappears():
    """DB 删行 = 模型从清单消失（旧的「代码层条目保留」兜底语义已退役）。"""
    kept = [m for m in models.get_available_models() if m["name"] != "MiniMax-M3"]
    models.set_dynamic_models(kept)
    assert models.get_model_entry("MiniMax-M3") is None


def test_get_model_entry_dynamic_only():
    models.set_dynamic_models([{"name": "MiniMax-M3", "provider": "custom"}])
    assert models.get_model_entry("MiniMax-M3")["provider"] == "custom"
    assert models.get_model_entry("nope") is None
    assert not models.is_known_model("nope")


# ── provider 解析（历史行为回归锁；注册表命中用例走 DB 注入）──────────


@pytest.mark.parametrize("name,want", [
    ("qwen3.7-plus", "qwen"),
    ("qwen3.7-plus@tp", "qwen_tp"),
    ("qwen2.5:3b", "ollama"),          # 带冒号 = 本地 Ollama
    ("deepseek-v4-flash", "deepseek"),
    ("MiniMax-M3", "minimax"),
    ("Qwen/Qwen3-32B-AWQ", "vllm"),    # 注册表命中，不得被 qwen 启发式抢走
    ("Qwen/Qwen3-32B", "siliconflow"),
    ("Qwen/Qwen3-8B", "siliconflow"),
])
def test_resolve_provider_registry_hits(name, want):
    assert models.resolve_provider(name) == want


@pytest.mark.parametrize("name,want", [
    ("deepseek-chat", "deepseek"),     # 名称启发式（不依赖注册表）
    ("qwen3-max", "qwen"),
    ("llama3", "ollama"),              # 未注册 + 判不出 -> 本地兜底（历史行为）
])
def test_resolve_provider_heuristics(name, want):
    models.reset_dynamic_models_for_tests()   # 启发式与注册表无关，空表更纯粹
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
    models.reset_dynamic_models_for_tests()
    assert models.get_model_billing("llama3") == "local"
