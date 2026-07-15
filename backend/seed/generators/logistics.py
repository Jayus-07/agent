"""物流域生成器 — FreightBooking (头程), Shipment (尾程), TrackingEvent, ReturnAuthorization。"""

from __future__ import annotations

import random
from datetime import datetime, timedelta

from backend.seed.core.generator import BaseGenerator
from backend.seed.utils import constants


class FreightBookingGenerator(BaseGenerator):
    """头程订舱生成器 — 供应商 → 海外仓的海运/空运/快递。"""

    entity_name = "freight_booking"

    def generate_one(self, ctx: "GenerationContext") -> dict:  # noqa: F821
        supp_count = ctx.count("supplier")
        wh_count = ctx.count("warehouse")
        supp_idx = self.rng.randint(0, max(0, supp_count - 1))
        origin_wh = self.rng.randint(0, max(0, wh_count - 1))
        dest_wh = self.rng.randint(0, max(0, wh_count - 1))

        mode = self.rng.choice(["SEA", "SEA", "SEA", "AIR", "EXPRESS"])
        carrier = self.rng.choice(constants.CARRIERS.get(mode, ["DHL"]))

        etd = datetime.now() - timedelta(days=self.rng.randint(30, 180))
        transit_days = {"SEA": 25, "AIR": 7, "EXPRESS": 4}.get(mode, 15)
        eta = etd + timedelta(days=transit_days + self.rng.randint(-3, 5))

        return {
            "booking_id": ctx.next_id("freight_booking", "FB"),
            "supplier_id": f"$ref:supplier:{supp_idx}",
            "origin_warehouse_id": f"$ref:warehouse:{origin_wh}",
            "dest_warehouse_id": f"$ref:warehouse:{dest_wh}",
            "carrier": carrier,
            "mode": mode,
            "etd": etd.strftime("%Y-%m-%d"),
            "eta": eta.strftime("%Y-%m-%d"),
            "status": self.rng.choice(["IN_TRANSIT", "ARRIVED", "CLEARED", "RECEIVED"]),
            "container_no": f"MSKU{self.rng.randint(100000, 999999)}",
            "tracking_no": f"{carrier[:4].upper()}{self.rng.randint(10000000, 99999999)}",
            "cost": round(self.rng.uniform(500, 8000), 2),
        }


class ShipmentGenerator(BaseGenerator):
    """尾程包裹生成器 — 为已发货/已完成订单生成物流包裹。"""

    entity_name = "shipment"

    def generate_one(self, ctx: "GenerationContext") -> dict:  # noqa: F821
        return {}

    def generate_many(self, ctx: "GenerationContext",
                      count: int | None = None) -> list[dict]:
        orders = ctx.get_entities("order")
        warehouses = ctx.get_entities("warehouse")
        if not orders or not warehouses:
            return []

        results = []
        for order_idx, order in enumerate(orders):
            status = order.get("status", "")
            if status not in ("SHIPPED", "DELIVERED", "REFUNDED"):
                continue

            wh = self.rng.choice(warehouses)
            carrier = self.rng.choice(constants.TAIL_CARRIERS)
            weight = self.rng.randint(100, 5000)

            shipped_at = datetime.now() - timedelta(days=self.rng.randint(1, 30))
            delivered_at = shipped_at + timedelta(days=self.rng.randint(1, 14))

            results.append({
                "shipment_id": ctx.next_id("shipment", "SHIP"),
                "order_id": order.get("order_id", f"$ref:order:{order_idx}"),
                "warehouse_id": wh.get("warehouse_id"),
                "carrier": carrier,
                "service_level": self.rng.choice(["STANDARD", "EXPEDITED", "EXPRESS"]),
                "tracking_no": f"1Z{self.rng.randint(1000000, 9999999)}{self.rng.randint(10000000, 99999999)}",
                "weight_g": weight,
                "declared_value": round(order.get("order_total", 50), 2),
                "status": "DELIVERED" if status == "DELIVERED" else "IN_TRANSIT",
                "shipped_at": shipped_at.isoformat(),
                "delivered_at": delivered_at.isoformat() if status == "DELIVERED" else None,
                "cost": round(self.rng.uniform(3, 30), 2),
            })

        return results


class TrackingEventGenerator(BaseGenerator):
    """物流轨迹生成器 — 为每个 Shipment 生成标准化轨迹。"""

    entity_name = "tracking_event"

    TRACKING_STEPS = [
        ("SHIPMENT_CREATED", "Shipment created", "Warehouse"),
        ("PICKED_UP", "Picked up by carrier", "Origin Facility"),
        ("IN_TRANSIT", "In transit", "Carrier Hub"),
        ("OUT_FOR_DELIVERY", "Out for delivery", "Local Facility"),
        ("DELIVERED", "Delivered", "Destination"),
    ]

    def generate_one(self, ctx: "GenerationContext") -> dict:  # noqa: F821
        return {}

    def generate_many(self, ctx: "GenerationContext",
                      count: int | None = None) -> list[dict]:
        shipments = ctx.get_entities("shipment")
        if not shipments:
            return []

        results = []
        for shp_idx, shipment in enumerate(shipments):
            base_time = datetime.now() - timedelta(days=self.rng.randint(1, 14))

            for step_idx, (code, desc, location) in enumerate(self.TRACKING_STEPS):
                event_time = base_time + timedelta(hours=step_idx * self.rng.randint(4, 48))

                results.append({
                    "tracking_event_id": ctx.next_id("tracking_event", "TE"),
                    "shipment_id": shipment.get("shipment_id", f"$ref:shipment:{shp_idx}"),
                    "status_code": code,
                    "description": desc,
                    "location": f"{location}, {ctx.faker.city()}",
                    "occurred_at": event_time.isoformat(),
                    "source": "API",
                })

                # 如果 Shipment 状态是 DELIVERED 才到最终状态
                if shipment.get("status") != "DELIVERED" and step_idx >= 3:
                    break

        return results


class ReturnAuthorizationGenerator(BaseGenerator):
    """退货授权生成器 — ~8% 订单产生退货。"""

    entity_name = "return_authorization"

    def generate_one(self, ctx: "GenerationContext") -> dict:  # noqa: F821
        return {}

    def generate_many(self, ctx: "GenerationContext",
                      count: int | None = None) -> list[dict]:
        orders = ctx.get_entities("order")
        customers = ctx.get_entities("customer")
        if not orders:
            return []

        results = []
        for order_idx, order in enumerate(orders):
            # ~8% 退货率
            if self.rng.random() > 0.08:
                continue
            if order.get("status") not in ("DELIVERED", "REFUNDED"):
                continue

            cust_idx = self.rng.randint(0, max(0, ctx.count("customer") - 1))
            requested = datetime.now() - timedelta(days=self.rng.randint(1, 60))

            results.append({
                "ra_id": ctx.next_id("return_authorization", "RA"),
                "order_id": order.get("order_id", f"$ref:order:{order_idx}"),
                "customer_id": f"$ref:customer:{cust_idx}",
                "reason": self.rng.choice([
                    "DEFECTIVE", "NOT_AS_DESCRIBED", "WRONG_SIZE",
                    "CHANGED_MIND", "ARRIVED_LATE", "DAMAGED",
                ]),
                "status": self.rng.choice(["APPROVED", "RECEIVED", "INSPECTED", "REFUNDED"]),
                "requested_at": requested.isoformat(),
                "refund_amount": round(order.get("order_total", 50) * self.rng.uniform(0.5, 1.0), 2),
            })

        return results
