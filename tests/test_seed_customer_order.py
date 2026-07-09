"""订单域 + 客户域 + 库存域生成器测试。"""

import os
import sys
from collections import Counter
from datetime import datetime

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from seed_data.core.context import GenerationContext
from seed_data.core.profile import SeedProfile
from seed_data.generators.master_data import (
    BrandGenerator, CategoryGenerator, ChannelGenerator,
    WarehouseGenerator, SupplierGenerator,
)
from seed_data.generators.product import (
    ProductGenerator, SkuGenerator, ListingGenerator,
)
from seed_data.generators.customer import (
    CustomerGenerator, CustomerAddressGenerator, ReviewGenerator,
)
from seed_data.generators.order import (
    OrderGenerator, OrderItemGenerator, OrderEventGenerator,
)
from seed_data.generators.inventory import (
    InventoryLevelGenerator, InventoryTransactionGenerator,
    InventoryHealthGenerator,
)
from seed_data.utils import constants


@pytest.fixture
def full_ctx():
    """构建完整的 Context，包含所有 PR-1/PR-2 前置数据。"""
    profile = SeedProfile.from_name("tiny")
    ctx = GenerationContext(profile, seed=42)

    # Master Data
    for gen_cls in [BrandGenerator, CategoryGenerator, ChannelGenerator,
                     WarehouseGenerator, SupplierGenerator]:
        g = gen_cls(ctx.rng, ctx.profile)
        ctx.register_batch(g.entity_name, g.generate_many(ctx))

    # Product domain
    for gen_cls in [ProductGenerator, SkuGenerator, ListingGenerator]:
        g = gen_cls(ctx.rng, ctx.profile)
        ctx.register_batch(g.entity_name, g.generate_many(ctx))

    ctx.resolve_all_refs()
    return ctx


class TestCustomerGenerator:
    def test_generate_many(self, full_ctx):
        gen = CustomerGenerator(full_ctx.rng, full_ctx.profile)
        entities = gen.generate_many(full_ctx, count=50)
        assert len(entities) == 50
        for c in entities:
            assert c["customer_id"].startswith("CUST")
            assert c["country"] in ("US", "DE", "GB", "FR", "JP", "CA", "AU")
            assert c["segment"] in ("NEW", "OCCASIONAL", "REGULAR", "VIP")

    def test_segment_distribution(self, full_ctx):
        gen = CustomerGenerator(full_ctx.rng, full_ctx.profile)
        entities = gen.generate_many(full_ctx, count=200)
        segments = Counter(c["segment"] for c in entities)
        # NEW 应该是最大的段
        assert segments.get("NEW", 0) > segments.get("VIP", 0)

    def test_market_distribution(self, full_ctx):
        gen = CustomerGenerator(full_ctx.rng, full_ctx.profile)
        entities = gen.generate_many(full_ctx, count=200)
        countries = Counter(c["country"] for c in entities)
        # US 应该是最大的市场
        assert countries.get("US", 0) > 0


