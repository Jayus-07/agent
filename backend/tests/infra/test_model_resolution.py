"""Model Governance STOP B —— Runtime Model Resolution（单一 resolver 语义）。

覆盖任务书 B12 要求：resolve_provider 双匹配（上游名不再落 ollama 兜底）、
model_identity_extras 完整身份链、ResolvedModelContext 由解析点一次性填全。

回归背景（STOP A 实机 1174 行错位）：豆包上游回传
`doubao-seed-2-0-mini-260428`，旧 resolve_provider 只认登记名 → miss →
名称启发式判不出 → 默认 provider="ollama" 且估价 0 元。
"""
from __future__ import annotations

import pytest

from backend.infra.llm import models
from backend.infra.llm.resolved_model import ResolvedModelContext

from backend.tests.infra.test_model_registry import (
    GOVERNANCE_MODELS,
    GOVERNANCE_PROVIDERS,
)


@pytest.fixture(autouse=True)
def _seeded():
    models.reset_dynamic_models_for_tests()
    models.set_dynamic_models([dict(m) for m in GOVERNANCE_MODELS])
    models.set_dynamic_providers([dict(p) for p in GOVERNANCE_PROVIDERS])
    yield
    models.reset_dynamic_models_for_tests()


def test_resolve_provider_by_upstream_alias_no_ollama_fallback():
    """上游回传名解析 provider：命中登记条目，不再落 ollama 兜底。

    这是豆包 usage 错位的 Registry 侧修复（P0-1 的一半；另一半在
    proxy 归属链，STOP C 收口）。"""
    assert models.resolve_provider("doubao-seed-2-0-mini-260428") == (
        "custom-doubao-seed-2-0-mini"
    )


def test_resolve_provider_registered_name_still_wins():
    assert models.resolve_provider("doubao-seed-2.0-mini") == (
        "custom-doubao-seed-2-0-mini"
    )


def test_identity_extras_full_chain():
    """身份链扩展字段一次取全：driver/upstream/kind/窗口/输出上限/能力。"""
    extras = models.model_identity_extras("doubao-seed-2.0-mini")
    assert extras["driver"] == "openai"
    assert extras["upstream_name"] == "doubao-seed-2-0-mini-260428"
    assert extras["model_kind"] == "chat"
    assert extras["context_length"] == 262144
    assert extras["max_output_tokens"] == 32768
    assert extras["capabilities"] == {
        "tools": True, "vision": True,
        "structured_output": False, "thinking": True,
    }


def test_identity_extras_empty_registry_is_soft():
    """注册表未加载（worker/单测态）→ 空 dict，构建方用 dataclass 默认值，
    不新增崩溃点（与 `_upstream_model_name` 的兜底语义一致）。"""
    models.reset_dynamic_models_for_tests()
    assert models.model_identity_extras("doubao-seed-2.0-mini") == {}


def test_resolved_model_context_carries_identity_chain():
    """ResolvedModelContext 能携带完整身份链（B4：下游禁止再自行推断）。"""
    extras = models.model_identity_extras("doubao-seed-2.0-mini")
    ctx = ResolvedModelContext(
        model_id="doubao-seed-2.0-mini",
        provider="custom-doubao-seed-2-0-mini",
        role="main",
        binding_source="db_binding",
        **extras,
    )
    payload = ctx.as_dict()
    assert payload["upstream_name"] == "doubao-seed-2-0-mini-260428"
    assert payload["context_length"] == 262144
    assert payload["capabilities"]["tools"] is True


def test_resolved_model_context_backward_compatible_defaults():
    """旧构造方式（只传原有字段）不破坏 —— 扩展字段全部带默认值。"""
    ctx = ResolvedModelContext(model_id="m", provider="p")
    assert ctx.driver == ""
    assert ctx.upstream_name == ""
    assert ctx.context_length is None
    assert ctx.capabilities == {}
