"""商品域生成器 — SPU (Product)、SKU、Listing。

FK 依赖链: Brand/Category → Product → SKU → Listing (引用 Channel)
所有 FK 使用 $ref 占位符，由 Factory 统一解析。
"""

from __future__ import annotations

import random

from backend.seed.core.generator import BaseGenerator
from backend.seed.utils import constants
from backend.seed.utils.distributions import pareto_int


# 产品数据已迁至 seed/data/product_data.py
from backend.seed.data.product_data import PRODUCT_TYPES, TITLE_PATTERNS, FEATURES, USE_CASES, BULLET_TEMPLATES, BULLET_DESCRIPTIONS, KNOWLEDGE_TEMPLATES  # noqa: E402

class ProductGenerator(BaseGenerator):
    """SPU (产品) 生成器 — 引用 Brand 和 Category。"""

    entity_name = "product"

    def generate_one(self, ctx: "GenerationContext") -> dict:  # noqa: F821
        prod_type, prod_type_en = self.rng.choice(PRODUCT_TYPES)
        adj = self.rng.choice(constants.PRODUCT_ADJECTIVES)
        material = self.rng.choice(constants.PRODUCT_MATERIALS)

        # 产品名称: "便携折叠收纳盒" 或 "智能无线充电板"
        name = f"{adj}{prod_type}"

        # 随机选择品牌和类目 (用 $ref 占位符)
        brand_count = ctx.count("brand")
        cat_count = ctx.count("category")

        # 选择类目中非根节点的（depth > 0）
        categories = ctx.get_entities("category")
        leaf_cats = [c for c in categories if c.get("depth", 0) > 0]
        if not leaf_cats:
            leaf_cats = categories

        brand_idx = self.rng.randint(0, max(0, brand_count - 1))
        cat_choice = self.rng.choice(leaf_cats)
        cat_idx = categories.index(cat_choice) if cat_choice in categories else 0

        lifecycle = self.rng.choice(["ACTIVE", "ACTIVE", "ACTIVE", "NEW", "MATURE"])

        return {
            "product_id": ctx.next_id("product", "P"),
            "code": f"SPU-{ctx.count('product') + 1:05d}",
            "name": name,
            "name_en": f"{adj} {prod_type_en}",
            "brand_id": f"$ref:brand:{brand_idx}",
            "category_id": f"$ref:category:{cat_idx}",
            "status": "ACTIVE",
            "lifecycle_stage": lifecycle,
            "target_market": self.rng.choice(["US", "EU", "JP", "ALL"]),
            "created_at": ctx.faker.date_between(start_date="-2y", end_date="today").isoformat(),
        }


