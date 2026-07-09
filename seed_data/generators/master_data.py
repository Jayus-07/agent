"""Master Data 生成器 — Brand, Category, Channel, Warehouse, Supplier。

这些是"基础数据"，几乎所有其他实体都依赖它们。
"""

from __future__ import annotations

import random

from seed_data.core.generator import BaseGenerator
from seed_data.utils import constants


class BrandGenerator(BaseGenerator):
    """品牌生成器 — 从 constants.BRANDS 枚举中取。"""

    entity_name = "brand"

    def generate_one(self, ctx: "GenerationContext") -> dict:  # noqa: F821
        brand_data = self.rng.choice(constants.BRANDS)
        return {
            "brand_id": ctx.next_id("brand", "B"),
            "name": brand_data["name"],
            "trademark_no": brand_data["trademark_no"],
            "owner": brand_data["owner"],
            "status": "ACTIVE",
        }


class CategoryGenerator(BaseGenerator):
    """类目生成器 — 从 constants.CATEGORY_TREE 构建层级树。

    策略:
    1. 将所有叶子路径展平为列表
    2. 生成时按深度展开 parent_id 引用
    """

    entity_name = "category"

    def __init__(self, rng: random.Random, profile: "SeedProfile"):  # noqa: F821
        super().__init__(rng, profile)
        # 预计算类目路径列表
        self._all_paths: list[list[str]] = self._flatten_tree(constants.CATEGORY_TREE)

    @staticmethod
    def _flatten_tree(tree: dict, parent_path: tuple = ()) -> list[list[str]]:
        """展平类目树为路径列表。

        例: [["家居用品", "厨房用品", "收纳整理"], ...]
        """
        paths = []
        for cat_name, children in tree.items():
            current = list(parent_path) + [cat_name]
            if isinstance(children, list):
                for leaf in children:
                    paths.append(current + [leaf])
            elif isinstance(children, dict):
                paths.extend(CategoryGenerator._flatten_tree(children, tuple(current)))
        return paths

    def _pick_paths(self, count: int, max_depth: int) -> list[list[str]]:
        """从类目树中选择 count 条路径。

        生成所有可能路径（包括截断路径），去重后采样。
        例: 全路径 ["家居用品", "厨房用品", "收纳整理"] (深度3)
        在 max_depth=2 时也会产出 ["家居用品", "厨房用品"]
        """
        # 从全路径生成所有可能的截断路径
        all_candidates: list[tuple[str, ...]] = []
        for full_path in self._all_paths:
            for end in range(1, min(len(full_path), max_depth) + 1):
                all_candidates.append(tuple(full_path[:end]))

        # 去重后排序（保证跨进程可复现，抵消 PYTHONHASHSEED 随机化）
        unique = sorted(set(all_candidates))
        self.rng.shuffle(unique)

        result = unique[:count]
        # 不够时循环采样
        while len(result) < count:
            result.append(self.rng.choice(unique))
        return [list(p) for p in result[:count]]

    def generate_many(self, ctx: "GenerationContext",  # noqa: F821
                      count: int | None = None) -> list[dict]:
        """生成完整的类目树（需要特殊处理层级关系）。"""
        if count is None:
            count = self.profile.entity_count(self.entity_name)
        spec = self.profile.entity_spec(self.entity_name)
        max_depth = spec.max_depth or 3

        paths = self._pick_paths(count, max_depth)

        # 去重收集所有唯一节点名
        seen: dict[str, str] = {}  # name → category_id
        categories: list[dict] = []

        for path in paths:
            parent_id = None
            for depth, name in enumerate(path):
                if name in seen:
                    parent_id = seen[name]
                    continue
                cat_id = ctx.next_id("category", "CAT")
                cat = {
                    "category_id": cat_id,
                    "parent_id": parent_id,
                    "name": name,
                    "depth": depth,
                    "status": "ACTIVE",
                }
                seen[name] = cat_id
                parent_id = cat_id
                categories.append(cat)

        return categories

    def generate_one(self, ctx: "GenerationContext") -> dict:  # noqa: F821
        """单条生成（通常不用，类目用 generate_many 批量生成）。"""
        path = self.rng.choice(self._all_paths)
        return {
            "category_id": ctx.next_id("category", "CAT"),
            "parent_id": "$ref:category:0",  # 占位符，由 generate_many 特殊处理
            "name": path[-1],
            "depth": len(path) - 1,
        }


