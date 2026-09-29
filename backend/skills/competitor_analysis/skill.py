"""skills/competitor_analysis/skill.py — Competitor Analysis Skill.
Capabilities: competitor.analyze / competitor.watch / competitor.history
（监控列表管理 list/add/remove/toggle 由 Planner 以 analyze+action 参数
表达，_select_tool 分发到 competitor_watchlist_tool）
"""
from backend.skills.base import BaseSkill
from backend.shared.logger import logger
from backend.tools.competitor import (
    competitor_analyze_tool,
    competitor_history_tool,
    competitor_watch_tool,
    competitor_watchlist_tool,
)


class CompetitorAnalysisSkill(BaseSkill):
    """竞品分析 Skill — 抓取竞品页面，抽取价格/促销/评价，快照存档与变价对比"""

    name = "competitor_analysis"
    # STOP G M1 显式声明（禁止隐式默认）：四个 capability 全部 Markdown 报告，text 型。
    # 单次页面抓取自身 timeout=90s（crawler_runtime），Skill 层必须大于它，
    # 否则抓取未完成就被判超时重试，重复发起完整抓取流程
    output_type = "text"
    default_timeout = 120.0

    @property
    def _tool_fn(self):
        return competitor_analyze_tool

    def _select_tool(self, capability: str, params: dict):
        """按 capability/action 分发到单职责 Tool，并把参数过滤到
        目标 Tool 签名内（LangChain invoke 遇未知参数会直接抛错）。"""
        action = params.get("action") or ""
        if action == "watch" or capability == "competitor.watch":
            tool = competitor_watch_tool
        elif action == "history" or capability == "competitor.history":
            tool = competitor_history_tool
        elif action in ("add", "remove", "toggle", "list"):
            tool = competitor_watchlist_tool
        else:
            tool = competitor_analyze_tool
        allowed = set(getattr(tool, "args", {}) or {})
        return tool, {k: v for k, v in params.items() if k in allowed}


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
