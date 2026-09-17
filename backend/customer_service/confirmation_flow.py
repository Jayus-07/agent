"""customer_service/confirmation_flow.py — 确认流程单一实现（P1 重构）

背景（audit-report §P0-2）：pending_handler.py 与 experts/action.py 存在
整段重复的确认处理逻辑且已漂移（仅前者有追问 retry 上限），快照加载
降级时确认请求会静默落入旧实现。本模块把「过期检查 → 意图检测 →
原子认领 → 状态流转 → 沙盒执行 → 审计构建」收敛为唯一实现，
两个调用方只做输出格式映射。

状态机（confirmation.py，不变）：
  PENDING → CONFIRMED → EXECUTING → SUCCESS / FAILED
  PENDING → CANCELLED / EXPIRED

幂等（P1 新增）：用户确认后先经 ConfirmationStore.claim_for_execution
原子认领（DB 条件 UPDATE pending→confirmed），认领失败 = 重复提交，
返回 duplicate 而不重复执行。
"""
from __future__ import annotations

from dataclasses import dataclass, field

# 注意：以下状态机函数一律经模块属性访问（confirmation_sm.is_expired(...)），
# 而非 from-import 快照 —— 既有测试通过
# patch("backend.customer_service.confirmation.is_expired") 打桩，
# 模块属性访问才能让 patch 生效（2026-09-17 P1 重构保持测试契约）。
from backend.customer_service import confirmation as confirmation_sm
from backend.shared.logger import logger


@dataclass
class ConfirmationOutcome:
    """确认流程结果 — 与输出格式解耦，调用方自行映射。"""

    kind: str                    # expired | duplicate | success | failed | cancelled | reask
    answer: str                  # 用户可见回复
    action_type: str = ""
    confirmation_state: str = ""  # 终态或 pending（reask）
    action_result: dict | None = None
    audit_entry: dict | None = None
    pending_action: dict | None = None  # reask 时返回（含 retry_count）
    extra_audit_entries: list = field(default_factory=list)


def process_confirmation(
    pending_action: dict,
    user_message: str,
    user_id: str,
    session_id: str,
) -> ConfirmationOutcome:
    """处理用户对 pending action 的响应 — 唯一入口。"""
    action_type = pending_action.get("action_type", "unknown")

    # ── 1. 过期检查（响应式；后台 Celery 扫描见 P2.4）──
    if confirmation_sm.is_expired(pending_action):
        return _handle_expired(pending_action, user_id, session_id)

    # ── 2. 意图检测 ──
    user_intent = confirmation_sm.detect_confirmation_intent(user_message)

    if user_intent == confirmation_sm.ConfirmationIntent.CONFIRM:
        return _handle_confirm(pending_action, user_id, session_id)

    if user_intent == confirmation_sm.ConfirmationIntent.CANCEL:
        return _handle_cancel(pending_action, user_id, session_id)

    # ── 3. 意图不明 → 追问（带 retry 上限，防止无限追问）──
    return _handle_reask(pending_action, user_id, session_id)


# ── 过期 ─────────────────────────────────────────────────────

def _handle_expired(
    pending_action: dict, user_id: str, session_id: str,
) -> ConfirmationOutcome:
    from backend.customer_service.audit import build_audit_entry
    from backend.customer_service.confirmation_store import get_confirmation_store
    from backend.observability.metrics import record_cs_confirmation

    CS = confirmation_sm.ConfirmationState
    confirmation_sm.transition(CS.PENDING_CONFIRMATION, CS.EXPIRED)
    # P1：终态写 expired（此前被 repo.clear 一律写成 cancelled）
    get_confirmation_store().clear(
        user_id, session_id, final_state=CS.EXPIRED.value,
    )
    record_cs_confirmation("expired")

    action_type = pending_action.get("action_type", "unknown")
    audit_entry = build_audit_entry(
        user_id=user_id,
        action_type=action_type,
        result="denied",
        detail="confirmation expired",
    )
    logger.info("[CSConfirmationFlow] expired: type=%s user=%s", action_type, user_id)

    return ConfirmationOutcome(
        kind="expired",
        answer="操作确认已超时，请重新发起。",
        action_type=action_type,
        confirmation_state=confirmation_sm.ConfirmationState.EXPIRED.value,
        audit_entry=audit_entry,
    )


# ── 确认 → 执行 ──────────────────────────────────────────────

