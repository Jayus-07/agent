"""请求级模型覆盖校验的契约测试（B.9 / docs/model-config-admin-ui-design.md §13.1）。

覆盖两层，且刻意分开断言，因为它们**必须**是同一份规则：
  - models.validate_override_model —— 规则单点（未注册 / Ollama 未启用 / Key 缺失 / 通过）
  - chat._validate_model_override —— API 边界 fail-fast（400）与合法放行
  - proxy.set_request_model  —— 保持宽容静默（由 test_llm_bind_tools.py 的 4 例锁定）
"""
import pytest
from fastapi import HTTPException

from backend.infra.llm.models import validate_override_model


class TestValidateOverrideModel:
    """规则单点：返回值即 (ok, reason)，reason 面向用户。"""

    def test_empty_means_no_override(self):
        assert validate_override_model("") == (True, "")
        assert validate_override_model("   ") == (True, "")
        assert validate_override_model(None) == (True, "")  # type: ignore[arg-type]

    def test_unregistered_model_rejected(self):
        ok, reason = validate_override_model("nonexistent-model-xyz")
        assert ok is False
        assert "未知模型" in reason

    def test_ollama_disabled_rejected(self):
        # 显式注入开关，不依赖运行时配置（与 proxy 的 monkeypatch 路径一致）
        ok, reason = validate_override_model("qwen2.5:3b", ollama_enabled=False)
        assert ok is False
        assert "Ollama" in reason

    def test_ollama_enabled_passes(self):
        ok, reason = validate_override_model("qwen2.5:3b", ollama_enabled=True)
        assert ok is True
        assert reason == ""

    def test_missing_provider_key_rejected(self, monkeypatch):
        monkeypatch.delenv("VLLM_API_KEY", raising=False)
        ok, reason = validate_override_model("Qwen/Qwen3-32B-AWQ")  # provider=vllm
        assert ok is False
        assert "VLLM_API_KEY" in reason

    def test_valid_model_with_key_passes(self, monkeypatch):
        monkeypatch.setenv("QWEN_TP_API_KEY", "sk-sp-test")
        ok, reason = validate_override_model("qwen3.7-plus@tp")
        assert ok is True
        assert reason == ""


class TestApiBoundaryFailFast:
    """API 边界：空放行、非法 400、合法放行。"""

    def test_absent_or_empty_passes(self):
        from backend.app.api.routes.chat import _validate_model_override

        _validate_model_override(None)
        _validate_model_override("")

    def test_unregistered_raises_400(self):
        from backend.app.api.routes.chat import _validate_model_override

        with pytest.raises(HTTPException) as ei:
            _validate_model_override("nonexistent-model-xyz")
        assert ei.value.status_code == 400
        detail = ei.value.detail
        assert detail["error"] == "InvalidModelOverride"
        assert "未知模型" in detail["message"]

    def test_missing_key_raises_400(self, monkeypatch):
        from backend.app.api.routes.chat import _validate_model_override

        monkeypatch.delenv("VLLM_API_KEY", raising=False)
        with pytest.raises(HTTPException) as ei:
            _validate_model_override("Qwen/Qwen3-32B-AWQ")
        assert ei.value.status_code == 400
        assert ei.value.detail["error"] == "InvalidModelOverride"

    def test_valid_model_passes(self, monkeypatch):
        from backend.app.api.routes.chat import _validate_model_override

        monkeypatch.setenv("QWEN_TP_API_KEY", "sk-sp-test")
        _validate_model_override("qwen3.7-plus@tp")
