"""Model Governance STOP B —— 模型身份语义（canonical 不可被覆盖）。

覆盖任务书 B12 要求：canonical != upstream、response 回传名不覆盖 canonical
身份、登记名唯一性。

口径（任务书 §4）：response.model 只是观测值，必须能映射回 canonical，
但**禁止**反向覆盖登记名 / 角色绑定 / 全局配置。
"""
from __future__ import annotations

import pytest

from backend.infra.llm import models

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


def test_canonical_id_maps_response_model_back_to_registered_name():
    """response 回传 upstream 名 → canonical 归一为登记名（观测口径统一）。"""
    assert models.canonical_model_id("doubao-seed-2-0-mini-260428") == (
        "doubao-seed-2.0-mini"
    )


def test_canonical_id_keeps_registered_name_unchanged():
    assert models.canonical_model_id("doubao-seed-2.0-mini") == "doubao-seed-2.0-mini"


def test_canonical_id_never_invents_identity():
    """未注册名原样返回（不猜、不覆盖）——调用方语义不变。"""
    assert models.canonical_model_id("totally-unknown") == "totally-unknown"
    assert models.canonical_model_id("") == ""


def test_registry_does_not_rewrite_names():
    """归一只发生在读取侧：注册表条目的 name/upstream 永不被改写。"""
    entry = models.get_model_entry("doubao-seed-2.0-mini")
    assert entry["name"] == "doubao-seed-2.0-mini"
    assert entry["upstream_name"] == "doubao-seed-2-0-mini-260428"


def test_alias_lookup_requires_exact_match():
    """别名精确匹配：前缀/包含关系不命中（260428 → 260215 是不同版本）。"""
    assert models.lookup_model_entry("doubao-seed-2-0-mini-260215") is None
    assert models.canonical_model_id("doubao-seed-2-0-mini-260215") == (
        "doubao-seed-2-0-mini-260215"
    )
