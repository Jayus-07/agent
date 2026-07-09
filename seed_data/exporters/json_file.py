"""JsonFileExporter — 导出 JSON 文件到磁盘（CI 用）。"""

from __future__ import annotations

import json
import os
from datetime import datetime
from pathlib import Path

from seed_data.core.context import GenerationContext


class JsonFileExporter:
    """将 Context 中所有实体导出为 JSON 文件。

    输出目录结构:
        data/seed/mvp/
        ├── brand.json
        ├── category.json
        ├── channel.json
        ├── warehouse.json
        ├── supplier.json
        └── _summary.json          # 生成元信息

    用法:
        exporter = JsonFileExporter("data/seed/mvp")
        exporter.export(ctx)
    """

    def __init__(self, output_dir: str = "data/seed"):
        self.output_dir = Path(output_dir)

    def export(self, ctx: GenerationContext) -> None:
        """导出所有实体为 JSON 文件。"""
        self.output_dir.mkdir(parents=True, exist_ok=True)

        for entity_name in ctx.entity_names:
            entities = ctx.get_entities(entity_name)
            if not entities:
                continue
            file_path = self.output_dir / f"{entity_name}.json"
            with open(file_path, "w", encoding="utf-8") as f:
                json.dump(entities, f, ensure_ascii=False, indent=2, default=str)

        # 导出摘要
        summary_path = self.output_dir / "_summary.json"
        summary = {
            "profile": ctx.profile.name,
            "description": ctx.profile.description,
            "seed": ctx.seed,
            "generated_at": datetime.now().isoformat(),
            "entity_counts": ctx.summary(),
        }
        with open(summary_path, "w", encoding="utf-8") as f:
            json.dump(summary, f, ensure_ascii=False, indent=2, default=str)

    def export_entity(self, ctx: GenerationContext, entity_name: str) -> None:
        """导出单个实体类型为 JSON 文件。"""
        entities = ctx.get_entities(entity_name)
        if not entities:
            return
        self.output_dir.mkdir(parents=True, exist_ok=True)
        file_path = self.output_dir / f"{entity_name}.json"
        with open(file_path, "w", encoding="utf-8") as f:
            json.dump(entities, f, ensure_ascii=False, indent=2, default=str)
