"""BaseFactory — 实体工厂基类。

调用 Generator 获取原始数据，解析 FK 引用，产出最终实体并注册到 Context。
"""

from __future__ import annotations

import re
from typing import Any


class BaseFactory:
    """实体工厂基类。

    职责:
    1. 调用 Generator.generate_one() 获取原始 dict
    2. 解析所有 "$ref:*" 占位符为实际 FK 值
    3. 将生成的实体注册到 GenerationContext

    用法:
        class BrandFactory(BaseFactory):
            def _resolve_fks(self, raw: dict) -> dict:
                return raw  # Brand 没有 FK
    """

    _REF_PATTERN = re.compile(r"\$ref:[a-z_]+\:\d+")

    def __init__(self, generator: "BaseGenerator", ctx: "GenerationContext"):  # noqa: F821
        self.generator = generator
        self.ctx = ctx

    def create(self, **overrides: Any) -> dict:
        """创建单条实体。

        Args:
            **overrides: 覆盖 Generator 生成的字段
        """
        raw = self.generator.generate_one(self.ctx)
        raw.update(overrides)
        return self._resolve_fks(raw)

    def create_batch(self, count: int | None = None) -> list[dict]:
        """批量创建实体，并自动注册到 Context。

        Args:
            count: 数量。None 时从 Profile 自动读取
        """
        if count is None:
            count = self.generator.profile.entity_count(self.generator.entity_name)
        results = []
        for _ in range(count):
            entity = self.create()
            results.append(entity)
        self.ctx.register_batch(self.generator.entity_name, results)
        return results

    def _resolve_fks(self, raw: dict) -> dict:
        """解析所有 $ref:entity:index 引用。

        子类可重写此方法以添加自定义 FK 解析逻辑。
        默认实现: 遍历所有以 '_id' 结尾的字段，对值调用 ctx.resolve_ref()。
        """
        resolved = {}
        for key, value in raw.items():
            if isinstance(value, str) and value.startswith("$ref:"):
                resolved[key] = self.ctx.resolve_ref(value)
            elif isinstance(value, dict):
                resolved[key] = self._resolve_fks(value)
            elif isinstance(value, list):
                resolved[key] = [
                    self.ctx.resolve_ref(v) if isinstance(v, str) and v.startswith("$ref:") else v
                    for v in value
                ]
            else:
                resolved[key] = value
        return resolved
