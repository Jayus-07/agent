"""库存域生成器 — InventoryLevel, InventoryTransaction, InventoryHealth。

库存数据与 SKU、Warehouse、Order 联动。
"""

from __future__ import annotations

import random
from datetime import datetime, timedelta
from collections import defaultdict

from seed_data.core.generator import BaseGenerator
from seed_data.utils.distributions import weighted_choice_dict


class InventoryLevelGenerator(BaseGenerator):
    """库存水平生成器 — 每个 SKU × 每个仓库一条记录。

    库存分配按 profile 中的 warehouse_allocation 分布。
    """

    entity_name = "inventory_level"

    def __init__(self, rng: random.Random, profile: "SeedProfile"):  # noqa: F821
        super().__init__(rng, profile)
        self._warehouse_alloc = profile.get_distribution(
            "inventory", "warehouse_allocation", {}
        )

    def generate_one(self, ctx: "GenerationContext") -> dict:  # noqa: F821
        return {}

    def generate_many(self, ctx: "GenerationContext",  # noqa: F821
                      count: int | None = None) -> list[dict]:
        """为每个 SKU 分配库存到各仓。"""
        skus = ctx.get_entities("sku")
        warehouses = ctx.get_entities("warehouse")
        if not skus or not warehouses:
            raise ValueError("生成库存前必须先有 SKU 和 Warehouse")

        # 构建仓库代码 → warehouse 对象映射
        wh_map = {w.get("code", ""): w for w in warehouses}

        results = []
        for sku in skus:
            for wh_code, alloc_pct in self._warehouse_alloc.items():
                wh = wh_map.get(wh_code)
                if not wh:
                    continue

                # 基础库存: 50-500 单位
                base_stock = self.rng.randint(50, 500)
                qty_on_hand = int(base_stock * self.rng.uniform(0.3, 2.0))
                qty_reserved = int(qty_on_hand * self.rng.uniform(0, 0.3))

                results.append({
                    "warehouse_id": wh.get("warehouse_id"),
                    "sku_id": sku.get("sku_id"),
                    "qty_on_hand": max(0, qty_on_hand),
                    "qty_reserved": max(0, qty_reserved),
                    "qty_available": max(0, qty_on_hand - qty_reserved),
                    "qty_in_transit": self.rng.randint(0, 200),
                    "last_updated": datetime.now().isoformat(),
                    "sync_source": "SEED",
                })

        return results


class InventoryTransactionGenerator(BaseGenerator):
    """库存事务生成器 — 不可变流水（append-only）。

    根据订单生成 OUTBOUND 事务，再生成 INBOUND 补充库存。
    """

    entity_name = "inventory_transaction"

    def generate_one(self, ctx: "GenerationContext") -> dict:  # noqa: F821
        return {}

    def generate_many(self, ctx: "GenerationContext",  # noqa: F821
                      count: int | None = None) -> list[dict]:
        """根据订单和库存生成事务流水。"""
        skus = ctx.get_entities("sku")
        warehouses = ctx.get_entities("warehouse")
        order_items = ctx.get_entities("order_item")
        orders = ctx.get_entities("order")

        if not skus or not warehouses:
            return []

        results = []

        # 为每个已发货/Delivered 的订单生成 OUTBOUND 事务
        order_status_map = {}
        for o in orders:
            order_status_map[o.get("order_id")] = o.get("status")

        for oi in order_items:
            order_id = oi.get("order_id")
            status = order_status_map.get(order_id, "")
            if status not in ("SHIPPED", "DELIVERED", "REFUNDED"):
                continue

            sku_id = oi.get("sku_id")
            qty = oi.get("quantity", 1)

            # 随机分配仓库
            wh = self.rng.choice(warehouses)

            # 发货时间（从订单时间推算）
            try:
                occurred = datetime.now() - timedelta(days=self.rng.randint(1, 30))
            except Exception:
                occurred = datetime.now()

            results.append({
                "txn_id": ctx.next_id("inventory_transaction", "ITXN"),
                "warehouse_id": wh.get("warehouse_id"),
                "sku_id": sku_id,
                "type": "OUTBOUND",
                "quantity": -qty,
                "ref_type": "ORDER",
                "ref_id": order_id,
                "occurred_at": occurred.isoformat(),
                "operator_id": "SYSTEM",
            })

        # 为每个 SKU × 仓库生成 INBOUND 补充库存事务
        for sku in skus:
            for wh in warehouses:
                # 每 SKU 每仓 1-3 次入库
                for _ in range(self.rng.randint(1, 3)):
                    qty = self.rng.randint(20, 300)
                    occurred = datetime.now() - timedelta(days=self.rng.randint(5, 90))
                    results.append({
                        "txn_id": ctx.next_id("inventory_transaction", "ITXN"),
                        "warehouse_id": wh.get("warehouse_id"),
                        "sku_id": sku.get("sku_id"),
                        "type": "INBOUND",
                        "quantity": qty,
                        "ref_type": "PURCHASE_ORDER",
                        "ref_id": f"PO-{self.rng.randint(100, 999)}",
                        "occurred_at": occurred.isoformat(),
                        "operator_id": "SYSTEM",
                    })

        return results


class InventoryHealthGenerator(BaseGenerator):
    """库存健康度生成器 — 基于库存水平的分析指标。"""

    entity_name = "inventory_health"

    def generate_one(self, ctx: "GenerationContext") -> dict:  # noqa: F821
        return {}

    def generate_many(self, ctx: "GenerationContext",  # noqa: F821
                      count: int | None = None) -> list[dict]:
        """为每个有库存的 SKU × 仓库生成健康度记录。"""
        levels = ctx.get_entities("inventory_level")
        if not levels:
            return []

        results = []
        for level in levels:
            qty = level.get("qty_on_hand", 0)
            # 根据库存量推算健康状态
            if qty > 200:
                status = "HEALTHY"
            elif qty > 50:
                status = self.rng.choice(["HEALTHY", "SLOW"])
            elif qty > 10:
                status = self.rng.choice(["SLOW", "AGED"])
            else:
                status = self.rng.choice(["AGED", "DEAD"])

            results.append({
                "health_id": ctx.next_id("inventory_health", "IH"),
                "sku_id": level.get("sku_id"),
                "warehouse_id": level.get("warehouse_id"),
                "days_of_supply": self.rng.randint(0, 180),
                "sell_through_rate": round(self.rng.uniform(0, 100), 2),
                "age_bucket": self.rng.choice(["0-30", "31-90", "91-180", "180+"]),
                "last_sale_at": (datetime.now() - timedelta(days=self.rng.randint(0, 90))).isoformat(),
                "status": status,
            })

        return results
