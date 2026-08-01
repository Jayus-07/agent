"""seed/demo/data.py — Phase 3 演示数据

按用户决策：
- 真实品类（手机/服装/美妆/家电/食品）
- 5-10 个商品（demo 够用）
- 30 天销售（足够看趋势）
- 阈值规则 + 通知策略（库存预警 workflow 跑得起来）

运行：
    cd backend && python -m seed.demo.data
"""
from __future__ import annotations

import random
from datetime import datetime, timedelta

from backend.shared.logger import logger


# ─────────────────────────────────────────────────────────────
# 商品（真实品类）
# ─────────────────────────────────────────────────────────────

PRODUCTS = [
    # 手机（3 款，覆盖 A 级 / B 级 / 长尾）
    {"product_id": "iPhone-15-Pro", "product_name": "iPhone 15 Pro 256G", "category": "手机", "supplier_grade": "A", "current_qty": 8,   "unit_price": 8999},
    {"product_id": "Huawei-Mate60", "product_name": "华为 Mate 60 Pro+", "category": "手机", "supplier_grade": "A", "current_qty": 3,   "unit_price": 7999},  # 即将缺货
    {"product_id": "Xiaomi-14-Pro", "product_name": "小米 14 Pro", "category": "手机", "supplier_grade": "B", "current_qty": 25,  "unit_price": 4999},

    # 服装（2 款）
    {"product_id": "Uniqlo-Fleece", "product_name": "优衣库 摇粒绒外套", "category": "服装", "supplier_grade": "A", "current_qty": 120, "unit_price": 299},
    {"product_id": "Nike-AirMax", "product_name": "Nike Air Max 90", "category": "服装", "supplier_grade": "A", "current_qty": 45,  "unit_price": 899},

    # 美妆（2 款）
    {"product_id": "PerfectDiary-Eyes", "product_name": "完美日记 动物眼影盘", "category": "美妆", "supplier_grade": "B", "current_qty": 60, "unit_price": 129},
    {"product_id": "MAC-Ruby-Woo", "product_name": "MAC Ruby Woo 口红", "category": "美妆", "supplier_grade": "A", "current_qty": 5,   "unit_price": 240},  # 即将缺货

    # 家电（2 款）
    {"product_id": "Haier-Fridge", "product_name": "海尔 变频冰箱 BCD-318", "category": "家电", "supplier_grade": "A", "current_qty": 12, "unit_price": 3999},
    {"product_id": "Dyson-V15", "product_name": "戴森 V15 吸尘器", "category": "家电", "supplier_grade": "A", "current_qty": 6,  "unit_price": 5290},

    # 食品（1 款）
    {"product_id": "Suntory-GreenTea", "product_name": "三得利 乌龙茶 500ml", "category": "食品", "supplier_grade": "B", "current_qty": 200, "unit_price": 4},
]


# ─────────────────────────────────────────────────────────────
# 销售历史（30 天，含趋势 + 异常）
# ─────────────────────────────────────────────────────────────

def generate_sales_history() -> list[dict]:
    """生成 30 天销售历史（含趋势 + 异常）"""
    random.seed(42)  # 固定种子保证可重复
    sales = []
    today = datetime.now().date()

    # 每个商品的"基础日销量 + 趋势 + 异常"
    profile = {
        # product_id: (base_qty, trend, anomaly_start, anomaly_factor)
        "iPhone-15-Pro": (5, 1.05, None, None),  # 微涨
        "Huawei-Mate60": (8, 1.10, 5, 0.3),    # 涨但 5 天前暴跌（异常）
        "Xiaomi-14-Pro": (3, 1.0, None, None),
        "Uniqlo-Fleece": (15, 1.08, None, None),  # 涨
        "Nike-AirMax": (4, 0.95, None, None),
        "PerfectDiary-Eyes": (6, 1.0, None, None),
        "MAC-Ruby-Woo": (10, 1.15, 10, 0.4),    # 涨但 10 天前骤降
        "Haier-Fridge": (1, 1.0, None, None),
        "Dyson-V15": (2, 1.0, None, None),
        "Suntory-GreenTea": (50, 1.0, None, None),
    }

    for days_ago in range(30, 0, -1):
        date = today - timedelta(days=days_ago)
        for product in PRODUCTS:
            pid = product["product_id"]
            base, trend, anom_start, anom_factor = profile[pid]
            # 基础销量 + 趋势 + 随机扰动
            qty = int(base * (trend ** (30 - days_ago)) * random.uniform(0.7, 1.3))
            # 异常（销量骤降）
            if anom_start and days_ago <= anom_start:
                qty = int(qty * anom_factor)
            sales.append({
                "date": date.isoformat(),
                "product_id": pid,
                "qty": max(qty, 0),
            })
    return sales


