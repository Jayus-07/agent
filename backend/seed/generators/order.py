"""订单域生成器 — Order, OrderItem, OrderEvent。

核心特性:
- 时间序列: 12 个月跨度，按天批量生成
- 季节性: monthly_factors + weekend_boost + 高斯噪声
- 状态机: 按 profile 分布分配状态
- 复购: ~15% 客户有 2+ 单
"""

from __future__ import annotations

import random
from collections import defaultdict
from datetime import datetime, timedelta
from typing import Any

from backend.seed.core.generator import BaseGenerator
from backend.seed.utils import constants
from backend.seed.utils.distributions import (
    seasonal_weight,
    weekend_boost,
    gaussian_noise,
    weighted_choice_dict,
)


class OrderGenerator(BaseGenerator):
    """订单生成器 — 时间序列驱动，按天批量生成。

    依赖: Customer（下单人）、Channel（来源渠道）
    """

    entity_name = "order"

    def __init__(self, rng: random.Random, profile: "SeedProfile"):  # noqa: F821
        super().__init__(rng, profile)
        # 从 profile 读取分布参数
        self._status_weights = profile.get_distribution("order", "status", {
            "DELIVERED": 0.70, "SHIPPED": 0.12, "CANCELLED": 0.08,
            "REFUNDED": 0.05, "PICKING": 0.03, "PAID": 0.02,
        })
        self._monthly_factors = profile.get_distribution(
            "order", "seasonality", {}
        ).get("monthly_factors", [1.0] * 12)
        self._weekend_boost = profile.get_distribution(
            "order", "seasonality", {}
        ).get("weekend_boost", 1.25)

    def generate_one(self, ctx: "GenerationContext") -> dict:  # noqa: F821
        """单条生成（不推荐，用 generate_many）。"""
        return self._make_order(ctx, datetime.now())

    def generate_many(self, ctx: "GenerationContext",  # noqa: F821
                      count: int | None = None) -> list[dict]:
        """按时间序列批量生成订单。"""
        if count is None:
            count = self.profile.entity_count(self.entity_name)

        customer_count = ctx.count("customer")
        channel_count = ctx.count("channel")
        if customer_count == 0 or channel_count == 0:
            raise ValueError("生成订单前必须先有 Customer 和 Channel")

        # 计算每日订单量
        end_date = datetime.now()
        start_date = end_date - timedelta(days=365)
        total_days = (end_date - start_date).days

        # 每天的基础订单量
        base_per_day = count / total_days

        # 计算每天的权重
        daily_weights = []
        total_weight = 0.0
        current = start_date
        while current <= end_date:
            month_factor = seasonal_weight(current.month, self._monthly_factors)
            wend_factor = weekend_boost(current.weekday(), self._weekend_boost)
            noise = gaussian_noise(self.rng, mean=1.0, sigma=0.15)
            weight = month_factor * wend_factor * noise
            daily_weights.append(weight)
            total_weight += weight
            current += timedelta(days=1)

        # 分配每天订单数
        daily_counts = []
        remaining = count
        for i, weight in enumerate(daily_weights[:-1]):
            day_count = int(count * weight / total_weight)
            daily_counts.append(day_count)
            remaining -= day_count
        daily_counts.append(max(0, remaining))  # 最后一天补齐

        # 按天生成订单
        results = []
        customers = ctx.get_entities("customer")
        channels = ctx.get_entities("channel")

        # 复购跟踪: customer_idx → last_order_date
        customer_last_order: dict[int, datetime] = {}

        current = start_date
        for day_idx, day_count in enumerate(daily_counts):
            order_date = current + timedelta(
                hours=self.rng.randint(0, 23),
                minutes=self.rng.randint(0, 59),
            )

            for _ in range(day_count):
                # 复购逻辑: 15% 概率从已有订单的客户中选取
                if customer_last_order and self.rng.random() < 0.15:
                    eligible = [
                        c for c, d in customer_last_order.items()
                        if (order_date - d).days > 30  # 距上次 > 30 天
                    ]
                    if eligible:
                        cust_idx = self.rng.choice(eligible)
                    else:
                        cust_idx = self.rng.randint(0, customer_count - 1)
                else:
                    cust_idx = self.rng.randint(0, customer_count - 1)

                customer_last_order[cust_idx] = order_date

                channel_idx = self.rng.randint(0, channel_count - 1)

                # 分配状态
                status = weighted_choice_dict(self.rng, self._status_weights)

                # 金额: Pareto 分布，默认 10-500 USD
                from backend.seed.utils.distributions import pareto_int
                total = pareto_int(self.rng, min_val=10, max_val=500, alpha=1.8)

                # 根据状态设置时间戳
                placed_at = order_date
                paid_at = placed_at + timedelta(minutes=self.rng.randint(0, 30)) if status != "PENDING" else None

                if status in ("SHIPPED", "DELIVERED", "REFUNDED"):
                    fulfilled_at = placed_at + timedelta(hours=self.rng.randint(1, 48))
                else:
                    fulfilled_at = None

                if status in ("CANCELLED",):
                    refunded_at = placed_at + timedelta(hours=self.rng.randint(1, 24))
                elif status == "REFUNDED":
                    refunded_at = placed_at + timedelta(days=self.rng.randint(1, 30))
                else:
                    refunded_at = None

                results.append({
                    "order_id": ctx.next_id("order", "ORD"),
                    "channel_id": f"$ref:channel:{channel_idx}",
                    "customer_id": f"$ref:customer:{cust_idx}",
                    "channel_order_id": f"{channels[channel_idx].get('code', 'CH')}-{ctx.faker.bothify(text='###-#######-#######')}",
                    "status": status,
                    "order_total": float(total),
                    "currency": channels[channel_idx].get("default_currency", "USD"),
                    "placed_at": placed_at.isoformat(),
                    "paid_at": paid_at.isoformat() if paid_at else None,
                    "fulfilled_at": fulfilled_at.isoformat() if fulfilled_at else None,
                    "refunded_at": refunded_at.isoformat() if refunded_at else None,
                })

            current += timedelta(days=1)

        return results


