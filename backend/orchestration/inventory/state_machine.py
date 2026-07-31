"""orchestration/inventory/state_machine.py — 告警状态机

设计（决策 2 + 2.5 + 用户确认）：
- 纯规则代码（不调 Agent）
- 6 个 transition：CREATE / UPGRADE / REMIND / REOPEN / RESOLVE / SILENT
- 升级策略 D-3：升级立即通知 + critical/out_of_stock 每 4h 提醒
- 人工 override：D-1 REOPEN（不新建 case）

输入：InventoryAssessment + 当前 Case（可能为 None）
输出：AlertDecision
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Literal

from backend.orchestration.inventory.state import (
    InventoryState,
    STATE_ORDER,
)
from backend.shared.logger import logger


# ─────────────────────────────────────────────────────────────
# AlertDecision
# ─────────────────────────────────────────────────────────────

@dataclass
class AlertDecision:
    """告警决策结果"""
    action: Literal["CREATE", "UPGRADE", "REMIND", "REOPEN", "RESOLVE", "SILENT"]
    notify: bool
    reason: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "action": self.action,
            "notify": self.notify,
            "reason": self.reason,
        }


# ─────────────────────────────────────────────────────────────
# 状态机常量
# ─────────────────────────────────────────────────────────────

# 提醒间隔（决策 2 D-3）
REMIND_INTERVAL_HOURS = 4


# ─────────────────────────────────────────────────────────────
# 核心：transition
# ─────────────────────────────────────────────────────────────

def transition(
    inventory_state: InventoryState,
    inventory_level: str,
    current_case: dict | None,
    last_event: dict | None,
    now: datetime | None = None,
) -> AlertDecision:
    """根据当前库存状态 + 现有 case 决定 action

    Args:
        inventory_state: 评估后的 InventoryState
        inventory_level: 评估后的 alert_level（info/warning/critical）
        current_case: 当前 case（None 表示首次）
        last_event: 当前 case 的最后一条事件（None 表示首次）
        now: 当前时间（默认 datetime.now()）

    Returns:
        AlertDecision
    """
    now = now or datetime.now()
    reason = []

    # 情况 1: 无 case → CREATE
    if current_case is None or last_event is None:
        if inventory_state == InventoryState.NORMAL:
            return AlertDecision(action="SILENT", notify=False, reason=["首次评估，库存正常"])
        return AlertDecision(
            action="CREATE",
            notify=True,
            reason=[f"首次触发，状态 {inventory_state.value}"],
        )

    # 情况 2: 现有 case（status == open）
    case_status = current_case.get("status", "open")
    last_state_str = last_event.get("to_state")  # 上次的状态
    last_event_type = last_event.get("event_type")
    last_notified_at = current_case.get("last_notified_at")
    last_state = InventoryState(last_state_str) if last_state_str else InventoryState.NORMAL

    # 情况 2.1: 状态未变
    if STATE_ORDER[inventory_state] == STATE_ORDER[last_state]:
        # REMIND 检查（critical/out_of_stock 持续 4h）
        if inventory_state in (InventoryState.CRITICAL, InventoryState.OUT_OF_STOCK):
            if _is_reminder_due(last_notified_at, now):
                reason.append(f"持续 {inventory_state.value} 超过 4 小时")
                return AlertDecision(action="REMIND", notify=True, reason=reason)
        # 否则 SILENT
        return AlertDecision(
            action="SILENT",
            notify=False,
            reason=[f"状态未变 ({inventory_state.value})"],
        )

    # 情况 2.2: 状态升级（更严重）
    if STATE_ORDER[inventory_state] > STATE_ORDER[last_state]:
        reason.append(f"状态升级: {last_state.value} -> {inventory_state.value}")
        return AlertDecision(action="UPGRADE", notify=True, reason=reason)

    # 情况 2.3: 状态降级（恢复）
    if inventory_state == InventoryState.NORMAL:
        if case_status == "open":
            reason.append("库存恢复 NORMAL")
            return AlertDecision(action="RESOLVE", notify=True, reason=reason)
        # 已经是 resolved 状态但库存又正常 → SILENT
        return AlertDecision(action="SILENT", notify=False, reason=["库存正常，无 open case"])

    # 状态降级（low → low 但 numeric 变化？或 critical → low？保留升级分支处理）
    if STATE_ORDER[inventory_state] < STATE_ORDER[last_state] and inventory_state != InventoryState.NORMAL:
        # 状态降级但不恢复正常（如 critical → low）
        if case_status == "open":
            reason.append(f"状态降级: {last_state.value} -> {inventory_state.value}")
            return AlertDecision(
                action="RESOLVE",  # 用 RESOLVE 表示告警阶段结束
                notify=True,
                reason=reason,
            )
        return AlertDecision(action="SILENT", notify=False, reason=["状态降级，无 open case"])

    # 兜底
    return AlertDecision(action="SILENT", notify=False, reason=["未匹配的 transition"])


def transition_for_resolved_case(
    inventory_state: InventoryState,
    current_case: dict,
    now: datetime | None = None,
) -> AlertDecision:
    """已 RESOLVED 的 case + 库存又异常 → REOPEN

    决策 2.5 选 D-1：REOPEN 同一 case，不新建

    注：CLOSED 是终态，不触发 REOPEN（人工彻底关闭）
    """
    now = now or datetime.now()
    case_status = current_case.get("status")
    resolution_type = current_case.get("resolution_type")

    if case_status == "resolved":
        if inventory_state != InventoryState.NORMAL:
            return AlertDecision(
                action="REOPEN",
                notify=True,
                reason=[
                    f"已 {resolution_type or 'resolved'} 的 case 再次异常，"
                    f"状态: {inventory_state.value}",
                ],
            )
    return AlertDecision(action="SILENT", notify=False, reason=["case 已 closed 或无异常"])


def transition_for_manual_resolve(
    inventory_state: InventoryState,
    current_case: dict | None,
) -> AlertDecision:
    """人工点"已解决"

    - 当前是 open case → RESOLVE_MANUAL（不发邮件，人已知道）
    - 当前无 open case → SILENT
    """
    if current_case and current_case.get("status") == "open":
        return AlertDecision(
            action="RESOLVE",
            notify=False,
            reason=["人工标记为已解决，不发邮件"],
        )
    return AlertDecision(action="SILENT", notify=False, reason=["无 open case"])


# ─────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────

def _is_reminder_due(last_notified_at: str | None, now: datetime) -> bool:
    """检查是否需要提醒（critical/out_of_stock 持续 >= 4h）"""
    if not last_notified_at:
        # 从未通知过（不应该走到这里）→ 当作需要通知
        return True
    try:
        last_dt = datetime.fromisoformat(last_notified_at)
    except (ValueError, TypeError):
        return True
    return now - last_dt >= timedelta(hours=REMIND_INTERVAL_HOURS)


# ─────────────────────────────────────────────────────────────
# 统一入口
# ─────────────────────────────────────────────────────────────

def decide(
    inventory_state: InventoryState,
    inventory_level: str,
    current_case: dict | None,
    last_event: dict | None,
    now: datetime | None = None,
) -> AlertDecision:
    """主决策入口（统一调用）

    逻辑：
    1. case 状态是 open/None → transition
    2. case 状态是 resolved/closed → transition_for_resolved_case
    """
    now = now or datetime.now()
    case_status = (current_case or {}).get("status")

    if case_status in ("resolved", "closed"):
        return transition_for_resolved_case(inventory_state, current_case, now)

    # case_status 是 open 或 None
    return transition(
        inventory_state, inventory_level, current_case, last_event, now
    )