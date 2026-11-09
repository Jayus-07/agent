"""memory_store_tool 证据门回归（安全审计 #50，ADR-0012 D6 证据门实现）。

修复前：模型可被诱导在无用户请求时调用 memory_store_tool 写入 verified
explicit 记忆，且 explicit 此后阻断 inferred 修正（CONFLICT_BLOCKED_EXPLICIT）
——投毒即固化。修复后：写入前校验当前会话最近用户消息确实表达过该内容
（规范化子串或关键词重合），无证据即拒绝（fail-closed）。
"""
from __future__ import annotations

from backend.tools.memory import _has_user_evidence


class TestEvidenceGate:
    def test_exact_restatement_passes(self):
        """用户原话明确表达 → 放行（子串通道）。"""
        texts = ["请记住我对花生过敏，以后推荐菜避开"]
        assert _has_user_evidence("用户对花生过敏", texts)

    def test_keyword_overlap_passes(self):
        """措辞不同但实质词全部命中同一条消息 → 放行（关键词通道）。"""
        texts = ["我平时只用微信支付，别的都不方便"]
        assert _has_user_evidence("用户偏好微信支付", texts)

    def test_no_evidence_rejected(self):
        """用户从未说过 → 拒绝（投毒场景：文档内容不在会话历史）。"""
        texts = ["帮我总结一下这份报告的要点"]
        assert not _has_user_evidence("用户对花生过敏", texts)

    def test_empty_history_rejected(self):
        """无任何用户消息 → 拒绝。"""
        assert not _has_user_evidence("用户喜欢深色主题", [])

    def test_partial_overlap_insufficient(self):
        """仅个别词重合（如只有「报告」）不构成证据。"""
        texts = ["这份报告太长了"]
        assert not _has_user_evidence("用户的报告风格偏好是简洁", texts)

    def test_normalization_robust(self):
        """标点/空格/全半角差异不影响命中。"""
        texts = ["请记住：我的紧急联系人 是 13800138000！"]
        assert _has_user_evidence("紧急联系人是13800138000", texts)
