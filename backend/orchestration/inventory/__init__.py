"""orchestration/inventory package — 库存告警子系统

模块：
- store.py — 数据访问（thresholds / cases / events / policies 4 张表）
- state.py — InventoryState 动态计算（min_qty + days_of_stock + sales_velocity）
- state_machine.py — 告警状态机（CREATE/UPGRADE/REMIND/RESOLVE/REOPEN）
- notification.py — 通知调度（多 Policy 合并 + 邮件渲染）

公开 API：
- get_inventory_store() — 单例访问
- evaluate() — 单商品评估
- decide() — 告警状态机决策入口
- plan() — 通知计划（多 Policy 合并）
- render_email_body() — 邮件模板渲染
"""
from backend.orchestration.inventory.store import (
    InventoryStore,
    get_inventory_store,
)
from backend.orchestration.inventory.state import (
    InventoryAssessment,
    InventoryState,
    STATE_ORDER,
    calculate_daily_sales_avg,
    calculate_stock_days,
    evaluate,
    evaluate_batch,
)
from backend.orchestration.inventory.store import (
    InventoryStore,
    get_inventory_store,
)
from backend.orchestration.inventory.state_machine import (
    AlertDecision,
    REMIND_INTERVAL_HOURS,
    decide,
    transition,
    transition_for_manual_resolve,
    transition_for_resolved_case,
)
from backend.orchestration.inventory.notification import (
    NotificationPlan,
    plan,
    render_email_body,
)

__all__ = [
    # store
    "InventoryStore",
    "get_inventory_store",
    # state
    "InventoryState",
    "InventoryAssessment",
    "STATE_ORDER",
    "evaluate",
    "evaluate_batch",
    "calculate_daily_sales_avg",
    "calculate_stock_days",
    # state_machine
    "AlertDecision",
    "REMIND_INTERVAL_HOURS",
    "decide",
    "transition",
    "transition_for_manual_resolve",
    "transition_for_resolved_case",
    # notification
    "NotificationPlan",
    "plan",
    "render_email_body",
]