"""PR-4 物流域 + 广告域 + 报表域测试。"""

import os
import sys

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
from seed_data.generators.logistics import (
    FreightBookingGenerator, ShipmentGenerator,
    TrackingEventGenerator, ReturnAuthorizationGenerator,
)
from seed_data.generators.advertising import (
    AdAccountGenerator, CampaignGenerator, AdGroupGenerator,
    AdGenerator, SpendRecordGenerator, PerformanceMetricGenerator,
)
from seed_data.generators.report import (
    ReportDefinitionGenerator, ReportExecutionGenerator,
)


@pytest.fixture
def full_ctx():
    """完整上下文 — 包含所有前置数据。"""
    profile = SeedProfile.from_name("tiny")
    ctx = GenerationContext(profile, seed=42)

    rng, prof = ctx.rng, ctx.profile
    for gen_cls in [BrandGenerator, CategoryGenerator, ChannelGenerator,
                     WarehouseGenerator, SupplierGenerator]:
        g = gen_cls(rng, prof)
        ctx.register_batch(g.entity_name, g.generate_many(ctx))
    for gen_cls in [ProductGenerator, SkuGenerator, ListingGenerator]:
        g = gen_cls(rng, prof)
        ctx.register_batch(g.entity_name, g.generate_many(ctx))
    # Customer + Order chain
    cg, og, oig, oeg = (CustomerGenerator(rng, prof), OrderGenerator(rng, prof),
                         OrderItemGenerator(rng, prof), OrderEventGenerator(rng, prof))
    ctx.register_batch("customer", cg.generate_many(ctx, count=50))
    ctx.register_batch("customer_address", CustomerAddressGenerator(rng, prof).generate_many(ctx))
    ctx.register_batch("order", og.generate_many(ctx, count=200))
    ctx.register_batch("order_item", oig.generate_many(ctx))
    ctx.register_batch("order_event", oeg.generate_many(ctx))
    for gen_cls in [InventoryLevelGenerator, InventoryTransactionGenerator,
                     InventoryHealthGenerator]:
        g = gen_cls(rng, prof)
        ctx.register_batch(g.entity_name, g.generate_many(ctx))
    ctx.resolve_all_refs()
    return ctx


class TestLogistics:
    def test_freight_booking(self, full_ctx):
        gen = FreightBookingGenerator(full_ctx.rng, full_ctx.profile)
        bookings = gen.generate_many(full_ctx, count=10)
        assert len(bookings) == 10
        for b in bookings:
            assert b["mode"] in ("SEA", "AIR", "EXPRESS")
            assert b["etd"] < b["eta"]

    def test_shipment(self, full_ctx):
        gen = ShipmentGenerator(full_ctx.rng, full_ctx.profile)
        shipments = gen.generate_many(full_ctx)
        assert len(shipments) > 0
        for s in shipments:
            assert s["tracking_no"]
            assert s["carrier"]

    def test_tracking_events(self, full_ctx):
        s_gen = ShipmentGenerator(full_ctx.rng, full_ctx.profile)
        full_ctx.register_batch("shipment", s_gen.generate_many(full_ctx))
        full_ctx.resolve_all_refs()

        gen = TrackingEventGenerator(full_ctx.rng, full_ctx.profile)
        events = gen.generate_many(full_ctx)
        assert len(events) > 0
        statuses = {e["status_code"] for e in events}
        assert "SHIPMENT_CREATED" in statuses

    def test_return_auth(self, full_ctx):
        gen = ReturnAuthorizationGenerator(full_ctx.rng, full_ctx.profile)
        ras = gen.generate_many(full_ctx)
        # ~8% 退货率，200 订单 ≈ 16 退货
        assert 0 <= len(ras) <= 50


