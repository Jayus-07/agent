"""workflows/daily_report.py — 经营日报 Workflow（Phase 1 示例）

架构（按企业方案）：
- Workflow 编排确定性流程（不调 Planner）
- 每个 step 是结构化操作（数据获取 / RAG 检索 / Agent 分析 / 报告 / 邮件）
- DAG 自动分层并行：fetch_sales + fetch_inventory + fetch_promotions 并行

Step 设计：
- Layer 0 (并行): fetch_sales, fetch_inventory, fetch_promotions
- Layer 1:        rag_query_template（依赖 3 个 fetch）
- Layer 2:        agent_analyze（依赖所有 fetch + rag）
- Layer 3:        generate_report
- Layer 4:        send_email
"""
from __future__ import annotations

from backend.orchestration.workflow import workflow, step
from backend.orchestration.workflow.skill_adapter import (
    call_sql,
    call_rag,
    call_report,
    call_email,
)
from backend.orchestration.capability.inventory_analyzer import InventoryAnalyzer
from backend.shared.logger import logger


@workflow(
    name="daily_report",
    description="每日经营日报 — SQL 拉数据 + RAG 查模板 + Agent 分析异常 + 报告 + 邮件",
    objects=["日报", "销售", "运营", "经营"],
    actions=["生成", "发送", "导出"],
    examples=[
        "生成今天的经营日报",
        "跑一下今天的销售日报",
        "把今天的日报发给我",
    ],
    default_kbs=["analytics"],
    category="report",
)
class DailyReport:
    """每日经营日报 — 6 个 Step，4 个并行层"""

    @step()
    async def fetch_sales(self, ctx):
        """Step 1: 拉今日销售数据（SQL）"""
        logger.info("[DailyReport] Step fetch_sales 开始")
        result = await call_sql({
            "query": "SELECT product_id, SUM(qty) as total_qty, SUM(amount) as total_amount "
                     "FROM sales WHERE date = today GROUP BY product_id"
        })
        return {"sales": result.get("rows", result)}

    @step()
    async def fetch_inventory(self, ctx):
        """Step 2: 拉当前库存（SQL）— 与 fetch_sales 并行"""
        logger.info("[DailyReport] Step fetch_inventory 开始")
        result = await call_sql({
            "query": "SELECT product_id, product_name, current_qty, min_qty "
                     "FROM inventory WHERE current_qty > 0"
        })
        return {"inventory": result.get("rows", result)}

    @step()
    async def fetch_promotions(self, ctx):
        """Step 3: 拉近期活动（SQL）— 与 fetch_sales/fetch_inventory 并行"""
        logger.info("[DailyReport] Step fetch_promotions 开始")
        result = await call_sql({
            "query": "SELECT promotion_id, product_id, discount, started_at, ended_at "
                     "FROM promotions WHERE ended_at > today"
        })
        return {"promotions": result.get("rows", result)}

    @step(
        depends_on=["fetch_sales", "fetch_inventory", "fetch_promotions"],
        timeout_sec=30,
        on_error="skip",
    )
    async def rag_query_template(self, ctx):
        """Step 4: 查日报模板（RAG）"""
        logger.info("[DailyReport] Step rag_query_template 开始")
        result = await call_rag({
            "query": "日报模板 章节结构 异常判断标准",
            "kb_id": "analytics",
            "top_k": 3,
        })
        return {"template": result.get("answer", result)}

    @step(
        depends_on=["fetch_sales", "fetch_inventory", "fetch_promotions", "rag_query_template"],
        timeout_sec=60,
        on_error="skip",
    )
    async def agent_analyze(self, ctx):
        """Step 5: Business Agent 分析异常（InventoryAnalyzer）

        注：这里调的是 Business Agent Skill（不是 Planner）。
        因为日报的"分析库存异常"是已知任务，输入数据格式固定。
        """
        logger.info("[DailyReport] Step agent_analyze 开始")
        analyzer = InventoryAnalyzer()
        result = await analyzer.run({
            "sales_data": ctx.outputs.get("fetch_sales", {}).get("sales", []),
            "inventory_data": ctx.outputs.get("fetch_inventory", {}).get("inventory", []),
            "rules": ctx.outputs.get("rag_query_template", {}).get("template", ""),
            "alert_level": "warning",
        })
        return {"analysis": result}

    @step(
        depends_on=["agent_analyze"],
        timeout_sec=60,
    )
    async def generate_report(self, ctx):
        """Step 6: 生成报告（Report Skill）"""
        logger.info("[DailyReport] Step generate_report 开始")
        result = await call_report({
            "template": "daily_report",
            "data": {
                "sales": ctx.outputs.get("fetch_sales", {}).get("sales", []),
                "inventory": ctx.outputs.get("fetch_inventory", {}).get("inventory", []),
                "promotions": ctx.outputs.get("fetch_promotions", {}).get("promotions", []),
                "analysis": ctx.outputs.get("agent_analyze", {}).get("analysis", {}),
                "template": ctx.outputs.get("rag_query_template", {}).get("template", ""),
            },
        })
        return {"report": result.get("content", result)}

    @step(
        depends_on=["generate_report"],
        timeout_sec=60,
        retry=2,
        on_error="abort",
    )
    async def send_email(self, ctx):
        """Step 7: 发邮件 + 写 daily_reports 表"""
        logger.info("[DailyReport] Step send_email 开始")
        report_output = ctx.outputs.get("generate_report", {}).get("report", {})
        if isinstance(report_output, str):
            body = report_output
        elif isinstance(report_output, dict):
            body = (
                f"## 销售摘要\n{report_output.get('sales_summary', '')}\n\n"
                f"## 库存预警\n{report_output.get('inventory_alerts', '')}\n\n"
                f"## Agent 分析\n{report_output.get('agent_analysis', '')}\n"
            )
        else:
            body = str(report_output)

        from datetime import date
        today = date.today().isoformat()

        # 提取 KPI 摘要（从上游 step output）
        sales = ctx.outputs.get("fetch_sales", {}).get("sales", [])
        inventory = ctx.outputs.get("fetch_inventory", {}).get("inventory", [])
        alerting = [i for i in inventory if isinstance(i, dict) and i.get("current_qty", 999) < i.get("min_qty", 0)]

        kpi_summary = {
            "total_products": len(inventory),
            "alert_count": len(alerting),
            "sales_records": len(sales),
            "report_date": today,
        }

        # 写 daily_reports 表
        from backend.seed.demo.runner import get_daily_report_store
        report_store = get_daily_report_store()
        report_store.save({
            "id": ctx.run_id,
            "report_date": today,
            "report_content": f"# 经营日报 {today}\n\n{body}",
            "kpi_summary": kpi_summary,
            "trace_id": ctx.trace_id or "",
            "status": "success",
        })
        logger.info(f"[DailyReport] 日报已写入 daily_reports: {ctx.run_id}")

        # 发邮件
        result = await call_email({
            "to": ["ops@demo.local", "ceo@demo.local"],
            "subject": f"[经营日报] {today}",
            "body": f"# 经营日报 {today}\n\n{body}",
        })
        return {"email": result, "report_id": ctx.run_id}


__all__ = ["DailyReport"]