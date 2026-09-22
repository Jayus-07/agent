# -*- coding: utf-8 -*-
"""test_llm_canonical_model.py — Step 1 模型身份统一单测（路由专项 2026-09-22）

覆盖：
- canonical_model_id：登记名直通 / upstream alias 归一 / 未注册不猜 / 空值
- _record_tokens：usage 按 canonical 聚合，upstream 原始值保留进
  _last_call_meta（trace 透传），configured_model_id 来自调用上下文
- 同名 alias 多行用量 → 聚合为同一个 key（turn usage）
"""
import pytest

from backend.infra.llm import models as models_mod
from backend.infra.llm import proxy as proxy_mod
from backend.infra.llm.resolved_model import (
    ResolvedModelContext,
    reset_current_resolved_model,
    set_current_resolved_model,
)

_REGISTRY = [
    {"name": "doubao-seed-2.0-mini", "provider": "custom-api",
     "upstream_name": "doubao-seed-2-0-mini-260215"},
    {"name": "qwen3.8-flash", "provider": "dashscope", "upstream_name": "qwen3.8-flash"},
    {"name": "kimi-k3", "provider": "moonshot", "upstream_name": ""},
]


@pytest.fixture(autouse=True)
def _registry_and_ctx():
    models_mod.set_dynamic_models([dict(m) for m in _REGISTRY])
    reset_current_resolved_model()
    proxy_mod._last_call_meta_var.set({})
    proxy_mod._last_tokens_var.set({})
    proxy_mod.reset_turn_usage()
    yield
    models_mod.reset_dynamic_models_for_tests()
    reset_current_resolved_model()
    proxy_mod._last_call_meta_var.set({})
    proxy_mod._last_tokens_var.set({})
    proxy_mod.reset_turn_usage()


class TestCanonicalModelId:
    def test_registered_name_passthrough(self):
        assert models_mod.canonical_model_id("doubao-seed-2.0-mini") == "doubao-seed-2.0-mini"

    def test_upstream_alias_normalized(self):
        assert models_mod.canonical_model_id("doubao-seed-2-0-mini-260215") == "doubao-seed-2.0-mini"

    def test_unknown_name_not_guessed(self):
        assert models_mod.canonical_model_id("totally-unknown") == "totally-unknown"

    def test_empty_passthrough(self):
        assert models_mod.canonical_model_id("") == ""
        assert models_mod.canonical_model_id(None) is None

    def test_empty_upstream_does_not_shadow(self):
        """upstream_name 为空时不得把其他名字吸到该条目。"""
        assert models_mod.canonical_model_id("kimi-k3") == "kimi-k3"


class TestRecordTokensCanonical:
    def _meta_chunk(self, upstream: str):
        class _Msg:
            content = "x"
            usage_metadata = {"input_tokens": 10, "output_tokens": 5, "total_tokens": 15}
            response_metadata = {"model_name": upstream} if upstream else {}
        return _Msg()

    def _patch_min(self, monkeypatch):
        monkeypatch.setattr(proxy_mod, "_get_provider_for", lambda m: "fake")
        # 计价软路径：状态化返回，不碰真实价目
        import decimal

        import backend.infra.llm.pricing as pricing_mod

        monkeypatch.setattr(
            pricing_mod, "calculate_llm_cost_with_status",
            lambda model, q: (decimal.Decimal("0.001"), "exact", "USD", {}))

    def test_upstream_alias_aggregates_to_canonical(self, monkeypatch):
        """同一模型不同 alias → usage 记 canonical，upstream 保留透传。"""
        self._patch_min(monkeypatch)
        set_current_resolved_model(ResolvedModelContext(
            model_id="doubao-seed-2.0-mini", provider="custom-api",
            role="main", binding_source="db_binding"))

        proxy_mod.record_llm_result(self._meta_chunk("doubao-seed-2-0-mini-260215"))

        meta = proxy_mod._last_call_meta_var.get()
        assert meta["model"] == "doubao-seed-2.0-mini"           # canonical 聚合键
        assert meta["upstream_model_id"] == "doubao-seed-2-0-mini-260215"  # trace 原始值
        assert meta["configured_model_id"] == "doubao-seed-2.0-mini"
        turn = proxy_mod.get_turn_usage()
        assert set(turn.keys()) == {"doubao-seed-2.0-mini"}

    def test_alias_and_registered_aggregate_one_row(self, monkeypatch):
        """两次调用分别用 alias 与登记名返回 → turn usage 聚合为一行。"""
        self._patch_min(monkeypatch)
        set_current_resolved_model(ResolvedModelContext(
            model_id="doubao-seed-2.0-mini", provider="custom-api", role="main"))

        proxy_mod.record_llm_result(self._meta_chunk("doubao-seed-2-0-mini-260215"))
        proxy_mod.record_llm_result(self._meta_chunk("doubao-seed-2.0-mini"))

        turn = proxy_mod.get_turn_usage()
        assert set(turn.keys()) == {"doubao-seed-2.0-mini"}
        assert turn["doubao-seed-2.0-mini"]["calls"] == 2
        assert turn["doubao-seed-2.0-mini"]["total_tokens"] == 30  # 不重复不丢失

    def test_stream_chunk_without_metadata_uses_context(self, monkeypatch):
        """流式 chunk 无 metadata：canonical 来自调用上下文。"""
        self._patch_min(monkeypatch)
        set_current_resolved_model(ResolvedModelContext(
            model_id="qwen3.8-flash", provider="dashscope",
            role="tool_selector", binding_source="db_binding"))

        class _Chunk:
            content = "x"
            usage_metadata = {"input_tokens": 1, "output_tokens": 1, "total_tokens": 2}
            response_metadata = {}

        proxy_mod.record_llm_result(_Chunk())

        meta = proxy_mod._last_call_meta_var.get()
        assert meta["model"] == "qwen3.8-flash"
        assert meta["model_role"] == "tool_selector"

    def test_vendor_echoed_alias_falls_back_to_configured(self, monkeypatch):
        """注册表不认识的厂商回传真名 → 归一到 configured 模型，原始值保留。"""
        self._patch_min(monkeypatch)
        set_current_resolved_model(ResolvedModelContext(
            model_id="doubao-seed-2.0-mini", provider="custom-api", role="main"))

        proxy_mod.record_llm_result(self._meta_chunk("doubao-seed-2-0-mini-260215-echoed"))

        meta = proxy_mod._last_call_meta_var.get()
        assert meta["model"] == "doubao-seed-2.0-mini"
        assert meta["upstream_model_id"] == "doubao-seed-2-0-mini-260215-echoed"