class ChannelGenerator(BaseGenerator):
    """销售渠道生成器 — 固定枚举，不随机。"""

    entity_name = "channel"

    def generate_one(self, ctx: "GenerationContext") -> dict:  # noqa: F821
        channel_data = self.rng.choice(constants.CHANNELS)
        return {
            "channel_id": ctx.next_id("channel", "CH"),
            "code": channel_data["code"],
            "name": channel_data["name"],
            "country": channel_data["country"],
            "default_currency": channel_data["default_currency"],
            "status": "ACTIVE",
        }

    def generate_many(self, ctx: "GenerationContext",  # noqa: F821
                      count: int | None = None) -> list[dict]:
        """渠道是确定性枚举 — 按 Profile 数量取前 N 个。"""
        if count is None:
            count = self.profile.entity_count(self.entity_name)
        count = min(count, len(constants.CHANNELS))
        results = []
        for ch in constants.CHANNELS[:count]:
            entity = {
                "channel_id": ctx.next_id("channel", "CH"),
                "code": ch["code"],
                "name": ch["name"],
                "country": ch["country"],
                "default_currency": ch["default_currency"],
                "status": "ACTIVE",
            }
            results.append(entity)
        return results

    def generate_one(self, ctx: "GenerationContext") -> dict:  # noqa: F821
        # 单条生成不支持（应使用 generate_many）
        return self.generate_many(ctx, count=1)[0]


class WarehouseGenerator(BaseGenerator):
    """仓库生成器 — 固定枚举。"""

    entity_name = "warehouse"

    def generate_one(self, ctx: "GenerationContext") -> dict:  # noqa: F821
        wh = self.rng.choice(constants.WAREHOUSES)
        return {
            "warehouse_id": ctx.next_id("warehouse", "WH"),
            "code": wh["code"],
            "name": wh["name"],
            "type": wh["type"],
            "country": wh["country"],
            "region": wh["region"],
            "address": wh["address"],
            "is_active": wh["is_active"],
        }

    def generate_many(self, ctx: "GenerationContext",  # noqa: F821
                      count: int | None = None) -> list[dict]:
        """仓库是确定性枚举。"""
        if count is None:
            count = self.profile.entity_count(self.entity_name)
        count = min(count, len(constants.WAREHOUSES))
        results = []
        for wh in constants.WAREHOUSES[:count]:
            entity = {
                "warehouse_id": ctx.next_id("warehouse", "WH"),
                "code": wh["code"],
                "name": wh["name"],
                "type": wh["type"],
                "country": wh["country"],
                "region": wh["region"],
                "address": wh["address"],
                "is_active": wh["is_active"],
            }
            results.append(entity)
        return results


class SupplierGenerator(BaseGenerator):
    """供应商生成器 — 混合确定性城市 + Faker 随机人名。"""

    entity_name = "supplier"

    def generate_one(self, ctx: "GenerationContext") -> dict:  # noqa: F821
        city = self.rng.choice(constants.SUPPLIER_CITIES)
        sup_type = self.rng.choice(constants.SUPPLIER_TYPES)
        payment = self.rng.choice(constants.PAYMENT_TERMS)

        # Faker 生成公司名（中英文混合）
        if self.rng.random() < 0.5:
            company_name = ctx.faker.company()
        else:
            prefixes = ["伟", "鑫", "宏", "瑞", "恒", "德", "盛", "华"]
            suffixes = ["电子", "塑胶", "五金", "纺织", "包装", "模具", "科技", "实业"]
            company_name = (f"{city}{self.rng.choice(prefixes)}{self.rng.choice(suffixes)}"
                            f"有限公司")

        return {
            "supplier_id": ctx.next_id("supplier", "SUP"),
            "name": company_name,
            "type": sup_type,
            "country": "CN",
            "city": city,
            "contact_name": ctx.faker.name(),
            "contact_email": ctx.faker.email(),
            "contact_phone": ctx.faker.phone_number(),
            "payment_terms": payment,
            "cooperation_status": self.rng.choice(["ACTIVE", "ACTIVE", "ACTIVE", "INACTIVE"]),
            "rating": self.rng.randint(3, 5),
        }