class TestOrderGenerator:
    def test_generate_many(self, full_ctx):
        # 先加 Customer
        cg = CustomerGenerator(full_ctx.rng, full_ctx.profile)
        full_ctx.register_batch("customer", cg.generate_many(full_ctx, count=100))

        gen = OrderGenerator(full_ctx.rng, full_ctx.profile)
        entities = gen.generate_many(full_ctx, count=200)
        assert len(entities) == 200

    def test_orders_have_timestamps(self, full_ctx):
        cg = CustomerGenerator(full_ctx.rng, full_ctx.profile)
        full_ctx.register_batch("customer", cg.generate_many(full_ctx, count=100))

        gen = OrderGenerator(full_ctx.rng, full_ctx.profile)
        entities = gen.generate_many(full_ctx, count=50)

        for o in entities:
            assert o["placed_at"], "订单必须有时 placed_at"
            # 验证 ISO 格式可解析
            datetime.fromisoformat(o["placed_at"])
            assert o["order_total"] > 0

    def test_status_distribution(self, full_ctx):
        cg = CustomerGenerator(full_ctx.rng, full_ctx.profile)
        full_ctx.register_batch("customer", cg.generate_many(full_ctx, count=100))

        gen = OrderGenerator(full_ctx.rng, full_ctx.profile)
        entities = gen.generate_many(full_ctx, count=200)

        statuses = Counter(o["status"] for o in entities)
        # DELIVERED 应该是最多的
        assert statuses.get("DELIVERED", 0) > 0
        # 所有状态应合法
        for s in statuses:
            assert s in constants.ORDER_STATUSES

    def test_repeat_customers(self, full_ctx):
        cg = CustomerGenerator(full_ctx.rng, full_ctx.profile)
        full_ctx.register_batch("customer", cg.generate_many(full_ctx, count=20))

        gen = OrderGenerator(full_ctx.rng, full_ctx.profile)
        entities = gen.generate_many(full_ctx, count=100)
        full_ctx.register_batch("order", entities)
        full_ctx.resolve_all_refs()

        # 检查是否有复购（同一 customer 多单）
        cust_orders = Counter(o["customer_id"] for o in entities
                              if not o["customer_id"].startswith("$ref:"))
        repeat_custs = [c for c, n in cust_orders.items() if n > 1]
        # 100 单中应该至少有复购
        assert len(repeat_custs) > 0, "应该有顾客复购"


class TestOrderItemGenerator:
    @pytest.fixture
    def ctx_with_orders(self, full_ctx):
        cg = CustomerGenerator(full_ctx.rng, full_ctx.profile)
        full_ctx.register_batch("customer", cg.generate_many(full_ctx, count=50))
        og = OrderGenerator(full_ctx.rng, full_ctx.profile)
        full_ctx.register_batch("order", og.generate_many(full_ctx, count=100))
        full_ctx.resolve_all_refs()
        return full_ctx

    def test_generate_items(self, ctx_with_orders):
        gen = OrderItemGenerator(ctx_with_orders.rng, ctx_with_orders.profile)
        items = gen.generate_many(ctx_with_orders)

        # 大部分订单应有 item
        assert len(items) > 0
        for item in items:
            assert item["quantity"] >= 1
            assert item["unit_price"] > 0


class TestOrderEventGenerator:
    @pytest.fixture
    def ctx_with_items(self, full_ctx):
        cg = CustomerGenerator(full_ctx.rng, full_ctx.profile)
        full_ctx.register_batch("customer", cg.generate_many(full_ctx, count=30))
        og = OrderGenerator(full_ctx.rng, full_ctx.profile)
        full_ctx.register_batch("order", og.generate_many(full_ctx, count=50))
        oig = OrderItemGenerator(full_ctx.rng, full_ctx.profile)
        full_ctx.register_batch("order_item", oig.generate_many(full_ctx))
        full_ctx.resolve_all_refs()
        return full_ctx

    def test_generate_events(self, ctx_with_items):
        gen = OrderEventGenerator(ctx_with_items.rng, ctx_with_items.profile)
        events = gen.generate_many(ctx_with_items)

        assert len(events) > 0
        for e in events:
            assert e["from_status"] in constants.ORDER_STATUSES
            assert e["to_status"] in constants.ORDER_STATUSES
            # 验证状态转换合法性
            valid_next = constants.VALID_TRANSITIONS.get(e["from_status"], [])
            assert e["to_status"] in valid_next, \
                f"非法状态转换: {e['from_status']} → {e['to_status']}"


