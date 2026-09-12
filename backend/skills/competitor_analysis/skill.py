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
    # 单次页面抓取自身 timeout=90s（crawler_runtime），Skill 层必须大于它，
    # 否则抓取未完成就被判超时重试，重复发起完整抓取流程
    default_timeout = 120.0
    description = (
        "竞品分析：抓取竞品商品页/官网，抽取价格、促销、评价数等结构化信息，"
        "存为快照并与历史对比（识别涨价/降价）。支持监控列表管理与价格历史查询。"
        "适合'分析这个竞品链接'、'竞品最近降价了吗'、'巡检所有监控的竞品'等请求。"
    )
    params_schema = {
        "action": {
            "type": "string", "required": False,
            "enum": ["analyze", "watch", "history", "add", "remove", "toggle", "list"],
            "description": "操作类型（默认 analyze）",
        },
        "url": {"type": "string", "required": False, "description": "竞品页面完整 URL（analyze/history/add/remove/toggle 需要）"},
        "name": {"type": "string", "required": False, "description": "竞品名称（add 时可选）"},
        "question": {"type": "string", "required": False, "description": "用户原始问题（其中的 URL 会被自动提取）"},
        "enabled": {"type": "boolean", "required": False, "description": "toggle 时是否启用（默认 True）"},
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
