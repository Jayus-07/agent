"""selection_funnel/economics.py — 单位经济测算（纯函数，零 IO）

利润口径（毛利瀑布简化版，2026-09-17 补退货退款损耗）：
  margin = (price − cost − price×fee_rate − logistics − price×ads_ratio
            − price×refund_ratio) / price

命名纪律（2026-09-17 口径正名）：
  - margin     = 贡献利润率：扣除平台扣点/物流/推广/退款等可归属成本后的比例，
                 econ 门控唯一判据；
  - gross_margin = 商品毛利率：(price − cost) / price，仅扣采购成本，纯展示
                 不门控——避免用户把贡献利润率误读为"扣完全部费用的净利水平"。

用途：漏斗层五的淘汰判据 + 推荐理由里的数字来源。
后续要暴露给主图 Planner 时再包 Skill，域内直接 import 本模块。
"""
from __future__ import annotations

from typing import Optional


def calc_unit_economics(
    price: Optional[float],
    unit_cost: Optional[float] = None,
    fee_rate: float = 0.055,
    logistics_fee: float = 5.0,
    ads_ratio: float = 0.15,
    refund_ratio: float = 0.03,
    default_cost_ratio: float = 0.45,
) -> dict:
    """计算单件毛利。price 缺失或非正时 margin=None + 说明。

    refund_ratio：退货退款损耗率（货值损失 + 逆向运费摊销的简化口径）。

    Returns:
        {price, unit_cost, unit_cost_estimated, platform_fee, logistics_fee,
         ads_fee, refund_loss, net_profit, margin, gross_margin, warnings}
    """
    warnings: list[str] = []
    unit_cost_estimated = False
    if price is None or price <= 0:
        return {"price": price, "unit_cost": unit_cost,
                "unit_cost_estimated": False,
                "platform_fee": None, "logistics_fee": None, "ads_fee": None,
                "refund_loss": None, "net_profit": None, "margin": None,
                "gross_margin": None,
                "warnings": ["售价缺失或非正，无法测算利润"]}
    if unit_cost is None:
        unit_cost = round(price * default_cost_ratio, 2)
        unit_cost_estimated = True
        warnings.append(f"未提供进货成本，按售价 {default_cost_ratio:.0%} 估计")

    platform_fee = round(price * fee_rate, 2)
    ads_fee = round(price * ads_ratio, 2)
    refund_loss = round(price * refund_ratio, 2)
    net = round(price - unit_cost - platform_fee - logistics_fee - ads_fee - refund_loss, 2)
    margin = round(net / price, 4)
    gross_margin = round((price - unit_cost) / price, 4)
    if unit_cost >= price:
        warnings.append("成本不低于售价，该品没有利润空间")
    return {
        "price": price, "unit_cost": unit_cost,
        "unit_cost_estimated": unit_cost_estimated,
        "platform_fee": platform_fee,
        "logistics_fee": logistics_fee,
        "ads_fee": ads_fee,
        "refund_loss": refund_loss,
        "net_profit": net,
        "margin": margin,
        "gross_margin": gross_margin,
        "warnings": warnings,
    }