def _handle_confirm(
    pending_action: dict, user_id: str, session_id: str,
) -> ConfirmationOutcome:
    from backend.customer_service.audit import build_audit_entry
    from backend.customer_service.confirmation_store import get_confirmation_store
    from backend.customer_service.experts.action import (
        _ACTION_TYPE_LABELS,
        _simulate_execute,
    )
    from backend.observability.metrics import record_cs_action, record_cs_confirmation

    store = get_confirmation_store()
    CS = confirmation_sm.ConfirmationState
    action_type = pending_action.get("action_type", "unknown")

    # ── 原子认领（幂等闸门）：认领失败 = 已被处理，绝不重复执行 ──
    claimed_id = store.claim_for_execution(user_id, session_id)
    if claimed_id is None:
        audit_entry = build_audit_entry(
            user_id=user_id,
            action_type=action_type,
            result="denied",
            detail="duplicate confirm ignored (already processed)",
        )
        logger.info(
            "[CSConfirmationFlow] duplicate confirm ignored: type=%s user=%s",
            action_type, user_id,
        )
        return ConfirmationOutcome(
            kind="duplicate",
            answer="该操作已处理过，无需重复确认。如需再次操作请重新发起。",
            action_type=action_type,
            confirmation_state=CS.USER_CONFIRMED.value,
            audit_entry=audit_entry,
        )

    confirmation_sm.transition(CS.PENDING_CONFIRMATION, CS.USER_CONFIRMED)
    confirmation_sm.transition(CS.USER_CONFIRMED, CS.EXECUTING)
    record_cs_confirmation("confirmed")

    try:
        record = _simulate_execute(pending_action)
        confirmation_sm.transition(CS.EXECUTING, CS.SUCCESS)
        store.clear(user_id, session_id, final_state=CS.SUCCESS.value)
        record_cs_action(action_type, "success")

        action_result = {
            "action_type": action_type,
            "status": "success",
            "action_record": record.to_dict(),
        }
        audit_entry = build_audit_entry(
            user_id=user_id,
            action_type=action_type,
            result="success",
            target_type=pending_action.get("target_type", ""),
            target_id=pending_action.get("target_id", ""),
            detail=f"simulated execution, action_id={record.action_id}",
        )

        label = _ACTION_TYPE_LABELS.get(action_type, action_type)
        answer = (
            f"✅ 操作已提交成功！\n\n"
            f"**操作类型:** {label}\n"
            f"*（当前为模拟模式，实际写操作将在 Phase 6 启用）*"
        )
        logger.info("[CSConfirmationFlow] confirmed+success: type=%s", action_type)

        return ConfirmationOutcome(
            kind="success",
            answer=answer,
            action_type=action_type,
            confirmation_state=CS.SUCCESS.value,
            action_result=action_result,
            audit_entry=audit_entry,
        )

    except Exception as e:
        confirmation_sm.transition(CS.EXECUTING, CS.FAILED)
        store.clear(user_id, session_id, final_state=CS.FAILED.value)
        record_cs_action(action_type, "failed")
        audit_entry = build_audit_entry(
            user_id=user_id,
            action_type=action_type,
            result="failure",
            detail=str(e),
        )
        logger.error("[CSConfirmationFlow] execution failed: %s", e, exc_info=True)

        return ConfirmationOutcome(
            kind="failed",
            answer="操作执行失败，请稍后重试或联系人工客服。",
            action_type=action_type,
            confirmation_state=CS.FAILED.value,
            audit_entry=audit_entry,
        )


# ── 取消 ─────────────────────────────────────────────────────

def _handle_cancel(
    pending_action: dict, user_id: str, session_id: str,
) -> ConfirmationOutcome:
    from backend.customer_service.audit import build_audit_entry
    from backend.customer_service.confirmation_store import get_confirmation_store
    from backend.observability.metrics import record_cs_confirmation

    CS = confirmation_sm.ConfirmationState
    confirmation_sm.transition(CS.PENDING_CONFIRMATION, CS.USER_CANCELLED)
    get_confirmation_store().clear(user_id, session_id)  # final_state=cancelled
    record_cs_confirmation("cancelled")

    action_type = pending_action.get("action_type", "unknown")
    audit_entry = build_audit_entry(
        user_id=user_id,
        action_type=action_type,
        result="denied",
        detail="user cancelled",
    )
    logger.info(
        "[CSConfirmationFlow] cancelled: type=%s user=%s", action_type, user_id,
    )

    return ConfirmationOutcome(
        kind="cancelled",
        answer="操作已取消。如有其他问题，请随时咨询。",
        action_type=action_type,
        confirmation_state=CS.USER_CANCELLED.value,
        audit_entry=audit_entry,
    )


# ── 追问 ─────────────────────────────────────────────────────

def _handle_reask(
    pending_action: dict, user_id: str, session_id: str,
) -> ConfirmationOutcome:
    from backend.config.customer_service import CS_MAX_CONFIRMATION_RETRIES
    from backend.customer_service.confirmation_store import get_confirmation_store

    proposal_text = pending_action.get("proposal_text", "")
    retries = int(pending_action.get("retry_count", 0)) + 1
    if retries > CS_MAX_CONFIRMATION_RETRIES:
        logger.warning(
            "[CSConfirmationFlow] 追问达上限 (%d 次)，按过期处理: user=%s",
            CS_MAX_CONFIRMATION_RETRIES, user_id,
        )
        return _handle_expired(pending_action, user_id, session_id)

    updated = {**pending_action, "retry_count": retries}
    get_confirmation_store().save(user_id, session_id, updated)

    return ConfirmationOutcome(
        kind="reask",
        answer=(
            f"您有一个待确认的操作：\n\n{proposal_text}\n\n"
            "请回复「确认」继续，或「取消」放弃。"
        ),
        action_type=pending_action.get("action_type", "unknown"),
        confirmation_state=confirmation_sm.ConfirmationState.PENDING_CONFIRMATION.value,
        pending_action=updated,
    )
