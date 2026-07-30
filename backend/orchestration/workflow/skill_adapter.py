"""workflow/skill_adapter.py — Skill 适配器

让现有 BaseSkill（SQL/RAG/Report/Email/...）能在 workflow step 里被便捷调用。

用法：
    from orchestration.workflow.skill_adapter import call_sql, call_rag, call_report, call_email

    @step()
    async def fetch_sales(self, ctx):
        return await call_sql({"query": "SELECT * FROM sales WHERE date=today"})
"""
from __future__ import annotations

from typing import Any

from backend.shared.logger import logger


def _build_state(step_id: str, capability: str, params: dict) -> dict:
    """构造 BaseSkill.execute() 所需的最小 state dict"""
    return {
        "current_step_id": step_id,
        "plan": {
            "nodes": {
                step_id: {
                    "capability": capability,
                    "params": params,
                }
            }
        },
        "step_results": {},
    }


def _extract_output(state: dict, step_id: str) -> dict:
    """从 BaseSkill.execute() 返回值提取 step output"""
    return state.get("step_results", {}).get(step_id, {}).get("output", {})


async def call_skill(skill_name: str, capability: str, params: dict) -> dict:
    """通用 Skill 调用入口

    Args:
        skill_name: Skill 名（"sql" / "rag" / "report" / "email" / ...）
        capability: capability 名（如 "sql.query"）
        params: 输入参数

    Returns:
        dict: Skill 输出
    """
    from backend.orchestration.skills.base import BaseSkill
    from backend.orchestration.skills.sql.skill import SQLSkill
    from backend.orchestration.skills.rag.skill import RAGSkill
    from backend.orchestration.skills.report.skill import ReportSkill
    from backend.orchestration.skills.email.skill import EmailSkill
    from backend.orchestration.skills.data_export.skill import DataExportSkill
    from backend.orchestration.skills.web_search.skill import WebSearchSkill
    from backend.orchestration.skills.web_crawl.skill import WebCrawlSkill

    skill_map: dict[str, type] = {
        "sql": SQLSkill,
        "rag": RAGSkill,
        "report": ReportSkill,
        "email": EmailSkill,
        "data_export": DataExportSkill,
        "web_search": WebSearchSkill,
        "web_crawl": WebCrawlSkill,
    }
    skill_cls = skill_map.get(skill_name)
    if skill_cls is None:
        raise ValueError(
            f"Unknown skill: {skill_name!r}, supported: {list(skill_map.keys())}"
        )

    skill = skill_cls()
    state = _build_state(skill_name, capability, params)
    result = await skill.execute(state, step_capability=capability)
    output = _extract_output(result, skill_name)
    if not output:
        logger.warning(f"[SkillAdapter] {skill_name}:{capability} 输出为空")
    return output


# 便捷封装（按 capability 命名）
async def call_sql(params: dict) -> dict:
    """调 SQL Skill

    params: {"query": "SELECT ..."} 或包含 question 等
    """
    return await call_skill("sql", "sql.query", params)


async def call_rag(params: dict) -> dict:
    """调 RAG Skill

    params: {"query": "...", "kb_id": "analytics", "top_k": 5}
    """
    return await call_skill("rag", "rag.search", params)


async def call_report(params: dict) -> dict:
    """调 Report Skill

    params: {"template": "daily_report", "data": {...}}
    """
    return await call_skill("report", "report.generate", params)


async def call_email(params: dict) -> dict:
    """调 Email Skill

    params: {"to": [...], "subject": "...", "body": "..."}
    """
    return await call_skill("email", "email.send", params)