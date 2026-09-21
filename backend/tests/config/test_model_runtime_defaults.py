"""test_model_runtime_defaults.py — 角色运行策略默认值（治理 2026-09-22）。

用户规格 §23 timeout 用例的后端纯函数部分：
- 全角色预算封顶（除 ocr 长任务外 timeout×(retry+1) ≤ 60s，杜绝 60s×N）
- embedding 禁止 fallback（语义空间一致性）
- 服务层策略校验（embedding fallback / retry 上限 / 非法枚举）
"""
from __future__ import annotations

import pytest

from backend.config.model_roles import (
    FAILURE_POLICIES,
    MODEL_ROLES,
    ROLE_RUNTIME_DEFAULTS,
    runtime_defaults,
)


class TestRuntimeDefaults:
    def test_all_roles_covered(self):
        """每个注册角色都有默认策略（未登记的走保守缺省）。"""
        for role in MODEL_ROLES:
            defaults = runtime_defaults(role)
            assert defaults.timeout_seconds >= 1
            assert 0 <= defaults.max_retries <= 3
            assert defaults.failure_policy in FAILURE_POLICIES

    def test_budget_capped_no_60s_x3(self):
        """timeout × (retry+1) ≤ 60s（ocr 除外）：封死 60s×3 ≈ 180s 事故。"""
        for role, defaults in ROLE_RUNTIME_DEFAULTS.items():
            if role == "ocr":
                continue
            budget = defaults.timeout_seconds * (defaults.max_retries + 1)
            assert budget <= 60, f"{role} 预算 {budget}s 超限"

    def test_embedding_fail_fast(self):
        """embedding 禁止 fallback：静默换语义空间会让新向量查旧索引。"""
        assert runtime_defaults("embedding").failure_policy == "fail_fast"

    def test_tool_selector_fast_fail(self):
        """tool_selector 必须快失败：短超时、零重试。"""
        defaults = runtime_defaults("tool_selector")
        assert defaults.timeout_seconds <= 10
        assert defaults.max_retries == 0


class TestPolicyValidation:
    """services.model_config.ModelConfigService._validate_policy_payload。

    纯校验逻辑（无 DB）；embedding fallback 拒绝是硬约束。
    """

    def _svc(self):
        from backend.services.model_config import ModelConfigService

        return ModelConfigService()

    def test_embedding_fallback_rejected(self):
        with pytest.raises(ValueError, match="embedding.*禁止 fallback|fallback"):
            self._svc()._validate_policy_payload(
                "embedding", {"failurePolicy": "fallback"})

    def test_retry_cap(self):
        with pytest.raises(ValueError):
            self._svc()._validate_policy_payload("main", {"maxRetries": 5})

    def test_timeout_range(self):
        with pytest.raises(ValueError):
            self._svc()._validate_policy_payload("main", {"timeoutSeconds": 0})
        with pytest.raises(ValueError):
            self._svc()._validate_policy_payload("main", {"timeoutSeconds": 9999})

    def test_valid_policy_normalized(self):
        normalized = self._svc()._validate_policy_payload(
            "main", {"failurePolicy": "fallback", "timeoutSeconds": 25,
                     "maxRetries": 1, "fallbackModel": ""})
        assert normalized == {
            "fallback_model": "",
            "timeout_seconds": 25,
            "max_retries": 1,
            "failure_policy": "fallback",
        }

    def test_unknown_enum_rejected(self):
        with pytest.raises(ValueError):
            self._svc()._validate_policy_payload(
                "main", {"failurePolicy": "whatever"})
