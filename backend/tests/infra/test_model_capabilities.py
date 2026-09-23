"""Model Governance STOP B —— Capability Model（fail-closed 读取口径）。

覆盖任务书 B10/B12 要求：capability true/false/未登记 的判定。
口径（B10）：tools / vision / structured_output / thinking 未登记 = False
（fail-closed，消费方不得默认放行）；stream 不进 schema（按已有行为兼容）。
"""
from __future__ import annotations

from backend.infra.llm import models


def test_explicit_true_and_false_are_respected():
    entry = {"capabilities": {"tools": True, "vision": False}}
    caps = models.model_capabilities(entry)
    assert caps["tools"] is True
    assert caps["vision"] is False
    assert caps["thinking"] is False  # 未登记 → fail-closed
    assert caps["structured_output"] is False


def test_missing_entry_fails_closed():
    caps = models.model_capabilities(None)
    assert caps == {
        "tools": False, "vision": False,
        "structured_output": False, "thinking": False,
    }


def test_empty_capabilities_fails_closed():
    """存量 16 行 capabilities 全 {} —— 读取必须全部 False（不猜）。"""
    assert models.model_capabilities({}) == {
        "tools": False, "vision": False,
        "structured_output": False, "thinking": False,
    }


def test_non_bool_values_are_not_trusted():
    """非布尔真值（字符串 "true"、1）不算声明 —— 只认显式 JSON true。"""
    entry = {"capabilities": {"tools": "true", "vision": 1}}
    caps = models.model_capabilities(entry)
    assert caps["tools"] is False
    assert caps["vision"] is False


def test_malformed_capabilities_fails_closed():
    assert models.model_capabilities({"capabilities": "not-a-dict"}) == {
        "tools": False, "vision": False,
        "structured_output": False, "thinking": False,
    }


def test_model_supports_unknown_capability_key():
    """schema 外的能力键：按未声明处理（False），不抛错。"""
    entry = {"capabilities": {"telepathy": True}}
    assert models.model_supports(entry, "telepathy") is False
    assert models.model_supports(entry, "tools") is False


def test_model_supports_positive_case():
    entry = {"capabilities": {"thinking": True}}
    assert models.model_supports(entry, "thinking") is True


def test_capability_keys_schema_is_frozen():
    """schema 固定四键（B9 避免复杂 DSL；stream 有意不在列）。"""
    assert models.MODEL_CAPABILITY_KEYS == (
        "tools", "vision", "structured_output", "thinking",
    )
