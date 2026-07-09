"""Master Data 生成器测试 — Brand, Category, Channel, Warehouse, Supplier。"""

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from seed_data.core.context import GenerationContext
from seed_data.core.profile import SeedProfile
from seed_data.generators.master_data import (
    BrandGenerator,
    CategoryGenerator,
    ChannelGenerator,
    WarehouseGenerator,
    SupplierGenerator,
)
from seed_data.utils import constants


# ======================= Fixtures =======================

@pytest.fixture
def tiny_profile():
    return SeedProfile.from_name("tiny")


@pytest.fixture
def ctx(tiny_profile):
    return GenerationContext(tiny_profile, seed=42)


# ======================= TestBrandGenerator =======================

class TestBrandGenerator:
    def test_generate_one(self, ctx):
        gen = BrandGenerator(ctx.rng, ctx.profile)
        entity = gen.generate_one(ctx)
        assert "brand_id" in entity
        assert entity["brand_id"].startswith("B")
        assert entity["name"] in [b["name"] for b in constants.BRANDS]
        assert entity["status"] == "ACTIVE"

    def test_generate_many(self, ctx):
        gen = BrandGenerator(ctx.rng, ctx.profile)
        entities = gen.generate_many(ctx, count=3)
        assert len(entities) == 3
        # 检查唯一性
        ids = [e["brand_id"] for e in entities]
        assert len(ids) == len(set(ids))

    def test_respects_profile_count(self, tiny_profile):
        ctx = GenerationContext(tiny_profile, seed=42)
        gen = BrandGenerator(ctx.rng, ctx.profile)
        entities = gen.generate_many(ctx)  # 默认从 profile 读取
        expected = tiny_profile.entity_count("brand")
        assert len(entities) == expected

    def test_reproducible(self, tiny_profile):
        ctx1 = GenerationContext(tiny_profile, seed=42)
        ctx2 = GenerationContext(tiny_profile, seed=42)

        gen1 = BrandGenerator(ctx1.rng, ctx1.profile)
        gen2 = BrandGenerator(ctx2.rng, ctx2.profile)

        e1 = gen1.generate_many(ctx1, count=5)
        e2 = gen2.generate_many(ctx2, count=5)
        assert e1 == e2


# ======================= TestCategoryGenerator =======================

class TestCategoryGenerator:
    def test_generate_many_tiny(self, ctx):
        gen = CategoryGenerator(ctx.rng, ctx.profile)
        entities = gen.generate_many(ctx, count=8)
        assert len(entities) >= 1  # 类目数会浮动

        # 检查层级结构
        root_cats = [e for e in entities if e["parent_id"] is None]
        assert len(root_cats) >= 1, "至少有一个根节点"

        # 检查 depth
        depths = [e["depth"] for e in entities]
        assert all(d >= 0 for d in depths)
        spec_max = ctx.profile.entity_spec("category").max_depth or 3
        assert all(d < spec_max for d in depths), f"深度不应超过 {spec_max-1}"

    def test_all_ids_unique(self, ctx):
        gen = CategoryGenerator(ctx.rng, ctx.profile)
        entities = gen.generate_many(ctx, count=20)
        ids = [e["category_id"] for e in entities]
        assert len(ids) == len(set(ids))

    def test_names_are_from_tree(self, ctx):
        gen = CategoryGenerator(ctx.rng, ctx.profile)
        entities = gen.generate_many(ctx, count=10)

        # 展平所有合法的类目名
        def collect_names(tree, names):
            for k, v in tree.items():
                names.add(k)
                if isinstance(v, list):
                    for leaf in v:
                        names.add(leaf)
                elif isinstance(v, dict):
                    collect_names(v, names)

        valid_names = set()
        collect_names(constants.CATEGORY_TREE, valid_names)

        for e in entities:
            assert e["name"] in valid_names, f"'{e['name']}' 不在类目树中"


# ======================= TestChannelGenerator =======================

class TestChannelGenerator:
    def test_generate_many(self, ctx):
        gen = ChannelGenerator(ctx.rng, ctx.profile)
        entities = gen.generate_many(ctx, count=3)
        assert len(entities) == 3
        codes = [e["code"] for e in entities]
        assert len(codes) == len(set(codes))  # 无重复

        # 检查渠道码合法性
        valid_codes = {c["code"] for c in constants.CHANNELS}
        for code in codes:
            assert code in valid_codes

    def test_respects_fixed_enum_order(self, ctx):
        """多次生成同样的数量得到相同结果（seed 固定）。"""
        ctx2 = GenerationContext(ctx.profile, seed=ctx.seed)
        gen1 = ChannelGenerator(ctx.rng, ctx.profile)
        gen2 = ChannelGenerator(ctx2.rng, ctx2.profile)

        e1 = gen1.generate_many(ctx, count=3)
        e2 = gen2.generate_many(ctx2, count=3)
        assert e1 == e2

    def test_cannot_exceed_available_channels(self, ctx):
        gen = ChannelGenerator(ctx.rng, ctx.profile)
        entities = gen.generate_many(ctx, count=100)
        # 应不超过 CHANNELS 长度
        assert len(entities) <= len(constants.CHANNELS)


