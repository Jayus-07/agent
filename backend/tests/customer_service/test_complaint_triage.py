# -*- coding: utf-8 -*-
"""test_complaint_triage.py — 投诉分级（2026-10-08 拍板 A+B 直做）回归。

分级语义：
- 非投诉（LLM 兜底也否认）→ 安抚不建单不转人工（防试探/误入）
- medium 灰区 → LLM 仲裁；escalate=False 走收集（安抚+三要素追问，不进人工队列）
- 仲裁失败 → fail-safe 偏人工（升级）
- 同会话第二次命中投诉（collect_count≥1）→ 跳过仲裁直接升级
- high/critical → 立即升级（旧行为）
"""
from unittest.mock import MagicMock, patch

import pytest


def _mock_service(detection_severity: str, is_complaint: bool = True,
                  arbitration: dict | None = None) -> MagicMock:
    svc = MagicMock()
    svc.detect_with_llm_fallback.return_value = MagicMock(
        is_complaint=is_complaint, severity=detection_severity,
        matched_patterns=["投诉"],
    )
    svc.create_ticket.return_value = MagicMock(ticket_id="CMP-TEST-1")
    svc.build_collect_response.return_value = "安抚+三要素追问"
    svc.build_comfort_response.return_value = "安抚（工单号: CMP-TEST-1）"
    svc.llm_escalate_arbitration.return_value = arbitration
    return svc


def _base_patches(svc):
    fake_store = MagicMock()
    return [
        patch("backend.observability.metrics.record_cs_handoff"),
        patch("backend.customer_service.handoff_store.get_handoff_store"),
        patch(
            "backend.customer_service.service.complaint_service.get_complaint_service",
            return_value=svc,
        ),
        # 工单/案件镜像落库是外部边界（PG）：测试统一 mock，不写真库
        patch("backend.customer_service.ticket_store.get_ticket_store",
              return_value=fake_store),
        patch("backend.customer_service.case.service.get_case_service",
              return_value=MagicMock(), create=True),
    ]


def _run(user_message: str, state_extra: dict | None = None):
    from backend.customer_service.experts.complaint import execute_complaint

    state = {
        "user_id": "u1", "session_id": "s1", "conversation_id": "conv_1",
        "tenant_id": "default",
    }
    state.update(state_extra or {})
    return execute_complaint(user_message=user_message, state=state)


@pytest.mark.parametrize("severity,arbitration,expect_escalate", [
    ("high", {"escalate": False, "reason": "x"}, True),      # high 直接升级，仲裁结果被忽略
    ("medium", {"escalate": True, "reason": "真实严重"}, True),
    ("medium", {"escalate": False, "reason": "轻度抱怨"}, False),
])
def test_triage_matrix(severity, arbitration, expect_escalate):
    svc = _mock_service(severity, arbitration=arbitration)
    patches = _base_patches(svc)
    for p in patches:
        p.start()
    try:
        with patch(
            "backend.customer_service.handoff.lifecycle.enter_waiting_handoff_sync",
            lambda **k: {"handoff_id": "h1", "handoff_state": "waiting_human",
                         "updated_at": "2026-10-08T00:00:00+00:00"},
        ):
            result = _run("我要投诉")
    finally:
        for p in patches:
            p.stop()

    assert result["status"] == "success"
    assert result["data"].get("escalated") is expect_escalate
    if expect_escalate:
        assert result["data"]["handoff_state"] == "waiting_human"
    else:
        assert "handoff_state" not in result["data"]
        assert result["data"]["complaint_classified"] == "collect"


def test_non_complaint_reassures_without_ticket():
    svc = _mock_service("low", is_complaint=False)
    patches = _base_patches(svc)
    for p in patches:
        p.start()
    try:
        result = _run("帮我看看有什么推荐")
    finally:
        for p in patches:
            p.stop()

    assert result["data"]["complaint_classified"] == "not_complaint"
    assert "handoff_state" not in result["data"]
    svc.create_ticket.assert_not_called()
    svc.llm_escalate_arbitration.assert_not_called()


def test_arbitration_failure_failsafe_escalates():
    svc = _mock_service("medium", arbitration=None)
    patches = _base_patches(svc)
    for p in patches:
        p.start()
    try:
        with patch(
            "backend.customer_service.handoff.lifecycle.enter_waiting_handoff_sync",
            lambda **k: {"handoff_id": "h1", "handoff_state": "waiting_human",
                         "updated_at": "2026-10-08T00:00:00+00:00"},
        ):
            result = _run("再不处理就没法用了")
    finally:
        for p in patches:
            p.stop()

    assert result["data"]["escalated"] is True
    assert result["data"]["arbitration_reason"] == "arbitration_failed"


def test_repeat_complaint_skips_arbitration():
    """第二次命中投诉（已收集过一次）→ 直接升级，不再仲裁。"""
    svc = _mock_service("medium", arbitration={"escalate": False, "reason": "轻度"})
    patches = _base_patches(svc)
    for p in patches:
        p.start()
    try:
        with patch(
            "backend.customer_service.handoff.lifecycle.enter_waiting_handoff_sync",
            lambda **k: {"handoff_id": "h1", "handoff_state": "waiting_human",
                         "updated_at": "2026-10-08T00:00:00+00:00"},
        ):
            result = _run("你们这服务真的不行", {
                "cs_context": {"complaint_collect_count": 1},
            })
    finally:
        for p in patches:
            p.stop()

    assert result["data"]["escalated"] is True
    svc.llm_escalate_arbitration.assert_not_called()