class TestAdvertising:
    @pytest.fixture
    def ad_ctx(self, full_ctx):
        rng, prof = full_ctx.rng, full_ctx.profile
        for gen_cls in [AdAccountGenerator, CampaignGenerator, AdGroupGenerator,
                         AdGenerator]:
            g = gen_cls(rng, prof)
            full_ctx.register_batch(g.entity_name, g.generate_many(full_ctx, count=10))
        full_ctx.resolve_all_refs()
        return full_ctx

    def test_ad_account(self, full_ctx):
        gen = AdAccountGenerator(full_ctx.rng, full_ctx.profile)
        accts = gen.generate_many(full_ctx, count=3)
        assert len(accts) == 3
        assert all(a["channel"] in ("AMAZON_ADS", "GOOGLE_ADS", "META_ADS", "TIKTOK_ADS")
                   for a in accts)

    def test_campaign(self, full_ctx):
        full_ctx.register_batch("ad_account",
                                AdAccountGenerator(full_ctx.rng, full_ctx.profile).generate_many(full_ctx, count=3))
        gen = CampaignGenerator(full_ctx.rng, full_ctx.profile)
        camps = gen.generate_many(full_ctx, count=10)
        assert len(camps) == 10
        for c in camps:
            assert c["daily_budget"] > 0
            assert c["total_budget"] > c["daily_budget"]

    def test_spend_record(self, ad_ctx):
        gen = SpendRecordGenerator(ad_ctx.rng, ad_ctx.profile)
        records = gen.generate_many(ad_ctx, count=50)
        assert len(records) == 50
        for r in records:
            assert r["spend"] > 0
            assert r["sales"] >= 0

    def test_performance_metric(self, ad_ctx):
        gen = PerformanceMetricGenerator(ad_ctx.rng, ad_ctx.profile)
        metrics = gen.generate_many(ad_ctx, count=30)
        assert len(metrics) == 30
        for m in metrics:
            assert "acos" in m
            assert "roas" in m


class TestReport:
    def test_report_definition(self, full_ctx):
        gen = ReportDefinitionGenerator(full_ctx.rng, full_ctx.profile)
        defs = gen.generate_many(full_ctx, count=5)
        assert len(defs) == 5
        for d in defs:
            assert d["name"]
            assert d["category"]

    def test_report_execution(self, full_ctx):
        full_ctx.register_batch("report_definition",
                                ReportDefinitionGenerator(full_ctx.rng, full_ctx.profile).generate_many(full_ctx, count=5))
        gen = ReportExecutionGenerator(full_ctx.rng, full_ctx.profile)
        execs = gen.generate_many(full_ctx, count=20)
        assert len(execs) == 20
        statuses = {e["status"] for e in execs}
        assert "SUCCESS" in statuses


class TestFullPR4Pipeline:
    """集成测试: 全部 9 个域。"""

    def test_all_domains_generated(self, full_ctx):
        rng, prof = full_ctx.rng, full_ctx.profile

        # PR-4 新增
        for gen_cls in [FreightBookingGenerator, ShipmentGenerator,
                         TrackingEventGenerator, ReturnAuthorizationGenerator]:
            g = gen_cls(rng, prof)
            full_ctx.register_batch(g.entity_name, g.generate_many(full_ctx))

        for gen_cls in [AdAccountGenerator, CampaignGenerator, AdGroupGenerator,
                         AdGenerator, SpendRecordGenerator, PerformanceMetricGenerator]:
            g = gen_cls(rng, prof)
            full_ctx.register_batch(g.entity_name, g.generate_many(full_ctx, count=10))

        for gen_cls in [ReportDefinitionGenerator, ReportExecutionGenerator]:
            g = gen_cls(rng, prof)
            full_ctx.register_batch(g.entity_name, g.generate_many(full_ctx, count=10))

        full_ctx.resolve_all_refs()

        # 验证跨域 FK
        for s in full_ctx.get_entities("shipment"):
            oid = s["order_id"]
            assert not str(oid).startswith("$ref:")

        for r in full_ctx.get_entities("spend_record"):
            aid = r["ad_id"]
            assert not str(aid).startswith("$ref:")
