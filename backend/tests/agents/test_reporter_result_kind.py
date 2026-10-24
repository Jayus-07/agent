"""reporter._result_kind / _append_degraded_disclosure 契约测试。

2026-10-25 回归：Tool Governance 去重缓存（dedupe_cache）是读操作的确定性
缓存复用（同一用户短窗口重复提问直接复用成功结果），不是降级供数——
此前 fallback_used 一律判 degraded，导致「同一问题 30s 内二次提问」的
正确答案被误挂「已使用降级结果，信息可能不完整」警告。
"""
from backend.agents.reporter.reporter import (
    _append_degraded_disclosure,
    _result_kind,
)


def _sr(**extra):
    base = {
        "step_id": "direct_1",
        "capability": "rag.search",
        "status": "success",
        "tool_status": "success",
        "output": "答案正文" + "x" * 100,
    }
    base.update(extra)
    return base


def test_dedupe_cache_hit_is_not_degraded():
    """缓存复用的成功结果不得判降级（核心回归）。"""
    assert _result_kind(_sr(fallback_used="dedupe_cache")) == "success"


def test_real_fallback_provider_still_degraded():
    """真降级供数（熔断/舱壁/预算旁路等失败路径外的新型 fallback）保持降级语义。"""
    assert _result_kind(_sr(fallback_used="circuit_breaker")) == "degraded"
    assert _result_kind(_sr(fallback_used="backup_provider")) == "degraded"


def test_degraded_flag_and_tool_status_unchanged():
    assert _result_kind(_sr(degraded=True)) == "degraded"
    assert _result_kind(_sr(tool_status="degraded")) == "degraded"


def test_disclosure_not_appended_for_dedupe_cache_hit():
    """透传答案不因缓存命中附加降级警告。"""
    answer = "正常答案"
    out = _append_degraded_disclosure(
        answer, {"direct_1": _sr(fallback_used="dedupe_cache")})
    assert out == answer


def test_disclosure_still_appended_for_real_degradation():
    out = _append_degraded_disclosure(
        "正常答案", {"direct_1": _sr(degraded=True)})
    assert "降级" in out and out != "正常答案"
