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
        on_error="abort",  # 邮件失败应该让 workflow 失败（不能假装成功）
    )
    async def send_email(self, ctx):
        """Step 7: 发邮件给运营 + CEO（Email Skill）"""
        logger.info("[DailyReport] Step send_email 开始")
        report = ctx.outputs.get("generate_report", {}).get("report", {})
        # report 可能是 string（call_report 返回 {"content": "..."}）或 dict
        if isinstance(report, str):
            body = report
        elif isinstance(report, dict):
            body = (
                f"## 销售摘要\n{report.get('sales_summary', '')}\n\n"
                f"## 库存预警\n{report.get('inventory_alerts', '')}\n\n"
                f"## Agent 分析\n{report.get('agent_analysis', '')}\n"
            )
        else:
            body = str(report)

        from datetime import date
        today = date.today().isoformat()
        result = await call_email({
            "to": ["ops@demo.local", "ceo@demo.local"],
            "subject": f"[经营日报] {today}",
            "body": f"# 经营日报 {today}\n\n{body}",
        })
        return {"email": result}


__all__ = ["DailyReport"]