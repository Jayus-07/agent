"""runner × input_guard 显式转人工豁免单测（2026-09-17）。

根因回归：guard 的「模糊问题→clarify」规则曾把「我想转接人工客服」
短路在图外（action=clarify layer=rule 13ms 直接回澄清话术），
cs_prefilter 直通层永远收不到该消息。runner 现对 CLARIFY + 显式
转人工豁免放行（BLOCK 不豁免）。
"""
import pytest

from backend.orchestration.graph.runner import _is_explicit_handoff
from backend.security.input_guard import GuardAction, get_input_guard


HANDOFF_PHRASE = "我想转接人工客服"


class _FakeResult:
    """模拟 GuardResult 的最小结构（model_copy 语义用 dict 替代）。"""

    def __init__(self, action):
        self.action = action
        self.category = "ambiguous"
        self.confidence = 0.55
        self.reason = "rule clarify"

    def model_copy(self, update):
        new = _FakeResult(update.get("action", self.action))
        new.confidence = update.get("confidence", self.confidence)
        new.reason = update.get("reason", self.reason)
        return new


def _exempt(question: str, guard_result):
    """复刻 runner 内的豁免判定分支（若 runner 改动需同步此处语义）。"""
    from backend.security.input_guard import GuardAction as GA

    if guard_result.action in (GA.BLOCK, GA.CLARIFY):
        if guard_result.action == GA.CLARIFY and _is_explicit_handoff(question):
            return guard_result.model_copy(update={
                "action": GA.ALLOW,
                "confidence": 1.0,
                "reason": f"explicit_handoff_bypass: {guard_result.reason}",
            })
        return None  # 短路
    return guard_result


class TestExplicitHandoffGuardExemption:
    def test_guard_actually_clarifies_handoff_phrase(self):
        """根因确认：真实 guard 对转人工话术返回 CLARIFY（而非 ALLOW）。"""
        result = get_input_guard().guard(HANDOFF_PHRASE, session_id="t-guard-1")
        assert result.action == GuardAction.CLARIFY

    def test_detector_hits_handoff_phrase(self):
        assert _is_explicit_handoff(HANDOFF_PHRASE) is True
        assert _is_explicit_handoff("帮我转人工") is True
        assert _is_explicit_handoff("不要机器人，找真人客服") is True

    def test_detector_not_misfire_on_business(self):
        assert _is_explicit_handoff("人工成本分析") is False
        assert _is_explicit_handoff("查询技术部有多少人") is False

    def test_clarify_handoff_is_exempted_to_allow(self):
        result = _exempt(HANDOFF_PHRASE, _FakeResult(GuardAction.CLARIFY))
        assert result is not None
        assert result.action == GuardAction.ALLOW
        assert result.confidence == 1.0
        assert "explicit_handoff_bypass" in result.reason

    def test_clarify_business_still_short_circuits(self):
        """真模糊业务问题不受豁免影响，仍短路澄清。"""
        assert _exempt("帮我看看那个数据", _FakeResult(GuardAction.CLARIFY)) is None

    def test_block_never_exempted(self):
        """安全拦截（BLOCK）即使命中转人工关键词也不放行。"""
        assert _exempt("转人工", _FakeResult(GuardAction.BLOCK)) is None
