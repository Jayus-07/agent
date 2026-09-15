# -*- coding: utf-8 -*-
"""test_cs_failed_reply.py — 客服专家失败话术映射回归（2026-09-15）

背景：匿名用户请求退款时，ActionExpert 抛 AuthenticationError；此前
reporter 直接落到泛化占位语「已收到您的问题…正在处理中，请稍候。」，
用户既不知失败原因也无从下手。修复后 expert_result 的 status/error
被映射为可操作提示。
"""
from backend.customer_service.reporter import (
    _assemble_answer,
    _failed_expert_reply,
)


def _assemble(expert_result: dict) -> str:
    return _assemble_answer({}, expert_result, "ai_active", {})


class TestFailedExpertReply:
    def test_authentication_error_maps_to_login_hint(self):
        out = _assemble({
            "status": "failed",
            "error": "AuthenticationError: user_id missing or anonymous in cs_context",
        })
        assert "身份" in out
        assert "登录" in out
        assert "正在处理中" not in out  # 不再是无意义占位语

    def test_permission_error(self):
        out = _failed_expert_reply({"status": "failed", "error": "PermissionDenied: 无权限"})
        assert "权限" in out

    def test_timeout_error(self):
        out = _failed_expert_reply({"status": "failed", "error": "Timeout: timed out after 30s"})
        assert "稍后重试" in out

    def test_generic_failure_actionable(self):
        out = _failed_expert_reply({"status": "failed", "error": "boom"})
        assert "转人工" in out

    def test_success_draft_not_affected(self):
        out = _assemble({"status": "success", "response_draft": "退款流程如下…"})
        assert out == "退款流程如下…"

    def test_action_result_still_has_priority(self):
        out = _assemble({
            "status": "success",
            "action_result": {"status": "success", "detail": "退款已提交"},
        })
        assert "退款已提交" in out

    def test_no_error_keeps_placeholder_path(self):
        """既无草稿也无 error：保持原有降级摘要（含占位语），不误报失败。"""
        out = _assemble({"status": "success"})
        assert "正在处理中" in out
