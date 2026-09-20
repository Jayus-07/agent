"""模型用途类型的目录与角色兼容性契约。"""
from __future__ import annotations

import pytest

from backend.infra.llm import models


@pytest.fixture(autouse=True)
def _reset_dynamic_catalog():
    models.reset_dynamic_models_for_tests()
    yield
    models.reset_dynamic_models_for_tests()


def test_db_row_without_kind_is_chat() -> None:
    """DB 行缺 `model_kind`（历史行/迁移前数据）→ 按 chat 兼容（§B.15 后入口为 DB-only）。"""
    models.set_dynamic_models([
        {"name": "qwen3.7-plus", "provider": "qwen", "source": "db"},
    ])
    entry = next(item for item in models.get_available_models() if item["name"] == "qwen3.7-plus")

    assert models.model_kind_of(entry) == "chat"


def test_dynamic_model_preserves_embedding_kind() -> None:
    models.set_dynamic_models([
        {
            "name": "custom-embedding",
            "provider": "custom-api",
            "model_kind": "embedding",
        }
    ])

    assert models.model_kind_of(models.get_model_entry("custom-embedding")) == "embedding"


@pytest.mark.parametrize(
    ("kind", "label"),
    [
        ("chat", "文本模型"),
        ("embedding", "向量模型"),
        ("rerank", "重排模型"),
        ("vision", "视觉模型"),
        ("speech", "语音模型"),
    ],
)
def test_catalog_accepts_all_five_model_kinds(kind: str, label: str) -> None:
    assert models.normalize_model_kind(kind) == kind
    assert models.MODEL_KIND_LABELS[kind] == label


def test_role_kind_compatibility_is_explicit() -> None:
    assert models.expected_model_kind("main") == "chat"
    assert models.expected_model_kind("embedding") == "embedding"
    assert models.expected_model_kind("rerank") == "rerank"
    assert models.is_model_kind_compatible("eval_gen", "chat")
    assert not models.is_model_kind_compatible("eval_gen", "embedding")
    assert models.is_model_kind_compatible("embedding", "embedding")
    assert not models.is_model_kind_compatible("embedding", "chat")