class TestInventoryLevel:
    @pytest.fixture
    def ctx_for_inventory(self, full_ctx):
        cg = CustomerGenerator(full_ctx.rng, full_ctx.profile)
        full_ctx.register_batch("customer", cg.generate_many(full_ctx, count=30))
        og = OrderGenerator(full_ctx.rng, full_ctx.profile)
        full_ctx.register_batch("order", og.generate_many(full_ctx, count=50))
        full_ctx.resolve_all_refs()
        return full_ctx

    def test_generate_levels(self, ctx_for_inventory):
        gen = InventoryLevelGenerator(ctx_for_inventory.rng, ctx_for_inventory.profile)
        levels = gen.generate_many(ctx_for_inventory)

        assert len(levels) > 0
        for lv in levels:
            # 库存不能为负
            assert lv["qty_on_hand"] >= 0
            assert lv["qty_reserved"] >= 0
            # qty_available = on_hand - reserved
            assert lv["qty_available"] == lv["qty_on_hand"] - lv["qty_reserved"]


class TestInventoryTransaction:
    def test_generate(self, full_ctx):
        # 准备完整上下文
        cg = CustomerGenerator(full_ctx.rng, full_ctx.profile)
        full_ctx.register_batch("customer", cg.generate_many(full_ctx, count=30))
        og = OrderGenerator(full_ctx.rng, full_ctx.profile)
        full_ctx.register_batch("order", og.generate_many(full_ctx, count=50))
        oig = OrderItemGenerator(full_ctx.rng, full_ctx.profile)
        full_ctx.register_batch("order_item", oig.generate_many(full_ctx))
        ilg = InventoryLevelGenerator(full_ctx.rng, full_ctx.profile)
        full_ctx.register_batch("inventory_level", ilg.generate_many(full_ctx))
        full_ctx.resolve_all_refs()

        gen = InventoryTransactionGenerator(full_ctx.rng, full_ctx.profile)
        txns = gen.generate_many(full_ctx)

        assert len(txns) > 0
        types = {t["type"] for t in txns}
        assert "OUTBOUND" in types
        assert "INBOUND" in types


class TestFullPR3Pipeline:
    """集成测试：完整 Master → Product → Customer → Order → Inventory 链路。"""

    def test_full_pipeline(self, full_ctx):
        """验证完整链路不报错且数据一致。"""
        profile = full_ctx.profile
        rng = full_ctx.rng

        # Customer
        cg = CustomerGenerator(rng, profile)
        full_ctx.register_batch("customer", cg.generate_many(full_ctx, count=100))

        # CustomerAddress
        ag = CustomerAddressGenerator(rng, profile)
        full_ctx.register_batch("customer_address", ag.generate_many(full_ctx))

        # Order
        og = OrderGenerator(rng, profile)
        full_ctx.register_batch("order", og.generate_many(full_ctx, count=500))

        # OrderItem
        oig = OrderItemGenerator(rng, profile)
        full_ctx.register_batch("order_item", oig.generate_many(full_ctx))

        # OrderEvent
        oeg = OrderEventGenerator(rng, profile)
        full_ctx.register_batch("order_event", oeg.generate_many(full_ctx))

        # Inventory
        ilg = InventoryLevelGenerator(rng, profile)
        full_ctx.register_batch("inventory_level", ilg.generate_many(full_ctx))

        itg = InventoryTransactionGenerator(rng, profile)
        full_ctx.register_batch("inventory_transaction", itg.generate_many(full_ctx))

        ihg = InventoryHealthGenerator(rng, profile)
        full_ctx.register_batch("inventory_health", ihg.generate_many(full_ctx))

        # Review
        rg = ReviewGenerator(rng, profile)
        full_ctx.register_batch("review", rg.generate_many(full_ctx, count=80))

        # 解析 FK
        resolved = full_ctx.resolve_all_refs()
        assert resolved > 0, "应该有 FK 引用被解析"

        # 验证核心链: Review → Order → Customer
        for rev in full_ctx.get_entities("review"):
            order_id = rev["order_id"]
            customer_id = rev["customer_id"]

            # 不应有未解析的 $ref
            assert not str(order_id).startswith("$ref:")
            assert not str(customer_id).startswith("$ref:")

        # 验证 Order → Customer
        for order in full_ctx.get_entities("order"):
            cid = order["customer_id"]
            assert not str(cid).startswith("$ref:")
            assert order["order_total"] > 0
            assert order["placed_at"] is not None