# ======================= TestWarehouseGenerator =======================

class TestWarehouseGenerator:
    def test_generate_many(self, ctx):
        gen = WarehouseGenerator(ctx.rng, ctx.profile)
        entities = gen.generate_many(ctx, count=3)
        assert len(entities) == 3

        codes = [e["code"] for e in entities]
        valid_codes = {w["code"] for w in constants.WAREHOUSES}
        for code in codes:
            assert code in valid_codes

    def test_all_types_present(self, ctx):
        gen = WarehouseGenerator(ctx.rng, ctx.profile)
        entities = gen.generate_many(ctx, count=8)  # 取全部
        types = {e["type"] for e in entities}
        assert "FBA" in types
        assert "3PL" in types
        assert "DOMESTIC" in types


# ======================= TestSupplierGenerator =======================

class TestSupplierGenerator:
    def test_generate_one(self, ctx):
        gen = SupplierGenerator(ctx.rng, ctx.profile)
        entity = gen.generate_one(ctx)
        assert entity["supplier_id"].startswith("SUP")
        assert entity["country"] == "CN"
        assert entity["city"] in constants.SUPPLIER_CITIES
        assert entity["type"] in constants.SUPPLIER_TYPES
        assert entity["payment_terms"] in constants.PAYMENT_TERMS
        assert "contact_email" in entity
        assert "@" in entity["contact_email"]
        assert 3 <= entity["rating"] <= 5

    def test_generate_many(self, ctx):
        gen = SupplierGenerator(ctx.rng, ctx.profile)
        entities = gen.generate_many(ctx, count=5)
        assert len(entities) == 5
        ids = [e["supplier_id"] for e in entities]
        assert len(ids) == len(set(ids))

    def test_cooperation_status_distribution(self, ctx):
        gen = SupplierGenerator(ctx.rng, ctx.profile)
        entities = gen.generate_many(ctx, count=50)
        active_count = sum(1 for e in entities if e["cooperation_status"] == "ACTIVE")
        # 大部分应为 ACTIVE（权重 3:1）
        assert active_count > len(entities) * 0.5


# ======================= TestFullPipeline =======================

class TestFullMasterDataPipeline:
    """集成测试：按依赖顺序生成全部 Master Data。"""

    def test_tiny_profile_full_pipeline(self, tiny_profile):
        ctx = GenerationContext(tiny_profile, seed=42)

        generators = [
            BrandGenerator(ctx.rng, ctx.profile),
            CategoryGenerator(ctx.rng, ctx.profile),
            ChannelGenerator(ctx.rng, ctx.profile),
            WarehouseGenerator(ctx.rng, ctx.profile),
            SupplierGenerator(ctx.rng, ctx.profile),
        ]

        for gen in generators:
            entities = gen.generate_many(ctx)
            ctx.register_batch(gen.entity_name, entities)

        # 验证数量
        assert ctx.count("brand") == 3
        assert ctx.count("channel") == 3
        assert ctx.count("warehouse") == 3
        assert ctx.count("supplier") == 5
        assert ctx.count("category") >= 1  # 类目数浮动

    def test_reproducible_full_pipeline(self, tiny_profile):
        """两次运行完全一致的种子数据。"""
        def run_pipeline():
            ctx = GenerationContext(tiny_profile, seed=42)
            gens = [
                BrandGenerator(ctx.rng, ctx.profile),
                CategoryGenerator(ctx.rng, ctx.profile),
                ChannelGenerator(ctx.rng, ctx.profile),
                WarehouseGenerator(ctx.rng, ctx.profile),
                SupplierGenerator(ctx.rng, ctx.profile),
            ]
            for gen in gens:
                entities = gen.generate_many(ctx)
                ctx.register_batch(gen.entity_name, entities)
            return ctx

        ctx1 = run_pipeline()
        ctx2 = run_pipeline()

        for name in ctx1.entity_names:
            assert ctx1.get_entities(name) == ctx2.get_entities(name), \
                f"实体 '{name}' 两次生成不一致"
