# -*- coding: utf-8 -*-
"""test_llm_error_taxonomy.py — Step 3 模型错误统一分类单测

覆盖（用户规格）：403 配额耗尽 / 401 鉴权失败 / 429 限流 / timeout /
5xx provider error；fallback 继续生效；无 fallback 时上抛明确模型错误
（ModelProviderError 携带 error_type/provider/model，原始异常为 __cause__）；
降级话术 metadata 携带错误分类。
"""
import pytest

from backend.infra.llm import proxy as proxy_mod
from backend.infra.llm.error_taxonomy import (
    ModelProviderError,
    classify_model_error,
)


# ── 分类器五类模拟 ────────────────────────────────────────────

class _APIErr(Exception):
    def __init__(self, message: str, status_code: int | None = None):
        super().__init__(message)
        self.status_code = status_code


class TestClassifyModelError:
    def test_403_quota_exhausted(self):
        err = _APIErr("Error code: 403 - Free quota exhausted. To continue "
                      "accessing the model, please add funds.", status_code=403)
        assert classify_model_error(err) == "quota_exhausted"

    def test_401_auth_failed(self):
        err = _APIErr("Error code: 401 - Invalid API key provided",
                      status_code=401)
        assert classify_model_error(err) == "auth_failed"

    def test_403_non_quota_is_auth(self):
        err = _APIErr("Error code: 403 - permission denied for this resource",
                      status_code=403)
        assert classify_model_error(err) == "auth_failed"

    def test_429_rate_limited(self):
        err = _APIErr("Error code: 429 - Too many requests", status_code=429)
        assert classify_model_error(err) == "rate_limited"

    def test_rate_limit_by_name(self):
        class RateLimitError(Exception):
            pass
        assert classify_model_error(RateLimitError("rate limit exceeded")) == "rate_limited"

    def test_timeout(self):
        class APITimeoutError(Exception):
            pass
        assert classify_model_error(APITimeoutError("request timed out")) == "timeout"

    def test_5xx_provider_error(self):
        err = _APIErr("Error code: 502 - bad gateway", status_code=502)
        assert classify_model_error(err) == "provider_error"

    def test_unknown_defaults_provider_error(self):
        assert classify_model_error(RuntimeError("某种未识别错误")) == "provider_error"

    def test_never_raises_on_weird_error(self):
        class _Weird:
            def __str__(self):
                raise RuntimeError("str 炸了")
        assert classify_model_error(_Weird()) == "provider_error"


# ── terminal failure 三条路径 ─────────────────────────────────

@pytest.fixture(autouse=True)
def _clean_ctx():
    from backend.infra.llm.resolved_model import reset_current_resolved_model

    reset_current_resolved_model()
    yield
    reset_current_resolved_model()


class TestTerminalFailurePaths:
    def _patch_resolution(self, monkeypatch):
        monkeypatch.setattr(proxy_mod, "get_active_model_name",
                            lambda: "primary-model")
        monkeypatch.setattr(proxy_mod, "_get_provider_for",
                            lambda m: "fake-provider")

    def test_no_fallback_raises_model_provider_error(self, monkeypatch):
        """无 fallback：上抛明确模型错误（分类 + 归属 + 原始异常为 cause）。"""
        self._patch_resolution(monkeypatch)
        monkeypatch.setattr(proxy_mod, "_get_fallback_llm", lambda: None)
        monkeypatch.setattr(proxy_mod, "LLM_ALLOW_DEGRADED_ANSWER", False)

        class _PrimaryFails:
            def invoke(self, *a, **k):
                raise _APIErr("Error code: 403 - Free quota exhausted",
                              status_code=403)

        monkeypatch.setattr(proxy_mod, "_resolve_active_llm",
                            lambda: _PrimaryFails())

        with pytest.raises(ModelProviderError) as ei:
            proxy_mod.llm.invoke("问题")

        assert ei.value.error_type == "quota_exhausted"
        assert ei.value.provider == "fake-provider"
        assert ei.value.model == "primary-model"
        assert isinstance(ei.value.origin, _APIErr)
        assert isinstance(ei.value.__cause__, _APIErr)

    def test_degraded_answer_carries_error_type(self, monkeypatch):
        """降级话术路径：metadata 携带 error_type/provider/model。"""
        self._patch_resolution(monkeypatch)
        monkeypatch.setattr(proxy_mod, "_get_fallback_llm", lambda: None)
        monkeypatch.setattr(proxy_mod, "LLM_ALLOW_DEGRADED_ANSWER", True)

        class _PrimaryFails:
            def invoke(self, *a, **k):
                raise _APIErr("Error code: 429 - rate limit", status_code=429)

        monkeypatch.setattr(proxy_mod, "_resolve_active_llm",
                            lambda: _PrimaryFails())

        result = proxy_mod.llm.invoke("问题")

        meta = getattr(result, "response_metadata", {})
        assert meta.get("llm_error_type") == "rate_limited"
        assert meta.get("llm_provider") == "fake-provider"
        assert meta.get("llm_model") == "primary-model"
        assert proxy_mod.is_degraded_response(result)

    def test_fallback_still_works_with_error_type(self, monkeypatch):
        """有 fallback：继续走现有 fallback，用量记 fallback 模型。"""
        self._patch_resolution(monkeypatch)

        class _PrimaryFails:
            def invoke(self, *a, **k):
                raise _APIErr("Error code: 502 - bad gateway", status_code=502)

        class _FakeFallback:
            def invoke(self, *a, **k):
                class _Msg:
                    content = "fallback 回答"
                    usage_metadata = {"input_tokens": 1, "output_tokens": 1,
                                      "total_tokens": 2}
                    response_metadata = {}
                return _Msg()

        monkeypatch.setattr(proxy_mod, "_resolve_active_llm",
                            lambda: _PrimaryFails())
        monkeypatch.setattr(proxy_mod, "_get_fallback_llm", lambda: _FakeFallback())
        monkeypatch.setattr(proxy_mod, "_configured_fallback_model",
                            lambda: "fallback-model")
        proxy_mod.reset_turn_usage()

        result = proxy_mod.llm.invoke("问题")

        assert "fallback" in str(result.content)
        turn = proxy_mod.get_turn_usage()
        assert "fallback-model" in turn
        assert "primary-model" not in turn
