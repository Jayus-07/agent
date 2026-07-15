"""报表域生成器 — ReportDefinition, ReportExecution。

提供日报/周报/月报模板定义和模拟执行记录。
"""

from __future__ import annotations

import random
from datetime import datetime, timedelta

from backend.seed.core.generator import BaseGenerator

REPORT_TEMPLATES = [
    {
        "name": "每日销售报表",
        "category": "SALES",
        "description": "每日销售额、订单数、客单价汇总",
        "schedule_cron": "0 8 * * *",
    },
    {
        "name": "每周品类表现",
        "category": "SALES",
        "description": "各品类销售额、环比增长率、退货率",
        "schedule_cron": "0 9 * * 1",
    },
    {
        "name": "库存预警日报",
        "category": "INVENTORY",
        "description": "低库存/滞销 SKU 预警清单",
        "schedule_cron": "0 7 * * *",
    },
    {
        "name": "广告效果周报",
        "category": "AD",
        "description": "ACoS/TACoS/ROAS 按 Campaign 汇总",
        "schedule_cron": "0 10 * * 1",
    },
    {
        "name": "月度利润核算",
        "category": "FINANCE",
        "description": "渠道利润核算：收入 - 成本 - 平台费 - 广告费 - 物流费",
        "schedule_cron": "0 8 1 * *",
    },
    {
        "name": "供应商绩效评分",
        "category": "OPERATIONS",
        "description": "交期达标率、质量合格率、价格竞争力",
        "schedule_cron": "0 9 1 * *",
    },
    {
        "name": "客户满意度报告",
        "category": "SALES",
        "description": "评论评分分布、差评趋势、退货原因 TOP 10",
        "schedule_cron": "0 10 * * 1",
    },
    {
        "name": "库存周转报告",
        "category": "INVENTORY",
        "description": "各仓库存周转天数、sell-through rate",
        "schedule_cron": "0 8 * * 5",
    },
]


class ReportDefinitionGenerator(BaseGenerator):
    """报表定义生成器。"""

    entity_name = "report_definition"

    def generate_one(self, ctx: "GenerationContext") -> dict:  # noqa: F821
        tmpl = self.rng.choice(REPORT_TEMPLATES)
        return {
            "report_id": ctx.next_id("report_definition", "RPT"),
            "name": tmpl["name"],
            "description": tmpl["description"],
            "category": tmpl["category"],
            "schedule_cron": tmpl["schedule_cron"],
            "owner_id": f"user_{self.rng.randint(1, 20):03d}",
        }

    def generate_many(self, ctx: "GenerationContext",
                      count: int | None = None) -> list[dict]:
        if count is None:
            count = self.profile.entity_count(self.entity_name)
        count = min(count, len(REPORT_TEMPLATES))
        results = []
        for i in range(count):
            tmpl = REPORT_TEMPLATES[i]
            results.append({
                "report_id": ctx.next_id("report_definition", "RPT"),
                "name": tmpl["name"],
                "description": tmpl["description"],
                "category": tmpl["category"],
                "schedule_cron": tmpl["schedule_cron"],
                "owner_id": f"user_{self.rng.randint(1, 20):03d}",
            })
        return results


class ReportExecutionGenerator(BaseGenerator):
    """报表执行记录生成器。"""

    entity_name = "report_execution"

    def generate_one(self, ctx: "GenerationContext") -> dict:  # noqa: F821
        return {}

    def generate_many(self, ctx: "GenerationContext",
                      count: int | None = None) -> list[dict]:
        reports = ctx.get_entities("report_definition")
        if not reports:
            return []

        if count is None:
            count = self.profile.entity_count(self.entity_name)

        results = []
        for i in range(count):
            rpt_idx = self.rng.randint(0, len(reports) - 1)
            started = datetime.now() - timedelta(hours=self.rng.randint(1, 720))
            finished = started + timedelta(seconds=self.rng.randint(1, 30))
            status = self.rng.choice(["SUCCESS", "SUCCESS", "SUCCESS", "FAILED"])

            results.append({
                "execution_id": ctx.next_id("report_execution", "EXEC"),
                "report_id": reports[rpt_idx].get("report_id", f"$ref:report_definition:{rpt_idx}"),
                "status": status,
                "started_at": started.isoformat(),
                "finished_at": finished.isoformat(),
                "result_url": f"/reports/{reports[rpt_idx].get('report_id', 'RPT0001')}/result-{i}.json" if status == "SUCCESS" else None,
                "error": "Timeout fetching data source" if status == "FAILED" and self.rng.random() < 0.5 else None,
            })

        return results
