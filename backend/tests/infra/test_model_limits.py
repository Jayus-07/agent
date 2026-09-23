"""Model Governance STOP B —— Context / Output limits（B7/B8 口径）。

覆盖任务书 B12 要求：NULL context fallback、context known、max_output_tokens
与窗口的概念分离。

Context Budget 本体冻结（CORE_FROZEN）：本文件只测 Registry 侧的读取
函数（models.resolve_output_token_cap），不触碰
context_budget/token_counter 的窗口算法。
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


def test_context_length_known_is_exposed():
    entry = models.get_model_entry("doubao-seed-2.0-mini")
    assert entry["context_length"] == 262144


def test_context_length_null_stays_null():
    """未登记窗口 → None（fail-safe：消费方回退全局更小窗口，不编造）。"""
    entry = models.get_model_entry("qwen3.8-flash")
    assert entry["context_length"] is None
    ctx_entry = models.model_identity_extras("qwen3.8-flash")
    assert ctx_entry["context_length"] is None


def test_max_output_tokens_preferred_over_window():
    """登记了 max_output_tokens → 用登记值（B8：输出上限 ≠ 上下文窗口）。"""
    entry = models.get_model_entry("doubao-seed-2.0-mini")
    assert models.resolve_output_token_cap(entry, 8192) == 32768


def test_max_output_tokens_missing_falls_back_to_window():
    """未登记（历史行为）→ 回退窗口值，零行为变化。"""
    entry = models.get_model_entry("qwen3.8-flash")
    assert models.resolve_output_token_cap(entry, 8192) == 8192
    assert models.resolve_output_token_cap(None, 8192) == 8192


def test_max_output_tokens_invalid_values_ignored():
    """脏数据（负数/0/非数字）不回退到危险方向：忽略登记值，用窗口。"""
    for bad in (0, -1, "abc", None):
        entry = {"max_output_tokens": bad}
        assert models.resolve_output_token_cap(entry, 8192) == 8192


def test_identity_extras_carries_limits():
    extras = models.model_identity_extras("doubao-seed-2.0-mini")
    assert extras["context_length"] == 262144
    assert extras["max_output_tokens"] == 32768
