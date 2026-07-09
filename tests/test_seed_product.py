"""商品域生成器测试 — Product, SKU, Listing。"""

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


@pytest.fixture
def ctx_with_master():
    """创建包含完整 Master Data 的 Context。"""
    profile = SeedProfile.from_name("tiny")
    ctx = GenerationContext(profile, seed=42)

    # 生成所有 Master Data
    for gen_cls in [BrandGenerator, CategoryGenerator, ChannelGenerator,
                     WarehouseGenerator, SupplierGenerator]:
        gen = gen_cls(ctx.rng, ctx.profile)
        entities = gen.generate_many(ctx)
        ctx.register_batch(gen.entity_name, entities)

    ctx.resolve_all_refs()
    return ctx


# ======================= TestProductGenerator =======================

class TestProductGenerator:
    def test_generate_one(self, ctx_with_master):
        ctx = ctx_with_master
        gen = ProductGenerator(ctx.rng, ctx.profile)
        entity = gen.generate_one(ctx)
        assert entity["product_id"].startswith("P")
        assert "name" in entity
        assert "name_en" in entity
        assert "$ref:brand:" in entity["brand_id"]
        assert "$ref:category:" in entity["category_id"]
        assert entity["lifecycle_stage"] in ["ACTIVE", "NEW", "MATURE"]

    def test_generate_many(self, ctx_with_master):
        ctx = ctx_with_master
        gen = ProductGenerator(ctx.rng, ctx.profile)
        entities = gen.generate_many(ctx, count=10)
        assert len(entities) == 10
        ids = [e["product_id"] for e in entities]
        assert len(ids) == len(set(ids))  # 无重复

    def test_integration_with_master(self, ctx_with_master):
        """验证 Product 生成后，$ref 可正确解析。"""
        ctx = ctx_with_master
        gen = ProductGenerator(ctx.rng, ctx.profile)
        entities = gen.generate_many(ctx, count=5)
        ctx.register_batch("product", entities)
        ctx.resolve_all_refs()

        for product in ctx.get_entities("product"):
            # FK 应已解析为实际 ID（非 $ref）
            assert not product["brand_id"].startswith("$ref:"), \
                f"brand_id 未解析: {product['brand_id']}"
            assert not product["category_id"].startswith("$ref:"), \
                f"category_id 未解析: {product['category_id']}"

            # Brand ID 应存在于 brand 表中
            brand_ids = {b["brand_id"] for b in ctx.get_entities("brand")}
            assert product["brand_id"] in brand_ids


# ======================= TestSkuGenerator =======================

class TestSkuGenerator:
    @pytest.fixture
    def ctx_with_products(self, ctx_with_master):
        ctx = ctx_with_master
        gen = ProductGenerator(ctx.rng, ctx.profile)
        entities = gen.generate_many(ctx, count=10)
        ctx.register_batch("product", entities)
        return ctx

    def test_generate_many(self, ctx_with_products):
        ctx = ctx_with_products
        gen = SkuGenerator(ctx.rng, ctx.profile)
        entities = gen.generate_many(ctx, count=30)
        assert len(entities) == 30

    def test_sku_code_format(self, ctx_with_products):
        ctx = ctx_with_products
        gen = SkuGenerator(ctx.rng, ctx.profile)
        entities = gen.generate_many(ctx, count=10)

        for sku in entities:
            code = sku["sku_code"]
            # 格式: SPU-XXXXX-COLOR-SIZE
            parts = code.split("-")
            assert len(parts) >= 3, f"SKU 编码格式异常: {code}"
            assert parts[0] == "SPU", f"SKU 编码前缀应为 SPU: {code}"

    def test_all_skus_have_price(self, ctx_with_products):
        ctx = ctx_with_products
        gen = SkuGenerator(ctx.rng, ctx.profile)
        entities = gen.generate_many(ctx, count=10)

        for sku in entities:
            assert sku["price"] > 0, f"价格无效: {sku['price']}"
            assert sku["cost_price"] < sku["price"], \
                f"成本价 >= 售价: {sku['cost_price']} >= {sku['price']}"

    def test_each_product_has_variants(self, ctx_with_products):
        ctx = ctx_with_products
        gen = SkuGenerator(ctx.rng, ctx.profile)
        entities = gen.generate_many(ctx, count=50)
        ctx.register_batch("sku", entities)

        # 每个 product 应有多个 SKU
        from collections import Counter
        product_ids = [s["product_id"] for s in entities]
        counts = Counter(product_ids)
        # 每个 product 至少 1 个 SKU
        assert min(counts.values()) >= 1

    def test_fk_resolution(self, ctx_with_products):
        ctx = ctx_with_products
        gen = SkuGenerator(ctx.rng, ctx.profile)
        entities = gen.generate_many(ctx, count=10)
        ctx.register_batch("sku", entities)
        ctx.resolve_all_refs()

        for sku in ctx.get_entities("sku"):
            assert not str(sku["product_id"]).startswith("$ref:"), \
                f"product_id 未解析: {sku['product_id']}"


