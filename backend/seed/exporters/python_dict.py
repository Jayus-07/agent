"""DictExporter — 返回 Python dict（测试用，直接内存访问）。"""

from __future__ import annotations

from backend.seed.core.context import GenerationContext


class DictExporter:
    """将 Context 中所有实体导出为嵌套 dict。

    用法:
        exporter = DictExporter()
        data = exporter.export(ctx)
        print(data["brand"])     # [{"brand_id": "B0001", ...}, ...]
        print(data["product"])   # [{"product_id": "P0001", ...}, ...]
    """

    def export(self, ctx: GenerationContext) -> dict[str, list[dict]]:
        """导出所有实体。

        Returns:
            {entity_name: [entity_dict, ...], ...}
        """
        result = {}
        for entity_name in ctx.entity_names:
            result[entity_name] = ctx.get_entities(entity_name)
        return result

    def export_summary(self, ctx: GenerationContext) -> dict:
        """导出摘要信息（计数 + Profile）。"""
        return {
            "profile": ctx.profile.name,
            "seed": ctx.seed,
            "entity_counts": ctx.summary(),
        }
