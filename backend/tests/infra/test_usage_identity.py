"""Model Governance STOP C —— Usage 身份归属（C2/C5 核心回归锁）。

场景还原（STOP A 实机 1174 行错位）：豆包注册名 doubao-seed-2.0-mini，
上游回传 doubao-seed-2-0-mini-260428（带版本号、连分隔符都不同，注册表
无法反查）。修复后身份权威 = 调用方声明（ctx > 显式参数），response 回传名
只落 upstream_model_id 观测列，provider 一律 ctx 权威。
"""
from __future__ import annotations

import pytest

from backend.infra.llm import models
from backend.infra.llm import proxy as proxy_mod
from backend.infra.llm.resolved_model import (
    ResolvedModelContext, reset_current_resolved_model,
)

from backend.tests.infra.test_model_registry import (
    GOVERNANCE_MODELS,
    GOVERNANCE_PROVIDERS,
)


class _FakeResult:
    def __init__(self, token_usage, response_model=""):
        self.response_metadata = {
            "token_usage": token_usage,
            "finish_reason": "stop",
        }
        if response_model:
            self.response_metadata["model_name"] = response_model


@pytest.fixture(autouse=True)
def _registry_and_state():
    models.reset_dynamic_models_for_tests()
    models.set_dynamic_models([dict(m) for m in GOVERNANCE_MODELS])
    models.set_dynamic_providers([dict(p) for p in GOVERNANCE_PROVIDERS])
    proxy_mod._last_tokens_var.set({})
    proxy_mod._last_call_meta_var.set({})
    yield
    models.reset_dynamic_models_for_tests()
    proxy_mod._last_tokens_var.set({})
    proxy_mod._last_call_meta_var.set({})
    reset_current_resolved_model()


_USAGE = {"prompt_tokens": 100, "completion_tokens": 50, "total_tokens": 150}


def test_ctx_authoritative_over_response_alias():
    """豆包场景：ctx 在场 + response 回传 upstream 名 → 归属登记名，
    provider=ctx.provider（不再 ollama），回传名只进 upstream_model_id。"""
    proxy_mod.set_current_resolved_model(ResolvedModelContext(
        model_id="doubao-seed-2.0-mini",
        provider="custom-doubao-seed-2-0-mini",
        role="main", binding_source="db_binding",
        driver="openai",
        upstream_name="doubao-seed-2-0-mini-260428",
    ))
    proxy_mod._record_tokens(
        _FakeResult(_USAGE, response_model="doubao-seed-2-0-mini-260428"))
    meta = proxy_mod._last_call_meta_var.get()
    assert meta["model"] == "doubao-seed-2.0-mini"
    assert meta["canonical_model_id"] == "doubao-seed-2.0-mini"
    assert meta["upstream_model_id"] == "doubao-seed-2-0-mini-260428"
    assert meta["provider"] == "custom-doubao-seed-2-0-mini"
    assert meta["binding_source"] == "db_binding"


def test_explicit_name_wins_without_ctx():
    """ctx 缺失 + 显式登记名（record_llm_result 补计量链）+ response 回传名
    → 登记名赢。旧实现 response 优先 → 被回传名带偏（581 行 role='' 错位）。"""
    proxy_mod._record_tokens(
        _FakeResult(_USAGE, response_model="doubao-seed-2-0-mini-260428"),
        model_name="doubao-seed-2.0-mini",
    )
    meta = proxy_mod._last_call_meta_var.get()
    assert meta["model"] == "doubao-seed-2.0-mini"
    assert meta["provider"] == "custom-doubao-seed-2-0-mini"
    assert meta["upstream_model_id"] == "doubao-seed-2-0-mini-260428"


def test_response_model_never_overwrites_canonical():
    """response 回传未注册别名（注册表无法反查）且 ctx 在场 → 绝不覆盖
    canonical 身份（任务书 §4 红线）。"""
    proxy_mod.set_current_resolved_model(ResolvedModelContext(
        model_id="qwen3.8-flash", provider="custom-api", role="main",
    ))
    proxy_mod._record_tokens(
        _FakeResult(_USAGE, response_model="qwen3-8-flash-some-vendor-alias"))
    meta = proxy_mod._last_call_meta_var.get()
    assert meta["model"] == "qwen3.8-flash"
    assert meta["upstream_model_id"] == "qwen3-8-flash-some-vendor-alias"


def test_registered_alias_resolved_via_registry():
    """已登记别名（upstream 双匹配）在 ctx 缺失时也能正确归一。"""
    proxy_mod._record_tokens(
        _FakeResult(_USAGE, response_model="doubao-seed-2-0-mini-260428"),
        # 显式传 upstream 别名（模拟调用方只有上游名）→ 双匹配命中登记条目
        model_name="doubao-seed-2-0-mini-260428",
    )
    meta = proxy_mod._last_call_meta_var.get()
    assert meta["model"] == "doubao-seed-2.0-mini"


def test_no_declaration_falls_back_to_observation():
    """完全无调用方声明（不应发生）：诚实落 response 观测值，不猜。"""
    proxy_mod._record_tokens(_FakeResult(_USAGE, response_model="unknown-model"))
    meta = proxy_mod._last_call_meta_var.get()
    assert meta["model"] == "unknown-model"


def test_provider_double_match_without_ctx():
    """ctx 缺失 + 回传名命中注册表别名 → provider 双匹配直查（不再 ollama）。"""
    proxy_mod._record_tokens(
        _FakeResult(_USAGE, response_model="doubao-seed-2-0-mini-260428"),
        model_name="doubao-seed-2-0-mini-260428",
    )
    assert proxy_mod._last_call_meta_var.get()["provider"] == (
        "custom-doubao-seed-2-0-mini")


def test_turn_usage_provider_from_ctx():
    """turn 汇总行 provider 不再字符串推断（STOP A R2）。"""
    proxy_mod.set_current_resolved_model(ResolvedModelContext(
        model_id="doubao-seed-2.0-mini",
        provider="custom-doubao-seed-2-0-mini", role="main",
    ))
    proxy_mod._record_tokens(
        _FakeResult(_USAGE, response_model="doubao-seed-2-0-mini-260428"))
    turn = proxy_mod.get_turn_usage()
    assert turn["doubao-seed-2.0-mini"]["provider"] == (
        "custom-doubao-seed-2-0-mini")