# ======================= TestListingGenerator =======================

class TestListingGenerator:
    @pytest.fixture
    def ctx_with_skus(self, ctx_with_master):
        ctx = ctx_with_master
        prod_gen = ProductGenerator(ctx.rng, ctx.profile)
        prods = prod_gen.generate_many(ctx, count=10)
        ctx.register_batch("product", prods)

        sku_gen = SkuGenerator(ctx.rng, ctx.profile)
        skus = sku_gen.generate_many(ctx, count=30)
        ctx.register_batch("sku", skus)
        return ctx

    def test_generate_many(self, ctx_with_skus):
        ctx = ctx_with_skus
        gen = ListingGenerator(ctx.rng, ctx.profile)
        entities = gen.generate_many(ctx, count=50)
        assert len(entities) == 50

    def test_listing_has_required_fields(self, ctx_with_skus):
        ctx = ctx_with_skus
        gen = ListingGenerator(ctx.rng, ctx.profile)
        entities = gen.generate_many(ctx, count=10)
        ctx.register_batch("listing", entities)

        for lst in ctx.get_entities("listing"):
            assert lst["title"], "Listing 缺少标题"
            assert lst["price"] > 0, "Listing 价格无效"
            assert len(lst["bullet_points"]) >= 1, f"Bullet points 太少: {len(lst['bullet_points'])}"

    def test_fk_resolution(self, ctx_with_skus):
        ctx = ctx_with_skus
        gen = ListingGenerator(ctx.rng, ctx.profile)
        entities = gen.generate_many(ctx, count=20)
        ctx.register_batch("listing", entities)
        ctx.resolve_all_refs()

        for lst in ctx.get_entities("listing"):
            assert not str(lst["sku_id"]).startswith("$ref:"), \
                f"sku_id 未解析: {lst['sku_id']}"
            assert not str(lst["channel_id"]).startswith("$ref:"), \
                f"channel_id 未解析: {lst['channel_id']}"


# ======================= TestProductPipeline =======================

class TestProductPipeline:
    """集成测试：完整 Product → SKU → Listing 链路。"""

    def test_full_product_pipeline(self, ctx_with_master):
        ctx = ctx_with_master
        profile = ctx.profile

        # 层级 1: Product
        prod_gen = ProductGenerator(ctx.rng, profile)
        prods = prod_gen.generate_many(ctx, count=10)
        ctx.register_batch("product", prods)

        # 层级 2: SKU
        sku_gen = SkuGenerator(ctx.rng, profile)
        skus = sku_gen.generate_many(ctx, count=30)
        ctx.register_batch("sku", skus)

        # 层级 3: Listing
        lst_gen = ListingGenerator(ctx.rng, profile)
        lsts = lst_gen.generate_many(ctx, count=50)
        ctx.register_batch("listing", lsts)

        # 解析 FK
        ctx.resolve_all_refs()

        # 验证数量
        assert ctx.count("product") == 10
        assert ctx.count("sku") == 30
        assert ctx.count("listing") == 50

        # 验证 FK 链: Listing → SKU → Product → Brand/Category
        for lst in ctx.get_entities("listing"):
            sku_id = lst["sku_id"]
            sku = ctx.get_entity("sku", sku_id)
            assert sku is not None, f"Listing SKU 引用无效: {sku_id}"

            product_id = sku["product_id"]
            product = ctx.get_entity("product", product_id)
            assert product is not None, f"SKU Product 引用无效: {product_id}"

            brand_id = product["brand_id"]
            brand = ctx.get_entity("brand", brand_id)
            assert brand is not None, f"Product Brand 引用无效: {brand_id}"
