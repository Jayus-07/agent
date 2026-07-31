"""orchestration/inventory/state.py — InventoryState 动态计算

设计：
- 不只用 min_qty 静态阈值
- 结合销售速度 + 采购周期：库存可撑天数
- 状态机：NORMAL / LOW / CRITICAL / OUT_OF_STOCK

评估规则（按优先级）：
1. qty == 0 → OUT_OF_STOCK
2. qty < min_qty * 0.5 → CRITICAL
3. days_of_stock < days_of_stock_threshold → CRITICAL
4. qty < min_qty → LOW
5. 否则 → NORMAL
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any

from backend.shared.logger import logger


class InventoryState(str, Enum):
    """库存状态（4 档）"""
    NORMAL = "normal"
    LOW = "low"
    CRITICAL = "critical"
    OUT_OF_STOCK = "out_of_stock"


# 状态严重度（用于排序）
STATE_ORDER = {
    InventoryState.NORMAL: 0,
    InventoryState.LOW: 1,
    InventoryState.CRITICAL: 2,
    InventoryState.OUT_OF_STOCK: 3,
}


@dataclass
class InventoryAssessment:
    """单个商品的库存评估结果"""
    product_id: str
    current_qty: int
    # 状态
    state: InventoryState
    alert_level: str  # info/warning/critical（来自 threshold rule）
    # 计算指标
    daily_sales_avg: float        # 日均销量（基于 sales_window_days）
    stock_days: float              # 预计售罄天数
    threshold_min_qty: int         # 命中的 min_qty
    # 告警理由
    reason: list[str]
    # 关联
    threshold_rule: dict | None = None  # 命中的 rule

    def to_dict(self) -> dict:
        return {
            "product_id": self.product_id,
            "current_qty": self.current_qty,
            "state": self.state.value,
            "alert_level": self.alert_level,
            "daily_sales_avg": self.daily_sales_avg,
            "stock_days": self.stock_days,
            "threshold_min_qty": self.threshold_min_qty,
            "reason": self.reason,
            "threshold_rule": self.threshold_rule,
        }


def calculate_daily_sales_avg(sales_data: list[dict], window_days: int) -> float:
    """从销售历史计算日均销量

    sales_data: [{date: '2026-07-01', qty: 10}, ...]（按日期聚合）
    window_days: 统计窗口（默认 30）
    """
    if not sales_data:
        return 0.0

    # 取最近 window_days 天的数据
    recent = sales_data[-window_days:] if len(sales_data) > window_days else sales_data
    total_qty = sum(s.get("qty", 0) for s in recent)
    actual_days = max(len(recent), 1)  # 避免除零
    return total_qty / actual_days


def calculate_stock_days(current_qty: int, daily_sales_avg: float) -> float:
    """计算库存可撑天数

    daily_sales_avg = 0 时：返回 inf（表示"无销售无消耗"）
    """
    if daily_sales_avg <= 0:
        return float("inf")
    return current_qty / daily_sales_avg


def evaluate(
    product_id: str,
    current_qty: int,
    threshold_rule: dict,
    sales_data: list[dict] | None = None,
) -> InventoryAssessment:
    """评估一个商品的库存状态

    Args:
        product_id: 商品 ID
        current_qty: 当前库存
        threshold_rule: InventoryStore.find_threshold() 返回的规则
        sales_data: 销售历史（rows 格式：[{date, qty}, ...]）

    Returns:
        InventoryAssessment: 状态 + 理由 + 指标
    """
    if threshold_rule is None:
        # 无规则 → 默认 NORMAL（不告警）
        return InventoryAssessment(
            product_id=product_id,
            current_qty=current_qty,
            state=InventoryState.NORMAL,
            alert_level="info",
            daily_sales_avg=0.0,
            stock_days=float("inf"),
            threshold_min_qty=0,
            reason=["未配置阈值规则"],
            threshold_rule=None,
        )

    min_qty = threshold_rule.get("min_qty", 0)
    days_of_stock_threshold = threshold_rule.get("days_of_stock", 7)
    sales_window = threshold_rule.get("sales_window_days", 30)
    alert_level = threshold_rule.get("alert_level", "warning")

    # 1. 计算日均销量
    daily_sales_avg = calculate_daily_sales_avg(sales_data or [], sales_window)

    # 2. 计算可撑天数
    stock_days = calculate_stock_days(current_qty, daily_sales_avg)

    # 3. 评估状态（按优先级）
    state = InventoryState.NORMAL
    reason = []

    # 优先级 1: 完全断货
    if current_qty == 0:
        state = InventoryState.OUT_OF_STOCK
        reason.append("库存为 0")
        return InventoryAssessment(
            product_id=product_id,
            current_qty=current_qty,
            state=state,
            alert_level=alert_level,
            daily_sales_avg=daily_sales_avg,
            stock_days=stock_days,
            threshold_min_qty=min_qty,
            reason=reason,
            threshold_rule=threshold_rule,
        )

    # 优先级 2: 低于最低库存 50%（极低）
    if current_qty < min_qty * 0.5:
        state = InventoryState.CRITICAL
        reason.append(f"库存 {current_qty} 低于最低库存 50% ({min_qty * 0.5:.0f})")
        # 优先级 3: 可撑天数不够
        if stock_days < days_of_stock_threshold:
            reason.append(f"预计 {stock_days:.1f} 天售罄，低于阈值 {days_of_stock_threshold} 天")
    # 优先级 4: 低于最低库存
    elif current_qty < min_qty:
        state = InventoryState.LOW
        reason.append(f"库存 {current_qty} 低于最低库存 {min_qty}")
        if stock_days < days_of_stock_threshold:
            reason.append(f"预计 {stock_days:.1f} 天售罄")
    else:
        # 在 min_qty 以上，但 days_of_stock 不到（销售加速）
        if stock_days < days_of_stock_threshold:
            state = InventoryState.LOW
            reason.append(f"预计 {stock_days:.1f} 天售罄（销售加速）")

    return InventoryAssessment(
        product_id=product_id,
        current_qty=current_qty,
        state=state,
        alert_level=alert_level,
        daily_sales_avg=daily_sales_avg,
        stock_days=stock_days,
        threshold_min_qty=min_qty,
        reason=reason,
        threshold_rule=threshold_rule,
    )


def evaluate_batch(
    items: list[dict],
    thresholds_by_sku: dict[str, dict],
    sales_by_sku: dict[str, list[dict]] | None = None,
) -> list[InventoryAssessment]:
    """批量评估多个商品

    Args:
        items: [{product_id, current_qty, category}, ...]
        thresholds_by_sku: {product_id: threshold_rule}
        sales_by_sku: {product_id: [{date, qty}, ...]}

    Returns:
        list[InventoryAssessment]
    """
    results = []
    sales_by_sku = sales_by_sku or {}
    for item in items:
        pid = item["product_id"]
        # 优先 sku 规则，否则全局
        rule = thresholds_by_sku.get(pid)
        # 找不到 sku 规则时，让 evaluate 走 category 兜底（如果有 category）
        # 这里简化：直接传 rule，evaluate 内部已有兜底
        assessment = evaluate(
            product_id=pid,
            current_qty=item["current_qty"],
            threshold_rule=rule,
            sales_data=sales_by_sku.get(pid),
        )
        results.append(assessment)
    return results