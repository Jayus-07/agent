"""BaseGenerator — 单实体生成器抽象基类。"""

from __future__ import annotations

import random
from abc import ABC, abstractmethod
from typing import Generic, TypeVar

T = TypeVar("T")


class BaseGenerator(ABC, Generic[T]):
    """单实体生成器基类。

    子类只需实现 entity_name 属性和 generate_one() 方法。
    generate_many() 自动从 Profile 读取数量。

    用法:
        class BrandGenerator(BaseGenerator):
            entity_name = "brand"

            def generate_one(self, ctx: "GenerationContext") -> dict:  # noqa: F821
                return {
                    "brand_id": ctx.next_id(self.entity_name, "B"),
                    "name": ctx.rng.choice(BRANDS),
                }
    """

    def __init__(self, rng: random.Random, profile: "SeedProfile"):  # noqa: F821
        self.rng = rng
        self.profile = profile

    @property
    @abstractmethod
    def entity_name(self) -> str:
        """对应 Profile 中的实体名称（如 "brand", "sku"）。"""
        ...

    @abstractmethod
    def generate_one(self, ctx: "GenerationContext") -> dict:  # noqa: F821
        """生成一条实体数据（dict），不做 FK 解析和外键组装。

        外键字段应使用 "$ref:entity:index" 占位符，由 Factory._resolve_fks() 统一处理。
        """
        ...

    def generate_many(self, ctx: "GenerationContext",  # noqa: F821
                      count: int | None = None) -> list[dict]:
        """生成多条实体。

        Args:
            ctx: 生成上下文
            count: 数量。None 时从 Profile 自动读取
        """
        if count is None:
            count = self.profile.entity_count(self.entity_name)
        return [self.generate_one(ctx) for _ in range(count)]
