"""客户域生成器 — Customer, CustomerAddress, Review。

Customer 是订单的前置依赖。
"""

from __future__ import annotations

import random

from backend.seed.core.generator import BaseGenerator

# 客户市场分布（与公司画像一致）
MARKET_DISTRIBUTION = {
    "US": 0.60, "DE": 0.15, "GB": 0.05, "FR": 0.03,
    "JP": 0.10, "CA": 0.04, "AU": 0.03,
}

# 客户分段
CUSTOMER_SEGMENTS = ["NEW", "OCCASIONAL", "REGULAR", "VIP"]

# 复购概率映射
REPEAT_PROBABILITY = {
    "NEW": 0.0,         # 新客户不会立即复购
    "OCCASIONAL": 0.05,  # 偶尔买家 5% 复购
    "REGULAR": 0.25,     # 常客 25% 复购
    "VIP": 0.60,         # VIP 60% 复购
}


class CustomerGenerator(BaseGenerator):
    """客户生成器 — 带市场分布和分段。"""

    entity_name = "customer"

    def __init__(self, rng: random.Random, profile: "SeedProfile"):  # noqa: F821
        super().__init__(rng, profile)

    def _random_country(self) -> str:
        """按市场分布随机选择国家。"""
        r = self.rng.random()
        cumulative = 0.0
        for country, prob in MARKET_DISTRIBUTION.items():
            cumulative += prob
            if r <= cumulative:
                return country
        return "US"

    def generate_one(self, ctx: "GenerationContext") -> dict:  # noqa: F821
        country = self._random_country()

        # 按国家生成地区化数据
        if country == "JP":
            locale = "ja_JP"
            name = ctx.faker.name()
        elif country == "DE":
            locale = "de_DE"
            name = ctx.faker.name()
        else:
            locale = "en_US"
            name = ctx.faker.name()

        segment = self.rng.choices(
            CUSTOMER_SEGMENTS,
            weights=[0.50, 0.30, 0.15, 0.05],
            k=1,
        )[0]

        # 注册时间随机（1-3 年前）
        first_order_date = ctx.faker.date_between(start_date="-3y", end_date="-30d")

        return {
            "customer_id": ctx.next_id("customer", "CUST"),
            "name": name,
            "email": ctx.faker.email(),
            "phone": ctx.faker.phone_number() if self.rng.random() < 0.3 else None,
            "country": country,
            "locale": locale,
            "segment": segment,
            "lifetime_value": round(self.rng.uniform(20, 2000), 2),
            "ltv_tier": self.rng.choice(["LOW", "MEDIUM", "HIGH"]),
            "first_order_at": first_order_date.isoformat() if hasattr(first_order_date, 'isoformat') else str(first_order_date),
            "last_order_at": None,
            "order_count": 0,
            "created_at": first_order_date.isoformat() if hasattr(first_order_date, 'isoformat') else str(first_order_date),
        }


class CustomerAddressGenerator(BaseGenerator):
    """客户地址生成器 — 每个客户 1-2 个地址。"""

    entity_name = "customer_address"

    def generate_one(self, ctx: "GenerationContext") -> dict:  # noqa: F821
        customer_count = ctx.count("customer")
        cust_idx = self.rng.randint(0, max(0, customer_count - 1))

        return {
            "address_id": ctx.next_id("customer_address", "ADDR"),
            "customer_id": f"$ref:customer:{cust_idx}",
            "line1": ctx.faker.street_address(),
            "line2": ctx.faker.secondary_address() if self.rng.random() < 0.3 else None,
            "city": ctx.faker.city(),
            "state": ctx.faker.state(),
            "postcode": ctx.faker.postcode(),
            "country": ctx.faker.country(),
            "is_default": True,
        }

    def generate_many(self, ctx: "GenerationContext",
                      count: int | None = None) -> list[dict]:
        """每个客户生成 1-2 个地址。"""
        if count is None:
            count = self.profile.entity_count(self.entity_name)

        customer_count = ctx.count("customer")
        if customer_count == 0:
            raise ValueError("生成地址前必须先有 Customer")

        results = []
        for c_idx in range(customer_count):
            # 每个客户 1-2 个地址
            num_addrs = self.rng.randint(1, 2)
            for _ in range(num_addrs):
                results.append({
                    "address_id": ctx.next_id("customer_address", "ADDR"),
                    "customer_id": f"$ref:customer:{c_idx}",
                    "line1": ctx.faker.street_address(),
                    "line2": ctx.faker.secondary_address() if self.rng.random() < 0.2 else None,
                    "city": ctx.faker.city(),
                    "state": ctx.faker.state(),
                    "postcode": ctx.faker.postcode(),
                    "country": ctx.faker.country(),
                    "is_default": len(results) == 0,  # 第一个地址是默认
                })
                if len(results) >= count:
                    return results[:count]

        return results


class ReviewGenerator(BaseGenerator):
    """评论生成器 — 基于已完成的订单。"""

    entity_name = "review"

    REVIEW_TITLES = [
        "Great product!", "Works as expected", "Good quality",
        "Better than expected", "Not bad", "Love it!",
        "Decent for the price", "Highly recommend", "Just okay",
        "Excellent value", "Five stars", "Would buy again",
        "Perfect gift", "Good but could be better",
    ]

    REVIEW_CONTENTS = [
        "I bought this for my home and it works perfectly. The quality is excellent.",
        "Good product for the price. Shipping was fast.",
        "Exactly what I needed. Fits perfectly and looks great.",
        "Nice quality. Would recommend to friends and family.",
        "Decent product. Nothing special but does the job.",
        "Better than I expected. Very happy with this purchase.",
        "The material is good and it's well made. Happy customer!",
        "Using it for a few weeks now, holding up well.",
    ]

    def generate_one(self, ctx: "GenerationContext") -> dict:  # noqa: F821
        cust_count = ctx.count("customer")
        order_count = ctx.count("order")
        sku_count = ctx.count("sku")

        cust_idx = self.rng.randint(0, max(0, cust_count - 1))
        order_idx = self.rng.randint(0, max(0, order_count - 1))
        sku_idx = self.rng.randint(0, max(0, sku_count - 1))

        rating = self.rng.choices([5, 4, 3, 2, 1], weights=[0.45, 0.30, 0.15, 0.07, 0.03], k=1)[0]

        return {
            "review_id": ctx.next_id("review", "REV"),
            "customer_id": f"$ref:customer:{cust_idx}",
            "sku_id": f"$ref:sku:{sku_idx}",
            "order_id": f"$ref:order:{order_idx}",
            "channel": self.rng.choice(["AMAZON_US", "AMAZON_EU", "SHOPIFY"]),
            "channel_review_id": ctx.faker.bothify(text="R#####???#"),
            "rating": rating,
            "title": self.rng.choice(self.REVIEW_TITLES),
            "content": self.rng.choice(self.REVIEW_CONTENTS),
            "language": "en",
            "posted_at": ctx.faker.date_between(start_date="-1y", end_date="today").isoformat(),
        }