class SkuGenerator(BaseGenerator):
    """SKU 生成器 — 每个 Product 生成 3-5 个变体（颜色 × 尺寸）。"""

    entity_name = "sku"

    def generate_one(self, ctx: "GenerationContext") -> dict:  # noqa: F821
        """单条生成（一般不单独用，用 generate_many 批量）。"""
        product_idx = self.rng.randint(0, max(0, ctx.count("product") - 1))
        color = self.rng.choice(constants.SKU_COLORS)
        size = self.rng.choice(constants.SKU_SIZES)
        price = pareto_int(self.rng, min_val=5, max_val=200, alpha=1.5)
        cost = int(price * self.rng.uniform(0.25, 0.35))

        return {
            "sku_id": ctx.next_id("sku", "SKU"),
            "product_id": f"$ref:product:{product_idx}",
            "sku_code": f"SKU-{ctx.count('sku') + 1:05d}",  # 编码由 Factory 修正
            "color": color,
            "size": size,
            "barcode": ctx.faker.ean13(),
            "weight_g": self.rng.randint(50, 5000),
            "price": float(price),
            "cost_price": float(cost),
            "currency": "USD",
            "country_of_origin": "CN",
            "status": "ACTIVE",
        }

    def generate_many(self, ctx: "GenerationContext",  # noqa: F821
                      count: int | None = None) -> list[dict]:
        """批量生成 SKU — 每个 Product 分配 3-5 个变体。"""
        if count is None:
            count = self.profile.entity_count(self.entity_name)

        product_count = ctx.count("product")
        if product_count == 0:
            raise ValueError("生成 SKU 前必须先有 Product")

        # 计算每个 product 分配几个 SKU
        avg_per_product = max(1, count // product_count)
        extra = count % product_count

        results = []
        for p_idx in range(product_count):
            num_variants = avg_per_product + (1 if p_idx < extra else 0)
            for _ in range(num_variants):
                sku_idx = ctx.count("sku")  # 当前已生成的 SKU 数
                color = self.rng.choice(constants.SKU_COLORS)
                size = self.rng.choice(constants.SKU_SIZES)
                price = pareto_int(self.rng, min_val=5, max_val=200, alpha=1.5)
                cost = int(price * self.rng.uniform(0.25, 0.35))

                # SKU 编码: MK202-RED-L
                products = ctx.get_entities("product")
                product = products[p_idx] if p_idx < len(products) else products[0]
                spu_code = product.get("code", f"SPU-{p_idx + 1:05d}")
                color_short = color[:3].upper() if color[:3].isascii() else color
                size_short = size[0].upper() if size and size[0].isascii() else size
                sku_code = f"{spu_code}-{color_short}-{size_short}"

                results.append({
                    "sku_id": ctx.next_id("sku", "SKU"),
                    "product_id": f"$ref:product:{p_idx}",
                    "sku_code": sku_code,
                    "color": color,
                    "size": size,
                    "barcode": ctx.faker.ean13(),
                    "weight_g": self.rng.randint(50, 5000),
                    "price": float(price),
                    "cost_price": float(cost),
                    "currency": "USD",
                    "country_of_origin": "CN",
                    "status": "ACTIVE",
                })

        return results


class ListingGenerator(BaseGenerator):
    """Listing 生成器 — 每个 SKU 在 2-3 个渠道上架。"""

    entity_name = "listing"

    def _generate_title(self, ctx: "GenerationContext",  # noqa: F821
                        sku: dict, product: dict) -> str:
        """生成 Amazon 风格的商品标题。"""
        prod_type_en = product.get("name_en", "Product")
        adj = self.rng.choice(constants.PRODUCT_ADJECTIVES)
        material = self.rng.choice(constants.PRODUCT_MATERIALS)
        feature = self.rng.choice(FEATURES)
        color = sku.get("color", "")
        size = sku.get("size", "")

        title = f"{adj} {prod_type_en} - {material} {feature}"
        if color:
            title += f", {color}"
        if size:
            title += f", {size}"
        # 限制长度
        if len(title) > 200:
            title = title[:197] + "..."
        return title

    def _generate_bullet_points(self, ctx: "GenerationContext") -> list[str]:  # noqa: F821
        """生成 5 条 bullet points。"""
        bullets = []
        materials = constants.PRODUCT_MATERIALS
        for i in range(5):
            desc = self.rng.choice(BULLET_DESCRIPTIONS)
            text = desc[0] + "：" + desc[1].format(
                self.rng.choice(materials),
                self.rng.randint(3, 10),
                self.rng.choice(["厨房", "浴室", "卧室", "办公室", "户外"]),
                self.rng.choice(["可折叠", "防滑", "静音", "轻量", "坚固"]),
                str(self.rng.randint(30, 80)),
                str(self.rng.randint(1, 5)),
                str(self.rng.randint(12, 48)),
            )
            bullets.append(text[:500])  # Amazon 限制每条约 500 字符
        return bullets

    def generate_one(self, ctx: "GenerationContext") -> dict:  # noqa: F821
        sku_count = ctx.count("sku")
        channel_count = ctx.count("channel")
        sku_idx = self.rng.randint(0, max(0, sku_count - 1))
        channel_idx = self.rng.randint(0, max(0, channel_count - 1))

        skus = ctx.get_entities("sku")
        products = ctx.get_entities("product")
        sku = skus[sku_idx] if sku_idx < len(skus) else {"color": "", "size": ""}

        # 找到 SKU 对应的 Product
        product_idx = 0
        for p in products:
            if p.get("product_id") == sku.get("product_id", "").replace("$ref:product:", ""):
                break
            product_idx += 1
        product = products[product_idx] if product_idx < len(products) else {"name_en": "Product"}

        channels = ctx.get_entities("channel")
        channel = channels[channel_idx] if channel_idx < len(channels) else {"code": "AMAZON_US"}

        price = sku.get("price", 19.99)
        title = self._generate_title(ctx, sku, product)
        bullets = self._generate_bullet_points(ctx)
        keywords = [w for w in title.replace(",", "").replace("-", " ").split() if len(w) > 2]
        keywords_str = " ".join(keywords[:20])

        return {
            "listing_id": ctx.next_id("listing", "LST"),
            "sku_id": f"$ref:sku:{sku_idx}",
            "channel_id": f"$ref:channel:{channel_idx}",
            "channel_listing_id": ctx.faker.bothify(text="B0######??##"),
            "locale": channel.get("country", "US").lower(),
            "title": title,
            "bullet_points": bullets,
            "description": f"{title}. Made of high quality materials. Perfect for daily use. "
                           f"Package includes: 1x {product.get('name_en', 'Product')}.",
            "images": [ctx.faker.image_url() for _ in range(self.rng.randint(3, 7))],
            "keywords": keywords_str,
            "status": "ACTIVE",
            "currency": channel.get("default_currency", "USD"),
            "price": float(price),
        }

    def generate_many(self, ctx: "GenerationContext",  # noqa: F821
                      count: int | None = None) -> list[dict]:
        """每个 SKU 分配 2-3 个渠道 Listing。"""
        if count is None:
            count = self.profile.entity_count(self.entity_name)

        sku_count = ctx.count("sku")
        channel_count = ctx.count("channel")
        if sku_count == 0 or channel_count == 0:
            raise ValueError("生成 Listing 前必须先有 SKU 和 Channel")

        results = []
        skus = ctx.get_entities("sku")
        products = ctx.get_entities("product")
        channels = ctx.get_entities("channel")

        listing_count = 0
        for sku_idx, sku in enumerate(skus):
            # 每个 SKU 随机 1-3 个渠道
            num_channels = self.rng.randint(1, min(3, channel_count))
            selected_channels = self.rng.sample(range(channel_count), num_channels)

            # 找到 SKU 对应的 Product
            product = products[0]
            for p in products:
                if p.get("product_id") == sku.get("product_id", "").replace("$ref:product:", ""):
                    break
                if p.get("product_id"):
                    product = p

            for ch_idx in selected_channels:
                if listing_count >= count:
                    break
                channel = channels[ch_idx]
                price = sku.get("price", 19.99)
                title = self._generate_title(ctx, sku, product)
                bullets = self._generate_bullet_points(ctx)
                keywords = [w for w in title.replace(",", "").replace("-", " ").split()
                           if len(w) > 2]
                keywords_str = " ".join(keywords[:20])

                results.append({
                    "listing_id": ctx.next_id("listing", "LST"),
                    "sku_id": sku.get("sku_id", f"$ref:sku:{sku_idx}"),
                    "channel_id": channel.get("channel_id", f"$ref:channel:{ch_idx}"),
                    "channel_listing_id": ctx.faker.bothify(text="B0######??##"),
                    "locale": channel.get("country", "US").lower(),
                    "title": title,
                    "bullet_points": bullets,
                    "description": (
                        f"{title}. Made of high quality materials. "
                        f"Package includes: 1x {product.get('name_en', 'Product')}."
                    ),
                    "images": [ctx.faker.image_url()
                              for _ in range(self.rng.randint(3, 7))],
                    "keywords": keywords_str,
                    "status": "ACTIVE",
                    "currency": channel.get("default_currency", "USD"),
                    "price": float(price),
                })
                listing_count += 1

            if listing_count >= count:
                break

        return results
