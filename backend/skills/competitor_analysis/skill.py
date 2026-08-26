"""skills/competitor_analysis/skill.py — Competitor Analysis Skill.
Capabilities: competitor.analyze / competitor.watch / competitor.history
"""
from backend.skills.base import BaseSkill
from backend.shared.logger import logger
from backend.tools.competitor import competitor_analyze_tool


class CompetitorAnalysisSkill(BaseSkill):
    """竞品分析 Skill — 抓取竞品页面，抽取价格/促销/评价，快照存档与变价对比"""

    name = "competitor_analysis"
    capabilities = ["competitor.analyze", "competitor.watch", "competitor.history"]
    description = (
        "竞品分析：抓取竞品商品页/官网，抽取价格、促销、评价数等结构化信息，"
        "存为快照并与历史对比（识别涨价/降价）。支持监控列表管理与价格历史查询。"
        "适合'分析这个竞品链接'、'竞品最近降价了吗'、'巡检所有监控的竞品'等请求。"
    )
    params_schema = {
        "action": "analyze（分析 URL，默认）| watch（巡检全部监控项）| history（价格历史）| add（加入监控）| remove（移除监控）| toggle（启用/停用）| list（查看监控列表）",
        "url": "竞品页面完整 URL",
        "name": "竞品名称（可选）",
        "question": "用户原始问题（其中的 URL 会被自动提取）",
        "enabled": "toggle 时是否启用（默认 True）",
    }
    examples = [
        {"action": "analyze", "url": "https://item.jd.com/100012043978.html", "question": "帮我分析这个竞品的价格"},
        {"action": "watch", "question": "巡检一下所有监控的竞品"},
        {"action": "history", "url": "https://item.jd.com/100012043978.html", "question": "这个竞品最近价格走势如何"},
        {"action": "add", "url": "https://item.jd.com/100012043978.html", "name": "iPhone 15 Pro Max"},
        {"action": "remove", "url": "https://item.jd.com/100012043978.html"},
    ]

    @property
    def _tool_fn(self):
        return competitor_analyze_tool


async def competitor_analysis_skill_node(state: dict) -> dict:
    """LangGraph 节点适配器 — 由 Supervisor / SkillExecutor 路由到此节点"""
    skill = CompetitorAnalysisSkill()
    cap = (
        state.get("plan", {})
        .get("nodes", {})
        .get(state.get("current_step_id", ""), {})
        .get("capability", "competitor.analyze")
    )
    logger.info(f"[CompetitorAnalysis Skill] cap={cap} step={state.get('current_step_id')}")
    return await skill.execute(state, step_capability=cap)
