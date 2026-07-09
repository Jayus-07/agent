"""广告域生成器 — AdAccount, Campaign, AdGroup, Ad, Keyword, SpendRecord, PerformanceMetric。

跨平台: Amazon Ads, Google Ads, Meta Ads, TikTok Ads
"""

from __future__ import annotations

import random
from datetime import datetime, timedelta

from seed_data.core.generator import BaseGenerator
from seed_data.utils import constants


class AdAccountGenerator(BaseGenerator):
    """广告账号生成器 — 每个广告平台 1 个账号。"""

    entity_name = "ad_account"

    def generate_one(self, ctx: "GenerationContext") -> dict:  # noqa: F821
        ch = self.rng.choice(constants.AD_CHANNELS)
        return {
            "ad_account_id": ctx.next_id("ad_account", "AA"),
            "channel": ch["code"],
            "account_id": ctx.faker.bothify(text="A3##########"),
            "currency": "USD",
            "status": "ACTIVE",
        }

    def generate_many(self, ctx: "GenerationContext",
                      count: int | None = None) -> list[dict]:
        if count is None:
            count = self.profile.entity_count(self.entity_name)
        count = min(count, len(constants.AD_CHANNELS))
        results = []
        for ch in constants.AD_CHANNELS[:count]:
            results.append({
                "ad_account_id": ctx.next_id("ad_account", "AA"),
                "channel": ch["code"],
                "account_id": ctx.faker.bothify(text="A3##########"),
                "currency": "USD",
                "status": "ACTIVE",
            })
        return results


class CampaignGenerator(BaseGenerator):
    """广告活动生成器。"""

    entity_name = "campaign"

    def generate_one(self, ctx: "GenerationContext") -> dict:  # noqa: F821
        acct_count = ctx.count("ad_account")
        acct_idx = self.rng.randint(0, max(0, acct_count - 1))
        start_date = ctx.faker.date_between(start_date="-6m", end_date="today")

        return {
            "campaign_id": ctx.next_id("campaign", "CAMP"),
            "ad_account_id": f"$ref:ad_account:{acct_idx}",
            "name": f"Campaign-{ctx.faker.word().upper()}-{self.rng.randint(100, 999)}",
            "type": self.rng.choice(constants.CAMPAIGN_TYPES),
            "status": self.rng.choice(["ACTIVE", "ACTIVE", "ACTIVE", "PAUSED", "ENDED"]),
            "daily_budget": round(self.rng.uniform(10, 200), 2),
            "total_budget": round(self.rng.uniform(300, 10000), 2),
            "start_date": start_date,
            "end_date": None,
            "target_market": self.rng.choice(["US", "EU", "JP", "ALL"]),
        }


class AdGroupGenerator(BaseGenerator):
    """广告组生成器。"""

    entity_name = "ad_group"

    def generate_one(self, ctx: "GenerationContext") -> dict:  # noqa: F821
        camp_count = ctx.count("campaign")
        camp_idx = self.rng.randint(0, max(0, camp_count - 1))

        return {
            "ad_group_id": ctx.next_id("ad_group", "AG"),
            "campaign_id": f"$ref:campaign:{camp_idx}",
            "name": f"AdGroup-{self.rng.randint(1, 50)}",
            "default_bid": round(self.rng.uniform(0.1, 5.0), 2),
            "status": "ACTIVE",
        }


class AdGenerator(BaseGenerator):
    """广告生成器。"""

    entity_name = "ad"

    def generate_one(self, ctx: "GenerationContext") -> dict:  # noqa: F821
        ag_count = ctx.count("ad_group")
        ag_idx = self.rng.randint(0, max(0, ag_count - 1))

        return {
            "ad_id": ctx.next_id("ad", "AD"),
            "ad_group_id": f"$ref:ad_group:{ag_idx}",
            "type": self.rng.choice(["KEYWORD", "PRODUCT", "ASIN", "AUTO"]),
            "status": "ACTIVE",
            "bid": round(self.rng.uniform(0.05, 3.0), 2),
        }


class SpendRecordGenerator(BaseGenerator):
    """广告花费记录生成器 — 每日花费数据。"""

    entity_name = "spend_record"

    def generate_one(self, ctx: "GenerationContext") -> dict:  # noqa: F821
        return {}

    def generate_many(self, ctx: "GenerationContext",
                      count: int | None = None) -> list[dict]:
        ads = ctx.get_entities("ad")
        if not ads:
            return []

        if count is None:
            count = self.profile.entity_count(self.entity_name)

        results = []
        for i in range(count):
            ad_idx = self.rng.randint(0, len(ads) - 1)
            day = datetime.now() - timedelta(days=self.rng.randint(1, 90))
            spend = round(self.rng.uniform(0.5, 50), 2)
            impressions = self.rng.randint(100, 10000)
            clicks = int(impressions * self.rng.uniform(0.005, 0.05))
            conversions = int(clicks * self.rng.uniform(0.02, 0.15))

            results.append({
                "spend_id": ctx.next_id("spend_record", "SPD"),
                "ad_id": ads[ad_idx].get("ad_id", f"$ref:ad:{ad_idx}"),
                "date": day.strftime("%Y-%m-%d"),
                "spend": spend,
                "impressions": impressions,
                "clicks": clicks,
                "conversions": conversions,
                "sales": round(conversions * self.rng.uniform(15, 80), 2),
            })

        return results


class PerformanceMetricGenerator(BaseGenerator):
    """广告效果指标生成器。"""

    entity_name = "performance_metric"

    def generate_one(self, ctx: "GenerationContext") -> dict:  # noqa: F821
        return {}

    def generate_many(self, ctx: "GenerationContext",
                      count: int | None = None) -> list[dict]:
        ads = ctx.get_entities("ad")
        skus = ctx.get_entities("sku")
        if not ads or not skus:
            return []

        if count is None:
            count = self.profile.entity_count(self.entity_name)

        results = []
        for i in range(count):
            ad_idx = self.rng.randint(0, len(ads) - 1)
            sku_idx = self.rng.randint(0, len(skus) - 1)

            sales = round(self.rng.uniform(0, 500), 2)
            spend = round(self.rng.uniform(0, sales * 0.4), 2)
            clicks = max(1, self.rng.randint(0, 100))

            results.append({
                "metric_id": ctx.next_id("performance_metric", "PM"),
                "ad_id": ads[ad_idx].get("ad_id", f"$ref:ad:{ad_idx}"),
                "sku_id": skus[sku_idx].get("sku_id", f"$ref:sku:{sku_idx}"),
                "date": (datetime.now() - timedelta(days=self.rng.randint(1, 30))).strftime("%Y-%m-%d"),
                "attributed_units": self.rng.randint(0, 10),
                "attributed_sales": sales,
                "cpc": round(spend / clicks, 2) if clicks > 0 else 0,
                "ctr": round(self.rng.uniform(0.001, 0.05), 4),
                "acos": round(spend / sales * 100, 2) if sales > 0 else 0,
                "tacos": round(spend / (sales * 1.5) * 100, 2) if sales > 0 else 0,
                "roas": round(sales / spend, 2) if spend > 0 else 0,
            })

        return results
