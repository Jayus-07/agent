"""错误七分类统一映射测试（M3 / 台账 D3）

核心断言：
1. 三套源词表（模型层 5 / 任务层 10 / ToolStatus 8）的每个取值都有
   非 unknown 的统一类映射——源词表将来加枚举而漏配映射时，模块导入
   期的 _ensure_full_coverage 会直接 fail-fast，这里同步兜一道断言。
2. 映射语义符合台账 D3 的口径表。
3. 未知/空/None 输入健壮（→ unknown，永不抛错）。
4. SUCCESS 语义正确（→ "success"，调用方据此跳过失败计数）。
5. 指标埋点：record_tool_result 失败时打 agent_tool_error_class_total、
   成功时不打。
"""
from __future__ import annotations

import pytest

from backend.core.tool_runtime.models import ToolStatus
from backend.infra.llm.error_taxonomy import MODEL_ERROR_TYPES
from backend.observability.error_taxonomy import (
    SUCCESS,
    UNIFIED_ERROR_CLASSES,
    unify_model_error,
    unify_task_error,
    unify_tool_status,
)
from backend.tasks.error_taxonomy import TASK_ERROR_TYPES


# ==================== 1. 全覆盖 ====================

class TestFullCoverage:
    @pytest.mark.parametrize("value", MODEL_ERROR_TYPES)
    def test_model_words_covered(self, value):
        assert unify_model_error(value) in UNIFIED_ERROR_CLASSES
        assert unify_model_error(value) != "unknown"

    @pytest.mark.parametrize("value", TASK_ERROR_TYPES)
    def test_task_words_covered(self, value):
        assert unify_task_error(value) in UNIFIED_ERROR_CLASSES
        assert unify_task_error(value) != "unknown"

    @pytest.mark.parametrize("status", [s for s in ToolStatus if s is not ToolStatus.SUCCESS])
    def test_tool_status_covered(self, status):
        assert unify_tool_status(status) in UNIFIED_ERROR_CLASSES
        assert unify_tool_status(status) != "unknown"


# ==================== 2. 映射语义（台账 D3 口径表） ====================

class TestMappingSemantics:
    def test_model_mapping(self):
        assert unify_model_error("timeout") == "timeout"
        assert unify_model_error("auth_failed") == "permission_denied"
        assert unify_model_error("rate_limited") == "provider_error"
        assert unify_model_error("quota_exhausted") == "provider_error"
        assert unify_model_error("provider_error") == "provider_error"

    def test_task_mapping(self):
        assert unify_task_error("timeout") == "timeout"
        assert unify_task_error("provider_timeout") == "timeout"
        assert unify_task_error("auth_failed") == "permission_denied"
        assert unify_task_error("permission_denied") == "permission_denied"
        assert unify_task_error("validation_error") == "validation_error"
        assert unify_task_error("illegal_transition") == "validation_error"
        assert unify_task_error("internal_error") == "business_error"
        assert unify_task_error("rate_limited") == "provider_error"
        assert unify_task_error("quota_exhausted") == "provider_error"
        assert unify_task_error("provider_error") == "provider_error"

    def test_tool_status_mapping(self):
        assert unify_tool_status(ToolStatus.TIMEOUT) == "timeout"
        assert unify_tool_status(ToolStatus.UNAVAILABLE) == "network_error"
        assert unify_tool_status(ToolStatus.UNAUTHORIZED) == "permission_denied"
        assert unify_tool_status(ToolStatus.INVALID_REQUEST) == "validation_error"
        assert unify_tool_status(ToolStatus.FAILED) == "business_error"
        assert unify_tool_status(ToolStatus.DEGRADED) == "contract_error"
        assert unify_tool_status(ToolStatus.RATE_LIMITED) == "provider_error"

    def test_str_and_enum_equivalent(self):
        """DB/JSON 回读是裸字符串，必须与枚举等价。"""
        assert unify_tool_status("timeout") == unify_tool_status(ToolStatus.TIMEOUT)
        assert unify_tool_status("degraded") == "contract_error"

    def test_success_sentinel(self):
        assert unify_tool_status(ToolStatus.SUCCESS) == SUCCESS == "success"
        assert unify_tool_status("success") == SUCCESS


# ==================== 3. 健壮性 ====================

class TestRobustness:
    @pytest.mark.parametrize("bad", [None, "", "  ", "not_a_real_error"])
    def test_unknown_inputs(self, bad):
        assert unify_model_error(bad) == "unknown"
        assert unify_task_error(bad) == "unknown"
        assert unify_tool_status(bad) == "unknown"

    def test_case_and_whitespace_normalization(self):
        """大小写/空白归一是特性：脏输入命中合法词表不落 unknown。"""
        assert unify_model_error(" TIMEOUT ") == "timeout"
        assert unify_task_error("Rate_Limited") == "provider_error"

    def test_null_is_unknown(self):
        assert unify_model_error(None) == "unknown"
        assert unify_task_error(None) == "unknown"
        assert unify_tool_status(None) == "unknown"


# ==================== 4. 指标埋点 ====================

class TestMetricsEmission:
    def _result(self, status: ToolStatus) -> object:
        from backend.core.tool_runtime.models import ToolResult

        return ToolResult(
            tool_name="t", status=status, data={}, latency_ms=1.0,
        )

    def test_failed_result_emits_unified_class(self, monkeypatch):
        from backend.core.tool_runtime import metrics as tr_metrics

        recorded: list[tuple[str, dict]] = []

        def fake_record(metric_name, labels, value=1):
            recorded.append((metric_name, labels))

        monkeypatch.setattr(tr_metrics, "_record", fake_record)
        tr_metrics.record_tool_result(self._result(ToolStatus.UNAVAILABLE), domain="travel")
        hits = [l for n, l in recorded if n == "agent_tool_error_class_total"]
        assert hits == [{"tool": "t", "domain": "travel", "error_class": "network_error"}]

    def test_success_result_does_not_emit(self, monkeypatch):
        from backend.core.tool_runtime import metrics as tr_metrics

        recorded: list[tuple[str, dict]] = []

        def fake_record(metric_name, labels, value=1):
            recorded.append((metric_name, labels))

        monkeypatch.setattr(tr_metrics, "_record", fake_record)
        tr_metrics.record_tool_result(self._result(ToolStatus.SUCCESS), domain="travel")
        assert not [n for n, _ in recorded if n == "agent_tool_error_class_total"]
        # 主指标仍在（既有行为不变）
        assert ("agent_tool_calls_total",
                {"tool": "t", "domain": "travel", "status": "success"}) in recorded