class OrderItemGenerator(BaseGenerator):
    """订单行项目生成器 — 每个订单 1-5 个 SKU。"""

    entity_name = "order_item"

    def generate_one(self, ctx: "GenerationContext") -> dict:  # noqa: F821
        """不单独使用。"""
        return {}

    def generate_many(self, ctx: "GenerationContext",  # noqa: F821
                      count: int | None = None) -> list[dict]:
        """为所有订单生成行项目。"""
        orders = ctx.get_entities("order")
        skus = ctx.get_entities("sku")
        if not orders or not skus:
            raise ValueError("生成 OrderItem 前必须先有 Order 和 SKU")

        results = []
        for order_idx, order in enumerate(orders):
            # 已取消订单没有 item（或很少）
            if order.get("status") == "CANCELLED" and self.rng.random() < 0.5:
                continue

            num_items = self.rng.randint(1, min(5, len(skus)))
            # 给每个 item 分配订单总金额的一部分
            total = order.get("order_total", 50)
            remaining = total
            selected_skus = self.rng.sample(range(len(skus)), num_items)

            for i, sku_idx in enumerate(selected_skus):
                is_last = (i == num_items - 1)
                if is_last:
                    item_total = round(max(1.0, remaining), 2)
                else:
                    max_val = max(3.0, remaining * 0.6)
                    item_total = round(self.rng.uniform(1.0, max_val), 2)
                item_total = min(item_total, remaining)
                remaining -= item_total
                remaining = max(0, remaining)

                qty = self.rng.randint(1, 3)
                unit_price = round(max(0.01, item_total / qty), 2)

                results.append({
                    "order_item_id": ctx.next_id("order_item", "OI"),
                    "order_id": order.get("order_id", f"$ref:order:{order_idx}"),
                    "sku_id": skus[sku_idx].get("sku_id", f"$ref:sku:{sku_idx}"),
                    "line_id": i + 1,
                    "quantity": qty,
                    "unit_price": unit_price,
                    "line_total": item_total,
                    "status": order.get("status", "DELIVERED"),
                })

        return results


class OrderEventGenerator(BaseGenerator):
    """订单事件生成器 — 为每笔订单生成状态变更事件。"""

    entity_name = "order_event"

    def generate_one(self, ctx: "GenerationContext") -> dict:  # noqa: F821
        return {}

    def generate_many(self, ctx: "GenerationContext",  # noqa: F821
                      count: int | None = None) -> list[dict]:
        """为每笔订单生成状态流转事件。"""
        orders = ctx.get_entities("order")
        if not orders:
            return []

        # 状态流转链
        status_chain = ["PENDING", "PAID", "ALLOCATED", "PICKING", "SHIPPED", "DELIVERED"]

        results = []
        for order_idx, order in enumerate(orders):
            current_status = order.get("status", "DELIVERED")
            placed_at_str = order.get("placed_at", "")

            try:
                base_time = datetime.fromisoformat(placed_at_str) if placed_at_str else datetime.now()
            except (ValueError, TypeError):
                base_time = datetime.now()

            # 找到当前状态在链中的位置
            if current_status in ("CANCELLED", "REFUNDED"):
                chain = status_chain[:2] + ["CANCELLED"] if current_status == "CANCELLED" else status_chain[:6] + ["REFUNDED"]
            else:
                try:
                    end_idx = status_chain.index(current_status) + 1
                except ValueError:
                    end_idx = len(status_chain)
                chain = status_chain[:end_idx]

            if len(chain) < 2:
                continue

            # 为每个状态转换生成事件
            event_time = base_time
            for i in range(len(chain) - 1):
                from_status = chain[i]
                to_status = chain[i + 1]

                # 跳过无效转换
                if from_status not in constants.VALID_TRANSITIONS:
                    continue
                if to_status not in constants.VALID_TRANSITIONS.get(from_status, []):
                    continue

                # 时间递增
                hours_gap = self.rng.uniform(0.5, 72)
                event_time = event_time + timedelta(hours=hours_gap)

                results.append({
                    "event_id": ctx.next_id("order_event", "OE"),
                    "order_id": order.get("order_id", f"$ref:order:{order_idx}"),
                    "from_status": from_status,
                    "to_status": to_status,
                    "reason": "System",
                    "occurred_at": event_time.isoformat(),
                    "operator_id": f"user_{self.rng.randint(1, 20):03d}",
                })

        return results