# ─────────────────────────────────────────────────────────────
# 阈值规则
# ─────────────────────────────────────────────────────────────

THRESHOLDS = [
    # SKU 级别（爆款特殊规则）
    {
        "rule_type": "sku",
        "product_id": "iPhone-15-Pro",
        "min_qty": 10,                # 爆款 iPhone 阈值低（5 件就告警）
        "days_of_stock": 7,
        "sales_window_days": 30,
        "alert_level": "critical",
    },
    # 品类级别（默认）
    {
        "rule_type": "category",
        "category": "手机",
        "min_qty": 15,
        "days_of_stock": 7,
        "sales_window_days": 30,
        "alert_level": "warning",
    },
    {
        "rule_type": "category",
        "category": "服装",
        "min_qty": 80,
        "days_of_stock": 10,
        "sales_window_days": 30,
        "alert_level": "warning",
    },
    {
        "rule_type": "category",
        "category": "美妆",
        "min_qty": 30,
        "days_of_stock": 7,
        "sales_window_days": 30,
        "alert_level": "warning",
    },
    {
        "rule_type": "category",
        "category": "家电",
        "min_qty": 10,
        "days_of_stock": 14,
        "sales_window_days": 30,
        "alert_level": "warning",
    },
    # Global 兜底
    {
        "rule_type": "global",
        "min_qty": 20,
        "days_of_stock": 7,
        "sales_window_days": 30,
        "alert_level": "info",
    },
]


# ─────────────────────────────────────────────────────────────
# 通知策略
# ─────────────────────────────────────────────────────────────

POLICIES = [
    {
        "policy_name": "critical_default",
        "alert_level": "critical",
        "notify_email": "ops@company.com;ceo@company.com",
    },
    {
        "policy_name": "warning_default",
        "alert_level": "warning",
        "notify_email": "ops@company.com",
    },
    {
        "policy_name": "phone_team",
        "category": "手机",
        "alert_level": "warning",
        "notify_email": "phone-team@company.com",
    },
    {
        "policy_name": "beauty_team",
        "category": "美妆",
        "alert_level": "warning",
        "notify_email": "beauty-team@company.com",
    },
    {
        "policy_name": "recover_default",
        "alert_level": None,            # 所有级别
        "inventory_state": None,        # 所有状态
        "notify_email": "ops@company.com",
        "notify_on_resolve": 1,         # 发恢复通知
        "notify_on_upgrade": 1,
        "notify_on_remind": 1,
    },
]


# ─────────────────────────────────────────────────────────────
# 演示场景定义（4 个）
# ─────────────────────────────────────────────────────────────

DEMO_SCENARIOS = [
    {
        "id": "daily_report",
        "title": "经营日报自动生成",
        "subtitle": "9:00 自动跑 · 销售 + 库存 + Agent 异常分析 + 邮件给 CEO",
        "workflow": "daily_report",
        "icon": "📊",
        "duration_estimate_s": 8,
        "expected_outcome": "邮件样例：销售摘要 + 库存异常 + Agent 分析建议",
    },
    {
        "id": "inventory_alert",
        "title": "库存风险预警",
        "subtitle": "动态评估（min_qty + 销售速度）· 状态机升级 · 多 Policy 合并通知",
        "workflow": "inventory_alert",
        "icon": "🚨",
        "duration_estimate_s": 5,
        "expected_outcome": "邮件样例：2 件商品 critical（Mate60 即将缺货 / MAC 口红）+ 1 件 warning",
    },
    {
        "id": "sales_anomaly",
        "title": "销量异常分析",
        "subtitle": "Agent 智能识别销量下跌 + RAG 找运营规则 + 综合分析",
        "workflow": None,  # 走 Agent 路径，不是 workflow
        "icon": "📉",
        "duration_estimate_s": 12,
        "expected_outcome": "Agent 分析：Mate60 / MAC 销量下跌原因（异常事件）",
    },
    {
        "id": "product_optimization",
        "title": "商品运营优化建议",
        "subtitle": "Agent 选低销量商品 + RAG 查标题规范 + 生成优化建议",
        "workflow": None,
        "icon": "💡",
        "duration_estimate_s": 10,
        "expected_outcome": "Agent 建议：iPhone-15 / Mate60 / Suntory 标题 + 卖点优化",
    },
]