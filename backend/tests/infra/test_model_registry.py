"""Model Governance STOP B —— Canonical Model Registry 注册表语义。

覆盖任务书 B12 要求：provider != driver、canonical != upstream（双匹配）、
disabled model、missing model。

fixture 模拟 `registry_store.refresh_registry()` 注入后的进程内注册表：
豆包条目刻意让 name != upstream_model_name（别名场景），provider 是
custom-* 而 driver 是 openai（provider 与 driver 分离的核心验收场景）。
"""
from __future__ import annotations

import pytest

from backend.infra.llm import models

GOVERNANCE_MODELS = [
    {
        "name": "doubao-seed-2.0-mini",
        "provider": "custom-doubao-seed-2-0-mini",
        "display": "豆包 Seed 2.0 Mini",
        "model_kind": "chat",
        # 别名：上游真实回传名与登记名不同（STOP A 实机：260428 版本号）
        "upstream_name": "doubao-seed-2-0-mini-260428",
        "context_length": 262144,
        "max_output_tokens": 32768,
        "capabilities": {"tools": True, "vision": True, "thinking": True},
        "source": "db",
    },
    {
        "name": "qwen3.8-flash",
        "provider": "custom-api",
        "display": "Qwen3.8 Flash",
        "model_kind": "chat",
        "upstream_name": "",
        "context_length": None,
        "max_output_tokens": None,
        "capabilities": {},
        "source": "db",
    },
]

GOVERNANCE_PROVIDERS = [
    {"id": "custom-doubao-seed-2-0-mini", "driver": "openai",
     "base_url": "https://ark.cn-beijing.volces.com/api/plan/v3", "billing": "metered"},
    {"id": "custom-api", "driver": "openai",
     "base_url": "https://maas.example.com/compatible-mode/v1", "billing": "metered"},
]


@pytest.fixture(autouse=True)
def _seeded():
    models.reset_dynamic_models_for_tests()
    models.set_dynamic_models([dict(m) for m in GOVERNANCE_MODELS])
    models.set_dynamic_providers([dict(p) for p in GOVERNANCE_PROVIDERS])
    yield
    models.reset_dynamic_models_for_tests()


def test_provider_is_not_driver():
    """provider != driver：provider=custom-doubao-... 的协议驱动是 openai。

    禁止 `driver=openai → provider=openai` 式推断（任务书 §3.4/3.5）。
    """
    provider = models.resolve_provider("doubao-seed-2.0-mini")
    assert provider == "custom-doubao-seed-2-0-mini"
    assert models.get_provider_driver(provider) == "openai"


def test_canonical_and_upstream_are_distinct_names():
    """canonical != upstream：登记名与上游名都注册后，两者各自可查。"""
    entry = models.get_model_entry("doubao-seed-2.0-mini")
    assert entry is not None
    assert entry["upstream_name"] == "doubao-seed-2-0-mini-260428"
    assert entry["upstream_name"] != entry["name"]


def test_lookup_matches_by_upstream_alias():
    """双匹配：上游回传名 → 命中登记条目（身份链唯一入口）。"""
    entry = models.lookup_model_entry("doubao-seed-2-0-mini-260428")
    assert entry is not None
    assert entry["name"] == "doubao-seed-2.0-mini"


def test_get_model_entry_strictly_by_registered_name():
    """get_model_entry 只认登记名：upstream 别名不能冒充登记名
    （override 校验、角色绑定的严格性依赖这一语义）。"""
    assert models.get_model_entry("doubao-seed-2-0-mini-260428") is None
    assert models.get_model_entry("doubao-seed-2.0-mini") is not None


def test_missing_model_returns_none():
    assert models.lookup_model_entry("no-such-model") is None
    assert models.lookup_model_entry("") is None
    assert models.get_model_entry("no-such-model") is None


def test_disabled_model_behaves_as_missing():
    """enabled=false 的行不出现在注册表（registry_store WHERE enabled=true）
    → 登记身份失效。名字仍会被历史启发式判为 qwen（宽松回落语义，见
    resolve_provider docstring「暂不 fail-closed」），但绝不能再解析出
    登记的 custom-api provider。"""
    kept = [m for m in models.get_available_models()
            if m["name"] != "qwen3.8-flash"]
    models.set_dynamic_models(kept)
    assert models.lookup_model_entry("qwen3.8-flash") is None
    assert models.resolve_provider("qwen3.8-flash") != "custom-api"
    # 启发式判不出的名字 → strict 模式明确失败（missing 语义）
    with pytest.raises(models.ProviderResolutionError):
        models.resolve_provider("totally-unregistered-model", strict=True)
