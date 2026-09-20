from __future__ import annotations

import pytest

from backend.infra.llm.factory import LLMFactory
from backend.infra.llm.models import (
    PROVIDER_API_KEY_ENV,
    get_model_pricing,
    set_dynamic_models,
)

# §B.15 起注册表 DB-only：注入迁移 0023 的 siliconflow/vllm 条目模拟注册表已加载
_SF_SEED = [
    {"name": "Qwen/Qwen3-32B", "provider": "siliconflow", "source": "db"},
    {"name": "Qwen/Qwen3-8B", "provider": "siliconflow", "source": "db"},
    {"name": "Qwen/Qwen3-32B-AWQ", "provider": "vllm", "source": "db"},
]


@pytest.fixture(autouse=True)
def _seeded_registry():
    set_dynamic_models([dict(m) for m in _SF_SEED])
    yield
    from backend.infra.llm import models as _m
    _m.reset_dynamic_models_for_tests()


def test_siliconflow_model_is_registered_with_key_env() -> None:
    """Qwen/Qwen3-8B 必须注册为 siliconflow provider，Key 环境变量单一事实源。"""
    from backend.infra.llm.models import get_available_models

    entry = next(m for m in get_available_models() if m["name"] == "Qwen/Qwen3-8B")
    assert entry["provider"] == "siliconflow"
    assert PROVIDER_API_KEY_ENV["siliconflow"] == "SILICONFLOW_API_KEY"


def test_factory_resolves_siliconflow_by_name_not_qwen_heuristic() -> None:
    """回归锁：'Qwen/Qwen3-8B'/'Qwen/Qwen3-32B' 含 qwen 字样，不得被启发式误路由到 DashScope。"""
    factory = LLMFactory()

    assert factory._get_provider("Qwen/Qwen3-8B") == "siliconflow"
    assert factory._get_provider("Qwen/Qwen3-32B") == "siliconflow"
    # 同名前缀的 vLLM 自托管型号不得被 siliconflow 注册表条目遮蔽
    assert factory._get_provider("Qwen/Qwen3-32B-AWQ") == "vllm"


def test_set_current_rejects_when_siliconflow_key_missing(monkeypatch) -> None:
    """Key 未配置时 fail-fast，不得静默构建实例。

    2026-09-19：凭据解析收敛到 infra/llm/credentials.py（在**调用时**读 config
    模块属性，而非导入时拷贝常量），故注入点从 factory 模块移到 config.llm 模块。
    2026-09-21：报错文案随 B15 DB-only 收口改为指向数据库配置，不再点名 env。
    """
    monkeypatch.setattr("backend.config.llm.SILICONFLOW_API_KEY", "")
    factory = LLMFactory()

    result = factory.set_current("Qwen/Qwen3-8B")

    assert result["ok"] is False
    assert "siliconflow" in result["error"]
    assert "未在数据库配置 API Key" in result["error"]


def test_pricing_lookup_returns_zero_for_unpriced_siliconflow_model() -> None:
    """未核价模型返回 (0, 0)，不得抛错——成本估算走 model_price_missing 告警。"""
    assert get_model_pricing("Qwen/Qwen3-8B") == (0.0, 0.0)
