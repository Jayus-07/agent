from __future__ import annotations

from backend.infra.llm.factory import LLMFactory
from backend.infra.llm.models import (
    AVAILABLE_MODELS,
    PROVIDER_API_KEY_ENV,
    get_model_pricing,
)


def test_siliconflow_model_is_registered_with_key_env() -> None:
    """Qwen/Qwen3-8B 必须注册为 siliconflow provider，Key 环境变量单一事实源。"""
    entry = next(m for m in AVAILABLE_MODELS if m["name"] == "Qwen/Qwen3-8B")
    assert entry["provider"] == "siliconflow"
    assert PROVIDER_API_KEY_ENV["siliconflow"] == "SILICONFLOW_API_KEY"


def test_factory_resolves_siliconflow_by_name_not_qwen_heuristic() -> None:
    """回归锁：'Qwen/Qwen3-8B' 含 qwen 字样，不得被启发式误路由到 DashScope。"""
    factory = LLMFactory()

    assert factory._get_provider("Qwen/Qwen3-8B") == "siliconflow"


def test_set_current_rejects_when_siliconflow_key_missing(monkeypatch) -> None:
    """Key 未配置时 fail-fast，不得静默构建实例。"""
    import backend.infra.llm.factory as factory_module

    monkeypatch.setattr(factory_module, "SILICONFLOW_API_KEY", "")
    factory = LLMFactory()

    result = factory.set_current("Qwen/Qwen3-8B")

    assert result["ok"] is False
    assert "SILICONFLOW_API_KEY" in result["error"]


def test_pricing_lookup_returns_zero_for_unpriced_siliconflow_model() -> None:
    """未核价模型返回 (0, 0)，不得抛错——成本估算走 model_price_missing 告警。"""
    assert get_model_pricing("Qwen/Qwen3-8B") == (0.0, 0.0)
