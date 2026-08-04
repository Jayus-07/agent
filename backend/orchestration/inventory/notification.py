"""orchestration/inventory/notification.py — 通知调度

设计（决策 3）：
- 多维 OR 合并：命中所有满足条件的 Policy
- 合并去重收件人：发一封邮件（决策 3 选 C 合并发一封）
- 决策 2.5：通知开关规则
  - CREATE: 全发
  - UPGRADE: notify_on_upgrade=1 发
  - REMIND: notify_on_remind=1 发
  - RESOLVE: notify_on_resolve=1 发
  - REOPEN: 同 UPGRADE
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

from backend.orchestration.inventory.state_machine import AlertDecision
from backend.orchestration.inventory.store import InventoryStore, get_inventory_store
from backend.shared.logger import logger


# ─────────────────────────────────────────────────────────────
# NotificationPlan
# ─────────────────────────────────────────────────────────────

@dataclass
class NotificationPlan:
    """通知计划（dispatch 前的中间表示）"""
    action: str
    recipients: list[str] = field(default_factory=list)
    matched_policies: list[dict] = field(default_factory=list)
    skipped_policies: list[dict] = field(default_factory=list)
    reason: str = ""

    def to_dict(self) -> dict:
        return {
            "action": self.action,
            "recipients": self.recipients,
            "matched_policies": [p["policy_name"] for p in self.matched_policies],
            "skipped_policies": [p["policy_name"] for p in self.skipped_policies],
            "reason": self.reason,
        }


# ─────────────────────────────────────────────────────────────
# Dispatcher
# ─────────────────────────────────────────────────────────────

def plan(
    decision: AlertDecision,
    inventory_state: str,        # InventoryState.value
    alert_level: str,            # info/warning/critical
    category: str | None = None,  # 商品品类（用于匹配 policy）
    store: InventoryStore | None = None,
) -> NotificationPlan | None:
    """根据 AlertDecision 算出 NotificationPlan

    Returns:
        NotificationPlan 当且仅当需要通知；None 表示 SILENT
    """
    if not decision.notify:
        return None  # SILENT

    store = store or get_inventory_store()

    # 1. 找匹配的 Policies（多维 OR 合并）
    matched = store.find_matching_policies(
        alert_level=alert_level,
        inventory_state=inventory_state,
        category=category,
    )

    # 2. 按 decision.action 过滤（policy 有 notify_on_* 开关）
    field_name = {
        "CREATE": "notify_on_upgrade",  # CREATE 也用 upgrade 开关（视为"创建告警"）
        "UPGRADE": "notify_on_upgrade",
        "REOPEN": "notify_on_upgrade",  # REOPEN 类似 UPGRADE
        "REMIND": "notify_on_remind",
        "RESOLVE": "notify_on_resolve",
    }.get(decision.action)

    skipped = []
    if field_name:
        filtered = []
        for p in matched:
            if p.get(field_name, 1):  # 默认 1（发）
                filtered.append(p)
            else:
                skipped.append(p)
        matched = filtered

    if not matched:
        return None  # 所有 policy 都关掉通知

    # 3. 合并去重收件人（决策 3 选 C）
    recipients_set = set()
    for p in matched:
        email = p.get("notify_email")
        if not email:
            continue
        # 支持 ; 分隔
        for addr in email.split(";"):
            addr = addr.strip()
            if addr:
                recipients_set.add(addr)

    if not recipients_set:
        return None

    return NotificationPlan(
        action=decision.action,
        recipients=sorted(recipients_set),
        matched_policies=matched,
        skipped_policies=skipped,
        reason=decision.reason[0] if decision.reason else "",
    )


def render_email_body(plan: NotificationPlan | None, extra: dict | None = None) -> tuple[str, str]:
    """根据 plan 生成邮件 (subject, body)

    Args:
        plan: NotificationPlan（None 时返回占位 subject）
        extra: 额外上下文（商品信息、case_id 等）

    Returns:
        (subject, body)
    """
    extra = extra or {}
    product_id = extra.get("product_id", "未知")

    if plan is None:
        # 所有 policy 都关闭通知时的占位
        return (
            f"[库存告警关闭] {product_id}",
            f"商品 {product_id} 状态变化，但所有通知策略均已关闭。",
        )

    state = plan.action  # CREATE/UPGRADE/REMIND/REOPEN/RESOLVE
    policies_str = ", ".join(p["policy_name"] for p in plan.matched_policies)

    # Subject
    subject_map = {
        "CREATE": f"⚠️ [库存预警] {product_id} 触发告警",
        "UPGRADE": f"🚨 [库存升级] {product_id} 状态升级",
        "REMIND": f"⏰ [库存提醒] {product_id} 持续低库存",
        "REOPEN": f"🔁 [库存重开] {product_id} 重新告警",
        "RESOLVE": f"✅ [库存恢复] {product_id} 已恢复",
    }
    subject = subject_map.get(state, f"[库存] {product_id} {state}")

    # Body
    body_lines = [
        f"# 库存告警 - {state}",
        f"",
        f"**商品**: {product_id}",
        f"**状态**: {plan.reason}",
        f"**时间**: {extra.get('detected_at', '')}",
        f"**当前库存**: {extra.get('current_qty', 'N/A')}",
        f"**日均销量**: {extra.get('daily_sales_avg', 'N/A')}",
        f"**预计售罄天数**: {extra.get('stock_days', 'N/A')}",
        f"**Case ID**: {extra.get('case_id', 'N/A')}",
        f"**命中策略**: {policies_str}",
    ]
    if plan.skipped_policies:
        skipped_str = ", ".join(p["policy_name"] for p in plan.skipped_policies)
        body_lines.append(f"**跳过策略**: {skipped_str}")

    body = "\n".join(body_lines)
    return subject, body